# scripts/setup_database.py

import os
import sys
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

# --- ИМПОРТ ИЗ ОСНОВНОГО ПРИЛОЖЕНИЯ ---
# Добавляем папку 'src' в sys.path, чтобы импортировать SshTunnelProcess
src_path = os.path.join(project_root, 'src')
sys.path.insert(0, src_path)
from main_window import SshTunnelProcess

sys.path.insert(0, project_root)

# Загружаем переменные окружения из файла .env в корне проекта
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- НАСТРОЙКА ЛОГИРОВАНИЯ (аналогично main_window.py) ---
log_file_path = os.path.join(project_root, 'app.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - (setup_database) - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# --- ЧТЕНИЕ КОНФИГУРАЦИИ ---

# Параметры SSH
SSH_HOST = os.getenv('SSH_HOST')
SSH_PORT = int(os.getenv('SSH_PORT', 22))
SSH_USER = os.getenv('SSH_USER')
# Старый способ: имя файла в папке /keys
SSH_KEY_FILENAME = os.getenv('SSH_KEY_FILENAME')

# Параметры PostgreSQL
REMOTE_DB_HOST = os.getenv('DB_HOST')
REMOTE_DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
MAIN_DB_NAME = os.getenv('TILDA_DB_NAME') # Используем новую переменную для главной БД

def main():
    """
    Главная функция, которая выполняет всю логику.
    """
    logging.info("--- Запуск скрипта инициализации базы данных ---")
    logging.info(f"Попытка подключения к серверу {SSH_HOST}...")

    try:
        # --- Используем тот же метод, что и в main_window.py ---
        if not SSH_KEY_FILENAME:
            raise ValueError("Переменная SSH_KEY_FILENAME не задана в .env файле.")
        
        ssh_key_path = os.path.join(project_root, 'keys', SSH_KEY_FILENAME)
        if not os.path.exists(ssh_key_path):
            raise FileNotFoundError(f"Файл SSH-ключа не найден по пути: {ssh_key_path}")

        with SshTunnelProcess(
            ssh_host=SSH_HOST,
            ssh_port=SSH_PORT,
            ssh_user=SSH_USER,
            ssh_key=ssh_key_path,
            remote_host=REMOTE_DB_HOST,
            remote_port=REMOTE_DB_PORT
        ) as tunnel:
            
            local_port = tunnel.local_bind_port
            logging.info(f"SSH-туннель успешно создан. Локальный порт: {local_port}.")

            if not MAIN_DB_NAME:
                raise ValueError("Переменная TILDA_DB_NAME не задана в .env файле.")

            # --- Этап 1: Создание базы данных ---
            logging.info(f"Подключаюсь к системной базе 'postgres' для создания '{MAIN_DB_NAME}'...")
            
            conn_system = psycopg2.connect(
                host='127.0.0.1', port=local_port,
                user=DB_USER, password=DB_PASSWORD, dbname='postgres'
            )
            conn_system.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            
            with conn_system.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (MAIN_DB_NAME,))
                if cur.fetchone():
                    logging.info(f"База данных '{MAIN_DB_NAME}' уже существует. Пропускаю создание.")
                else:
                    logging.info(f"Создаю базу данных '{MAIN_DB_NAME}'...")
                    cur.execute(f"CREATE DATABASE {MAIN_DB_NAME}")
                    logging.info("База данных успешно создана.")
            conn_system.close()

            # --- Этап 2: Создание таблиц в новой базе данных ---
            logging.info(f"Подключаюсь к '{MAIN_DB_NAME}' для создания таблиц...")

            conn_new_db = psycopg2.connect(
                host='127.0.0.1', port=local_port,
                user=DB_USER, password=DB_PASSWORD, dbname=MAIN_DB_NAME
            )
            with conn_new_db.cursor() as cur:
                # Создаем перечисляемый тип для ролей пользователей
                logging.info("Создаю тип 'user_role' (супервизор, администратор, пользователь)...")
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                            CREATE TYPE user_role AS ENUM ('супервизор', 'администратор', 'пользователь');
                        END IF;
                    END$$;
                """)

                logging.info("Создаю таблицу 'clients' для хранения настроек подключений...")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS clients (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) UNIQUE NOT NULL,
                        ssh_host VARCHAR(255),
                        ssh_port INTEGER,
                        ssh_user VARCHAR(100),
                        ssh_private_key TEXT,
                        db_host VARCHAR(255),
                        db_port INTEGER,
                        db_name VARCHAR(100),
                        db_user VARCHAR(100),
                        db_password VARCHAR(255),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    );
                """)
                
                logging.info("Создаю таблицу 'users' со связью с 'clients'...")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        login VARCHAR(100) UNIQUE NOT NULL,                        
                        password_hash VARCHAR(255) NOT NULL,
                        role user_role NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        client_id INTEGER,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        
                        CONSTRAINT fk_client
                            FOREIGN KEY(client_id) 
                            REFERENCES clients(id)
                            ON DELETE SET NULL
                    );
                """)
            conn_new_db.commit()
            logging.info("Все таблицы и типы успешно созданы или уже существовали.")
            conn_new_db.close()

    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Произошла критическая ошибка: {e}\n{error_details}")
    finally:
        logging.info("--- Скрипт завершил работу. ---")        

if __name__ == "__main__":
    try:
        main()
    finally:
        # Эта строка не даст окну закрыться, чтобы можно было увидеть результат
        input("\nНажмите Enter для выхода...")