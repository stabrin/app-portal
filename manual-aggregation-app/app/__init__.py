import os
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from markupsafe import Markup
import html

# --- Импорты из нашего приложения ---
# Импортируем экземпляры расширений, которые нужно инициализировать
from .auth import login_manager 

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

    # --- 2. Регистрация кастомных фильтров для шаблонов ---
    @app.template_filter('gs_highlight')
    def gs_highlight_filter(text):
        """Фильтр Jinja2 для подсветки символа GS в строке."""
        if not text or not isinstance(text, str):
            return text
        gs_char = '\x1d'
        highlighted_gs = '<span class="text-danger fw-bold">[GS]</span>'
        # Безопасный способ: экранируем части строки, затем соединяем
        parts = text.split(gs_char)
        escaped_parts = [html.escape(part) for part in parts]
        return Markup(highlighted_gs.join(escaped_parts))

    # --- 3. Инициализация расширений ---
    # Связываем расширения с нашим приложением.
    login_manager.init_app(app)
    csrf.init_app(app) # Включаем CSRF защиту для всего приложения

    # --- 4. Регистрация Blueprints ---
    # Импортируем и регистрируем Blueprints внутри фабрики,
    # чтобы избежать циклических зависимостей, которые могут вызывать
    # ошибки при запуске приложения.
    from .routes import manual_aggregation_bp
    from .api import api_bp

    app.register_blueprint(manual_aggregation_bp, url_prefix='/manual-aggregation')
    app.register_blueprint(api_bp, url_prefix='/manual-aggregation')
    
    # --- 5. Возвращаем готовое приложение ---
    return app