# src/db_connector.py

import os
import logging
import psycopg2
from contextlib import contextmanager
from dotenv import load_dotenv

@contextmanager
def get_main_db_connection():
    """
    Контекстный менеджер, который возвращает готовое SSL-соединение
    с ГЛАВНОЙ базой данных (tilda_db).
    """
    # Загружаем переменные из .env файла
    desktop_app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    dotenv_path = os.path.join(desktop_app_root, '.env')
    load_dotenv(dotenv_path=dotenv_path)

    # Находим путь к сертификату сервера
    app_portal_root = os.path.abspath(os.path.join(desktop_app_root, '..'))
    cert_path = os.path.join(app_portal_root, 'secrets', 'postgres', 'server.crt')
    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Сертификат сервера не найден по пути: {cert_path}")

    # Подключаемся к БД напрямую с использованием SSL
    conn = psycopg2.connect(
        dbname=os.getenv("TILDA_DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"), # ВАЖНО: здесь должен быть внешний адрес сервера
        port=os.getenv("DB_PORT"),
        connect_timeout=5,
        sslmode='verify-full',
        sslrootcert=cert_path
    )
    yield conn
    conn.close()