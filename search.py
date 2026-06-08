"""
Логика поиска по базе жителей (~16 млн записей).

Уровни:
  1) Точный/префиксный поиск через FTS5 — очень быстрый.
  2) Нечёткий (fuzzy) поиск — устойчивость к опечаткам (Левенштейн) и
     казахско-русским вариантам букв.

Дополнительно: фильтры по разобранному адресу (город/район/улица/дом),
пагинация, сортировка, поиск «соседей» по дому.
Подсветка совпадений делается на фронтенде по введённым токенам.
"""
from __future__ import annotations

import datetime
import logging
import re
import sqlite3

TABLE = "residents"
FTS = "residents_fts"
ADDR = "residents_addr"

# Поля формы -> колонки FTS5
FTS_FIELDS = {
    "Фамилия": "Фамилия",
    "Имя": "Имя",
    "Отчество": "Отчество",
    "Адрес": "Адрес",
    "Мобильный": "Мобильный_normalized",
}

NAME_FIELDS = ["Фамилия", "Имя", "Отчество"]

# Фильтры по разобранному адресу: поле формы -> колонка residents_addr
ADDR_FIELDS = {
    "Город": "city",
    "Район": "district",
    "Улица": "street",
    "Дом": "house",
}

DISPLAY_FIELDS = [
    "Фамилия", "Имя", "Отчество", "Пол", "Дата рождения", "Идентификатор",
    "ИНН", "Мобильный", "Рабочий", "Домашний", "Гражданство",
    "Национальность", "Адрес",
]

# Колонки, по которым разрешена сортировка (защита от инъекций)
ALLOWED_SORT = {
    "Фамилия", "Имя", "Отчество", "Дата рождения", "ИНН",
    "Гражданство", "Национальность",
}

_TRANSLIT = str.maketrans({
    "Ә": "А", "Ғ": "Г", "Қ": "К", "Ң": "Н", "Ө": "О",
    "Ұ": "У", "Ү": "У", "Һ": "Х", "І": "И", "Ё": "Е", "Й": "И",
})


def normalize(s: str | None) -> str:
    if not s:
        return ""
    s = str(s).upper().translate(_TRANSLIT)
    return re.sub(r"\s+", " ", s).strip()


def normalize_phone(s: str | None) -> str:
    digits = re.sub(r"\D", "", s or "")
    if digits.startswith("8") and 10 <= len(digits) <= 11:
        digits = "7" + digits[1:]
    return digits


def normalize_date(s: str | None) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return s


def levenshtein(a: str, b: str, max_dist: int) -> int:
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        row_min = i
        ca = a[i - 1]
        for j in range(1, len(b) + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_dist:
            return max_dist + 1
        prev = cur
    return prev[len(b)]


# Спецсимволы синтаксиса FTS5 — вырезаем из пользовательского ввода,
# чтобы исключить FTS-инъекцию, ошибки парсера и DoS на дорогих запросах.
_FTS_STRIP = re.compile(r'["*:^(){}\[\]\-+~]')


def _fts_clean(value: str) -> str:
    v = _FTS_STRIP.sub(" ", str(value))
    return re.sub(r"\s+", " ", v).strip()


def _fts_token(value: str) -> str:
    """Безопасный FTS5-токен: фраза в кавычках + префикс. '' если пусто."""
    v = _fts_clean(value)
    if not v:
        return ""
    return f'"{v}"*'


def _strip_internal(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.endswith("_normalized")}


def _scalar_filters(params: dict):
    """Точные фильтры (ИНН, дата) по таблице residents (алиас r)."""
    where, args = [], []
    inn = (params.get("ИНН") or "").strip()
    if inn:
        where.append('r."ИНН" = ?')
        args.append(inn)
    dob = normalize_date(params.get("Дата рождения"))
    if dob:
        where.append('r."Дата рождения" LIKE ?')
        args.append(f"{dob}%")
    return where, args


# Нормализация значения пола из формы -> как хранится в колонке "Пол".
_GENDER_MAP = {
    "м": "Мужской", "муж": "Мужской", "мужской": "Мужской",
    "m": "Мужской", "male": "Мужской",
    "ж": "Женский", "жен": "Женский", "женский": "Женский",
    "f": "Женский", "female": "Женский",
}


def _parse_age(v) -> int | None:
    """Целый возраст 0..130 или None (пустое/мусор/вне диапазона)."""
    s = str(v if v is not None else "").strip()
    if not s.isdigit():
        return None
    n = int(s)
    return n if 0 <= n <= 130 else None


def _shift_years(d: datetime.date, years: int) -> datetime.date:
    """d минус `years` лет; 29 февраля в невисокосный год -> 28 февраля."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def _refine_filters(params: dict, today: datetime.date | None = None):
    """
    Уточняющие фильтры по таблице residents (алиас r): пол и возраст.
    Возраст переводится в диапазон дат рождения (колонка хранит ISO
    гггг-мм-дд, поэтому сравнение строк = сравнение дат). Эти фильтры
    только сужают уже найденное и сами по себе поиск не запускают.
    """
    where, args = [], []
    gender = _GENDER_MAP.get((params.get("Пол") or "").strip().lower())
    if gender:
        where.append('r."Пол" = ?')
        args.append(gender)

    today = today or datetime.date.today()
    age_min = _parse_age(params.get("Возраст от"))
    age_max = _parse_age(params.get("Возраст до"))
    if age_min is not None and age_max is not None and age_min > age_max:
        age_min, age_max = age_max, age_min
    if age_min is not None:
        # Возраст >= age_min  =>  родился не позже (сегодня - age_min лет).
        # Берём строгую границу «< следующий день», чтобы не зависеть от
        # возможного хвоста времени в значении.
        upper = _shift_years(today, age_min) + datetime.timedelta(days=1)
        where.append('r."Дата рождения" < ?')
        args.append(upper.isoformat())
    if age_max is not None:
        # Возраст <= age_max  =>  родился не раньше (сегодня - (age_max+1) лет + 1 день).
        lower = _shift_years(today, age_max + 1) + datetime.timedelta(days=1)
        where.append('r."Дата рождения" >= ?')
        args.append(lower.isoformat())
    return where, args


def _addr_filters(params: dict):
    """Фильтры по разобранному адресу (алиас a = residents_addr)."""
    where, args = [], []
    needs_join = False
    for form_field, col in ADDR_FIELDS.items():
        v = params.get(form_field)
        if not v:
            continue
        needs_join = True
        if col in ("city", "district", "house"):
            # Точное совпадение -> seek по индексу (значения берутся из
            # автодополнения). Город покрывается idx_addr_neighbors(city,...).
            val = str(v).strip().upper() if col == "house" else normalize(v)
            where.append(f"a.{col} = ?")
            args.append(val)
        else:
            # Улица — префикс (часто вводят частично), обычно вместе с городом.
            where.append(f"a.{col} LIKE ?")
            args.append(f"{normalize(v)}%")
    return needs_join, where, args


def _order_clause(sort_by: str | None, sort_dir: str) -> str:
    if sort_by in ALLOWED_SORT:
        direction = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        return f' ORDER BY r."{sort_by}" {direction}'
    return ""


def _fts_conditions(params: dict):
    conditions = []
    fio = (params.get("ФИО") or "").strip()
    if fio:
        for tok in fio.split():
            t = _fts_token(tok)
            if not t:
                continue
            conditions.append(
                f"({{Фамилия}} : {t} OR {{Имя}} : {t} OR {{Отчество}} : {t})"
            )
    for form_field, col in FTS_FIELDS.items():
        value = params.get(form_field)
        if not value:
            continue
        if col.endswith("_normalized"):
            value = normalize_phone(value)
            if not value:
                continue
        t = _fts_token(value)
        if not t:
            continue
        conditions.append(f"{{{col}}} : {t}")
    return conditions


def _exact_search(conn, params, limit, offset, order_sql):
    scalar_w, scalar_a = _scalar_filters(params)
    addr_join, addr_w, addr_a = _addr_filters(params)
    refine_w, refine_a = _refine_filters(params)
    fts = _fts_conditions(params)

    joins = ""
    where = []
    args = []

    if fts:
        from_clause = f"{FTS} f JOIN {TABLE} r ON r.rowid = f.rowid"
        where.append(f"{FTS} MATCH ?")
        args.append(" AND ".join(fts))
    else:
        from_clause = f"{TABLE} r"

    if addr_join:
        joins += f" JOIN {ADDR} a ON a.rowid = r.rowid"
        where += addr_w
        args += addr_a
    where += scalar_w
    args += scalar_a
    where += refine_w
    args += refine_a

    if not where:
        return [], 0

    where_sql = " WHERE " + " AND ".join(where)
    base = f"FROM {from_clause}{joins}{where_sql}"
    total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT r.rowid AS _rowid, r.* {base}{order_sql} LIMIT ? OFFSET ?",
        args + [limit, offset],
    ).fetchall()
    return [_strip_internal(dict(r)) for r in rows], total


def _name_requirements(params):
    reqs = []
    for f in NAME_FIELDS:
        v = normalize(params.get(f))
        if v:
            reqs.append((v, (f,)))
    fio = normalize(params.get("ФИО"))
    if fio:
        for tok in fio.split():
            reqs.append((tok, tuple(NAME_FIELDS)))
    return reqs


def _fuzzy_search(conn, params, limit, offset, candidate_cap=30000):
    reqs = _name_requirements(params)
    if not reqs:
        return [], 0
    scalar_w, scalar_a = _scalar_filters(params)
    addr_join, addr_w, addr_a = _addr_filters(params)
    refine_w, refine_a = _refine_filters(params)

    extra_join = f" JOIN {ADDR} a ON a.rowid = r.rowid" if addr_join else ""
    extra_where = addr_w + scalar_w + refine_w
    extra_args = addr_a + scalar_a + refine_a

    candidates = []
    for token, fields in sorted(reqs, key=lambda r: -len(r[0])):
        prefix = token[:4] if len(token) >= 4 else token[:3]
        if not prefix:
            continue
        t = _fts_token(prefix)
        if not t:
            continue
        match = " OR ".join(f"{{{FTS_FIELDS[f]}}} : {t}" for f in fields)
        where = [f"{FTS} MATCH ?"] + extra_where
        args = [f"({match})"] + extra_args
        base = (f"FROM {FTS} f JOIN {TABLE} r ON r.rowid = f.rowid{extra_join} "
                f"WHERE " + " AND ".join(where))
        candidates = conn.execute(
            f"SELECT r.rowid AS _rowid, r.* {base} LIMIT ?", args + [candidate_cap]
        ).fetchall()
        if candidates:
            break
    if not candidates:
        return [], 0

    scored = []
    for r in candidates:
        row = dict(r)
        total_dist = 0
        ok = True
        for token, fields in reqs:
            th = max(1, len(token) // 4)
            best = min(
                (levenshtein(token, normalize(row.get(f)), th) for f in fields),
                default=th + 1,
            )
            if best > th:
                ok = False
                break
            total_dist += best
        if ok:
            scored.append((total_dist, row))

    scored.sort(key=lambda x: x[0])
    page = scored[offset:offset + limit]
    return [_strip_internal(r) for _, r in page], len(scored)


def search(conn: sqlite3.Connection, params: dict, limit: int = 50,
           offset: int = 0, sort_by: str | None = None,
           sort_dir: str = "asc") -> dict:
    """
    Главная функция поиска. Возвращает
    {results, total, mode, error, page_size, offset}.
    mode: exact | fuzzy | none.
    """
    try:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        order_sql = _order_clause(sort_by, sort_dir)

        has_text = any(params.get(f) for f in list(FTS_FIELDS) + ["ФИО"])
        addr_join, addr_w, _ = _addr_filters(params)
        scalar_w, _ = _scalar_filters(params)
        if not (has_text or addr_w or scalar_w):
            return {"results": [], "total": 0, "mode": "none", "error": None,
                    "page_size": limit, "offset": offset}

        rows, total = _exact_search(conn, params, limit, offset, order_sql)
        if rows:
            return {"results": rows, "total": total, "mode": "exact",
                    "error": None, "page_size": limit, "offset": offset}

        # Если был запрос по ФИО — пробуем нечёткий поиск.
        rows, total = _fuzzy_search(conn, params, limit, offset)
        mode = "fuzzy" if rows else "none"
        return {"results": rows, "total": total, "mode": mode, "error": None,
                "page_size": limit, "offset": offset}
    except sqlite3.Error as e:
        logging.error("search failed: %s", e)
        return {"results": [], "total": 0, "mode": "none",
                "error": "Внутренняя ошибка поиска", "page_size": limit,
                "offset": offset}


def neighbors(conn: sqlite3.Connection, rowid: int, limit: int = 100) -> dict:
    """Жители по тому же дому (город+улица+дом), что и заданная запись."""
    try:
        a = conn.execute(
            f"SELECT city, street, house FROM {ADDR} WHERE rowid=?", (rowid,)
        ).fetchone()
        if not a or not (a["city"] and a["street"] and a["house"]):
            return {"results": [], "total": 0, "error": None}
        base = (f"FROM {ADDR} a JOIN {TABLE} r ON r.rowid=a.rowid "
                f"WHERE a.city=? AND a.street=? AND a.house=?")
        args = [a["city"], a["street"], a["house"]]
        total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT r.rowid AS _rowid, r.* {base} LIMIT ?", args + [limit]
        ).fetchall()
        return {"results": [_strip_internal(dict(r)) for r in rows],
                "total": total, "error": None}
    except sqlite3.Error as e:
        logging.error("neighbors failed: %s", e)
        return {"results": [], "total": 0, "error": "Внутренняя ошибка"}


PHONE_NORM_COLS = ("Мобильный_normalized", "Рабочий_normalized",
                   "Домашний_normalized")


def surname_stem(surname: str | None) -> str:
    """
    Основа фамилии без родовых окончаний — чтобы матчить мужские/женские формы:
    НИДЗЕЛЬСКИЙ/НИДЗЕЛЬСКАЯ → НИДЗЕЛЬСК, ИВАНОВ/ИВАНОВА → ИВАНОВ.
    Возвращает '' если основа слишком короткая (защита от ложных совпадений).
    Сравнение идёт с сырой колонкой "Фамилия" (UPPER), поэтому Й/И не сводим.
    """
    s = re.sub(r"\s+", " ", str(surname or "").upper()).strip()
    if not s:
        return ""
    for suf in ("ИЙ", "ЫЙ", "ОЙ", "АЯ", "ЯЯ"):
        if s.endswith(suf) and len(s) > len(suf) + 2:
            s = s[:-2]
            break
    else:
        if s.endswith("А") and len(s) > 4:
            s = s[:-1]
    return s if len(s) >= 4 else ""


def patronymic_root(otch: str | None) -> str:
    """
    Имя отца из отчества: СТАНИСЛАВОВИЧ/СТАНИСЛАВОВНА → СТАНИСЛАВ,
    казах. «СТАНИСЛАВ ҰЛЫ»/«...ҚЫЗЫ» → СТАНИСЛАВ. '' если не распознано.
    """
    s = re.sub(r"\s+", " ", str(otch or "").upper()).strip()
    if not s:
        return ""
    # Казахские формы (отдельным словом или суффиксом).
    for kz in (" ҰЛЫ", " УЛЫ", " ҚЫЗЫ", " КЫЗЫ", " ТЕГІ", " ТЕГИ"):
        if s.endswith(kz):
            return s[:-len(kz)].strip()
    for kz in ("ҰЛЫ", "УЛЫ", "ҚЫЗЫ", "КЫЗЫ"):
        if s.endswith(kz) and len(s) > len(kz) + 2:
            return s[:-len(kz)]
    # Русские формы — от длинных к коротким, чтобы не отрезать лишнего
    # (-вич для муж. рода, -вна для жен. рода и их варианты).
    for suf in ("ИНИЧНА", "ЬЕВИЧ", "ОВИЧ", "ЕВИЧ", "ИЧНА",
                "ОВНА", "ЕВНА", "ВИЧ", "ВНА", "ИЧ", "НА"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            return s[:-len(suf)]
    return s


def house_base(house: str | None) -> str:
    """Базовый номер дома без корпуса/литеры: 9/1→9, 12А→12, 5-3→5. '' если нет цифр."""
    m = re.match(r"\s*(\d+)", str(house or ""))
    return m.group(1) if m else ""


def _phone_variants(phone: str) -> set[str]:
    """Варианты одного номера с разными префиксами (7/8) и без кода страны."""
    d = re.sub(r"\D", "", phone or "")
    out: set[str] = set()
    if not d:
        return out
    out.add(d)
    core = d[-10:] if len(d) >= 10 else d  # последние 10 цифр — «ядро» номера
    out.add(core)
    out.add("7" + core)
    out.add("8" + core)
    return out


def _phones_of(conn, rowid: int) -> list[str]:
    """Нормализованные телефоны записи (все три поля) одним списком без дублей."""
    row = conn.execute(
        f'SELECT "{PHONE_NORM_COLS[0]}" AS m, "{PHONE_NORM_COLS[1]}" AS r, '
        f'"{PHONE_NORM_COLS[2]}" AS h FROM {TABLE} WHERE rowid=?', (rowid,)
    ).fetchone()
    if not row:
        return []
    phones: list[str] = []
    for v in (row["m"], row["r"], row["h"]):
        for tok in str(v or "").split():
            if tok and tok not in phones:
                phones.append(tok)
    return phones


def connections(conn: sqlite3.Connection, rowid: int, limit: int = 100) -> dict:
    """
    Связи человека:
      relatives — жители того же дома, связанные по фамилии (основа ловит
                  муж./жен. род: НИДЗЕЛЬСКИЙ ↔ НИДЗЕЛЬСКАЯ) ИЛИ по отчеству
                  (общее отчество = брат/сестра; имя = корню отчества =
                  родитель/ребёнок — ловит детей с другой фамилией). У каждого
                  в поле `_relation` указана причина;
      phone     — любой житель с тем же номером (по всей базе), с учётом
                  вариантов префикса 7/8 и формата записи.
    Сам человек исключается из обоих списков.
    """
    try:
        # --- Родственники: тот же дом, связь по фамилии ИЛИ по отчеству ---
        relatives, rel_total = [], 0
        addr = conn.execute(
            f"SELECT city, street, house FROM {ADDR} WHERE rowid=?", (rowid,)
        ).fetchone()
        self_row = conn.execute(
            f'SELECT "Фамилия" AS s, "Имя" AS n, "Отчество" AS p '
            f'FROM {TABLE} WHERE rowid=?', (rowid,)
        ).fetchone()
        if self_row and addr and addr["city"] and addr["street"]:
            t_stem = surname_stem(self_row["s"])
            t_surname = normalize(self_row["s"])
            t_name = normalize(self_row["n"])              # имя искомого
            t_root = normalize(patronymic_root(self_row["p"]))  # имя его отца
            base = house_base(addr["house"])
            if addr["house"]:
                # Дом известен — сопоставляем по базовому номеру (9, 9/1, 9/2 →
                # один дом), т.к. в данных корпус указан непоследовательно.
                if base:
                    house_cond, house_args = "(a.house=? OR a.house GLOB ?)", [base, base + "[^0-9]*"]
                else:
                    house_cond, house_args = "a.house=?", [addr["house"]]
                street_only = False
            else:
                # Дом НЕ указан (≈5% базы) — слабый фолбэк: однофамильцы на той
                # же улице (без отчества — иначе слишком широко). Помечаем особо.
                house_cond, house_args = "1=1", []
                street_only = True
            occ = conn.execute(
                f"SELECT r.rowid AS _rowid, r.* FROM {ADDR} a "
                f"JOIN {TABLE} r ON r.rowid=a.rowid "
                f"WHERE a.city=? AND a.street=? AND {house_cond} "
                f"AND a.rowid<>? LIMIT 5000",
                [addr["city"], addr["street"], *house_args, rowid],
            ).fetchall()
            for o in occ:
                reasons = []
                o_surname = normalize(o["Фамилия"])
                o_name = normalize(o["Имя"])
                o_root = normalize(patronymic_root(o["Отчество"]))
                # Фамилия: по основе (муж./жен. род) или точно (короткие).
                surname_match = ((t_stem and surname_stem(o["Фамилия"]) == t_stem)
                                 or (not t_stem and t_surname and o_surname == t_surname))
                if street_only:
                    # Без номера дома доверяем только фамилии.
                    if surname_match:
                        reasons.append("та же улица")
                else:
                    if surname_match:
                        reasons.append("фамилия")
                    # Отчество: общий отец, родитель или ребёнок.
                    if t_root and o_root == t_root:
                        reasons.append("общее отчество")
                    if t_root and o_name == t_root:
                        reasons.append("возможно родитель")
                    if t_name and o_root == t_name:
                        reasons.append("возможно ребёнок")
                if reasons:
                    d = _strip_internal(dict(o))
                    d["_relation"] = ", ".join(dict.fromkeys(reasons))
                    relatives.append(d)
            rel_total = len(relatives)
            relatives = relatives[:limit]

        # --- Связи по телефону: тот же номер по всей базе ---
        phone_rows, phone_total = [], 0
        phones = _phones_of(conn, rowid)
        if phones:
            # Расширяем каждый номер вариантами префикса (7/8) и ядром.
            variants = sorted({v for p in phones for v in _phone_variants(p)})
            # FTS-токены — это сами цифры; ищем точное совпадение токена.
            # Фильтр по нескольким колонкам: один блок {col1 col2 col3} : "...".
            cols = "{" + " ".join(PHONE_NORM_COLS) + "}"
            match = " OR ".join(f'{cols} : "{p}"' for p in variants)
            base = (f"FROM {FTS} f JOIN {TABLE} r ON r.rowid=f.rowid "
                    f"WHERE {FTS} MATCH ? AND r.rowid<>?")
            args = [match, rowid]
            phone_total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
            rows = conn.execute(
                f"SELECT r.rowid AS _rowid, r.* {base} LIMIT ?", args + [limit]
            ).fetchall()
            phone_rows = [_strip_internal(dict(r)) for r in rows]

        return {
            "relatives": relatives, "relatives_total": rel_total,
            "phone": phone_rows, "phone_total": phone_total,
            "phones": phones, "error": None,
        }
    except sqlite3.Error as e:
        logging.error("connections failed: %s", e)
        return {"relatives": [], "relatives_total": 0, "phone": [],
                "phone_total": 0, "phones": [], "error": "Внутренняя ошибка"}


def suggest(conn: sqlite3.Connection, field: str, query: str, limit: int = 10):
    """Автодополнение: топ значений по префиксу, отсортированных по частоте."""
    q = normalize(query)
    # Минимум 2 символа — защита от перечисления ПДн перебором префиксов.
    if len(q) < 2 or field not in {"surname", "name", "city", "district", "street"}:
        return []
    rows = conn.execute(
        "SELECT value FROM suggest WHERE field=? AND value_norm LIKE ? "
        "ORDER BY freq DESC LIMIT ?",
        (field, f"{q}%", limit),
    ).fetchall()
    return [r["value"] for r in rows]
