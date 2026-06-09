"""
Тесты новых возможностей: связи, закладки, история, личная активность,
раскрытие ПДн (аудит), 2FA (TOTP), админ-статистика, поиск по адресу.
Требуют реальную БД данных.
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

DB_PATH = os.environ.get(
    "KZ_DB_PATH", str(Path(__file__).resolve().parents[1] / "kazakhstan_data.db")
)
HAS_DB = Path(DB_PATH).exists()
pytestmark = pytest.mark.skipif(not HAS_DB, reason="нет файла БД")

import auth
import pyotp
from fastapi.testclient import TestClient

INN = "841110350547"  # ДОЩАНОВ — есть адрес и телефон


@pytest.fixture(scope="module")
def client():
    Path(os.environ["KZ_USERS_DB"]).unlink(missing_ok=True)
    c = auth.connect()
    auth.init_schema(c)
    auth.create_user(c, username="admin", password="Admin12345!", role="admin",
                     created_by="test")
    c.close()
    import app as appmod
    cl = TestClient(appmod.app)
    cl.post("/api/login", data={"username": "admin", "password": "Admin12345!"})
    return cl


def _first_rowid(client, **params):
    r = client.get("/api/search", params=params)
    assert r.status_code == 200
    rows = r.json()["results"]
    assert rows, "поиск ничего не вернул"
    return int(rows[0]["_rowid"]), rows[0]


# ---------- Поиск по адресу (список жителей дома) ----------
def test_address_only_search(client):
    rowid, row = _first_rowid(client, inn=INN)
    # берём адресные части из соседей этого человека
    n = client.get("/api/neighbors", params={"rowid": rowid}).json()
    assert n["total"] >= 1
    # адресный поиск по городу+улице+дому должен вернуть жителей
    r = client.get("/api/search", params={
        "city": "HЕКРАСОВСКИИ", "street": "ГАГАРИНА", "house": "24"})
    assert r.status_code == 200
    assert r.json()["total"] >= 1


# ---------- Связи ----------
def test_connections(client):
    rowid, _ = _first_rowid(client, inn=INN)
    r = client.get("/api/connections", params={"rowid": rowid})
    assert r.status_code == 200
    body = r.json()
    for key in ("relatives", "phone", "relatives_total", "phone_total", "phones"):
        assert key in body
    # FTS-путь по телефону не должен падать (регрессия: синтаксис {col col})
    assert body["error"] is None
    assert body["phones"], "у тестовой записи есть телефон → phones не пуст"
    # у самого человека есть телефон → он не должен попасть в свой же список
    assert all(int(x["_rowid"]) != rowid for x in body["phone"])


def test_surname_stem_gender_pairs():
    """Основа фамилии сводит муж./жен. формы к одному виду."""
    import search as S
    for male, female in [("ИВАНОВ", "ИВАНОВА"), ("НИДЗЕЛЬСКИЙ", "НИДЗЕЛЬСКАЯ"),
                         ("ТОЛСТОЙ", "ТОЛСТАЯ"), ("ПУТИН", "ПУТИНА"),
                         ("НАЗАРБАЕВ", "НАЗАРБАЕВА")]:
        sm, sf = S.surname_stem(male), S.surname_stem(female)
        assert sm and sm == sf, f"{male}/{female}: {sm} != {sf}"
    # короткие/несклоняемые → пустая основа (фолбэк на точное совпадение)
    assert S.surname_stem("ЛИ") == "" and S.surname_stem("КИМ") == ""


def test_patronymic_root():
    """Корень отчества: рус. -вич/-вна и казах. -ұлы/-қызы (вкл. казах. буквы)."""
    import search as S
    assert S.patronymic_root("СТАНИСЛАВОВИЧ") == "СТАНИСЛАВ"
    assert S.patronymic_root("СТАНИСЛАВОВНА") == "СТАНИСЛАВ"
    assert S.patronymic_root("СЕРГЕЕВНА") == "СЕРГЕ"
    assert S.patronymic_root("НҰРЛАНҰЛЫ") == "НҰРЛАН"        # казах. буквы
    assert S.patronymic_root("СЕРИК УЛЫ") == "СЕРИК"          # кириллица, отдельным словом
    assert S.patronymic_root("АЙГЕРИМ ҚЫЗЫ") == "АЙГЕРИМ"
    assert S.patronymic_root("") == ""


def test_house_base():
    import search as S
    assert S.house_base("9/1") == "9" and S.house_base("9") == "9"
    assert S.house_base("12А") == "12" and S.house_base("5-3") == "5"
    assert S.house_base("ГСК") == ""


def test_connections_house_base_matches_corpus(client):
    """Родственник в том же доме с иначе записанным номером (9 vs 9/1) находится."""
    import sqlite3
    import search as S
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rid = conn.execute(
        "SELECT rowid FROM residents WHERE \"ИНН\"='850804350023'").fetchone()["rowid"]
    res = S.connections(conn, rid)
    conn.close()
    names = {str(r["Имя"]) for r in res["relatives"]}
    assert {"МАРТИН", "АЛЬБИНА"} <= names  # сын (дом 9) и дочь (дом 9/1)


def test_connections_relative_by_patronymic(client):
    """Ребёнок с другой фамилией находится по отчеству (корень = имя родителя)."""
    import sqlite3
    import search as S
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rid = conn.execute(
        "SELECT rowid FROM residents WHERE \"ИНН\"='850804350023'").fetchone()["rowid"]
    res = S.connections(conn, rid)
    conn.close()
    # у найденных родственников проставлена причина связи
    assert res["relatives"]
    assert all(r.get("_relation") for r in res["relatives"])
    assert any("ребёнок" in r["_relation"] or "отчество" in r["_relation"]
               for r in res["relatives"])


def test_connections_relatives_gender(client):
    """НИДЗЕЛЬСКИЙ находит НИДЗЕЛЬСКАЯ в том же доме (муж./жен. род)."""
    import sqlite3
    import search as S
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rid = conn.execute(
        "SELECT rowid FROM residents WHERE \"ИНН\"='850804350023'").fetchone()["rowid"]
    res = S.connections(conn, rid)
    conn.close()
    assert res["error"] is None and res["relatives_total"] >= 1
    assert any("НИДЗЕЛЬСК" in str(r["Фамилия"]).upper() for r in res["relatives"])


def test_connections_finds_relatives(client):
    """Регрессия: связи (родственники + телефон) реально работают через FTS."""
    import sqlite3
    import search as S
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # семья из 3+ однофамильцев в одном доме
    rid = conn.execute(
        "SELECT a.rowid FROM residents_addr a JOIN residents r ON r.rowid=a.rowid "
        "WHERE a.city='ШАХТИНСК' AND a.street='40 ЛЕТ ПОБЕДЫ' AND a.house='68' "
        "AND r.\"Фамилия\"='ЛУЦУК' LIMIT 1").fetchone()["rowid"]
    res = S.connections(conn, rid)
    conn.close()
    assert res["error"] is None
    assert res["relatives_total"] >= 1


# ---------- Раскрытие ПДн пишется в аудит ----------
def test_reveal_audited(client):
    rowid, _ = _first_rowid(client, inn=INN)
    r = client.post("/api/reveal", data={"rowid": rowid, "field": "ИНН"})
    assert r.status_code == 200
    acts = {e["action"] for e in client.get("/api/my/activity").json()["events"]}
    assert "reveal" in acts


# ---------- История и активность ----------
def test_history_and_activity(client):
    client.get("/api/search", params={"inn": INN})
    h = client.get("/api/my/history")
    assert h.status_code == 200
    # ключи параметров — английские (как в форме поиска), не русские из аудита
    assert any((item["params"] or {}).get("inn") == INN for item in h.json()["history"])
    a = client.get("/api/my/activity")
    assert a.status_code == 200 and len(a.json()["events"]) > 0


# ---------- Закладки ----------
def test_bookmarks_crud(client):
    rowid, row = _first_rowid(client, inn=INN)
    r = client.post("/api/bookmarks", json={
        "rowid": rowid, "label": "Тест", "data": {k: v for k, v in row.items()}})
    assert r.status_code == 200
    lst = client.get("/api/bookmarks").json()["bookmarks"]
    assert any(b["rowid_ref"] == rowid for b in lst)
    bid = next(b["id"] for b in lst if b["rowid_ref"] == rowid)
    # повторное добавление не плодит дубль (UNIQUE user+rowid)
    client.post("/api/bookmarks", json={"rowid": rowid, "label": "Тест2", "data": {}})
    lst2 = client.get("/api/bookmarks").json()["bookmarks"]
    assert sum(b["rowid_ref"] == rowid for b in lst2) == 1
    assert client.delete(f"/api/bookmarks/{bid}").status_code == 200
    lst3 = client.get("/api/bookmarks").json()["bookmarks"]
    assert not any(b["rowid_ref"] == rowid for b in lst3)


def test_bookmarks_isolated_per_user(client):
    client.post("/api/admin/users", data={
        "username": "bmuser", "password": "User12345!", "role": "user"})
    rowid, row = _first_rowid(client, inn=INN)
    client.post("/api/bookmarks", json={"rowid": rowid, "label": "A", "data": {}})
    other = TestClient(__import__("app").app)
    other.post("/api/login", data={"username": "bmuser", "password": "User12345!"})
    assert other.get("/api/bookmarks").json()["bookmarks"] == []


# ---------- 2FA (TOTP) ----------
def test_2fa_full_flow():
    # отдельный клиент/пользователь, чтобы не ломать сессию admin
    base = TestClient(__import__("app").app)
    admin = TestClient(__import__("app").app)
    admin.post("/api/login", data={"username": "admin", "password": "Admin12345!"})
    admin.post("/api/admin/users", data={
        "username": "tfa", "password": "User12345!", "role": "user"})
    base.post("/api/login", data={"username": "tfa", "password": "User12345!"})
    setup = base.post("/api/2fa/setup").json()
    assert "secret" in setup and setup["uri"].startswith("otpauth://")
    code = pyotp.TOTP(setup["secret"]).now()
    assert base.post("/api/2fa/enable", data={"otp": code}).status_code == 200
    assert base.get("/api/me").json()["totp_enabled"] is True
    # новый вход теперь требует код
    fresh = TestClient(__import__("app").app)
    r = fresh.post("/api/login", data={"username": "tfa", "password": "User12345!"})
    assert r.status_code == 200 and r.json().get("otp_required") is True
    code2 = pyotp.TOTP(setup["secret"]).now()
    r = fresh.post("/api/login", data={
        "username": "tfa", "password": "User12345!", "otp": code2})
    assert r.status_code == 200 and r.json().get("role") == "user"


# ---------- Админ-статистика ----------
def test_remember_me_long_session():
    """«Запомнить меня» выдаёт куку с большим сроком жизни."""
    short = TestClient(__import__("app").app)
    rs = short.post("/api/login", data={"username": "admin", "password": "Admin12345!"})
    long_ = TestClient(__import__("app").app)
    rl = long_.post("/api/login",
                    data={"username": "admin", "password": "Admin12345!", "remember": "1"})
    import re
    def max_age(resp):
        m = re.search(r"[Mm]ax-[Aa]ge=(\d+)", resp.headers.get("set-cookie", ""))
        return int(m.group(1)) if m else 0
    assert max_age(rs) > 0 and max_age(rl) > 0
    assert max_age(rl) > max_age(rs) * 10  # дни против часов


def test_admin_stats(client):
    r = client.get("/api/admin/stats", params={"days": 7})
    assert r.status_code == 200
    body = r.json()
    for key in ("by_action", "top_users", "by_day", "failed_logins", "anomalies"):
        assert key in body
    assert body["by_action"].get("search", 0) >= 1


def test_stats_forbidden_for_user(client):
    u = TestClient(__import__("app").app)
    client.post("/api/admin/users", data={
        "username": "stuser", "password": "User12345!", "role": "user"})
    u.post("/api/login", data={"username": "stuser", "password": "User12345!"})
    assert u.get("/api/admin/stats").status_code == 403


# ---------- Обратный поиск по телефону ----------
def test_phone_lookup(client):
    """Режим «по телефону»: находит владельца номера из его же записи."""
    rowid, row = _first_rowid(client, inn=INN)
    detail = client.get("/api/search", params={"inn": INN}).json()["results"][0]
    phone = str(detail.get("Мобильный") or "").split(",")[0].strip()
    if not phone:
        pytest.skip("у тестовой записи нет телефона")
    r = client.get("/api/phone_lookup", params={"phone": phone})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert body["mode"] == "exact"
    found = {int(x["_rowid"]) for x in body["results"]}
    assert rowid in found


def test_phone_lookup_requires_phone(client):
    assert client.get("/api/phone_lookup", params={"phone": ""}).status_code == 400


# ---------- Экспорт выбранных строк (bulk) ----------
def test_export_rows(client):
    rowid, _ = _first_rowid(client, inn=INN)
    r = client.post("/api/export_rows", json={"rowids": [rowid]})
    assert r.status_code == 200
    assert "spreadsheet" in r.headers.get("content-type", "")
    assert int(r.headers.get("content-length", "1")) > 0


def test_export_rows_empty(client):
    assert client.post("/api/export_rows", json={"rowids": []}).status_code == 400


# ---------- Фильтр по гражданству/национальности ----------
def test_search_with_citizenship_filter(client):
    """Фильтр гражданства не ломает поиск и не расширяет выборку."""
    base = client.get("/api/search", params={"surname": "ДОЩАНОВ"}).json()
    if base["total"] == 0:
        pytest.skip("нет данных по фамилии")
    citi = str(base["results"][0].get("Гражданство") or "").strip()
    if not citi:
        pytest.skip("нет значения гражданства")
    r = client.get("/api/search", params={"surname": "ДОЩАНОВ", "citizenship": citi})
    assert r.status_code == 200
    assert r.json()["total"] <= base["total"]


# ---------- Аномалии содержат user_id (для блокировки в 1 клик) ----------
def test_anomalies_include_user_id(client):
    body = client.get("/api/admin/stats", params={"days": 7}).json()
    for an in body["anomalies"]:
        assert "user_id" in an
