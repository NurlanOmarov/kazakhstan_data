"""
Сжатие kazakhstan_data.db: удаляем индексы, которые больше не используются
поиском (ФИО ищется через FTS5, адрес — через residents_addr), и выполняем
VACUUM. idx_inn оставляем (точный поиск по ИИН).

ВНИМАНИЕ: VACUUM требует ~размер БД свободного места и эксклюзивный доступ.
Остановите приложение перед запуском.
"""
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "kazakhstan_data.db"
DROP = ["idx_lastname", "idx_firstname", "idx_patronymic", "idx_address"]

t0 = time.time()
conn = sqlite3.connect(DB)
for idx in DROP:
    print(f"DROP INDEX {idx}", flush=True)
    conn.execute(f"DROP INDEX IF EXISTS {idx};")
conn.commit()
print("VACUUM… (это долго)", flush=True)
conn.execute("VACUUM;")
conn.execute("PRAGMA journal_mode=DELETE;")
conn.close()
print(f"Готово за {time.time()-t0:.0f}с", flush=True)
