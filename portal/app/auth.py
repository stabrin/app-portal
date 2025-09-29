from flask_login import LoginManager, UserMixin
from psycopg2.extras import RealDictCursor
from .db import get_db_connection

# 1. Инициализация Flask-Login
login_manager = LoginManager()
# Указываем, куда перенаправлять, если пользователь не залогинен.
# Вероятно, это будет страница входа одного из приложений, например, dmkod.
# Используем прямой URL-путь, так как это другое приложение.
# Указываем на собственную страницу входа портала.
login_manager.login_view = 'main.login'
login_manager.login_message = "Пожалуйста, войдите, чтобы получить доступ к порталу."
login_manager.login_message_category = "info"

# 2. Модель пользователя (копия из dmkod-integration-app для совместимости)
class User(UserMixin):
    def __init__(self, user_data):
        self.id = user_data['id']
        self.username = user_data['username']
        self.is_admin = user_data.get('is_admin', False)

# 3. Загрузчик пользователя
@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
        user_data = cur.fetchone()
    conn.close()
    if user_data:
        return User(user_data)
    return None