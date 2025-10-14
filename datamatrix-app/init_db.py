import os
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

def get_db_connection():
    """Устанавливает соединение с БД для локальных скриптов."""
    load_dotenv()
    try:
        # Для локальных скриптов, подключающихся к контейнеру Docker,
        # мы используем 'localhost' из-за проброса портов.
        # Мы игнорируем DB_HOST из .env, который может быть 'postgres'
        # для межконтейнерного взаимодействия.
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host='localhost',
            port=os.getenv("DB_PORT")
        )
        return conn
    except psycopg2.OperationalError as e:
        print(f"Ошибка подключения к базе данных: {e}")
        return None

def sanitize_for_index_name(name: str) -> str:
    """Убирает недопустимые символы для имен индексов."""
    return name.replace("-", "_").replace(" ", "_")

def create_schema(conn):
    """Создает схему базы данных, если таблицы не существуют."""
    
    # Получаем имена таблиц из .env
    products_table = os.getenv('TABLE_PRODUCTS', 'products')
    packages_table = os.getenv('TABLE_PACKAGES', 'packages')
    items_table = os.getenv('TABLE_ITEMS', 'items')
    orders_table = os.getenv('TABLE_ORDERS', 'orders')
    aggregation_tasks_table = os.getenv('TABLE_AGGREGATION_TASKS', 'aggregation_tasks')

    # Очищенные имена для использования в названиях индексов
    products_table_sanitized = sanitize_for_index_name(products_table)
    packages_table_sanitized = sanitize_for_index_name(packages_table)
    items_table_sanitized = sanitize_for_index_name(items_table)
    orders_table_sanitized = sanitize_for_index_name(orders_table)

    # Шаблоны SQL-запросов с плейсхолдерами
    sql_commands = [
        # --- НОВАЯ ТАБЛИЦА ДЛЯ СЧЕТЧИКОВ ---
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS system_counters (
            counter_name VARCHAR(50) PRIMARY KEY,
            current_value BIGINT NOT NULL
        );
        """),
        sql.SQL("COMMENT ON TABLE system_counters IS 'Таблица для хранения системных счетчиков';"),
        
        # --- ИНИЦИАЛИЗАЦИЯ СЧЕТЧИКА SSCC ---
        # Вставляем начальное значение, только если его там еще нет
        sql.SQL("""
        INSERT INTO system_counters (counter_name, current_value)
        VALUES ('sscc_id', 93)
        ON CONFLICT (counter_name) DO NOTHING;
        """), 
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(80) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            is_admin BOOLEAN DEFAULT FALSE NOT NULL
        );
        """),
        sql.SQL("COMMENT ON TABLE users IS 'Пользователи системы';"),               
        # 1. Таблица Products (без изменений)
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {table} (
            gtin VARCHAR(14) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            description_1 TEXT,
            description_2 TEXT,
            description_3 TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(table=sql.Identifier(products_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Справочник товаров (номенклатура)';").format(table=sql.Identifier(products_table)),

        # 2. Таблица Orders (НОВАЯ)
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {table} (
            id SERIAL PRIMARY KEY,
            client_name VARCHAR(100) NOT NULL,
            order_date DATE NOT NULL DEFAULT CURRENT_DATE,
            status VARCHAR(50) DEFAULT 'new',
            notes TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(table=sql.Identifier(orders_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Заказы на маркировку';").format(table=sql.Identifier(orders_table)),
        sql.SQL("COMMENT ON COLUMN {table}.status IS 'Статус заказа (new, processing, completed, failed)';").format(table=sql.Identifier(orders_table)),
        sql.SQL("""
        CREATE INDEX IF NOT EXISTS {index_name} ON {table}(client_name);
        """).format(
            index_name=sql.Identifier(f'idx_orders_client_name_{orders_table_sanitized}'),
            table=sql.Identifier(orders_table)
        ),

        # 3. Таблица Packages (без изменений, но ссылка на нее теперь будет с именем из .env)
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {table} (
            id SERIAL PRIMARY KEY,
            sscc VARCHAR(18) NOT NULL UNIQUE,
            owner VARCHAR(100) NOT NULL,
            level SMALLINT NOT NULL CHECK (level > 0),
            parent_id INTEGER REFERENCES {parent_table}(id) ON DELETE SET NULL, -- ID родительской упаковки
            parent_sscc VARCHAR(18), -- Временное поле для SSCC родителя при импорте
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(table=sql.Identifier(packages_table), parent_table=sql.Identifier(packages_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Транспортные упаковки всех уровней (короба, паллеты)';").format(table=sql.Identifier(packages_table)),
        sql.SQL("COMMENT ON COLUMN {table}.parent_sscc IS 'Временное поле для SSCC родителя при импорте из внешних систем';").format(table=sql.Identifier(packages_table)),
        sql.SQL("""
        CREATE INDEX IF NOT EXISTS {index_name} ON {table}(parent_id);
        """).format(
            index_name=sql.Identifier(f'idx_packages_parent_id_{packages_table_sanitized}'),
            table=sql.Identifier(packages_table)
        ),
        sql.SQL("""
        CREATE INDEX IF NOT EXISTS {index_name} ON {table}(owner);
        """).format(
            index_name=sql.Identifier(f'idx_packages_owner_{packages_table_sanitized}'),
            table=sql.Identifier(packages_table)
        ),

        # 4. Таблица Items (ОБНОВЛЕНА)
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {table} (
            datamatrix VARCHAR(255) PRIMARY KEY,
            gtin VARCHAR(14) NOT NULL REFERENCES {products_table}(gtin) ON UPDATE CASCADE,
            serial VARCHAR(100) NOT NULL,
            crypto_part_91 VARCHAR(100),
            crypto_part_92 VARCHAR(255),
            crypto_part_93 VARCHAR(100),
            code_8005 VARCHAR(4),
            package_id INTEGER REFERENCES {packages_table}(id) ON DELETE SET NULL,
            order_id INTEGER NOT NULL REFERENCES {orders_table}(id) ON DELETE CASCADE, -- Заменили поля на order_id
            tirage_number VARCHAR(50),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(
            table=sql.Identifier(items_table),
            products_table=sql.Identifier(products_table),
            packages_table=sql.Identifier(packages_table),
            orders_table=sql.Identifier(orders_table)
        ),
        sql.SQL("COMMENT ON TABLE {table} IS 'Индивидуальные экземпляры товаров с DataMatrix';").format(table=sql.Identifier(items_table)),
        # 5. ОБНОВЛЕННАЯ Таблица aggregation_tasks
        sql.SQL("""
        CREATE TABLE IF NOT EXISTS {table} (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES {orders_table}(id) ON DELETE CASCADE,
            container_id VARCHAR(100) NOT NULL, -- ЗАМЕНИЛИ quantity
            gtin VARCHAR(14) NOT NULL,
            sscc VARCHAR(18) NOT NULL UNIQUE,
            owner VARCHAR(100) NOT NULL,
            status VARCHAR(50) DEFAULT 'pending',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """).format(
            table=sql.Identifier(aggregation_tasks_table),
            orders_table=sql.Identifier(os.getenv('TABLE_ORDERS', 'orders'))
        ),
        sql.SQL("COMMENT ON TABLE {table} IS 'Задания на агрегацию и печать SSCC';").format(table=sql.Identifier(aggregation_tasks_table)),
        sql.SQL("COMMENT ON COLUMN {table}.container_id IS 'Идентификатор контейнера/партии от клиента';").format(table=sql.Identifier(aggregation_tasks_table)),
        sql.SQL("""
        CREATE INDEX IF NOT EXISTS {index_name} ON {table}(gtin);
        """).format(
            index_name=sql.Identifier(f'idx_items_gtin_{items_table_sanitized}'),
            table=sql.Identifier(items_table)
        ),
        # Новый индекс для быстрой выборки всех items по заказу
        sql.SQL("""
        CREATE INDEX IF NOT EXISTS {index_name} ON {table}(order_id);
        """).format(
            index_name=sql.Identifier(f'idx_items_order_id_{items_table_sanitized}'),
            table=sql.Identifier(items_table)
        )

    ]

    try:
        with conn.cursor() as cur:
            print("Проверяю и создаю схему базы данных...")
            # Важно: выполняем команды в правильном порядке зависимостей
            # Сначала создаются таблицы, на которые будут ссылаться другие (products, orders, packages)
            for command in sql_commands:
                print(f"Выполняю команду:\n{command.as_string(cur.connection)}\n")
                cur.execute(command)
            print("Схема успешно создана или уже существовала.")
        conn.commit()
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Ошибка при создании схемы: {error}")
        conn.rollback()
    finally:
        if conn is not None:
            conn.close()

if __name__ == '__main__':
    conn = get_db_connection()
    if conn:
        create_schema(conn)