import sqlite3
from werkzeug.security import generate_password_hash
import getpass
import os

DB_FILE = 'users.db' # Название файла базы данных пользователей
ADMIN_USERNAME_DEFAULT = 'admin' # Логин администратора по умолчанию

def setup_users_database():
    """
    Создает базу данных пользователей и добавляет первого администратора.
    """
    print(f"--- Настройка базы данных пользователей: {DB_FILE} ---")

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        # Создаем таблицу пользователей, если она еще не существует
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0 NOT NULL
            );
        ''')
        conn.commit()
        print(f"Таблица 'users' создана или уже существует в {DB_FILE}.")

        # Проверяем, существует ли администратор по умолчанию
        cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", (ADMIN_USERNAME_DEFAULT,))
        if cursor.fetchone()[0] == 0:
            print(f"\nОбнаружено: Администратор '{ADMIN_USERNAME_DEFAULT}' не найден.")
            print("Пожалуйста, создайте учетную запись администратора.")
            
            username = ADMIN_USERNAME_DEFAULT
            password = getpass.getpass("Введите пароль для администратора: ")
            confirm_password = getpass.getpass("Повторите пароль: ")

            if password != confirm_password:
                print("\n[ОШИБКА] Пароли не совпадают. Запуск скрипта прерван.")
                return

            hashed_password = generate_password_hash(password)
            
            cursor.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                           (username, hashed_password, 1)) # is_admin = 1 для администратора
            conn.commit()
            print(f"\nАдминистратор '{username}' успешно создан.")
        else:
            print(f"Администратор '{ADMIN_USERNAME_DEFAULT}' уже существует. Пропускаем создание.")

        print("\n--- Настройка базы данных пользователей завершена ---")

    except sqlite3.Error as e:
        print(f"[ОШИБКА БАЗЫ ДАННЫХ] Произошла ошибка SQLite: {e}")
    except Exception as e:
        print(f"[ОБЩАЯ ОШИБКА] Произошла непредвиденная ошибка: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    setup_users_database()
