# scripts/setup_database.py

import os
import sys
import psycopg2
import io
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sshtunnel import SSHTunnelForwarder
from dotenv import load_dotenv
from paramiko import RSAKey, Ed25519Key, ECDSAKey, DSSKey

# --- НАСТРОЙКА ПУТЕЙ ---
# Добавляем корневую папку проекта в пути Python,
# чтобы можно было импортировать модули из src и т.д.

# Абсолютный путь к текущему файлу (setup_database.py)
current_file_path = os.path.dirname(os.path.abspath(__file__))
# Путь к корневой папке проекта (на уровень выше, чем scripts)
project_root = os.path.abspath(os.path.join(current_file_path, '..'))
sys.path.insert(0, project_root)

# Загружаем переменные окружения из файла .env в корне проекта
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- ЧТЕНИЕ КОНФИГУРАЦИИ ---

# Параметры SSH
SSH_HOST = os.getenv('SSH_HOST')
SSH_PORT = int(os.getenv('SSH_PORT', 22))
SSH_USER = os.getenv('SSH_USER')
# Новый способ: ключ как строка в .env
SSH_PRIVATE_KEY_STR = os.getenv('SSH_PRIVATE_KEY')
# Старый способ: имя файла в папке /keys
SSH_KEY_FILENAME = os.getenv('SSH_KEY_FILENAME')

# Параметры PostgreSQL
REMOTE_DB_HOST = os.getenv('DB_HOST')
REMOTE_DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
NEW_DB_NAME = os.getenv('DB_NAME')

def load_ssh_key():
    """
    Загружает SSH-ключ. Приоритет у ключа из переменной SSH_PRIVATE_KEY.
    Если его нет, ищет файл по SSH_KEY_FILENAME.
    """
    if SSH_PRIVATE_KEY_STR:
        print("Найден ключ в переменной .env. Загружаю его...")
        key_file_obj = io.StringIO(SSH_PRIVATE_KEY_STR)
        # Пытаемся загрузить ключ разных форматов
        for key_class in (RSAKey, Ed25519Key, ECDSAKey, DSSKey):
            try:
                key_file_obj.seek(0)
                return key_class.from_private_key(key_file_obj)
            except Exception:
                continue
        raise ValueError("Не удалось распознать формат приватного ключа из переменной SSH_PRIVATE_KEY.")
    
    if SSH_KEY_FILENAME:
        print(f"Ключ в .env не найден. Ищу файл '{SSH_KEY_FILENAME}' в папке /keys...")
        return os.path.join(project_root, 'keys', SSH_KEY_FILENAME)

    raise ValueError("Не удалось найти SSH-ключ. Укажите SSH_PRIVATE_KEY или SSH_KEY_FILENAME в .env файле.")

def main():
    """
    Главная функция, которая выполняет всю логику.
    """
    print("--- Запуск скрипта инициализации базы данных ---")
    print(f"Попытка подключения к серверу {SSH_HOST}...")

    try:
        ssh_key = load_ssh_key()

        with SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USER,
            ssh_pkey=ssh_key,
            remote_bind_address=(REMOTE_DB_HOST, REMOTE_DB_PORT)
        ) as tunnel:
            
            local_port = tunnel.local_bind_port
            print(f"SSH-туннель успешно создан. Локальный порт: {local_port}.")

            # --- Этап 1: Создание базы данных ---
            print(f"\nПодключаюсь к системной базе 'postgres' для создания '{NEW_DB_NAME}'...")
            
            conn_system = psycopg2.connect(
                host='127.0.0.1', port=local_port,
                user=DB_USER, password=DB_PASSWORD, dbname='postgres'
            )
            conn_system.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            
            with conn_system.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (NEW_DB_NAME,))
                if cur.fetchone():
                    print(f"База данных '{NEW_DB_NAME}' уже существует. Пропускаю создание.")
                else:
                    print(f"Создаю базу данных '{NEW_DB_NAME}'...")
                    cur.execute(f"CREATE DATABASE {NEW_DB_NAME}")
                    print("База данных успешно создана.")
            conn_system.close()

            # --- Этап 2: Создание таблиц в новой базе данных ---
            print(f"\nПодключаюсь к '{NEW_DB_NAME}' для создания таблиц...")

            conn_new_db = psycopg2.connect(
                host='127.0.0.1', port=local_port,
                user=DB_USER, password=DB_PASSWORD, dbname=NEW_DB_NAME
            )
            with conn_new_db.cursor() as cur:
                print("Создаю таблицу 'clients'...")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS clients (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL
                    );
                """)
                
                print("Создаю таблицу 'users'...")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        login VARCHAR(100) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(50) NOT NULL
                    );
                """)
            conn_new_db.commit()
            print("Таблицы 'clients' и 'users' успешно созданы.")
            conn_new_db.close()

    except FileNotFoundError:
        print(f"\n[ОШИБКА] Файл SSH-ключа не найден. Проверьте имя файла в .env и его наличие в папке /keys.")
    except Exception as e:
        print(f"\n[ОШИБКА] Произошла ошибка: {e}")
    finally:
        print("\n--- Скрипт завершил работу. ---")
        input("Нажмите Enter для выхода...") # Эта строка не даст окну закрыться

if __name__ == "__main__":
    main()