import pandas as pd
import sqlite3
import os
import time
import re

# --- Конфигурация файла и базы данных ---
input_csv_file = 'Жители Казахстан 16kk.csv'
output_db_file = 'kazakhstan_data.db'
table_name = 'residents'
fts_table_name = f"{table_name}_fts" # Имя FTS-таблицы

# Параметры чтения CSV-файла
csv_delimiter = ';'
csv_encoding = 'utf-8'

# Размер чанка (количество строк, читаемых за раз)
chunk_size = 100000

# НОВОЕ: Определение ALL_FIELDS здесь, чтобы оно было доступно
ALL_FIELDS = [
    "Фамилия", "Имя", "Отчество", "Пол", "Дата рождения", "Идентификатор", "ИНН",
    "Мобильный", "Рабочий", "Домашний", "Гражданство", "Национальность", "Адрес",
    "Адрес подтвержден", "Дата начала проживания", "Дата конца проживания"
]

print(f"--- Запуск скрипта импорта CSV в SQLite с FTS ---")
print(f"Исходный CSV-файл: {input_csv_file}")
print(f"Целевая база данных SQLite: {output_db_file}")
print(f"Основная таблица: {table_name}")
print(f"FTS таблица: {fts_table_name}")

# --- Проверка существования входного файла ---
if not os.path.exists(input_csv_file):
    print(f"[ОШИБКА] Входной CSV-файл не найден по пути: '{input_csv_file}'")
    print("Пожалуйста, убедитесь, что путь к файлу указан корректно.")
    exit()

# --- Вспомогательная функция для очистки номеров телефонов ---
def normalize_phone_numbers(numbers_str):
    if pd.isna(numbers_str):
        return ""
    
    numbers_list = str(numbers_str).split(',')
    
    cleaned_and_filtered_numbers = []
    for num_part in numbers_list:
        cleaned_num = re.sub(r'\D', '', num_part).strip()
        if cleaned_num:
            cleaned_and_filtered_numbers.append(cleaned_num)
            
    return " ".join(cleaned_and_filtered_numbers)


# --- Основной процесс импорта ---
try:
    start_time = time.time()
    total_rows_processed = 0
    first_chunk_processed = False

    conn = sqlite3.connect(output_db_file)
    cursor = conn.cursor()
    print(f"Успешно подключено к базе данных SQLite: {output_db_file}")

    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA cache_size = -500000;")
    conn.commit()

    print(f"Начало чтения CSV-файла частями (по {chunk_size} строк)...")
    for i, chunk in enumerate(pd.read_csv(input_csv_file, sep=csv_delimiter, encoding=csv_encoding, chunksize=chunk_size)):
        # Очистка поля ИНН
        if 'ИНН' in chunk.columns:
            chunk['ИНН'] = chunk['ИНН'].astype(str).str.strip().str.replace(r'[^\x20-\x7E]', '', regex=True)
            chunk['ИНН'] = chunk['ИНН'].str.replace(r'\s+', '', regex=True)

        # --- Нормализация номеров телефонов ---
        if 'Мобильный' in chunk.columns:
            chunk['Мобильный_normalized'] = chunk['Мобильный'].apply(normalize_phone_numbers)
        else:
            chunk['Мобильный_normalized'] = ''

        if 'Рабочий' in chunk.columns:
            chunk['Рабочий_normalized'] = chunk['Рабочий'].apply(normalize_phone_numbers)
        else:
            chunk['Рабочий_normalized'] = ''

        if 'Домашний' in chunk.columns:
            chunk['Домашний_normalized'] = chunk['Домашний'].apply(normalize_phone_numbers)
        else:
            chunk['Домашний_normalized'] = ''
        # --- Конец нормализации номеров телефонов ---

        if not first_chunk_processed:
            # Убедимся, что все ALL_FIELDS колонки присутствуют
            # НОВОЕ ИСПРАВЛЕНИЕ: Это может быть излишним, если ALL_FIELDS используется только для FTS и обычных индексов.
            # Но если какие-то колонки гарантированно должны быть в основной таблице, этот блок полезен.
            # all_cols_in_chunk = chunk.columns.tolist()
            # for col in ALL_FIELDS:
            #     if col not in all_cols_in_chunk:
            #         chunk[col] = '' 

            # Добавляем нормализованные телефонные колонки, если они не в ALL_FIELDS
            # Они должны быть в чанке, потому что мы их создаем выше
            
            chunk.to_sql(table_name, conn, if_exists='replace', index=False)
            first_chunk_processed = True
            print(f"Создана основная таблица '{table_name}' и записаны первые {len(chunk)} строк.")
        else:
            chunk.to_sql(table_name, conn, if_exists='append', index=False)
            print(f"Добавлено {len(chunk)} строк. Всего обработано: {total_rows_processed + len(chunk)} строк.")
        
        total_rows_processed += len(chunk)

    print(f"Всего строк обработано и добавлено в основную базу данных: {total_rows_processed}")
    end_import_time = time.time()
    print(f"Время, затраченное на импорт данных: {end_import_time - start_time:.2f} секунд.")

    # --- Создание обычных индексов ---
    cursor = conn.cursor()
    print("Начало создания обычных индексов...")
    index_start_time = time.time()

    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_lastname ON {table_name}(\"Фамилия\")")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_firstname ON {table_name}(\"Имя\")")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_patronymic ON {table_name}(\"Отчество\")")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_inn ON {table_name}(\"ИНН\")")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_address ON {table_name}(\"Адрес\")")

    conn.commit()
    print("Все обычные индексы созданы.")

    # --- Создание таблицы полнотекстового поиска (FTS5) ---
    print(f"Начало создания и наполнения FTS5-таблицы '{fts_table_name}'...")
    fts_start_time = time.time()

    cursor.execute(f"DROP TABLE IF EXISTS {fts_table_name};")
    conn.commit()

    cursor.execute(f"""
        CREATE VIRTUAL TABLE {fts_table_name} USING fts5(
            "Фамилия", "Имя", "Отчество", "Адрес", 
            "Мобильный_normalized", "Рабочий_normalized", "Домашний_normalized",
            content='{table_name}', content_rowid='rowid'
        );
    """)
    conn.commit()
    print(f"Виртуальная FTS-таблица '{fts_table_name}' создана.")

    # Наполняем FTS-таблицу данными
    print(f"Наполнение FTS-таблицы данными из '{table_name}' (это займет время)...")
    cursor.execute(f"""
        INSERT INTO {fts_table_name}(rowid, "Фамилия", "Имя", "Отчество", "Адрес", "Мобильный_normalized", "Рабочий_normalized", "Домашний_normalized")
        SELECT rowid, "Фамилия", "Имя", "Отчество", "Адрес", "Мобильный_normalized", "Рабочий_normalized", "Домашний_normalized" FROM {table_name};
    """)
    conn.commit()
    fts_end_time = time.time()
    print(f"FTS-таблица успешно наполнена за {fts_end_time - fts_start_time:.2f} секунд.")
    print(f"Общее время выполнения скрипта: {fts_end_time - start_time:.2f} секунд.")

except FileNotFoundError:
    print(f"[ОШИБКА] Файл '{input_csv_file}' не найден. Проверьте путь.")
except pd.errors.EmptyDataError:
    print(f"[ОШИБКА] Файл '{input_csv_file}' пуст или имеет некорректный формат.")
except Exception as e:
    print(f"[КРИТИЧЕСКАЯ ОШИБКА] Произошла непредвиденная ошибка во время импорта или индексации: {e}")
finally:
    if 'conn' in locals() and conn:
        conn.close()
        print("Подключение к базе данных SQLite закрыто.")
    print("--- Скрипт импорта завершен ---")