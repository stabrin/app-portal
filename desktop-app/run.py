# run.py
import sys
import os

# Добавляем папку desktop-app в путь, чтобы Python мог найти пакет 'src'.
# Это необходимо для запуска как из исходников, так и после сборки PyInstaller.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.auth import main

if __name__ == "__main__":
    main()