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
    с ГЛАВНОЙ базой данных (portal_db).
    """
    # Загружаем переменные из .env файла
    # --- ИЗМЕНЕНИЕ: Убираем зависимость от .env и хардкодим параметры ---
    db_params = {
        "dbname": "tilda_db",
        "user": "portal_user",
        "password": "!T-W0rkshop",
        "host": "109.172.115.204",
        "port": "5432",
        "connect_timeout": 5,
        "sslmode": 'verify-full'
    }

    # Находим путь к сертификату сервера
    desktop_app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    app_portal_root = os.path.abspath(os.path.join(desktop_app_root, '..'))
    cert_path = os.path.join(app_portal_root, 'secrets', 'postgres', 'server.crt')
    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Сертификат сервера не найден по пути: {cert_path}")

    # Подключаемся к БД напрямую с использованием SSL
    db_params['sslrootcert'] = cert_path
    conn = psycopg2.connect(**db_params)
    yield conn
    conn.close()