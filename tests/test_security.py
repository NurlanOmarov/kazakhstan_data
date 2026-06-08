"""
Security-тесты: устойчивость к инъекциям, блокировка, отзыв токенов,
санитизация ввода. Часть требует БД данных (FTS/suggest), часть — нет.
"""
import sqlite3
from pathlib import Path

import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
from search import search, suggest, neighbors, _fts_clean, _fts_token

DB_PATH = str(Path(__file__).resolve().parents[1] / "kazakhstan_data.db")
HAS_DB = Path(DB_PATH).exists()
needs_db = pytest.mark.skipif(not HAS_DB, reason="нет файла БД")


@pytest.fixture()
def dconn():
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ---------- Санитизация FTS (без БД) ----------

def test_fts_clean_strips_special():
    assert '"' not in _fts_clean('Иван"ов')
    assert _fts_clean('a*b:c^(d){ИНН}') == "a b c d ИНН"
    assert _fts_token("") == ""           # пустой ввод -> пустой токен
    assert _fts_token('"*:^') == ""       # только спецсимволы -> пусто


# ---------- Инъекции через поиск (нужна БД) ----------

@needs_db
@pytest.mark.parametrize("evil", [
    '" OR 1=1 --',
    '{ИНН}:751014450225',          # попытка перенацелить колонку FTS
    'a" OR {Адрес}:"x',
    'NEAR(a b)',
    '*',
    "'; DROP TABLE residents; --",
])
def test_fts_injection_safe(dconn, evil):
    # Не должно быть исключений/500 и утечки ошибок наружу.
    res = search(dconn, {"Фамилия": evil})
    assert res["error"] is None
    assert isinstance(res["results"], list)


@needs_db
def test_sql_injection_in_inn(dconn):
    res = search(dconn, {"ИНН": "751014450225' OR '1'='1"})
    # Параметризовано -> точное несовпадение, не дамп всей базы
    assert res["total"] == 0


@needs_db
def test_order_by_injection_ignored(dconn):
    # sort_by не из белого списка -> игнорируется, без ошибки
    res = search(dconn, {"Фамилия": "Иванова"}, limit=5,
                 sort_by="ИНН; DROP TABLE residents", sort_dir="asc")
    assert res["error"] is None


@needs_db
def test_suggest_min_prefix(dconn):
    assert suggest(dconn, "surname", "С") == []     # 1 символ -> пусто
    assert suggest(dconn, "surname", "Сы")          # 2 символа -> работает
    assert suggest(dconn, "evil_field", "Сыз") == []  # неизвестное поле -> пусто


@needs_db
def test_no_db_error_leak(dconn):
    # Текст ошибки БД не должен утекать; при норм. запросе error None
    res = search(dconn, {"ИНН": "751014450225"})
    assert res["error"] is None


# ---------- Auth: блокировка и отзыв токенов (temp users.db) ----------

@pytest.fixture()
def uconn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "u.db"))
    c.row_factory = sqlite3.Row
    auth.init_schema(c)
    auth.create_user(c, username="bob", password="Secret12345", role="user")
    yield c
    c.close()


def test_lockout_escalates_and_persists(uconn):
    user = auth.get_user(uconn, "bob")
    # делаем MAX_FAILED неудач
    for _ in range(auth.MAX_FAILED):
        auth.record_login_failure(uconn, auth.get_user(uconn, "bob"))
    u = auth.get_user(uconn, "bob")
    assert u["failed_attempts"] >= auth.MAX_FAILED   # счётчик НЕ сброшен
    assert auth.is_locked(u)                          # заблокирован
    # успешный вход сбрасывает
    auth.record_login_success(uconn, u["id"], "1.2.3.4")
    u2 = auth.get_user(uconn, "bob")
    assert u2["failed_attempts"] == 0 and not auth.is_locked(u2)


def test_password_change_invalidates_tokens(uconn):
    user = auth.get_user(uconn, "bob")
    token = auth.make_token(user)
    payload = auth.decode_token(token)
    assert payload["ver"] == user["token_version"]
    # меняем пароль -> token_version растёт
    auth.set_password(uconn, user["id"], "NewSecret12345")
    updated = auth.get_user(uconn, "bob")
    assert updated["token_version"] != payload["ver"]   # старый токен невалиден


def test_revoke_tokens(uconn):
    user = auth.get_user(uconn, "bob")
    v0 = user["token_version"]
    auth.revoke_tokens(uconn, user["id"])
    assert auth.get_user(uconn, "bob")["token_version"] == v0 + 1


def test_account_expiry(uconn):
    auth.update_user(uconn, auth.get_user(uconn, "bob")["id"],
                     expires_at="2000-01-01T00:00:00+00:00")
    assert not auth.account_valid(auth.get_user(uconn, "bob"))


def test_password_hash_not_plaintext(uconn):
    u = auth.get_user(uconn, "bob")
    assert u["password_hash"] != "Secret12345"
    assert auth.verify_password("Secret12345", u["password_hash"])
    assert not auth.verify_password("wrong", u["password_hash"])
