"""
Одноразовое построение вспомогательных структур в kazakhstan_data.db:

  residents_addr — разобранный адрес (region/city/district/street/house),
                   rowid совпадает с residents.rowid (для JOIN).
  suggest        — словарь автодополнения (field, value, value_norm, freq).

Запуск:  .venv/bin/python build_aux.py
Идемпотентно: таблицы пересоздаются заново.
"""
import sqlite3
import sys
import time
from collections import Counter, defaultdict

from address import parse_address, FIELDS
from search import normalize

DB = sys.argv[1] if len(sys.argv) > 1 else "kazakhstan_data.db"
CHUNK = 50000

# Поля, по которым строим автодополнение: (поле_suggest, источник)
SUGGEST_FROM = {
    "surname": "Фамилия",
    "name": "Имя",
}
SUGGEST_ADDR = ("city", "district", "street")  # из разобранного адреса


def main():
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-500000;")

    print("Пересоздаю residents_addr и suggest…", flush=True)
    conn.execute("DROP TABLE IF EXISTS residents_addr;")
    conn.execute(
        "CREATE TABLE residents_addr ("
        "rowid INTEGER PRIMARY KEY, region TEXT, city TEXT, "
        "district TEXT, street TEXT, house TEXT);"
    )
    conn.execute("DROP TABLE IF EXISTS suggest;")
    conn.execute(
        "CREATE TABLE suggest (field TEXT, value TEXT, value_norm TEXT, freq INTEGER);"
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM residents").fetchone()[0]
    print(f"Всего записей: {total}", flush=True)

    # Счётчики автодополнения: freq по нормализованному значению,
    # display — первое встреченное оригинальное написание.
    freq = defaultdict(Counter)            # field -> Counter(norm -> count)
    display = defaultdict(dict)            # field -> {norm: original}

    def add_suggest(field, original):
        if not original:
            return
        norm = normalize(original)
        if not norm:
            return
        freq[field][norm] += 1
        if norm not in display[field]:
            display[field][norm] = original.strip()

    src = conn.cursor()
    src.execute('SELECT rowid, "Фамилия", "Имя", "Адрес" FROM residents')

    ins = conn.cursor()
    done = 0
    while True:
        batch = src.fetchmany(CHUNK)
        if not batch:
            break
        addr_rows = []
        for rowid, surname, name, address in batch:
            p = parse_address(address)
            addr_rows.append((
                rowid,
                normalize(p["region"]), normalize(p["city"]),
                normalize(p["district"]), normalize(p["street"]),
                (p["house"] or "").strip().upper(),
            ))
            add_suggest("surname", surname)
            add_suggest("name", name)
            add_suggest("city", p["city"])
            add_suggest("district", p["district"])
            add_suggest("street", p["street"])
        ins.executemany(
            "INSERT INTO residents_addr "
            "(rowid, region, city, district, street, house) VALUES (?,?,?,?,?,?)",
            addr_rows,
        )
        conn.commit()
        done += len(batch)
        if done % (CHUNK * 10) == 0 or done == total:
            print(f"  обработано {done}/{total} ({done*100//total}%) "
                  f"за {time.time()-t0:.0f}с", flush=True)

    print("Записываю словарь suggest…", flush=True)
    sug_rows = []
    for field, counter in freq.items():
        for norm, cnt in counter.items():
            # отсекаем совсем редкие значения для имён/улиц, чтобы словарь не пух
            sug_rows.append((field, display[field][norm], norm, cnt))
    ins.executemany(
        "INSERT INTO suggest (field, value, value_norm, freq) VALUES (?,?,?,?)",
        sug_rows,
    )
    conn.commit()
    print(f"  записей в suggest: {len(sug_rows)}", flush=True)

    print("Создаю индексы…", flush=True)
    conn.execute("CREATE INDEX idx_addr_city ON residents_addr(city);")
    conn.execute("CREATE INDEX idx_addr_district ON residents_addr(district);")
    conn.execute(
        "CREATE INDEX idx_addr_neighbors ON residents_addr(city, street, house);")
    conn.execute(
        "CREATE INDEX idx_suggest ON suggest(field, value_norm);")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    print(f"Готово за {time.time()-t0:.0f}с", flush=True)


if __name__ == "__main__":
    main()
