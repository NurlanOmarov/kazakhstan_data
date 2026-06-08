import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import sqlite3
import re
from datetime import datetime
import pandas as pd
import io
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, HiddenField # Добавлен HiddenField для ID пользователя
from wtforms.validators import DataRequired, EqualTo, ValidationError

# --- НОВОЕ: Импорт для Flask-Limiter ---
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
# --- КОНЕЦ НОВОГО ---

# Создаем экземпляр Flask приложения
app = Flask(__name__)
app.secret_key = 'your_super_secret_key_for_spy_app_12345' 

# --- НОВОЕ: Конфигурация Flask-Limiter ---
# Storage URI: "memory://" означает, что лимиты хранятся в памяти сервера.
# key_func=get_remote_address: Лимиты применяются к каждому уникальному IP-адресу.
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri="memory://",
    strategy="fixed-window"
)
# --- КОНЕЦ НОВОГО ---

# --- Конфигурация баз данных ---

# База данных с данными жителей
DB_FILE = 'kazakhstan_data.db' 
TABLE_NAME = 'residents' 
FTS_TABLE_NAME = f"{TABLE_NAME}_fts" 

# База данных пользователей
USERS_DB_FILE = 'users.db' 

ALL_FIELDS = [
    "Фамилия", "Имя", "Отчество", "Пол", "Дата рождения", "Идентификатор", "ИНН",
    "Мобильный", "Рабочий", "Домашний", "Гражданство", "Национальность", "Адрес",
    "Адрес подтвержден", "Дата начала проживания", "Дата конца проживания"
]

FTS_SEARCH_FIELDS_MAP = {
    "Фамилия": "Фамилия",
    "Имя": "Имя",
    "Отчество": "Отчество",
    "Адрес": "Адрес",
    "Мобильный": "Мобильный_normalized",
    "Рабочий": "Рабочий_normalized",
    "Домашний": "Домашний_normalized"
}

FORM_FIELDS = [
    "Фамилия",
    "Имя",
    "Отчество",
    "Дата рождения",
    "ИНН",
    "Мобильный",
    "Адрес"
]

DISPLAY_FIELDS = ALL_FIELDS


print(f"[DEBUG] Инициализация приложения. Путь к БД данных: {DB_FILE}")
print(f"[DEBUG] Путь к БД пользователей: {USERS_DB_FILE}")

data_db_exists = os.path.exists(DB_FILE)
if not data_db_exists:
    print(f"[ERROR] Файл базы данных данных не найден: '{DB_FILE}'")
    print("[ERROR] Пожалуйста, убедитесь, что вы запустили 'csv_to_sqlite.py' для создания базы данных.")

users_db_exists = os.path.exists(USERS_DB_FILE)
if not users_db_exists:
    print(f"[ERROR] Файл базы данных пользователей не найден: '{USERS_DB_FILE}'")
    print("[ERROR] Пожалуйста, запустите 'users_db_setup.py' для создания базы данных пользователей и администратора.")


# --- Вспомогательные функции для нормализации данных ---

def normalize_user_phone_input(user_input_str):
    if not user_input_str:
        return ""
    
    cleaned_num = re.sub(r'\D', '', str(user_input_str)).strip()
    
    if cleaned_num.startswith('8') and len(cleaned_num) >= 10 and len(cleaned_num) <= 11:
        cleaned_num = '7' + cleaned_num[1:]
        
    return cleaned_num

def normalize_date_input(date_str):
    if not date_str:
        return ""
    
    match = re.match(r'(\d{1,2})[./-](\d{1,2})[./-](\d{4})', date_str)
    if match:
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    
    if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
        return date_str
        
    return ""

def format_date_for_display(date_str):
    if not date_str:
        return ""
    try:
        dt_obj = datetime.strptime(str(date_str), '%Y-%m-%d')
        return dt_obj.strftime('%d.%m.%Y')
    except (ValueError, TypeError):
        return str(date_str)


# --- ФУНКЦИИ УПРАВЛЕНИЯ ПОЛЬЗОВАТЕЛЯМИ (ДОБАВЛЕНЫ НОВЫЕ) ---

def get_user_by_username(username):
    """Получает пользователя по имени из users.db."""
    if not users_db_exists:
        return None
    conn = None
    try:
        conn = sqlite3.connect(USERS_DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        return user
    except sqlite3.Error as e:
        print(f"[ОШИБКА БД ПОЛЬЗОВАТЕЛЕЙ] Ошибка при получении пользователя: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_all_users():
    """Получает список всех пользователей из users.db."""
    if not users_db_exists:
        return []
    conn = None
    try:
        conn = sqlite3.connect(USERS_DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, is_admin FROM users")
        users = cursor.fetchall()
        return users
    except sqlite3.Error as e:
        print(f"[ОШИБКА БД ПОЛЬЗОВАТЕЛЕЙ] Ошибка при получении всех пользователей: {e}")
        return []
    finally:
        if conn:
            conn.close()

def delete_user_from_db(user_id):
    """Удаляет пользователя по ID из users.db."""
    if not users_db_exists:
        return False
    conn = None
    try:
        conn = sqlite3.connect(USERS_DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        print(f"[DEBUG] Пользователь с ID {user_id} удален из БД.")
        return True
    except sqlite3.Error as e:
        print(f"[ОШИБКА БД ПОЛЬЗОВАТЕЛЕЙ] Ошибка при удалении пользователя {user_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()

def update_user_password(user_id, new_password_hash):
    """Обновляет пароль пользователя по ID в users.db."""
    if not users_db_exists:
        return False
    conn = None
    try:
        conn = sqlite3.connect(USERS_DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))
        conn.commit()
        print(f"[DEBUG] Пароль пользователя с ID {user_id} обновлен в БД.")
        return True
    except sqlite3.Error as e:
        print(f"[ОШИБКА БД ПОЛЬЗОВАТЕЛЕЙ] Ошибка при обновлении пароля пользователя {user_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()


def add_user_to_db(username, password_hash, is_admin=0):
    """Добавляет нового пользователя в users.db."""
    if not users_db_exists:
        print("[ОШИБКА] Невозможно добавить пользователя: база данных пользователей не существует.")
        return False
    conn = None
    try:
        conn = sqlite3.connect(USERS_DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                       (username, password_hash, is_admin))
        conn.commit()
        print(f"[DEBUG] Пользователь '{username}' успешно добавлен в БД.")
        return True
    except sqlite3.IntegrityError: 
        print(f"[ОШИБКА БД ПОЛЬЗОВАТЕЛЕЙ] Пользователь '{username}' уже существует.")
        return False
    except sqlite3.Error as e:
        print(f"[ОШИБКА БД ПОЛЬЗОВАТЕЛЕЙ] Ошибка при добавлении пользователя: {e}")
        return False
    finally:
        if conn:
            conn.close()

def is_admin_logged_in():
    """Проверяет, авторизован ли текущий пользователь как администратор."""
    if not session.get('logged_in'):
        return False
    
    username = session.get('username')
    if username:
        user = get_user_by_username(username)
        if user and user['is_admin'] == 1:
            return True
    return False


# --- Формы для Flask-WTF ---

class LoginForm(FlaskForm):
    """Форма входа."""
    username = StringField('Имя Агента', validators=[DataRequired()])
    password = PasswordField('Кодовое Слово', validators=[DataRequired()])

class RegisterForm(FlaskForm):
    """Форма регистрации нового пользователя администратором."""
    username = StringField('Логин нового пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    confirm_password = PasswordField('Подтверждение пароля', validators=[DataRequired(), EqualTo('password', message='Пароли должны совпадать')])
    is_admin_new_user = StringField('Сделать администратором? (да/нет)', default='нет')

    def validate_username(self, field):
        if get_user_by_username(field.data):
            raise ValidationError('Это имя пользователя уже занято. Выберите другое.')
    
    def validate_is_admin_new_user(self, field):
        if field.data.lower() not in ['да', 'нет', 'yes', 'no', '']:
            raise ValidationError('Введите "да" или "нет" (или "yes/no").')

class DeleteUserForm(FlaskForm):
    """Форма для удаления пользователя."""
    user_id = HiddenField(validators=[DataRequired()]) # Скрытое поле для ID пользователя

class ResetPasswordForm(FlaskForm):
    """Форма для сброса/изменения пароля пользователя."""
    user_id = HiddenField(validators=[DataRequired()])
    new_password = PasswordField('Новый пароль', validators=[DataRequired()])
    confirm_new_password = PasswordField('Подтвердите новый пароль', validators=[DataRequired(), EqualTo('new_password', message='Пароли должны совпадать')])


# --- Основная функция для получения данных из базы данных (без изменений) ---
def get_data_from_db(search_params=None, limit=None):
    data = []
    total_matches = 0
    error_msg = None

    if not data_db_exists: 
        return [], 0, "Файл базы данных данных не найден на сервере. Создайте его, запустив csv_to_sqlite.py."

    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA cache_size = -500000;")
        conn.commit()

        sql_field_names = {field: f'"{field}"' for field in ALL_FIELDS}
        
        main_where_clauses = []
        main_params = []
        fts_rowids = None

        final_fts_conditions = []
        
        for form_field, fts_col_name in FTS_SEARCH_FIELDS_MAP.items():
            value = search_params.get(form_field)
            if value:
                if '_normalized' in fts_col_name:
                    cleaned_value = normalize_user_phone_input(value)
                    if cleaned_value:
                        final_fts_conditions.append(f'"{fts_col_name}":{cleaned_value}*')
                else:
                    final_fts_conditions.append(f'"{fts_col_name}":{value}*')
        
        if final_fts_conditions:
            fts_query_string = " AND ".join(final_fts_conditions)
            print(f"[DEBUG] Итоговый FTS-запрос: SELECT rowid FROM {FTS_TABLE_NAME} WHERE {FTS_TABLE_NAME} MATCH '{fts_query_string}'")
            
            cursor.execute(f"SELECT rowid FROM {FTS_TABLE_NAME} WHERE {FTS_TABLE_NAME} MATCH ?", (fts_query_string,))
            fts_rowids = [row[0] for row in cursor.fetchall()]
            
            if not fts_rowids:
                conn.close()
                return [], 0, None

            if len(fts_rowids) > 0:
                main_where_clauses.append(f"rowid IN ({','.join(map(str, fts_rowids))})")
            else:
                conn.close()
                return [], 0, None
        
        for field in ALL_FIELDS:
            if field in FTS_SEARCH_FIELDS_MAP:
                continue
            if f"{field}_normalized" in FTS_SEARCH_FIELDS_MAP.values():
                continue

            value = search_params.get(field)
            if value:
                if field == "ИНН":
                    main_where_clauses.append(f"{sql_field_names[field]} = ?")
                    main_params.append(value)
                elif field == "Дата рождения":
                    normalized_date = normalize_date_input(value)
                    if normalized_date:
                        main_where_clauses.append(f"{sql_field_names[field]} LIKE ?")
                        main_params.append(f"{normalized_date}%")
                    else:
                        print(f"[WARNING] Некорректный формат даты '{value}' для поля 'Дата рождения'. Поиск не будет применен.")
                else:
                    main_where_clauses.append(f"{sql_field_names[field]} LIKE ?")
                    main_params.append(f"%{value}%")
        
        final_where_sql = "WHERE " + " AND ".join(main_where_clauses) if main_where_clauses else ""
        
        count_query = f"SELECT COUNT(*) FROM {TABLE_NAME} {final_where_sql}"
        cursor.execute(count_query, main_params)
        total_matches = cursor.fetchone()[0]
        print(f"[DEBUG] Всего найдено совпадений в БД: {total_matches}")

        data_query = f"SELECT * FROM {TABLE_NAME} {final_where_sql}"
        if limit is not None:
            data_query += " LIMIT ?"
            params_for_data = main_params + [limit]
        else:
            params_for_data = main_params
        
        cursor.execute(data_query, params_for_data)
        
        for row in cursor.fetchall():
            row_dict = dict(row)
            if "Дата рождения" in row_dict:
                row_dict["Дата рождения"] = format_date_for_display(row_dict["Дата рождения"])
            data.append(row_dict)
            
        conn.close()
        print(f"[DEBUG] Извлечено {len(data)} строк из БД для отображения.")

    except sqlite3.Error as e:
        print(f"[CRITICAL ERROR] Ошибка SQLite при запросе: {e}")
        error_msg = f"Ошибка базы данных: {e}"
    except Exception as e:
        print(f"[КРИТИЧЕСКАЯ ОШИБКА] Неизвестная ошибка при работе с БД: {e}")
        error_msg = f"Произошла ошибка: {e}"

    return data, total_matches, error_msg

# --- Роуты Flask приложения ---

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per 15 minutes") # 5 запросов за 15 минут с одного IP
def login():
    print(f"[DEBUG] Запрос к /login. Метод: {request.method}")
    form = LoginForm() 
    
    if form.validate_on_submit(): 
        username = form.username.data
        password = form.password.data

        user = get_user_by_username(username) # Ищем пользователя в БД

        if user and check_password_hash(user['password_hash'], password):
            session['logged_in'] = True
            session['username'] = user['username'] # Сохраняем имя пользователя в сессии
            session['is_admin'] = (user['is_admin'] == 1) # Сохраняем статус админа
            
            print(f"[DEBUG] Успешная авторизация пользователя: {username} (Админ: {session['is_admin']})")
            flash('Доступ разрешен. Система безопасности активирована.', 'success')
            return redirect(url_for('index'))
        else:
            print("[DEBUG] Ошибка авторизации.")
            flash('Доступ запрещен. Неверный логин или пароль.', 'danger') 
    elif request.method == 'POST':
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Ошибка в поле {field}: {error}", 'danger')
        print(f"[DEBUG] Валидация формы не пройдена: {form.errors}")

    return render_template('login.html', form=form) 

@app.route('/logout')
def logout():
    print("[DEBUG] Выход из системы.")
    session.pop('logged_in', None) 
    session.pop('username', None) # Удаляем имя пользователя из сессии
    session.pop('is_admin', None) # Удаляем статус админа из сессии
    flash('Доступ отозван.', 'info') 
    return redirect(url_for('login')) 

# --- МАРШРУТ: РЕГИСТРАЦИЯ НОВЫХ ПОЛЬЗОВАТЕЛЕЙ АДМИНИСТРАТОРОМ ---
@app.route('/admin/register_user', methods=['GET', 'POST'])
def admin_register_user():
    # Проверяем, авторизован ли пользователь как администратор
    if not is_admin_logged_in():
        flash('Доступ запрещен. Только администраторы могут регистрировать новых пользователей.', 'danger')
        return redirect(url_for('login'))

    form = RegisterForm()
    print(f"[DEBUG] Запрос к /admin/register_user. Метод: {request.method}. Админ: {session.get('username')}")

    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        is_admin_flag = 1 if form.is_admin_new_user.data.lower() in ['да', 'yes'] else 0

        hashed_password = generate_password_hash(password)
        
        if add_user_to_db(username, hashed_password, is_admin_flag):
            flash(f"Пользователь '{username}' успешно зарегистрирован. Статус админа: {'Да' if is_admin_flag else 'Нет'}.", 'success')
            print(f"[DEBUG] Администратор {session.get('username')} зарегистрировал пользователя {username} (admin={is_admin_flag}).")
            return redirect(url_for('admin_register_user')) # Остаемся на странице регистрации
        else:
            flash(f"Ошибка при регистрации пользователя '{username}'. Возможно, такое имя уже существует.", 'danger')
            print(f"[DEBUG] Ошибка регистрации пользователя {username}.")
    elif request.method == 'POST': # Если форма была отправлена, но валидация не прошла
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Ошибка в поле {field}: {error}", 'danger')
        print(f"[DEBUG] Валидация формы регистрации не пройдена: {form.errors}")

    return render_template('admin_register_user.html', form=form)

# --- НОВЫЙ МАРШРУТ: УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (СПИСОК, УДАЛЕНИЕ, СБРОС ПАРОЛЯ) ---
@app.route('/admin/manage_users', methods=['GET', 'POST'])
def admin_manage_users():
    # Проверяем, авторизован ли пользователь как администратор
    if not is_admin_logged_in():
        flash('Доступ запрещен. Только администраторы могут управлять пользователями.', 'danger')
        return redirect(url_for('login'))
    
    # Формы для каждого действия
    delete_form = DeleteUserForm()
    reset_form = ResetPasswordForm()

    users = get_all_users() # Получаем список всех пользователей

    # Обработка POST-запросов от форм управления пользователями
    if request.method == 'POST':
        action = request.form.get('action') # Определяем, какое действие было запрошено

        if action == 'delete' and delete_form.validate_on_submit():
            user_id_to_delete = delete_form.user_id.data
            # Предотвращаем удаление самого себя или последнего администратора (если это правило)
            if int(user_id_to_delete) == session.get('user_id'): # user_id нужно хранить в сессии
                flash("Нельзя удалить собственную учетную запись!", 'danger')
            else:
                # Проверяем, не является ли это попыткой удалить последнего админа (по желанию)
                user_to_delete = None
                for u in users:
                    if u['id'] == int(user_id_to_delete):
                        user_to_delete = u
                        break
                
                if user_to_delete and user_to_delete['is_admin'] == 1:
                    # Проверяем, сколько всего админов
                    admin_count = sum(1 for u in users if u['is_admin'] == 1)
                    if admin_count <= 1:
                        flash("Нельзя удалить последнего администратора в системе!", 'danger')
                        return redirect(url_for('admin_manage_users'))

                if delete_user_from_db(user_id_to_delete):
                    flash(f"Пользователь (ID: {user_id_to_delete}) успешно удален.", 'success')
                    print(f"[DEBUG] Администратор {session.get('username')} удалил пользователя ID {user_id_to_delete}.")
                    # Обновляем список пользователей после удаления
                    users = get_all_users() 
                else:
                    flash(f"Ошибка при удалении пользователя (ID: {user_id_to_delete}).", 'danger')
            
        elif action == 'reset_password' and reset_form.validate_on_submit():
            user_id_to_reset = reset_form.user_id.data
            new_password = reset_form.new_password.data
            
            hashed_new_password = generate_password_hash(new_password)
            if update_user_password(user_id_to_reset, hashed_new_password):
                flash(f"Пароль пользователя (ID: {user_id_to_reset}) успешно сброшен.", 'success')
                print(f"[DEBUG] Администратор {session.get('username')} сбросил пароль пользователя ID {user_id_to_reset}.")
            else:
                flash(f"Ошибка при сбросе пароля пользователя (ID: {user_id_to_reset}).", 'danger')
        else: # Валидация форм не прошла
            # Показываем ошибки для обеих форм
            for field, errors in delete_form.errors.items():
                for error in errors: flash(f"Ошибка удаления: {field}: {error}", 'danger')
            for field, errors in reset_form.errors.items():
                for error in errors: flash(f"Ошибка сброса пароля: {field}: {error}", 'danger')
            print(f"[DEBUG] Валидация форм управления не пройдена: delete_form={delete_form.errors}, reset_form={reset_form.errors}")

        # После POST-запроса всегда перенаправляем, чтобы избежать повторной отправки формы
        return redirect(url_for('admin_manage_users'))

    # Для GET-запроса или при первой загрузке страницы, отображаем список пользователей
    return render_template('admin_manage_users.html', 
                           users=users, 
                           delete_form=delete_form, 
                           reset_form=reset_form)


# Главный маршрут приложения, защищенный авторизацией
@app.route('/', methods=['GET', 'POST'])
def index():
    if not session.get('logged_in'):
        flash('Требуется авторизация для доступа к данным.', 'warning')
        return redirect(url_for('login'))

    print(f"[DEBUG] Запрос к корневому URL (/). Метод: {request.method}")

    search_params = {}
    error_message = None

    if request.method == 'POST':
        print("[DEBUG] Обработка POST-запроса (применение фильтра).")
        for field in FORM_FIELDS:
            param_value = request.form.get(field)
            if param_value:
                search_params[field] = param_value
                print(f"[DEBUG] Параметр поиска '{field}': '{param_value}'")
        if not search_params:
            print("[DEBUG] Фильтры не указаны.")
        session['last_search_params'] = search_params
    else: # GET-запрос
        print("[DEBUG] GET-запрос. Отображение начальных данных.")
        search_params = session.get('last_search_params', {})


    display_data, total_found, error_message = get_data_from_db(search_params=search_params, limit=100)

    return render_template('index.html',
                           data=display_data,       
                           fields=DISPLAY_FIELDS,   
                           form_fields_list=FORM_FIELDS, 
                           search_params=search_params, 
                           total_found=total_found,     
                           error_message=error_message,
                           is_admin=session.get('is_admin', False)) 

@app.route('/export_excel', methods=['GET'])
def export_excel():
    if not session.get('logged_in'):
        flash('Требуется авторизация для экспорта.', 'warning')
        return redirect(url_for('login'))

    print("[DEBUG] Запрос на экспорт в Excel.")
    
    search_params = session.get('last_search_params', {})
    
    all_filtered_data, total_found, error_msg = get_data_from_db(search_params=search_params, limit=None)

    if error_msg:
        flash(f"Ошибка при экспорте данных: {error_msg}", 'danger')
        return redirect(url_for('index'))

    if not all_filtered_data:
        flash("Нет данных для экспорта.", 'info')
        return redirect(url_for('index'))

    df_export = pd.DataFrame(all_filtered_data)

    if not df_export.empty:
        cols_to_drop = [col for col in df_export.columns if col.endswith('_normalized')]
        df_export = df_export.drop(columns=cols_to_drop, errors='ignore') 
        df_export = df_export[DISPLAY_FIELDS]


    output = io.BytesIO()
    df_export.to_excel(output, index=False, engine='openpyxl') 
    output.seek(0) 

    return send_file(output, 
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     download_name='kazakhstan_data_filtered.xlsx',
                     as_attachment=True)


#if __name__ == '__main__':
#    print("[DEBUG] Запуск Flask-приложения...")
#    app.run(debug=True, host='0.0.0.0', port=5001)
#    print("[DEBUG] Flask-приложение завершило работу.")
