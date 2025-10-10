import os
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

def get_db_connection():
    """Устанавливает соединение с БД для локальных скриптов."""
    # Загружаем переменные из .env файла в корне проекта
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(dotenv_path=dotenv_path)
    
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST_LOCAL", "localhost"), # Используем локальный хост для скриптов
            port=os.getenv("DB_PORT")
        )
        return conn
    except psycopg2.OperationalError as e:
        print(f"Ошибка подключения к базе данных: {e}")
        print("Убедитесь, что в .env файле корректно указаны параметры подключения к БД.")
        return None

def update_schema(conn):
    """
    Обновляет схему базы данных.
    Добавляет новые таблицы и колонки, если они не существуют.
    """
    
    # Имена таблиц, можно вынести в .env при необходимости
    orders_table = 'orders'
    product_groups_table = 'dmkod_product_groups'
    aggregation_details_table = 'dmkod_aggregation_details'
    order_files_table = 'dmkod_order_files'
    delta_result_table = 'delta_result'

    # Список команд для обновления схемы
    sql_commands = [
        # 1. Создание таблицы товарных групп 'dmkod_product_groups'
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {pg_table} (
            id SERIAL PRIMARY KEY,
            group_name VARCHAR(100) NOT NULL UNIQUE,
            display_name VARCHAR(255) NOT NULL,
            fias_required BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(pg_table=sql.Identifier(product_groups_table)),
        sql.SQL("COMMENT ON TABLE {pg_table} IS 'Справочник товарных групп для ДМкод';").format(pg_table=sql.Identifier(product_groups_table)),
        # Безопасное добавление колонок, если они отсутствуют
        sql.SQL("ALTER TABLE {pg_table} ADD COLUMN IF NOT EXISTS code_template TEXT;").format(pg_table=sql.Identifier(product_groups_table)),
        sql.SQL("ALTER TABLE {pg_table} ADD COLUMN IF NOT EXISTS dm_template TEXT;").format(pg_table=sql.Identifier(product_groups_table)),
        sql.SQL("COMMENT ON COLUMN {pg_table}.dm_template IS 'Шаблон DataMatrix кода';").format(pg_table=sql.Identifier(product_groups_table)),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_pg_group_name ON {pg_table}(group_name);").format(pg_table=sql.Identifier(product_groups_table)),

        # 2. Модификация таблицы 'orders'
        # Используем ALTER TABLE ... ADD COLUMN IF NOT EXISTS для безопасности
        sql.SQL("ALTER TABLE {orders} ADD COLUMN IF NOT EXISTS fias_code VARCHAR(36);").format(orders=sql.Identifier(orders_table)),
        sql.SQL("COMMENT ON COLUMN {orders}.fias_code IS 'Код адреса по ФИАС (GUID)';").format(orders=sql.Identifier(orders_table)),

        sql.SQL("ALTER TABLE {orders} ADD COLUMN IF NOT EXISTS api_status VARCHAR(36);").format(orders=sql.Identifier(orders_table)),
        sql.SQL("COMMENT ON COLUMN {orders}.api_status IS 'Статус интеграйии';").format(orders=sql.Identifier(orders_table)),
        
        sql.SQL("ALTER TABLE {orders} ADD COLUMN IF NOT EXISTS api_order_id INTEGER;").format(orders=sql.Identifier(orders_table)),
        sql.SQL("COMMENT ON COLUMN {orders}.api_order_id IS 'ID заказа из внешнего API ДМкод';").format(orders=sql.Identifier(orders_table)),

        sql.SQL("ALTER TABLE {orders} ADD COLUMN IF NOT EXISTS participant_id INTEGER;").format(orders=sql.Identifier(orders_table)),
        sql.SQL("COMMENT ON COLUMN {orders}.participant_id IS 'ID участника из внешнего API ДМкод';").format(orders=sql.Identifier(orders_table)),

        sql.SQL("ALTER TABLE {orders} ADD COLUMN IF NOT EXISTS product_group_id INTEGER REFERENCES {pg_table}(id);").format(orders=sql.Identifier(orders_table), pg_table=sql.Identifier(product_groups_table)),
        sql.SQL("COMMENT ON COLUMN {orders}.product_group_id IS 'ID товарной группы из справочника dmkod_product_groups';").format(orders=sql.Identifier(orders_table)),
        
        # 3. Создание новой таблицы 'dmkod_aggregation_details'
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {agg_details} (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES {orders}(id) ON DELETE CASCADE,
            gtin VARCHAR(14) NOT NULL,
            api_id INTEGER,   
            dm_quantity INTEGER NOT NULL,
            aggregation_level SMALLINT NOT NULL DEFAULT 0,
            production_date DATE,
            expiry_date DATE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(
            agg_details=sql.Identifier(aggregation_details_table),
            orders=sql.Identifier(orders_table)
        ),
        sql.SQL("COMMENT ON TABLE {agg_details} IS 'Детализация задания на агрегацию для ДМкод';").format(agg_details=sql.Identifier(aggregation_details_table)),
        # Безопасное добавление колонки для JSON с кодами
        sql.SQL("ALTER TABLE {agg_details} ADD COLUMN IF NOT EXISTS api_codes_json JSONB;").format(agg_details=sql.Identifier(aggregation_details_table)),
        sql.SQL("COMMENT ON COLUMN {agg_details}.api_codes_json IS 'JSON с кодами маркировки, полученными от API для этого тиража';").format(agg_details=sql.Identifier(aggregation_details_table)),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_agg_details_order_id ON {agg_details}(order_id);").format(agg_details=sql.Identifier(aggregation_details_table)),

        # 4. Создание таблицы для хранения оригинальных файлов заказа
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {order_files} (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES {orders}(id) ON DELETE CASCADE,
            filename VARCHAR(255) NOT NULL,
            file_data BYTEA NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(
            order_files=sql.Identifier(order_files_table),
            orders=sql.Identifier(orders_table)
        ),
        sql.SQL("COMMENT ON TABLE {order_files} IS 'Оригинальные файлы заказов от клиентов для ДМкод';").format(order_files=sql.Identifier(order_files_table)),

        # 5. Создание таблицы для результатов интеграции с "Дельта"
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {delta_table} (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES {orders_table}(id) ON DELETE CASCADE,
            printrun_id INTEGER,
            utilisation_upload_id INTEGER,
            codes_json JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(
            delta_table=sql.Identifier(delta_result_table),
            orders_table=sql.Identifier(orders_table)
        ),
        sql.SQL("COMMENT ON TABLE {delta_table} IS 'Результаты обработки для системы Дельта';").format(delta_table=sql.Identifier(delta_result_table)),

        # 6. Сброс счетчика SSCC для перехода на новую логику GCP.
        # Устанавливаем начальное значение 1.
    ]

    try:
        with conn.cursor() as cur:
            print("Проверяю и обновляю схему базы данных...")
            for command in sql_commands:
                print(f"Выполняю: {command.as_string(cur.connection)}")
                cur.execute(command)
            print("\nСхема успешно обновлена.")
        conn.commit()
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"\nОшибка при обновлении схемы: {error}")
        conn.rollback()
    finally:
        if conn is not None:
            conn.close()

if __name__ == '__main__':
    db_conn = get_db_connection()
    if db_conn:
        update_schema(db_conn)