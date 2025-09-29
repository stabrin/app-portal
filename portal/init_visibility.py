import os
import psycopg2
from dotenv import load_dotenv

def initialize_visibility_table():
    """
    Создает таблицу app_visibility, если она не существует,
    и заполняет ее начальными данными для известных приложений.
    """
    # Загружаем переменные из .env файла в корне проекта
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
    else:
        # Если в корне нет, пробуем загрузить из текущей директории
        load_dotenv()

    conn = None
    try:
        # Подключаемся к БД
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST_LOCAL", "localhost"), # Используем локальный хост для скриптов
            port=os.getenv("DB_PORT")
        )
        with conn.cursor() as cur:
            # 1. Создаем таблицу, если она не существует
            print("Создание таблицы 'app_visibility'...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_visibility (
                    app_name VARCHAR(100) PRIMARY KEY,
                    visibility_rule TEXT NOT NULL
                );
            """)
            print("Таблица 'app_visibility' успешно создана или уже существует.")

            # 2. Определяем начальные данные для приложений
            initial_apps = {
                'dmkod-integration-app': 'admin', # По умолчанию доступ только пользователю 'admin'
                'manual-aggregation-app': 'All',  # Доступ всем
                'datamatrix-app': 'All'           # Доступ всем
            }

            # 3. Добавляем данные, если их еще нет (чтобы не перезаписывать существующие настройки)
            print("Заполнение таблицы начальными данными...")
            for app_name, rule in initial_apps.items():
                cur.execute("INSERT INTO app_visibility (app_name, visibility_rule) VALUES (%s, %s) ON CONFLICT (app_name) DO NOTHING;", (app_name, rule))
            conn.commit()
            print("Начальные данные для приложений успешно добавлены.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()
        print("Скрипт завершил работу.")

if __name__ == '__main__':
    initialize_visibility_table()