import os
from flask import Flask

# Импортируем наш blueprint из файла routes.py
from .routes import dmkod_bp

def create_app():
    """
    Фабрика приложений.
    """
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.getenv('DMKOD_SECRET_KEY', 'a-default-secret-key')
    )

    # КЛЮЧЕВОЙ МОМЕНТ:
    # Регистрируем blueprint и указываем префикс URL.
    # Теперь Flask будет знать, что все роуты внутри dmkod_bp
    # должны начинаться с /dmkod/
    app.register_blueprint(dmkod_bp, url_prefix='/dmkod')
    
    return app