import os
from flask import Flask
from flask_wtf.csrf import CSRFProtect

# --- Импорты из нашего приложения ---
# Импортируем экземпляры расширений, которые нужно инициализировать
from .auth import login_manager 
# Импортируем наши blueprints (наборы роутов)
from .routes import manual_aggregation_bp
from .api import api_bp

# Инициализируем CSRF-защиту. 
# Это необходимо для безопасности, особенно при работе с AJAX-запросами.
csrf = CSRFProtect()

def create_app():
    """
    Фабрика приложений. Создает и конфигурирует экземпляр Flask-приложения.
    """
    app = Flask(__name__)

    # --- 1. Конфигурация ---
    # Загружаем конфигурацию из переменных окружения.
    # Это безопасный и стандартный способ для работы с Docker.
    app.config.update(
        SECRET_KEY=os.getenv('MANUAL_AGGREGATION_SECRET_KEY', 'a-very-secret-dev-key-that-should-be-changed'),
        # Добавляем пути к Redis для будущего использования в state_service
        REDIS_HOST=os.getenv('REDIS_HOST', 'redis'),
        REDIS_PORT=int(os.getenv('REDIS_PORT', 6379))
    )

    # --- 2. Инициализация расширений ---
    # Связываем расширения с нашим приложением.
    login_manager.init_app(app)
    csrf.init_app(app) # Включаем CSRF защиту для всего приложения

    # --- 3. Регистрация Blueprints ---
    # Регистрируем роуты.
    # Nginx уже направляет все запросы с /manual-aggregation/* на это приложение,
    # поэтому мы можем задать префикс здесь, чтобы все url_for работали корректно.
    
    # Все UI-роуты (админка, логин и т.д.)
    app.register_blueprint(manual_aggregation_bp, url_prefix='/manual-aggregation')
    
    # Новый API для сканирования. Он будет доступен по пути /manual-aggregation/api/*
    # Префикс /api задан внутри api.py, он автоматически добавится к этому.
    app.register_blueprint(api_bp, url_prefix='/manual-aggregation')
    
    # --- 4. Возвращаем готовое приложение ---
    return app