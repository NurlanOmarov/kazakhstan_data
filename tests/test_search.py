"""
Тесты логики поиска. Часть тестов требует реальную БД kazakhstan_data.db
(пропускаются, если файла нет).
"""
import os
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import datetime

from search import (
    normalize, normalize_phone, normalize_date, levenshtein, search,
    _refine_filters, _shift_years,
)

DB_PATH = os.environ.get(
    "KZ_DB_PATH", str(Path(__file__).resolve().parents[1] / "kazakhstan_data.db")
)
HAS_DB = Path(DB_PATH).exists()
needs_db = pytest.mark.skipif(not HAS_DB, reason="нет файла БД")


# ---------- Юнит-тесты нормализации (без БД) ----------

def test_normalize_upper_and_spaces():
    assert normalize("  иванов   иван ") == "ИВАНОВ ИВАН"

def test_normalize_kazakh_letters():
    # Ә→А, Қ→К, Ө→О, Ұ/Ү→У, і→И, ё→е, й→и
    assert normalize("Әбілқайыр") == normalize("Абилкаиыр")
    assert normalize("Өмір") == "ОМИР"

def test_normalize_phone():
    assert normalize_phone("+7 (701) 544-19-95") == "77015441995"
    assert normalize_phone("8 701 544 1995") == "77015441995"

def test_normalize_date():
    assert normalize_date("14.10.1975") == "1975-10-14"
    assert normalize_date("1975-10-14") == "1975-10-14"

def test_levenshtein_basic():
    assert levenshtein("РЫСПАЕВНА", "РЫСБАЕВНА", 2) == 1
    assert levenshtein("ИВАН", "ИВАН", 2) == 0
    # обрыв по cutoff
    assert levenshtein("АБВ", "ЯЮЭЬ", 1) == 2  # > max_dist -> max_dist+1


# ---------- Фильтры пол/возраст (без БД) ----------

def test_refine_gender_normalization():
    assert _refine_filters({"Пол": "м"})[1] == ["Мужской"]
    assert _refine_filters({"Пол": "Женский"})[1] == ["Женский"]
    assert _refine_filters({"Пол": "женский"})[1] == ["Женский"]
    # мусор -> фильтр не добавляется
    assert _refine_filters({"Пол": "xyz"}) == ([], [])

def test_refine_age_bounds():
    today = datetime.date(2026, 6, 8)
    w, a = _refine_filters({"Возраст от": "20", "Возраст до": "30"}, today=today)
    # верх (>=20 лет): родился до 2006-06-09; низ (<=30): родился с 1995-06-09
    assert ('r."Дата рождения" < ?', "2006-06-09") in list(zip(w, a))
    assert ('r."Дата рождения" >= ?', "1995-06-09") in list(zip(w, a))

def test_refine_age_swapped_and_garbage():
    today = datetime.date(2026, 6, 8)
    # перепутанные местами от/до -> нормализуются
    swapped = _refine_filters({"Возраст от": "30", "Возраст до": "20"}, today=today)
    normal = _refine_filters({"Возраст от": "20", "Возраст до": "30"}, today=today)
    assert swapped == normal
    # пустое/мусор не даёт условий
    assert _refine_filters({"Возраст от": "", "Возраст до": "abc"}) == ([], [])

def test_shift_years_leap_day():
    # 29 февраля високосного 2024 минус 1 год -> 28 февраля 2023
    assert _shift_years(datetime.date(2024, 2, 29), 1) == datetime.date(2023, 2, 28)


# ---------- Интеграционные тесты (нужна БД) ----------

@pytest.fixture()
def conn():
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@needs_db
def test_exact_search_found(conn):
    res = search(conn, {"Фамилия": "Сыздыкова", "Имя": "Багыжан"})
    assert res["mode"] == "exact"
    assert res["total"] >= 1
    assert any("СЫЗДЫКОВА" in (r.get("Фамилия") or "") for r in res["results"])

@needs_db
def test_exact_is_case_insensitive(conn):
    lower = search(conn, {"Фамилия": "сыздыкова", "Имя": "багыжан"})
    upper = search(conn, {"Фамилия": "СЫЗДЫКОВА", "Имя": "БАГЫЖАН"})
    assert lower["total"] == upper["total"] >= 1

@needs_db
def test_fuzzy_typo_in_patronymic(conn):
    # Пользователь ввёл "Рыспаевна", в базе "РЫСБАЕВНА"
    res = search(conn, {"Фамилия": "Сыздыкова", "Имя": "Багыжан",
                        "Отчество": "Рыспаевна"})
    assert res["mode"] == "fuzzy"
    assert res["total"] >= 1
    top = res["results"][0]
    assert top["Отчество"] == "РЫСБАЕВНА"
    assert top["ИНН"] == "751014450225"

@needs_db
def test_fio_single_line_exact(conn):
    res = search(conn, {"ФИО": "Сыздыкова Багыжан"})
    assert res["total"] >= 1
    assert any(r["ИНН"] == "751014450225" for r in res["results"])

@needs_db
def test_fio_single_line_with_typo(conn):
    # Опечатка в одном из токенов -> нечёткий поиск
    res = search(conn, {"ФИО": "Сыздыкова Багжан Рыспаевна"})
    assert res["total"] >= 1
    assert any(r["ИНН"] == "751014450225" for r in res["results"])

@needs_db
def test_search_by_inn(conn):
    res = search(conn, {"ИНН": "751014450225"})
    assert res["total"] >= 1
    assert res["results"][0]["Имя"] == "БАГЫЖАН"

@needs_db
def test_no_internal_columns_in_output(conn):
    res = search(conn, {"ИНН": "751014450225"})
    assert res["results"]
    for k in res["results"][0]:
        assert not k.endswith("_normalized")

@needs_db
def test_empty_query_returns_nothing(conn):
    res = search(conn, {})
    assert res["total"] == 0
    assert res["results"] == []


@needs_db
def test_pagination(conn):
    # Частая фамилия -> много результатов, проверяем страницы
    p1 = search(conn, {"Фамилия": "Иванова"}, limit=10, offset=0)
    p2 = search(conn, {"Фамилия": "Иванова"}, limit=10, offset=10)
    assert p1["total"] == p2["total"]
    if p1["total"] > 10:
        ids1 = {r["_rowid"] for r in p1["results"]}
        ids2 = {r["_rowid"] for r in p2["results"]}
        assert ids1.isdisjoint(ids2)  # страницы не пересекаются


@needs_db
def test_rowid_present_for_neighbors(conn):
    res = search(conn, {"ИНН": "751014450225"})
    assert res["results"][0].get("_rowid")


@needs_db
def test_address_filter(conn):
    from search import neighbors
    res = search(conn, {"Город": "Алматы", "Улица": "Декарта", "Дом": "5"})
    assert res["total"] >= 1
    # «соседи» по той же записи дают тот же дом
    rid = res["results"][0]["_rowid"]
    nb = neighbors(conn, rid)
    assert nb["total"] >= 1


@needs_db
def test_suggest(conn):
    from search import suggest
    res = suggest(conn, "surname", "Сызд", limit=5)
    assert any("СЫЗДЫКОВА" == v for v in res)
    cities = suggest(conn, "city", "Алм", limit=5)
    assert any("Алматы" == v for v in cities)


@needs_db
def test_gender_age_refine_on_db(conn):
    base = search(conn, {"Фамилия": "Иванов"}, limit=200)
    flt = search(conn, {"Фамилия": "Иванов", "Пол": "Женский",
                        "Возраст от": "30", "Возраст до": "50"}, limit=200)
    assert flt["total"] <= base["total"]
    today = datetime.date.today()
    for r in flt["results"]:
        assert r["Пол"] == "Женский"
        y, m, d = map(int, str(r["Дата рождения"])[:10].split("-"))
        age = today.year - y - ((today.month, today.day) < (m, d))
        assert 30 <= age <= 50


@needs_db
def test_refine_alone_does_not_scan(conn):
    # Только пол/возраст без имени/адреса -> поиск не запускается
    res = search(conn, {"Пол": "Мужской", "Возраст от": "20", "Возраст до": "40"})
    assert res["mode"] == "none"
    assert res["total"] == 0


@needs_db
def test_sort(conn):
    res = search(conn, {"Фамилия": "Иванова"}, limit=20, sort_by="Имя", sort_dir="asc")
    names = [r["Имя"] for r in res["results"] if r.get("Имя")]
    assert names == sorted(names)
