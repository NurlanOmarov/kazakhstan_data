"""
API-тесты (авторизация, роли, квоты, экспорт). Требуют реальную БД данных.
Используют временную users.db.
"""
import os
import tempfile
from pathlib import Path

import pytest

# Окружение должно быть настроено ДО импорта auth/app.
os.environ.setdefault("KZ_USERS_DB", tempfile.mktemp(suffix=".db"))
os.environ["KZ_SECURE_COOKIES"] = "0"
os.environ["KZ_JWT_SECRET"] = "test-secret-please-change"
os.environ["KZ_RATELIMIT"] = "0"  # отключаем rate-limit в тестах (общий IP testclient)

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = os.environ.get(
    "KZ_DB_PATH", str(Path(__file__).resolve().parents[1] / "kazakhstan_data.db")
)
HAS_DB = Path(DB_PATH).exists()
pytestmark = pytest.mark.skipif(not HAS_DB, reason="нет файла БД")

import auth
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    # чистая users.db со схемой и админом
    Path(os.environ["KZ_USERS_DB"]).unlink(missing_ok=True)
    c = auth.connect()
    auth.init_schema(c)
    auth.create_user(c, username="admin", password="Admin12345!", role="admin",
                     created_by="test")
    c.close()
    import app as appmod
    return TestClient(appmod.app)


def login(client, u, p):
    return client.post("/api/login", data={"username": u, "password": p})


def test_search_requires_auth(client):
    client.cookies.clear()
    r = client.get("/api/search", params={"surname": "Иванов"})
    assert r.status_code == 401


def test_login_bad_password(client):
    client.cookies.clear()
    r = login(client, "admin", "wrong")
    assert r.status_code == 401


def test_login_and_search(client):
    client.cookies.clear()
    r = login(client, "admin", "Admin12345!")
    assert r.status_code == 200 and r.json()["role"] == "admin"
    r = client.get("/api/search", params={"inn": "751014450225"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert body["results"][0]["Имя"] == "БАГЫЖАН"


def test_role_enforcement(client):
    # создаём обычного пользователя
    client.cookies.clear()
    login(client, "admin", "Admin12345!")
    client.post("/api/admin/users", data={
        "username": "user1", "password": "User12345!", "role": "user"})
    # под обычным юзером админка запрещена
    client.cookies.clear()
    login(client, "user1", "User12345!")
    r = client.get("/api/admin/users")
    assert r.status_code == 403
    # но поиск доступен
    r = client.get("/api/search", params={"inn": "751014450225"})
    assert r.status_code == 200


def test_export_xlsx(client):
    client.cookies.clear()
    login(client, "admin", "Admin12345!")
    r = client.get("/api/export", params={"inn": "751014450225"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"  # xlsx = zip


def test_audit_recorded(client):
    client.cookies.clear()
    login(client, "admin", "Admin12345!")
    r = client.get("/api/admin/audit")
    assert r.status_code == 200
    actions = {e["action"] for e in r.json()["events"]}
    assert "login_success" in actions
    assert "search" in actions


# ---------- Security ----------

def test_health_no_data_leak(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok"}        # без records и без текста ошибок


def test_protected_endpoints_require_auth(client):
    client.cookies.clear()
    for path, params in [
        ("/api/search", {"inn": "1"}),
        ("/api/suggest", {"field": "surname", "q": "Ив"}),
        ("/api/neighbors", {"rowid": "1"}),
        ("/api/export", {"inn": "1"}),
        ("/api/admin/users", {}),
        ("/api/admin/audit", {}),
    ]:
        assert client.get(path, params=params).status_code == 401


def test_admin_endpoints_forbidden_for_user(client):
    client.cookies.clear()
    login(client, "admin", "Admin12345!")
    client.post("/api/admin/users", data={
        "username": "user2", "password": "User12345!", "role": "user"})
    client.cookies.clear()
    login(client, "user2", "User12345!")
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/admin/users", data={
        "username": "x", "password": "Yyyyyyyy1", "role": "user"}).status_code == 403


def test_admin_update_validates_role(client):
    client.cookies.clear()
    login(client, "admin", "Admin12345!")
    client.post("/api/admin/users", data={
        "username": "user3", "password": "User12345!", "role": "user"})
    uid = next(u["id"] for u in client.get("/api/admin/users").json()["users"]
               if u["username"] == "user3")
    # некорректная роль -> 400
    assert client.patch(f"/api/admin/users/{uid}", json={"role": "superadmin"}).status_code == 400
    # нечисловой лимит -> 400
    assert client.patch(f"/api/admin/users/{uid}",
                        json={"daily_search_limit": "abc"}).status_code == 400


def test_short_password_rejected(client):
    client.cookies.clear()
    login(client, "admin", "Admin12345!")
    r = client.post("/api/admin/users", data={
        "username": "weak", "password": "123", "role": "user"})
    assert r.status_code == 400


def test_password_change_revokes_session(client):
    client.cookies.clear()
    login(client, "admin", "Admin12345!")
    client.post("/api/admin/users", data={
        "username": "user4", "password": "User12345!", "role": "user"})
    # сессия user4
    uc = TestClient(__import__("app").app)
    login(uc, "user4", "User12345!")
    assert uc.get("/api/search", params={"inn": "751014450225"}).status_code == 200
    # админ сбрасывает пароль user4 -> старая сессия user4 должна отвалиться
    uid = next(u["id"] for u in client.get("/api/admin/users").json()["users"]
               if u["username"] == "user4")
    client.post(f"/api/admin/users/{uid}/password", data={"password": "Changed12345"})
    assert uc.get("/api/search", params={"inn": "751014450225"}).status_code == 401
