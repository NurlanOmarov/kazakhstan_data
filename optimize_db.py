"""
Безопасные оптимизации БД без потери (с приростом) производительности:
  - idx_addr_city удаляется: точный фильтр города покрывается
    idx_addr_neighbors(city, street, house) по левому префиксу;
  - FTS5 optimize: слияние сегментов индекса (меньше места, быстрее MATCH);
  - VACUUM: дефрагментация и возврат места.

Остановите приложение перед запуском (нужен эксклюзивный доступ).
"""
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "kazakhstan_data.db"
t0 = time.time()
conn = sqlite3.connect(DB)

print("DROP INDEX idx_addr_city (покрыт idx_addr_neighbors)", flush=True)
conn.execute("DROP INDEX IF EXISTS idx_addr_city;")
conn.commit()

print("FTS5 optimize… (слияние сегментов)", flush=True)
conn.execute("INSERT INTO residents_fts(residents_fts) VALUES('optimize');")
conn.commit()
print(f"  ok за {time.time()-t0:.0f}с", flush=True)

print("VACUUM… (долго)", flush=True)
conn.execute("VACUUM;")
conn.execute("PRAGMA journal_mode=DELETE;")
conn.close()
print(f"Готово за {time.time()-t0:.0f}с", flush=True)
