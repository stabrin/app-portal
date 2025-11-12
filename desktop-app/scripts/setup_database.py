# scripts/setup_database.py

import os
import logging
import traceback
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# Настраиваем логгер для этого модуля
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - (setup_database) - %(message)s',
    handlers=[
        logging.StreamHandler() # Вывод в консоль, чтобы видеть в черном окне
    ]
)

# --- ИСПРАВЛЕНИЕ: Добавляем корень проекта в sys.path, чтобы импорт 'src' работал при запуске через subprocess ---
import sys
project_root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

# Загружаем переменные окружения из файла .env в корне проекта
project_root_for_env = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
dotenv_path = os.path.join(project_root_for_env, '..', '.env') # Ищем .env в корне app-portal
load_dotenv(dotenv_path=dotenv_path)

from src.db_connector import get_main_db_connection

# --- ЧТЕНИЕ КОНФИГУРАЦИИ ---

# Параметры PostgreSQL
DB_HOST = os.getenv('DB_HOST') # Внешний адрес сервера
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
MAIN_DB_NAME = os.getenv('DB_NAME')

def initialize_main_database():
    """
    Проверяет существование главной БД, создает ее при необходимости,
    а затем создает/обновляет в ней необходимую схему (таблицы, типы).
    """
    logger.info("--- Запуск функции инициализации главной базы данных ---")

    try:
        # --- ИЗМЕНЕНИЕ: Используем единый способ подключения ---
        logger.info("Подключаюсь к главной БД для создания/обновления таблиц...")
        with get_main_db_connection() as conn_main_db:
            conn_main_db.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn_main_db.cursor() as cur:
                # Создаем перечисляемый тип для ролей пользователей
                logger.info("Создаю тип 'user_role' (супервизор, администратор, пользователь)...")
                cur.execute("""
                    DO $$
                    BEGIN
                        -- Возвращаем проверку, чтобы скрипт можно было запускать повторно
                        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                            CREATE TYPE user_role AS ENUM ('супервизор', 'администратор', 'пользователь');
                        END IF;
                    END$$;
                """)
                logger.info("Тип 'user_role' создан или уже существует.")

                logger.info("Создаю таблицу 'clients' для хранения настроек подключений...")
                cur.execute("""
                    -- Возвращаем проверку IF NOT EXISTS
                    CREATE TABLE IF NOT EXISTS clients (
                        id SERIAL PRIMARY KEY, name VARCHAR(255) UNIQUE NOT NULL, 
                        db_host VARCHAR(255), db_port INTEGER, db_name VARCHAR(100), 
                        db_user VARCHAR(100), db_password VARCHAR(255),
                        db_ssl_cert TEXT, -- Поле для хранения SSL сертификата БД клиента
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
                """)
                logger.info("Таблица 'clients' создана или уже существует.")
                # Добавляем колонку, если таблица уже была создана без нее (для обратной совместимости)
                cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS db_ssl_cert TEXT;")
                cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS api_base_url VARCHAR(255);")
                cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS api_email VARCHAR(255);")
                cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS api_password VARCHAR(255);")
                cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS local_server_address VARCHAR(255);")
                cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS local_server_port INTEGER;")
                
                logger.info("Создаю таблицу 'users' со связью с 'clients'...")
                cur.execute("""
                    -- Возвращаем проверку IF NOT EXISTS
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, login VARCHAR(100) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL, role user_role NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE, client_id INTEGER,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        
                        CONSTRAINT fk_client
                            FOREIGN KEY(client_id)
                            REFERENCES clients(id)
                            ON DELETE SET NULL );
                """)
                logger.info("Таблица 'users' создана или уже существует.")

        logger.info("Все объекты базы данных успешно созданы или уже существовали.")
        return True, "Инициализация главной БД прошла успешно."
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"Произошла критическая ошибка: {e}\n{error_details}")
        return False, f"Произошла критическая ошибка: {e}"

if __name__ == "__main__":
    success, message = initialize_main_database()
    print(message) # Выводим сообщение в stdout, чтобы subprocess мог его перехватить
    if not success:
        sys.exit(1)