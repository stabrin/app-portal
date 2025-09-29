from flask import Flask
import os
from .auth import login_manager

def create_app():
    app = Flask(__name__)

    # 1. Настройка секретного ключа для сессий
    # Используем тот же ключ, что и в dmkod-app для единой сессии
    secret_key = os.getenv('DMKOD_SECRET_KEY')
    if not secret_key:
        raise ValueError("Не установлена переменная окружения DMKOD_SECRET_KEY")
    app.config['SECRET_KEY'] = secret_key

    # 2. Инициализация Flask-Login
    login_manager.init_app(app)

    # Импортируем Blueprint из файла routes.py
    from .routes import main_blueprint
    
    # Регистрируем его без префикса (он будет корневым)
    app.register_blueprint(main_blueprint)

    @app.context_processor
    def inject_version():
        """
        Внедряет версию приложения из переменной окружения в контекст всех шаблонов.
        """
        return dict(APP_VERSION=os.getenv('APP_VERSION', 'dev'))

    return app