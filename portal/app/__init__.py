from flask import Flask
import os

def create_app():
    app = Flask(__name__)

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