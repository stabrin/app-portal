# manual-aggregation-app/init_ma_db.py
import os
import psycopg2
from dotenv import load_dotenv

# Загружаем переменные из .env в корне проекта
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

def initialize_database():
    """Создает и обновляет таблицы для приложения ручной агрегации."""
    conn = None
    try:
        # Для локальных скриптов, подключающихся к контейнеру Docker,
        # мы используем 'localhost' из-за проброса портов.
        # Собираем строку подключения вручную, чтобы игнорировать DB_HOST=postgres из .env
        conn = psycopg2.connect(
            host='localhost',
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            port=os.getenv('DB_PORT', '5432')
        )
        cur = conn.cursor()

        # Включаем расширение pgcrypto для генерации UUID.
        cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
        print("1. Расширение 'pgcrypto' включено.")

        # 1. Таблица заказов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ma_orders (
                id SERIAL PRIMARY KEY,
                client_name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(50) NOT NULL DEFAULT 'new',
                aggregation_levels JSONB,
                employee_count INTEGER NOT NULL,
                set_capacity INTEGER
            );
        """)

        # 2. Таблица токенов доступа для сотрудников
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ma_employee_tokens (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES ma_orders(id) ON DELETE CASCADE,
                access_token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP WITH TIME ZONE,
                employee_name VARCHAR(255)
            );
        """)
        # Добавляем колонку, если таблица уже существует (для идемпотентности)
        cur.execute("ALTER TABLE ma_employee_tokens ADD COLUMN IF NOT EXISTS employee_name VARCHAR(255);")
        print("2. Таблицы 'ma_orders' и 'ma_employee_tokens' проверены/созданы.")

        # 3. Таблица рабочих смен/сессий сотрудников (создаем до ma_aggregations)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ma_work_sessions (
                id SERIAL PRIMARY KEY,
                employee_token_id INTEGER NOT NULL REFERENCES ma_employee_tokens(id) ON DELETE CASCADE,
                employee_name VARCHAR(255),
                order_id INTEGER,
                start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP WITH TIME ZONE,
                workstation_id VARCHAR(100)
            );
        """)
        # Обновляем существующую таблицу, если нужно
        cur.execute("ALTER TABLE ma_work_sessions ADD COLUMN IF NOT EXISTS employee_name VARCHAR(255);")
        # В старой версии order_id мог быть NOT NULL с FK, что мешает. Приводим к актуальному виду.
        cur.execute("ALTER TABLE ma_work_sessions ALTER COLUMN order_id DROP NOT NULL;")
        cur.execute("ALTER TABLE ma_work_sessions DROP CONSTRAINT IF EXISTS ma_work_sessions_order_id_fkey;")
        print("3. Таблица 'ma_work_sessions' проверена/создана.")

        # 4. Таблица для хранения иерархии вложений
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ma_aggregations (
                id BIGSERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES ma_orders(id) ON DELETE CASCADE,
                employee_token_id INTEGER NOT NULL REFERENCES ma_employee_tokens(id) ON DELETE CASCADE,
                work_session_id INTEGER REFERENCES ma_work_sessions(id) ON DELETE SET NULL,
                child_code VARCHAR(255) NOT NULL, 
                child_type VARCHAR(50) NOT NULL,
                parent_code VARCHAR(255) NOT NULL,
                parent_type VARCHAR(50) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Добавляем колонку и удаляем старое ограничение
        cur.execute("ALTER TABLE ma_aggregations ADD COLUMN IF NOT EXISTS work_session_id INTEGER REFERENCES ma_work_sessions(id) ON DELETE SET NULL;")
        cur.execute("ALTER TABLE ma_aggregations DROP CONSTRAINT IF EXISTS ma_aggregations_child_code_parent_code_key;")
        print("4. Таблица 'ma_aggregations' проверена/создана.")

        cur.execute("""
            COMMENT ON TABLE ma_aggregations IS 'Хранит иерархическую связь "что во что вложено" для процесса агрегации.';
            COMMENT ON COLUMN ma_aggregations.child_code IS 'Код вложенного элемента (DM товара, DM набора, SSCC короба).';
            COMMENT ON COLUMN ma_orders.set_capacity IS 'Максимальное количество товаров в наборе (если задано).';
            COMMENT ON COLUMN ma_aggregations.parent_code IS 'Код родительского контейнера (DM набора, SSCC короба, SSCC паллета).';
            COMMENT ON COLUMN ma_aggregations.work_session_id IS 'ID рабочей сессии, в рамках которой была создана запись.';
            COMMENT ON COLUMN ma_employee_tokens.employee_name IS 'Имя сотрудника, привязанное к пропуску.';
            COMMENT ON COLUMN ma_work_sessions.employee_name IS 'Имя сотрудника на момент начала сессии.';
        """)
        print("5. Комментарии к таблицам обновлены.")

        conn.commit()
        print("\nСхема базы данных для 'manual-aggregation-app' успешно проверена и обновлена.")

    except Exception as e:
        print(f"Ошибка при инициализации БД: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    initialize_database()