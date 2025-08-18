# manual-aggregation-app/app/auth.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask_login import LoginManager, UserMixin
from bcrypt import checkpw

# --- База данных ---
def get_db_connection():
    return psycopg2.connect(os.getenv('DATABASE_URL'))

# --- Настройка Flask-Login ---
login_manager = LoginManager()
login_manager.login_view = 'manual_aggregation_app.login_choice' # Страница выбора типа входа
login_manager.login_message = "Пожалуйста, войдите для доступа."

class User(UserMixin):
    """Универсальная модель пользователя (Админ или Сотрудник)."""
    def __init__(self, user_id, role, data):
        self.id = user_id
        self.role = role  # 'admin' or 'employee'
        self.data = data # Словарь с доп. данными

@login_manager.user_loader
def load_user(user_id):
    # --- НАЧАЛО БЛОКА ОТЛАДКИ ---
    print(f"--- load_user вызван с user_id: '{user_id}' ---")
    
    try:
        role, u_id = user_id.split(':', 1)
        print(f"Распарсили: role='{role}', u_id='{u_id}'")
    except Exception as e:
        print(f"ОШИБКА: не удалось распарить user_id. Ошибка: {e}")
        return None

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if role == 'admin':
            print(f"Ищу админа с id={u_id}")
            cur.execute("SELECT * FROM users WHERE id = %s AND is_admin = true", (u_id,))
            user_data = cur.fetchone()
            print(f"Результат из БД: {user_data}")
            if user_data:
                print("Админ найден, возвращаю объект User")
                return User(user_id, 'admin', user_data)
        
        elif role == 'employee':
            # Ищем в нашей новой таблице токенов
            cur.execute("SELECT * FROM ma_employee_tokens WHERE id = %s AND is_active = true", (u_id,))
            token_data = cur.fetchone()
            if token_data:
                return User(user_id, 'employee', token_data)
    return None

# Функции для проверки учетных данных
def verify_admin_credentials(username, password):
    """Проверяет логин/пароль админа по таблице `users`."""
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE username = %s AND is_admin = true", (username,))
        user_data = cur.fetchone()
    if user_data and checkpw(password.encode('utf-8'), user_data['password_hash'].encode('utf-8')):
        return User(f"admin:{user_data['id']}", 'admin', user_data)
    return None

def verify_employee_token(access_token):
    """Проверяет токен сотрудника и обновляет время последнего входа."""
    conn = get_db_connection()
    user_to_return = None
    with conn: # Используем with conn для автоматической транзакции
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Ищем активный токен
            cur.execute(
                "SELECT * FROM ma_employee_tokens WHERE access_token = %s AND is_active = true",
                (access_token,)
            )
            token_data = cur.fetchone()
            
            if token_data:
                # Если токен найден, ОБНОВЛЯЕМ время входа
                cur.execute(
                    "UPDATE ma_employee_tokens SET last_login = NOW() WHERE id = %s",
                    (token_data['id'],)
                )
                user_to_return = User(f"employee:{token_data['id']}", 'employee', token_data)
                
    return user_to_return