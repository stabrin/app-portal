# run.py
import sys
import os
import logging

# --- ИСПРАВЛЕНИЕ: Добавляем явные импорты для PyInstaller ---
# Это решает проблему "Hidden import not found" при компиляции.
import babel.numbers
import jinja2
# import _cffi_backend
# import mx.DateTime

# Добавляем папку desktop-app в путь, чтобы Python мог найти пакет 'src'.
# Это необходимо для запуска как из исходников, так и после сборки PyInstaller.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- НОВЫЙ БЛОК: Централизованная настройка логирования ---
from dotenv import load_dotenv
project_root = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(project_root, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)

# Получаем уровень логирования из .env, по умолчанию 'INFO'
log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)

log_file_path = os.path.join(project_root, 'app.log')
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - [%(name)s.%(funcName)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
from src.auth import main

if __name__ == "__main__":
    main()