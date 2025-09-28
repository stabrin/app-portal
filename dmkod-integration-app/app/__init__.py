import os
import datetime
from flask import Flask
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
from .auth import login_manager

def create_app():
    """
    Фабрика приложений, организованная по аналогии с другими приложениями портала.
    """
    # Загружаем переменные окружения из корневого .env файла
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)

    app = Flask(__name__)

    # Настройка секретного ключа
    secret_key = os.getenv('DMKOD_SECRET_KEY')
    if not secret_key:
        raise ValueError("Не установлена переменная окружения DMKOD_SECRET_KEY")
    app.config['SECRET_KEY'] = secret_key

    # Инициализация Flask-Login
    login_manager.init_app(app)

    # Инициализация CSRF-защиты
    CSRFProtect(app)

    # Внедряем переменные в контекст всех шаблонов
    @app.context_processor
    def inject_global_vars():
        return dict(current_year=datetime.date.today().year)
        
    # Импорт и регистрация Blueprint из routes.py
    from .routes import dmkod_bp
    app.register_blueprint(dmkod_bp, url_prefix='/dmkod')

    return app