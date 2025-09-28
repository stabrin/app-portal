import os
import datetime
import logging
from flask import Flask
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect

# --- Импорты из нашего приложения ---
from .auth import login_manager
from .routes import dmkod_bp

def create_app():
    """
    Фабрика приложений, организованная по аналогии с datamatrix-app.
    """
    # Настраиваем логирование на самом раннем этапе
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Загружаем переменные окружения из корневого .env файла
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)

    app = Flask(__name__, template_folder='templates')

    logging.debug("Flask app created. Template folder is set to 'templates'.")

    # Настройка секретного ключа
    secret_key = os.getenv('DMKOD_SECRET_KEY')
    if not secret_key:
        raise ValueError("Не установлена переменная окружения DMKOD_SECRET_KEY")
    app.config['SECRET_KEY'] = secret_key
    logging.debug("Secret key configured.")

    # Инициализация расширений
    login_manager.init_app(app)
    CSRFProtect(app)
    logging.debug("Flask-Login and CSRFProtect initialized.")

    # Регистрация Blueprint с маршрутами
    app.register_blueprint(dmkod_bp, url_prefix='/dmkod')
    logging.debug("Blueprint 'dmkod_bp' registered with prefix '/dmkod'.")

    return app