# scripts/setup_database.py

import os
import psycopg2
import logging
import traceback
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# --- НАСТРОЙКА ПУТЕЙ ---
# Добавляем корневую папку проекта в пути Python,
# чтобы можно было импортировать модули из src и т.д.

# Абсолютный путь к текущему файлу (setup_database.py)
current_file_path = os.path.dirname(os.path.abspath(__file__))
# Путь к корневой папке проекта (на уровень выше, чем scripts)
project_root = os.path.abspath(os.path.join(current_file_path, '..'))

# --- НАСТРОЙКА ЛОГИРОВАНИЯ (аналогично main_window.py) ---
# Делаем это в самом начале, чтобы логгировать даже ошибки импорта.
log_file_path = os.path.join(project_root, 'app.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - (setup_database) - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler() # Вывод в консоль, чтобы видеть в черном окне
    ]
)

# --- ИМПОРТ ИЗ ОСНОВНОГО ПРИЛОЖЕНИЯ ---
# Добавляем папку 'src' в sys.path, чтобы импортировать SshTunnelProcess
import sys
src_path = os.path.join(project_root, 'src')

# Загружаем переменные окружения из файла .env в корне проекта
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- ЧТЕНИЕ КОНФИГУРАЦИИ ---

# Параметры PostgreSQL
DB_HOST = os.getenv('DB_HOST') # Внешний адрес сервера
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
MAIN_DB_NAME = os.getenv('DB_NAME') # Используем новую переменную для главной БД

def main():
    """
    Главная функция, которая выполняет всю логику.
    """
    logging.info("--- Запуск скрипта инициализации базы данных ---")
    logging.info(f"Попытка подключения к серверу {DB_HOST} по SSL...")

    try:
        # Находим путь к сертификату сервера
        app_portal_root = os.path.abspath(os.path.join(project_root, '..'))
        cert_path = os.path.join(app_portal_root, 'secrets', 'postgres', 'server.crt')
        if not os.path.exists(cert_path):
            raise FileNotFoundError(f"Сертификат сервера не найден по пути: {cert_path}")

        if not MAIN_DB_NAME:
            raise ValueError("Переменная DB_NAME не задана в .env файле.")

        # --- Этап 1: Создание базы данных ---
        logging.info(f"Подключаюсь к системной базе 'postgres' для создания '{MAIN_DB_NAME}'...")
        
        conn_system = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, dbname='postgres',
            sslmode='verify-full', sslrootcert=cert_path
        )
        conn_system.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        
        with conn_system.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (MAIN_DB_NAME,))
            if cur.fetchone():
                logging.info(f"База данных '{MAIN_DB_NAME}' уже существует. Пропускаю создание.")
            else:
                logging.info(f"Создаю базу данных '{MAIN_DB_NAME}'...")
                cur.execute(f"CREATE DATABASE {MAIN_DB_NAME}")
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (MAIN_DB_NAME,))
                if cur.fetchone():
                    logging.info(f"ПРОВЕРКА УСПЕШНА: База данных '{MAIN_DB_NAME}' теперь существует.")
                else:
                    raise Exception(f"КРИТИЧЕСКАЯ ОШИБКА: Команда CREATE DATABASE для '{MAIN_DB_NAME}' выполнилась, но база данных не появилась.")
        conn_system.close()

        # --- Этап 2: Создание таблиц в новой базе данных ---
        logging.info(f"Подключаюсь к '{MAIN_DB_NAME}' для создания таблиц...")

        conn_new_db = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, dbname=MAIN_DB_NAME,
            sslmode='verify-full', sslrootcert=cert_path
        )
        conn_new_db.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        
        with conn_new_db.cursor() as cur:
                # Создаем перечисляемый тип для ролей пользователей
                logging.info("Создаю тип 'user_role' (супервизор, администратор, пользователь)...")
                cur.execute("""
                    DO $$
                    BEGIN
                        -- Возвращаем проверку, чтобы скрипт можно было запускать повторно
                        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                            CREATE TYPE user_role AS ENUM ('супервизор', 'администратор', 'пользователь');
                        END IF;
                    END$$;
                """)
                logging.info("Тип 'user_role' создан или уже существует.")

                logging.info("Создаю таблицу 'clients' для хранения настроек подключений...")
                cur.execute("""
                    -- Возвращаем проверку IF NOT EXISTS
                    CREATE TABLE IF NOT EXISTS clients (
                        id SERIAL PRIMARY KEY, name VARCHAR(255) UNIQUE NOT NULL, ssh_host VARCHAR(255),
                        ssh_port INTEGER, ssh_user VARCHAR(100), ssh_private_key TEXT, db_host VARCHAR(255),
                        db_port INTEGER, db_name VARCHAR(100), db_user VARCHAR(100), db_password VARCHAR(255),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
                """)
                logging.info("Таблица 'clients' создана или уже существует.")
                
                logging.info("Создаю таблицу 'users' со связью с 'clients'...")
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
                logging.info("Таблица 'users' создана или уже существует.")

        logging.info("Все объекты базы данных успешно созданы или уже существовали.")
        conn_new_db.close()
        
    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Произошла критическая ошибка: {e}\n{error_details}")

if __name__ == "__main__":
    try:
        main()
        logging.info("--- Скрипт успешно завершил работу. ---")
    finally:
        # Корректно завершаем работу логгера, чтобы избежать ошибок при выходе
        # и гарантировать, что все транзакции успеют завершиться.
        logging.shutdown()
        # Эта строка не даст окну закрыться, чтобы можно было увидеть результат
        input("\nНажмите Enter для выхода...")