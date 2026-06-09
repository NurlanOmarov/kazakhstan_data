"""
Регрессионные тесты исправлений ИБ:
  - path traversal в SPA-роуте закрыт (файлы вне frontend/dist не отдаются);
  - client_ip берёт ПОСЛЕДНИЙ X-Forwarded-For (защита от спуфинга);
  - повторный /api/2fa/setup при включённом 2FA блокируется (409);
  - fail-fast по KZ_JWT_SECRET в прод-режиме.
Не требуют БД данных — только users.db (создаётся во временном файле).
"""
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("KZ_USERS_DB", tempfile.mktemp(suffix=".db"))
os.environ["KZ_SECURE_COOKIES"] = "0"
os.environ["KZ_JWT_SECRET"] = "test-secret-please-change"
os.environ["KZ_RATELIMIT"] = "0"

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
from fastapi.testclient import TestClient
from starlette.requests import Request


@pytest.fixture(scope="module")
def appmod():
    Path(os.environ["KZ_USERS_DB"]).unlink(missing_ok=True)
    c = auth.connect()
    auth.init_schema(c)
    auth.create_user(c, username="admin", password="Admin12345!", role="admin",
                     created_by="test")
    c.close()
    import app as m
    return m


@pytest.fixture()
def client(appmod):
    cl = TestClient(appmod.app)
    cl.post("/api/login", data={"username": "admin", "password": "Admin12345!"})
    return cl


# ---------- Path traversal ----------
@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "frontend/dist").exists(),
                    reason="нет frontend/dist")
@pytest.mark.parametrize("evil", [
    "../../auth.py", "../../users.db", "../app.py",
    "../../../../etc/passwd", "..%2f..%2fauth.py",
])
def test_spa_path_traversal_blocked(appmod, evil):
    # Прямой вызов обработчика: путь вне dist должен схлопнуться в index.html.
    resp = appmod.spa(evil)
    served = Path(getattr(resp, "path"))
    dist = appmod.FRONTEND_DIST.resolve()
    # Отданный файл обязан лежать внутри frontend/dist.
    served.resolve().relative_to(dist)


# ---------- X-Forwarded-For ----------
def _req_with_xff(appmod, xff):
    scope = {
        "type": "http", "method": "GET", "path": "/", "headers":
        [(b"x-forwarded-for", xff.encode())], "client": ("10.0.0.1", 12345),
    }
    return appmod.client_ip(Request(scope))


def test_client_ip_takes_last_xff(appmod):
    # Клиент подделал первый элемент; реальный (добавлен nginx) — последний.
    assert _req_with_xff(appmod, "1.2.3.4, 203.0.113.9") == "203.0.113.9"
    assert _req_with_xff(appmod, "9.9.9.9") == "9.9.9.9"


def test_client_ip_fallback_to_peer(appmod):
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [],
             "client": ("10.0.0.1", 12345)}
    assert appmod.client_ip(Request(scope)) == "10.0.0.1"


# ---------- 2FA setup не сбрасывает активный 2FA ----------
def test_2fa_setup_blocked_when_enabled(appmod, client):
    # Принудительно помечаем 2FA включённым в users.db.
    uc = auth.connect()
    uid = auth.get_user(uc, "admin")["id"]
    uc.execute("UPDATE users SET totp_secret='X', totp_enabled=1 WHERE id=?", (uid,))
    uc.commit()
    uc.close()
    try:
        r = client.post("/api/2fa/setup")
        assert r.status_code == 409
        # Секрет не должен быть перегенерирован / 2FA не отключён.
        uc = auth.connect()
        row = auth.get_user_by_id(uc, uid)
        uc.close()
        assert row["totp_enabled"] == 1 and row["totp_secret"] == "X"
    finally:
        uc = auth.connect()
        uc.execute("UPDATE users SET totp_secret='', totp_enabled=0 WHERE id=?", (uid,))
        uc.commit()
        uc.close()


# ---------- Fail-fast по JWT-секрету в проде ----------
def test_jwt_secret_required_in_prod(monkeypatch):
    # _secret_key() читает env в момент вызова — reload не нужен.
    monkeypatch.delenv("KZ_JWT_SECRET", raising=False)
    monkeypatch.setenv("KZ_SECURE_COOKIES", "1")
    with pytest.raises(RuntimeError):
        auth._secret_key()
