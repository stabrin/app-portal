import os
import psycopg2
from dotenv import load_dotenv

# Загружаем переменные, чтобы этот модуль тоже мог их видеть
load_dotenv()

def get_db_connection():
    """
    Единая функция для установки соединения с базой данных.
    Читает DATABASE_URL из переменных окружения.
    """
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL не установлена в переменных окружения!")
        
    return psycopg2.connect(database_url)