from flask_login import UserMixin

class DmkodUser(UserMixin):
    """Простая модель пользователя для Flask-Login."""
    def __init__(self, user_id, username, role):
        self.id = user_id
        self.username = username
        self.role = role