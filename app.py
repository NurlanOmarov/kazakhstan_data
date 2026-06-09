"""
Защищённый сервис поиска по базе жителей Казахстана.

Безопасность: авторизация (JWT в httpOnly cookie), роли user/admin,
аудит всех действий, дневные квоты (защита от массового скачивания),
rate-limiting, security-заголовки.

Запуск (dev):  uvicorn app:app --port 8000
Прод — см. deploy/ (gunicorn + nginx + self-signed HTTPS).
"""
import io
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import (FastAPI, Request, Response, Depends, HTTPException,
                     Form, Query)
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import auth
import search as S

DB_FILE = os.environ.get("KZ_DB_PATH", str(Path(__file__).parent / "kazakhstan_data.db"))
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
SECURE_COOKIES = os.environ.get("KZ_SECURE_COOKIES", "1") == "1"
DEV_CORS = os.environ.get("KZ_DEV_CORS", "0") == "1"


def client_ip(request: Request) -> str:
    """
    IP клиента с учётом reverse-proxy.

    Берём ПОСЛЕДНИЙ элемент X-Forwarded-For — его добавляет наш nginx
    ($proxy_add_x_forwarded_for дописывает реальный $remote_addr в конец).
    Первые элементы клиент может подделать сам, поэтому им доверять нельзя:
    иначе обходится rate-limit и фальсифицируется IP в аудит-логе.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else ""


RATELIMIT_ENABLED = os.environ.get("KZ_RATELIMIT", "1") == "1"
limiter = Limiter(key_func=client_ip, default_limits=["240/minute"],
                  enabled=RATELIMIT_ENABLED)
app = FastAPI(title="KZ Search", docs_url=None, redoc_url=None, openapi_url=None)
app.state.limiter = limiter


# Гарантируем актуальную схему users.db (миграции колонок/таблиц) при старте,
# чтобы после обновления кода не падать на отсутствующих полях (totp, bookmarks).
try:
    _uc = auth.connect()
    auth.init_schema(_uc)
    _uc.close()
except sqlite3.Error as _e:
    logging.error("Не удалось применить миграции users.db: %s", _e)


@app.exception_handler(RateLimitExceeded)
def _ratelimit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"detail": "Слишком много запросов. Подождите."},
                        status_code=429)


# ---------- Security-заголовки ----------
@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if SECURE_COOKIES:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


if DEV_CORS:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )


# ---------- Соединения ----------
# Опционально: путь к расширению sqlite-zstd для прозрачного сжатия БД.
SQLITE_ZSTD_EXT = os.environ.get("KZ_SQLITE_ZSTD", "")


@contextmanager
def data_conn():
    conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Прозрачное сжатие (sqlite-zstd) — если расширение собрано и указано.
    if SQLITE_ZSTD_EXT:
        try:
            conn.enable_load_extension(True)
            conn.load_extension(SQLITE_ZSTD_EXT)
            conn.enable_load_extension(False)
        except (AttributeError, sqlite3.OperationalError) as e:
            logging.error("Не удалось загрузить sqlite-zstd (%s): %s",
                          SQLITE_ZSTD_EXT, e)
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA cache_size = -200000;")
    conn.execute("PRAGMA mmap_size = 30000000000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def users_conn():
    conn = auth.connect()
    try:
        yield conn
    finally:
        conn.close()


# ---------- Авторизация (dependencies) ----------
def current_user(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Требуется авторизация")
    payload = auth.decode_token(token)
    if not payload:
        raise HTTPException(401, "Сессия истекла, войдите заново")
    with users_conn() as uc:
        user = auth.get_user_by_id(uc, int(payload["sub"]))
        if not user or not auth.account_valid(user):
            raise HTTPException(401, "Учётная запись недоступна")
        # Отзыв сессий: при смене пароля/блокировке token_version растёт.
        if payload.get("ver") != user["token_version"]:
            raise HTTPException(401, "Сессия завершена, войдите заново")
    return user


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Недостаточно прав")
    return user


def _note_anomaly(uc, user, action: str, ip: str):
    """Детектор всплеска активности: лог 'anomaly' при превышении часового порога."""
    lim = auth.hourly_limit(action)
    if lim and auth.count_last_hour(uc, user["id"], action) == lim:
        auth.audit(uc, action="anomaly", user=user, ip=ip,
                   detail={"reason": f"{action}: больше {lim} за час"})


# ---------- Вход / выход ----------
@app.post("/api/login")
@limiter.limit("10/minute")
def login(request: Request, response: Response,
          username: str = Form(...), password: str = Form(...),
          otp: str = Form(""), remember: str = Form("")):
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")[:300]
    with users_conn() as uc:
        user = auth.get_user(uc, username)
        if not user or auth.is_locked(user) or not auth.account_valid(user) \
                or not auth.verify_password(password, user["password_hash"]):
            if user:
                auth.record_login_failure(uc, user)
            auth.audit(uc, action="login_fail", user=user, ip=ip, user_agent=ua,
                       detail={"username": username})
            raise HTTPException(401, "Неверный логин или пароль")
        # Двухфакторная аутентификация (TOTP), если включена у пользователя.
        if user["totp_enabled"]:
            if not otp:
                # Пароль верный, но нужен код — просим фронт показать поле OTP.
                return JSONResponse({"otp_required": True})
            if not auth.verify_totp(user["totp_secret"], otp):
                auth.audit(uc, action="login_fail", user=user, ip=ip,
                           user_agent=ua, detail={"reason": "bad_otp"})
                raise HTTPException(401, "Неверный код 2FA")
        auth.record_login_success(uc, user["id"], ip)
        # «Запомнить на устройстве» — долгая сессия (для установленного PWA).
        long_session = remember.lower() in ("1", "true", "on", "yes")
        ttl_hours = auth.TOKEN_TTL_LONG_DAYS * 24 if long_session else auth.TOKEN_TTL_HOURS
        auth.audit(uc, action="login_success", user=user, ip=ip, user_agent=ua,
                   detail={"remember": True} if long_session else None)
        token = auth.make_token(user, ttl_hours)
        role = user["role"]
    resp = JSONResponse({"username": username, "role": role})
    resp.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, samesite="strict",
        secure=SECURE_COOKIES, max_age=ttl_hours * 3600, path="/",
    )
    return resp


@app.post("/api/logout")
def logout(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    payload = auth.decode_token(token) if token else None
    if payload:
        with users_conn() as uc:
            user = auth.get_user_by_id(uc, int(payload["sub"]))
            auth.audit(uc, action="logout", user=user, ip=client_ip(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"username": user["username"], "role": user["role"],
            "full_name": user["full_name"],
            "totp_enabled": bool(user["totp_enabled"])}


# ---------- Поиск ----------
def _params_from_query(**kw):
    return {
        "ФИО": kw.get("fio", ""), "Фамилия": kw.get("surname", ""),
        "Имя": kw.get("name", ""), "Отчество": kw.get("patronymic", ""),
        "Дата рождения": kw.get("dob", ""), "ИНН": kw.get("inn", ""),
        "Мобильный": kw.get("phone", ""), "Адрес": kw.get("address", ""),
        "Город": kw.get("city", ""), "Район": kw.get("district", ""),
        "Улица": kw.get("street", ""), "Дом": kw.get("house", ""),
        "Пол": kw.get("gender", ""),
        "Возраст от": kw.get("age_min", ""), "Возраст до": kw.get("age_max", ""),
        "Гражданство": kw.get("citizenship", ""),
        "Национальность": kw.get("nationality", ""),
    }


@app.get("/api/search")
def api_search(request: Request, user=Depends(current_user),
               fio: str = "", surname: str = "", name: str = "",
               patronymic: str = "", dob: str = "", inn: str = "",
               phone: str = "", address: str = "", city: str = "",
               district: str = "", street: str = "", house: str = "",
               gender: str = "", age_min: str = "", age_max: str = "",
               citizenship: str = "", nationality: str = "",
               page: int = 1, page_size: int = 50,
               sort_by: str = "", sort_dir: str = "asc"):
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    with users_conn() as uc:
        limit = auth.effective_limit(user, "search")
        if auth.count_today_any(uc, user["id"], auth.LOOKUP_ACTIONS) >= limit:
            raise HTTPException(429, f"Превышен дневной лимит запросов ({limit})")
    params = _params_from_query(
        fio=fio, surname=surname, name=name, patronymic=patronymic, dob=dob,
        inn=inn, phone=phone, address=address, city=city, district=district,
        street=street, house=house, gender=gender, age_min=age_min,
        age_max=age_max, citizenship=citizenship, nationality=nationality)
    with data_conn() as dc:
        result = S.search(dc, params, limit=page_size,
                          offset=(page - 1) * page_size,
                          sort_by=sort_by or None, sort_dir=sort_dir)
    result["fields"] = S.DISPLAY_FIELDS
    result["count"] = len(result["results"])
    result["page"] = page
    with users_conn() as uc:
        auth.audit(uc, action="search", user=user, ip=client_ip(request),
                   detail={k: v for k, v in params.items() if v},
                   result_count=result["total"])
        _note_anomaly(uc, user, "search", client_ip(request))
    return result


@app.get("/api/suggest")
def api_suggest(field: str, q: str, user=Depends(current_user)):
    with data_conn() as dc:
        return {"results": S.suggest(dc, field, q, limit=10)}


@app.get("/api/neighbors")
def api_neighbors(request: Request, rowid: int, user=Depends(current_user)):
    with users_conn() as uc:
        limit = auth.effective_limit(user, "search")
        if auth.count_today_any(uc, user["id"], auth.LOOKUP_ACTIONS) >= limit:
            raise HTTPException(429, f"Превышен дневной лимит запросов ({limit})")
    with data_conn() as dc:
        result = S.neighbors(dc, rowid, limit=100)
    result["fields"] = S.DISPLAY_FIELDS
    with users_conn() as uc:
        auth.audit(uc, action="neighbors", user=user, ip=client_ip(request),
                   detail={"rowid": rowid}, result_count=result["total"])
    return result


@app.get("/api/connections")
def api_connections(request: Request, rowid: int, user=Depends(current_user)):
    with users_conn() as uc:
        limit = auth.effective_limit(user, "search")
        if auth.count_today_any(uc, user["id"], auth.LOOKUP_ACTIONS) >= limit:
            raise HTTPException(429, f"Превышен дневной лимит запросов ({limit})")
    with data_conn() as dc:
        result = S.connections(dc, rowid, limit=100)
    result["fields"] = S.DISPLAY_FIELDS
    with users_conn() as uc:
        auth.audit(uc, action="connections", user=user, ip=client_ip(request),
                   detail={"rowid": rowid},
                   result_count=result["relatives_total"] + result["phone_total"])
    return result


@app.post("/api/reveal")
def api_reveal(request: Request, user=Depends(current_user),
               rowid: int = Form(...), field: str = Form(...)):
    """Аудит раскрытия замаскированного поля (ИИН/телефон) — для трассировки."""
    with users_conn() as uc:
        auth.audit(uc, action="reveal", user=user, ip=client_ip(request),
                   detail={"rowid": rowid, "field": field[:40]})
    return {"ok": True}


# ---------- Личный кабинет: история и активность ----------
@app.get("/api/my/history")
def my_history(user=Depends(current_user)):
    with users_conn() as uc:
        return {"history": auth.user_history(uc, user["id"], limit=30)}


@app.get("/api/my/activity")
def my_activity(user=Depends(current_user), limit: int = 200):
    with users_conn() as uc:
        rows = auth.recent_audit(uc, limit=min(limit, 500), user_id=user["id"])
        return {"events": [dict(r) for r in rows]}


# ---------- Закладки ----------
@app.get("/api/bookmarks")
def list_bookmarks(user=Depends(current_user)):
    with users_conn() as uc:
        out = []
        for r in auth.list_bookmarks(uc, user["id"]):
            d = dict(r)
            try:
                d["data"] = json.loads(d["data"])
            except (ValueError, TypeError):
                d["data"] = {}
            out.append(d)
    return {"bookmarks": out, "fields": S.DISPLAY_FIELDS}


@app.post("/api/bookmarks")
def add_bookmark(request: Request, user=Depends(current_user),
                 payload: dict = None):
    import json as _json
    payload = payload or {}
    try:
        rowid_ref = int(payload.get("rowid"))
    except (ValueError, TypeError):
        raise HTTPException(400, "Некорректный rowid")
    label = str(payload.get("label", ""))[:200]
    note = str(payload.get("note", ""))[:500]
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise HTTPException(400, "Некорректные данные")
    with users_conn() as uc:
        ok = auth.add_bookmark(uc, user["id"], rowid_ref, label,
                               _json.dumps(data, ensure_ascii=False), note)
    if not ok:
        raise HTTPException(409, f"Достигнут лимит закладок ({auth.MAX_BOOKMARKS})")
    return {"ok": True}


@app.delete("/api/bookmarks/{bid}")
def delete_bookmark(bid: int, user=Depends(current_user)):
    with users_conn() as uc:
        auth.delete_bookmark(uc, user["id"], bid)
    return {"ok": True}


# ---------- 2FA (TOTP) ----------
@app.post("/api/2fa/setup")
def twofa_setup(user=Depends(current_user)):
    """Сгенерировать секрет (ещё не активен) и вернуть данные для приложения."""
    # Если 2FA уже включён — не даём перегенерировать секрет (иначе повторный
    # вызов сбросил бы totp_enabled и отключил защиту без пароля/кода).
    # Сначала /2fa/disable, потом заново /2fa/setup.
    with users_conn() as uc:
        fresh = auth.get_user_by_id(uc, user["id"])
        if fresh["totp_enabled"]:
            raise HTTPException(409, "2FA уже включён — сначала отключите его")
        secret = auth.gen_totp_secret()
        auth.set_totp_secret(uc, user["id"], secret)
    return {"secret": secret, "uri": auth.totp_uri(user["username"], secret)}


@app.post("/api/2fa/enable")
def twofa_enable(request: Request, user=Depends(current_user),
                 otp: str = Form(...)):
    with users_conn() as uc:
        fresh = auth.get_user_by_id(uc, user["id"])
        if not auth.verify_totp(fresh["totp_secret"], otp):
            raise HTTPException(400, "Неверный код — попробуйте ещё раз")
        auth.enable_totp(uc, user["id"])
        auth.audit(uc, action="2fa_enable", user=user, ip=client_ip(request))
    return {"ok": True}


@app.post("/api/2fa/disable")
def twofa_disable(request: Request, user=Depends(current_user),
                  password: str = Form(...), otp: str = Form("")):
    with users_conn() as uc:
        fresh = auth.get_user_by_id(uc, user["id"])
        if not auth.verify_password(password, fresh["password_hash"]):
            raise HTTPException(401, "Неверный пароль")
        if fresh["totp_enabled"] and not auth.verify_totp(fresh["totp_secret"], otp):
            raise HTTPException(400, "Неверный код 2FA")
        auth.disable_totp(uc, user["id"])
        auth.audit(uc, action="2fa_disable", user=user, ip=client_ip(request))
    return {"ok": True}


# ---------- Экспорт в Excel ----------
@app.get("/api/export")
def api_export(request: Request, user=Depends(current_user),
               fio: str = "", surname: str = "", name: str = "",
               patronymic: str = "", dob: str = "", inn: str = "",
               phone: str = "", address: str = "", city: str = "",
               district: str = "", street: str = "", house: str = "",
               gender: str = "", age_min: str = "", age_max: str = "",
               citizenship: str = "", nationality: str = ""):
    with users_conn() as uc:
        limit = auth.effective_limit(user, "export")
        if auth.count_today(uc, user["id"], "export") >= limit:
            raise HTTPException(429, f"Превышен дневной лимит экспортов ({limit})")
    params = _params_from_query(
        fio=fio, surname=surname, name=name, patronymic=patronymic, dob=dob,
        inn=inn, phone=phone, address=address, city=city, district=district,
        street=street, house=house, gender=gender, age_min=age_min,
        age_max=age_max, citizenship=citizenship, nationality=nationality)
    with data_conn() as dc:
        result = S.search(dc, params, limit=auth.EXPORT_MAX_ROWS, offset=0)
    rows = result["results"]
    if not rows:
        raise HTTPException(404, "Нет данных для экспорта")
    buf = _build_xlsx(rows)
    with users_conn() as uc:
        auth.audit(uc, action="export", user=user, ip=client_ip(request),
                   detail={k: v for k, v in params.items() if v},
                   result_count=len(rows))
        _note_anomaly(uc, user, "export", client_ip(request))
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="export.xlsx"'},
    )


def _build_xlsx(rows: list[dict]) -> io.BytesIO:
    """Собирает xlsx из списка записей по DISPLAY_FIELDS."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Результаты"
    ws.append(S.DISPLAY_FIELDS)
    for r in rows:
        ws.append([r.get(c, "") for c in S.DISPLAY_FIELDS])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@app.post("/api/export_rows")
def api_export_rows(request: Request, user=Depends(current_user),
                    payload: dict = None):
    """Экспорт в Excel выбранных строк (bulk) по списку rowid."""
    payload = payload or {}
    raw = payload.get("rowids")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "Не переданы строки для экспорта")
    try:
        rowids = [int(x) for x in raw][:1000]
    except (ValueError, TypeError):
        raise HTTPException(400, "Некорректный список строк")
    with users_conn() as uc:
        limit = auth.effective_limit(user, "export")
        if auth.count_today(uc, user["id"], "export") >= limit:
            raise HTTPException(429, f"Превышен дневной лимит экспортов ({limit})")
    with data_conn() as dc:
        rows = S.rows_by_ids(dc, rowids)
    if not rows:
        raise HTTPException(404, "Нет данных для экспорта")
    buf = _build_xlsx(rows)
    with users_conn() as uc:
        auth.audit(uc, action="export", user=user, ip=client_ip(request),
                   detail={"selected": len(rows)}, result_count=len(rows))
        _note_anomaly(uc, user, "export", client_ip(request))
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="export-selected.xlsx"'},
    )


@app.get("/api/phone_lookup")
def api_phone_lookup(request: Request, phone: str, user=Depends(current_user),
                     page: int = 1, page_size: int = 50):
    """Обратный поиск по телефону: все владельцы номера по всей базе."""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    if not (phone or "").strip():
        raise HTTPException(400, "Укажите номер телефона")
    with users_conn() as uc:
        limit = auth.effective_limit(user, "search")
        if auth.count_today_any(uc, user["id"], auth.LOOKUP_ACTIONS) >= limit:
            raise HTTPException(429, f"Превышен дневной лимит запросов ({limit})")
    with data_conn() as dc:
        result = S.phone_lookup(dc, phone, limit=page_size,
                                offset=(page - 1) * page_size)
    result["fields"] = S.DISPLAY_FIELDS
    result["mode"] = "exact"
    result["page"] = page
    result["page_size"] = page_size
    result["count"] = len(result["results"])
    with users_conn() as uc:
        auth.audit(uc, action="search", user=user, ip=client_ip(request),
                   detail={"Мобильный": phone}, result_count=result["total"])
        _note_anomaly(uc, user, "search", client_ip(request))
    return result


# ---------- Админ: пользователи ----------
@app.get("/api/admin/users")
def admin_list_users(admin=Depends(require_admin)):
    with users_conn() as uc:
        return {"users": [dict(u) for u in auth.list_users(uc)]}


@app.post("/api/admin/users")
def admin_create_user(request: Request, admin=Depends(require_admin),
                      username: str = Form(...), password: str = Form(...),
                      role: str = Form("user"), full_name: str = Form(""),
                      note: str = Form(""), expires_at: str = Form(""),
                      daily_search_limit: int = Form(0),
                      daily_export_limit: int = Form(0)):
    if role not in ("user", "admin"):
        raise HTTPException(400, "Некорректная роль")
    if len(password) < 8:
        raise HTTPException(400, "Пароль должен быть не короче 8 символов")
    with users_conn() as uc:
        if auth.get_user(uc, username):
            raise HTTPException(409, "Пользователь уже существует")
        auth.create_user(uc, username=username, password=password, role=role,
                         full_name=full_name, note=note,
                         created_by=admin["username"], expires_at=expires_at,
                         daily_search_limit=daily_search_limit,
                         daily_export_limit=daily_export_limit)
        auth.audit(uc, action="admin_create_user", user=admin,
                   ip=client_ip(request), detail={"username": username, "role": role})
    return {"ok": True}


@app.patch("/api/admin/users/{uid}")
def admin_update_user(uid: int, request: Request, admin=Depends(require_admin),
                      payload: dict = None):
    payload = payload or {}
    # Валидация и приведение типов (защита от мусора в БД)
    clean = {}
    if "role" in payload:
        if payload["role"] not in ("user", "admin"):
            raise HTTPException(400, "Некорректная роль")
        clean["role"] = payload["role"]
    if "is_active" in payload:
        clean["is_active"] = 1 if payload["is_active"] in (1, True, "1", "true") else 0
    for k in ("daily_search_limit", "daily_export_limit"):
        if k in payload:
            try:
                clean[k] = max(0, int(payload[k]))
            except (ValueError, TypeError):
                raise HTTPException(400, f"Поле {k} должно быть числом")
    for k in ("full_name", "note", "expires_at"):
        if k in payload:
            clean[k] = str(payload[k])[:300]

    with users_conn() as uc:
        target = auth.get_user_by_id(uc, uid)
        if not target:
            raise HTTPException(404, "Не найден")
        # запрет понизить/заблокировать самого себя
        if uid == admin["id"] and ("role" in clean or clean.get("is_active") == 0):
            raise HTTPException(400, "Нельзя менять собственную роль/статус")
        auth.update_user(uc, uid, **clean)
        auth.audit(uc, action="admin_update_user", user=admin,
                   ip=client_ip(request), detail={"uid": uid, "changes": payload})
    return {"ok": True}


@app.post("/api/admin/users/{uid}/password")
def admin_reset_password(uid: int, request: Request,
                         admin=Depends(require_admin), password: str = Form(...)):
    if len(password) < 8:
        raise HTTPException(400, "Пароль должен быть не короче 8 символов")
    with users_conn() as uc:
        if not auth.get_user_by_id(uc, uid):
            raise HTTPException(404, "Не найден")
        auth.set_password(uc, uid, password)
        auth.audit(uc, action="admin_reset_password", user=admin,
                   ip=client_ip(request), detail={"uid": uid})
    return {"ok": True}


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, request: Request, admin=Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Нельзя удалить себя")
    with users_conn() as uc:
        auth.delete_user(uc, uid)
        auth.audit(uc, action="admin_delete_user", user=admin,
                   ip=client_ip(request), detail={"uid": uid})
    return {"ok": True}


@app.get("/api/admin/audit")
def admin_audit(admin=Depends(require_admin), limit: int = 200,
                user_id: int = Query(None)):
    with users_conn() as uc:
        rows = auth.recent_audit(uc, limit=min(limit, 1000), user_id=user_id)
        return {"events": [dict(r) for r in rows]}


@app.get("/api/admin/stats")
def admin_stats(admin=Depends(require_admin), days: int = 7):
    days = max(1, min(days, 90))
    with users_conn() as uc:
        return auth.stats_overview(uc, days=days)


# ---------- Health ----------
@app.get("/api/health")
def health():
    # Не раскрываем детали (кол-во записей, текст ошибки) — только статус.
    try:
        with data_conn() as dc:
            dc.execute("SELECT 1 FROM residents LIMIT 1").fetchone()
        return {"status": "ok"}
    except sqlite3.Error as e:
        logging.error("health check failed: %s", e)
        return JSONResponse({"status": "error"}, status_code=500)


# ---------- Отдача React-сборки ----------
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    _DIST_ROOT = FRONTEND_DIST.resolve()

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        if path:
            # Защита от path traversal: отдаём файл только если он реально
            # лежит ВНУТРИ frontend/dist (resolve() схлопывает «..»).
            try:
                target = (FRONTEND_DIST / path).resolve()
                target.relative_to(_DIST_ROOT)
                if target.is_file():
                    return FileResponse(target)
            except (ValueError, OSError):
                pass
        return FileResponse(FRONTEND_DIST / "index.html")
