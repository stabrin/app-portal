# src/utils.py
import sys
import os

def resource_path(relative_path):
    """
    Возвращает абсолютный путь к ресурсу. Работает как для исходников,
    так и для скомпилированного приложения (PyInstaller).
    """
    try:
        # PyInstaller создает временную папку и сохраняет путь в _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        # Если мы не в скомпилированном приложении, используем путь относительно
        # корня приложения (desktop-app).
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    return os.path.join(base_path, relative_path)