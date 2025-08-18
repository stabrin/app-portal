from flask import Flask

def create_app():
    app = Flask(__name__)

    # Импортируем Blueprint из файла routes.py
    from .routes import main_blueprint
    
    # Регистрируем его без префикса (он будет корневым)
    app.register_blueprint(main_blueprint)

    return app