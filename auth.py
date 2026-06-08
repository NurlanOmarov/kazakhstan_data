"""
Авторизация, управление пользователями и аудит.

Хранилище — users.db (отдельно от данных). Пароли — bcrypt.
Сессия — JWT в httpOnly+SameSite=Strict cookie.

Безопасность:
  - блокировка учётки после N неудачных входов;
  - срок действия учётки (expires_at);
  - дневные квоты на поиск/экспорт (защита от массового скачивания);
  - полный аудит-лог (вход/выход/поиск/экспорт/действия админа).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
import pyotp

# ---------- Конфигурация ----------
USERS_DB = os.environ.get("KZ_USERS_DB", str(Path(__file__).parent / "users.db"))
TOKEN_TTL_HOURS = int(os.environ.get("KZ_TOKEN_TTL_HOURS", "8"))
# «Долгая» сессия для доверенного устройства (PWA на телефоне) — дни.
TOKEN_TTL_LONG_DAYS = int(os.environ.get("KZ_TOKEN_TTL_LONG_DAYS", "30"))
MAX_FAILED = int(os.environ.get("KZ_MAX_FAILED", "5"))
LOCK_MINUTES = int(os.environ.get("KZ_LOCK_MINUTES", "15"))
DEFAULT_DAILY_SEARCH = int(os.environ.get("KZ_DAILY_SEARCH", "500"))
DEFAULT_DAILY_EXPORT = int(os.environ.get("KZ_DAILY_EXPORT", "10"))
EXPORT_MAX_ROWS = int(os.environ.get("KZ_EXPORT_MAX_ROWS", "2000"))
# Почасовые пороги для детектора аномалий (всплеск активности одного юзера).
HOURLY_SEARCH = int(os.environ.get("KZ_HOURLY_SEARCH", "200"))
HOURLY_EXPORT = int(os.environ.get("KZ_HOURLY_EXPORT", "5"))
MAX_BOOKMARKS = int(os.environ.get("KZ_MAX_BOOKMARKS", "500"))
ISSUER = os.environ.get("KZ_TOTP_ISSUER", "KZ Search")
COOKIE_NAME = "kz_session"


def _secret_key() -> str:
    """
    JWT-секрет: приоритетно из env KZ_JWT_SECRET (обязательно для прода —
    одинаков для всех воркеров). Иначе пробуем файл .secret_key. Если файл
    недоступен на запись (например, в контейнере) — эфемерный ключ + предупреждение.
    """
    env = os.environ.get("KZ_JWT_SECRET")
    if env:
        return env
    path = Path(__file__).parent / ".secret_key"
    try:
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_hex(32)
        path.write_text(key)
        os.chmod(path, 0o600)
        logging.warning("KZ_JWT_SECRET не задан — создан локальный .secret_key. "
                        "Для прода задайте KZ_JWT_SECRET в окружении.")
        return key
    except OSError:
        logging.warning("KZ_JWT_SECRET не задан и .secret_key недоступен — "
                        "использую эфемерный ключ (сессии не переживут перезапуск). "
                        "Задайте KZ_JWT_SECRET!")
        return secrets.token_hex(32)


SECRET = _secret_key()


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


# ---------- Соединение и схема ----------
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(USERS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'user',
          full_name TEXT DEFAULT '',
          note TEXT DEFAULT '',
          is_active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          created_by TEXT DEFAULT '',
          expires_at TEXT DEFAULT '',
          daily_search_limit INTEGER DEFAULT 0,
          daily_export_limit INTEGER DEFAULT 0,
          last_login_at TEXT DEFAULT '',
          last_login_ip TEXT DEFAULT '',
          login_count INTEGER NOT NULL DEFAULT 0,
          failed_attempts INTEGER NOT NULL DEFAULT 0,
          locked_until TEXT DEFAULT '',
          token_version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          user_id INTEGER,
          username TEXT,
          action TEXT NOT NULL,
          ip TEXT,
          user_agent TEXT,
          detail TEXT,
          result_count INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_audit_user_ts ON audit_log(user_id, ts);
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
        CREATE INDEX IF NOT EXISTS idx_audit_action_ts ON audit_log(action, ts);
        CREATE TABLE IF NOT EXISTS bookmarks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          rowid_ref INTEGER NOT NULL,
          label TEXT DEFAULT '',
          note TEXT DEFAULT '',
          data TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(user_id, rowid_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id, id);
        """
    )
    # Миграции: добавить недостающие колонки в старые БД.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "token_version" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    if "totp_secret" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT ''")
    if "totp_enabled" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
    conn.commit()


# ---------- Пароли ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------
def make_token(user: sqlite3.Row, ttl_hours: int | None = None) -> str:
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "ver": user["token_version"],
        "exp": now() + timedelta(hours=ttl_hours or TOKEN_TTL_HOURS),
        "iat": now(),
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---------- Пользователи ----------
def get_user(conn, username: str):
    return conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()


def get_user_by_id(conn, uid: int):
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def list_users(conn):
    return conn.execute(
        "SELECT id, username, role, full_name, note, is_active, created_at, "
        "created_by, expires_at, daily_search_limit, daily_export_limit, "
        "last_login_at, last_login_ip, login_count, failed_attempts, locked_until, "
        "totp_enabled "
        "FROM users ORDER BY id"
    ).fetchall()


def create_user(conn, *, username, password, role="user", full_name="",
                note="", created_by="", expires_at="",
                daily_search_limit=0, daily_export_limit=0):
    conn.execute(
        "INSERT INTO users (username, password_hash, role, full_name, note, "
        "created_at, created_by, expires_at, daily_search_limit, daily_export_limit) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (username, hash_password(password), role, full_name, note,
         _iso(now()), created_by, expires_at,
         daily_search_limit, daily_export_limit),
    )
    conn.commit()


def set_password(conn, uid: int, password: str):
    # Смена пароля инвалидирует все ранее выданные токены (token_version++).
    conn.execute(
        "UPDATE users SET password_hash=?, failed_attempts=0, locked_until='', "
        "token_version=token_version+1 WHERE id=?",
        (hash_password(password), uid),
    )
    conn.commit()


def revoke_tokens(conn, uid: int):
    """Принудительно завершить все сессии пользователя."""
    conn.execute("UPDATE users SET token_version=token_version+1 WHERE id=?", (uid,))
    conn.commit()


def update_user(conn, uid: int, **fields):
    allowed = {"role", "full_name", "note", "is_active", "expires_at",
               "daily_search_limit", "daily_export_limit", "locked_until"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return
    args.append(uid)
    conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()


def delete_user(conn, uid: int):
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()


# ---------- Вход: блокировки, телеметрия ----------
def is_locked(user) -> bool:
    lu = user["locked_until"]
    if not lu:
        return False
    try:
        return datetime.fromisoformat(lu) > now()
    except ValueError:
        return False


def account_valid(user) -> bool:
    if not user["is_active"]:
        return False
    exp = user["expires_at"]
    if exp:
        try:
            if datetime.fromisoformat(exp) < now():
                return False
        except ValueError:
            pass
    return True


def record_login_success(conn, uid: int, ip: str):
    conn.execute(
        "UPDATE users SET last_login_at=?, last_login_ip=?, "
        "login_count=login_count+1, failed_attempts=0, locked_until='' WHERE id=?",
        (_iso(now()), ip, uid),
    )
    conn.commit()


def record_login_failure(conn, user):
    # Счётчик НЕ обнуляется при блокировке — растёт до успешного входа.
    # Блокировка эскалирует: чем больше попыток, тем дольше.
    attempts = user["failed_attempts"] + 1
    locked_until = user["locked_until"] or ""
    if attempts >= MAX_FAILED:
        mult = attempts - MAX_FAILED + 1
        locked_until = _iso(now() + timedelta(minutes=LOCK_MINUTES * mult))
    conn.execute(
        "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
        (attempts, locked_until, user["id"]),
    )
    conn.commit()


# ---------- Аудит и квоты ----------
def audit(conn, *, action, user=None, ip="", user_agent="", detail=None,
          result_count=None):
    conn.execute(
        "INSERT INTO audit_log (ts, user_id, username, action, ip, user_agent, "
        "detail, result_count) VALUES (?,?,?,?,?,?,?,?)",
        (_iso(now()),
         user["id"] if user else None,
         user["username"] if user else None,
         action, ip, user_agent,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None,
         result_count),
    )
    conn.commit()


def count_today(conn, uid: int, action: str) -> int:
    start = _iso(now().replace(hour=0, minute=0, second=0, microsecond=0))
    return conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE user_id=? AND action=? AND ts>=?",
        (uid, action, start),
    ).fetchone()[0]


def effective_limit(user, action: str) -> int:
    if action == "search":
        return user["daily_search_limit"] or DEFAULT_DAILY_SEARCH
    if action == "export":
        return user["daily_export_limit"] or DEFAULT_DAILY_EXPORT
    return 0


def recent_audit(conn, limit=200, user_id=None):
    if user_id:
        return conn.execute(
            "SELECT * FROM audit_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def count_last_hour(conn, uid: int, action: str) -> int:
    start = _iso(now() - timedelta(hours=1))
    return conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE user_id=? AND action=? AND ts>=?",
        (uid, action, start),
    ).fetchone()[0]


def hourly_limit(action: str) -> int:
    if action == "search":
        return HOURLY_SEARCH
    if action == "export":
        return HOURLY_EXPORT
    return 0


# Русские ключи деталей аудита -> английские ключи SearchParams (для фронта).
_AUDIT_KEY_TO_PARAM = {
    "ФИО": "fio", "Фамилия": "surname", "Имя": "name", "Отчество": "patronymic",
    "Дата рождения": "dob", "ИНН": "inn", "Мобильный": "phone", "Адрес": "address",
    "Город": "city", "Район": "district", "Улица": "street", "Дом": "house",
}


# ---------- Личная активность и история поиска ----------
def user_history(conn, uid: int, limit: int = 30):
    """Последние поисковые запросы пользователя (из аудита) — для повтора."""
    rows = conn.execute(
        "SELECT ts, detail, result_count FROM audit_log "
        "WHERE user_id=? AND action='search' AND detail IS NOT NULL AND detail<>'{}' "
        "ORDER BY id DESC LIMIT ?",
        (uid, limit * 3),
    ).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["detail"] in seen:
            continue
        seen.add(r["detail"])
        try:
            raw = json.loads(r["detail"])
        except (ValueError, TypeError):
            continue
        # Переводим русские ключи аудита в английские ключи формы поиска.
        params = {_AUDIT_KEY_TO_PARAM.get(k, k): v for k, v in raw.items()}
        out.append({"ts": r["ts"], "params": params,
                    "result_count": r["result_count"]})
        if len(out) >= limit:
            break
    return out


# ---------- Закладки ----------
def add_bookmark(conn, uid: int, rowid_ref: int, label: str, data: str,
                 note: str = "") -> bool:
    if conn.execute("SELECT COUNT(*) FROM bookmarks WHERE user_id=?",
                    (uid,)).fetchone()[0] >= MAX_BOOKMARKS:
        return False
    conn.execute(
        "INSERT OR REPLACE INTO bookmarks "
        "(id, user_id, rowid_ref, label, note, data, created_at) "
        "VALUES ((SELECT id FROM bookmarks WHERE user_id=? AND rowid_ref=?), "
        "?,?,?,?,?,?)",
        (uid, rowid_ref, uid, rowid_ref, label[:200], note[:500], data, _iso(now())),
    )
    conn.commit()
    return True


def list_bookmarks(conn, uid: int):
    return conn.execute(
        "SELECT id, rowid_ref, label, note, data, created_at FROM bookmarks "
        "WHERE user_id=? ORDER BY id DESC", (uid,)
    ).fetchall()


def delete_bookmark(conn, uid: int, bid: int):
    conn.execute("DELETE FROM bookmarks WHERE id=? AND user_id=?", (bid, uid))
    conn.commit()


# ---------- 2FA (TOTP) ----------
def set_totp_secret(conn, uid: int, secret: str):
    conn.execute("UPDATE users SET totp_secret=?, totp_enabled=0 WHERE id=?",
                 (secret, uid))
    conn.commit()


def enable_totp(conn, uid: int):
    conn.execute("UPDATE users SET totp_enabled=1 WHERE id=?", (uid,))
    conn.commit()


def disable_totp(conn, uid: int):
    conn.execute("UPDATE users SET totp_secret='', totp_enabled=0 WHERE id=?", (uid,))
    conn.commit()


def gen_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(username: str, secret: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        # valid_window=1 — допускаем рассинхрон времени ±30 сек.
        return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)
    except (ValueError, TypeError):
        return False


# ---------- Аналитика (для админа) ----------
def stats_overview(conn, days: int = 7) -> dict:
    """Сводка по аудиту за N дней: действия по типам, топ-пользователи, по дням."""
    since = _iso(now() - timedelta(days=days))
    by_action = {r["action"]: r["n"] for r in conn.execute(
        "SELECT action, COUNT(*) n FROM audit_log WHERE ts>=? GROUP BY action",
        (since,))}
    top_users = [dict(r) for r in conn.execute(
        "SELECT username, "
        "SUM(action='search') searches, SUM(action='export') exports, "
        "COUNT(*) total FROM audit_log "
        "WHERE ts>=? AND user_id IS NOT NULL GROUP BY user_id "
        "ORDER BY total DESC LIMIT 10", (since,))]
    by_day = [dict(r) for r in conn.execute(
        "SELECT substr(ts,1,10) day, "
        "SUM(action='search') searches, SUM(action='export') exports, "
        "SUM(action='login_success') logins FROM audit_log "
        "WHERE ts>=? GROUP BY day ORDER BY day", (since,))]
    failed_logins = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='login_fail' AND ts>=?",
        (since,)).fetchone()[0]
    anomalies = [dict(r) for r in conn.execute(
        "SELECT ts, username, detail FROM audit_log "
        "WHERE action='anomaly' AND ts>=? ORDER BY id DESC LIMIT 20", (since,))]
    return {"days": days, "by_action": by_action, "top_users": top_users,
            "by_day": by_day, "failed_logins": failed_logins,
            "anomalies": anomalies}
