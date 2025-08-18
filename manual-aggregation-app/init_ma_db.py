# manual-aggregation-app/init_ma_db.py
import os
import psycopg2
from dotenv import load_dotenv

# Загружаем переменные из .env в корне проекта
load_dotenv(dotenv_path='../.env')

def initialize_database():
    """Создает таблицы для приложения ручной агрегации, если они не существуют."""
    conn = None
    try:
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        cur = conn.cursor()

        # Включаем расширение pgcrypto для генерации UUID.
        # IF NOT EXISTS гарантирует, что команда не вызовет ошибку, если расширение уже включено.
        cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')

        # Таблица заказов
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

        # Таблица токенов доступа для сотрудников
        # Теперь DEFAULT gen_random_uuid() будет работать
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ma_employee_tokens (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES ma_orders(id) ON DELETE CASCADE,
                access_token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP WITH TIME ZONE
            );
        """)

        # Таблица для хранения иерархии вложений. Это центральная таблица для всей агрегации.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ma_aggregations (
                id BIGSERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES ma_orders(id) ON DELETE CASCADE,
                employee_token_id INTEGER NOT NULL REFERENCES ma_employee_tokens(id),
                child_code VARCHAR(255) NOT NULL, 
                child_type VARCHAR(50) NOT NULL, -- 'product', 'set', 'box', 'pallet'
                parent_code VARCHAR(255) NOT NULL,
                parent_type VARCHAR(50) NOT NULL, -- 'set', 'box', 'pallet', 'container'
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (child_code, parent_code) 
            );
        """)

        # Таблица рабочих смен/сессий сотрудников
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ma_work_sessions (
                id SERIAL PRIMARY KEY,
                employee_token_id INTEGER NOT NULL REFERENCES ma_employee_tokens(id),
                order_id INTEGER NOT NULL REFERENCES ma_orders(id),
                start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP WITH TIME ZONE,
                workstation_id VARCHAR(100)
            );
        """)

        cur.execute("""
            COMMENT ON TABLE ma_aggregations IS 'Хранит иерархическую связь "что во что вложено" для процесса агрегации.';
            COMMENT ON COLUMN ma_aggregations.child_code IS 'Код вложенного элемента (DM товара, DM набора, SSCC короба).';
            COMMENT ON COLUMN ma_orders.set_capacity IS 'Максимальное количество товаров в наборе (если задано).';
            COMMENT ON COLUMN ma_aggregations.parent_code IS 'Код родительского контейнера (DM набора, SSCC короба, SSCC паллета).';        
        """)

        conn.commit()
        print("Расширение 'pgcrypto' включено.")
        print("Таблицы 'ma_orders' и 'ma_employee_tokens' успешно созданы или уже существуют.")

    except Exception as e:
        print(f"Ошибка при инициализации БД: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    initialize_database()