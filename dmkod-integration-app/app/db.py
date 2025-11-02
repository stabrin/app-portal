# dmkod-integration-app/app/db.py

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    """Устанавливает соединение с базой данных PostgreSQL."""
    # --- ИЗМЕНЕНО: Гибкая настройка подключения с поддержкой SSL ---
    conn_params = {
        'host': os.getenv('DB_HOST'),
        'port': os.getenv('DB_PORT'),
        'dbname': os.getenv('DB_NAME'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD')
    }

    # Проверяем, задан ли режим SSL в переменных окружения
    ssl_mode = os.getenv("DB_SSL_MODE")
    if ssl_mode:
        conn_params['sslmode'] = ssl_mode
        # Если указан путь к корневому сертификату, добавляем его
        # Это важно для режимов 'verify-ca' и 'verify-full'
        ssl_rootcert = os.getenv("DB_SSL_ROOTCERT")
        if ssl_rootcert:
            conn_params['sslrootcert'] = ssl_rootcert

    conn = psycopg2.connect(**conn_params)
    return conn