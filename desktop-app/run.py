# run.py
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
    except Exception:
        # Если мы не в скомпилированном приложении, используем обычный путь
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# Добавляем папку desktop-app в путь, чтобы Python мог найти пакет 'src'.
# Это необходимо для запуска как из исходников, так и после сборки PyInstaller.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.auth import main

if __name__ == "__main__":
    main()