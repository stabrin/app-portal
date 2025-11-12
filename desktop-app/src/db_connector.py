# src/db_connector.py

import os
import logging
import psycopg2
from contextlib import contextmanager

from dotenv import load_dotenv

from .utils import project_root_path # --- ИЗМЕНЕНИЕ: Импортируем новую функцию для доступа к корню проекта ---

@contextmanager
def get_main_db_connection():
    """

    Контекстный менеджер, который возвращает готовое SSL-соединение
    с ГЛАВНОЙ базой данных (portal_db).
    """
    # --- ИЗМЕНЕНИЕ: Убираем зависимость от .env и хардкодим параметры ---
    db_params = {
        "dbname": "tilda_db",
        "user": "portal_user",
        "password": "!T-W0rkshop", 
        "host": "109.172.115.204",
        "port": "5432",
        "connect_timeout": 10,
        "sslmode": 'verify-full'
    }

    # --- ИЗМЕНЕНИЕ: Используем project_root_path для доступа к папке secrets в корне проекта ---
    cert_path = project_root_path(os.path.join('secrets', 'postgres', 'server.crt'))

    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Сертификат сервера не найден по пути: {cert_path}")

    # Подключаемся к БД напрямую с использованием SSL
    db_params['sslrootcert'] = cert_path
    conn = psycopg2.connect(**db_params)
    yield conn
    conn.close()