"""
Создание users.db (схема) и первого администратора.

Пароль админа: из env KZ_ADMIN_PASSWORD, иначе генерируется и печатается.
Запуск:  KZ_ADMIN_PASSWORD=... .venv/bin/python init_users.py
"""
import os
import secrets

import auth

ADMIN = os.environ.get("KZ_ADMIN_USER", "admin")


def main():
    conn = auth.connect()
    auth.init_schema(conn)
    if auth.get_user(conn, ADMIN):
        print(f"Администратор '{ADMIN}' уже существует — пропускаю.")
        conn.close()
        return
    password = os.environ.get("KZ_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    auth.create_user(
        conn, username=ADMIN, password=password, role="admin",
        full_name="Администратор", created_by="init",
    )
    conn.close()
    print("=" * 50)
    print(f"Создан администратор: {ADMIN}")
    print(f"Пароль: {password}")
    print("Сохраните пароль и смените его после первого входа.")
    print("=" * 50)


if __name__ == "__main__":
    main()
