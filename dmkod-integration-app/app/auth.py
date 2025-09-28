from flask_login import LoginManager, UserMixin
from psycopg2.extras import RealDictCursor
from .db import get_db_connection

# 1. Инициализация Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'dmkod_integration_app.login'
login_manager.login_message = "Пожалуйста, войдите, чтобы получить доступ к этой странице."
login_manager.login_message_category = "info"

# 2. Модель пользователя
class User(UserMixin):
    """Модель пользователя для Flask-Login."""
    def __init__(self, user_data):
        self.id = user_data['id']
        self.username = user_data['username']
        self.password_hash = user_data['password_hash']
        self.is_admin = user_data['is_admin']

# 3. Загрузчик пользователя
@login_manager.user_loader
def load_user(user_id):
    """Загружает пользователя из БД по его ID."""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
            user_data = cur.fetchone()
        conn.close()
        if user_data:
            return User(user_data)
    except Exception:
        # В случае ошибки с БД или некорректного user_id, просто возвращаем None
        return None
    return None