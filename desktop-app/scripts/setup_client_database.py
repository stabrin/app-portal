# desktop-app/scripts/setup_client_database.py

import logging
import psycopg2
from psycopg2 import sql

def update_client_db_schema(conn):
    """
    Применяет все необходимые изменения схемы для базы данных клиента.
    Объединяет логику из init_db.py (dmkod-integration-app),
    init_db.py (datamatrix-app) и init_ma_db.py (manual-aggregation-app).

    :param conn: Активное подключение к базе данных клиента (psycopg2 connection).
    :return: True, если все прошло успешно, иначе False.
    """
    
    # Имена таблиц, как они определены в исходных скриптах.
    # Для простоты здесь они захардкожены, т.к. .env файлы приложений недоступны.
    # datamatrix-app
    products_table = 'products'
    packages_table = 'packages'
    items_table = 'items'
    orders_table = 'orders'
    aggregation_tasks_table = 'aggregation_tasks'
    users_table = 'users'
    system_counters_table = 'system_counters'
    
    # dmkod-integration-app
    product_groups_table = 'dmkod_product_groups'
    aggregation_details_table = 'dmkod_aggregation_details'
    order_files_table = 'dmkod_order_files'
    delta_result_table = 'delta_result'

    # manual-aggregation-app
    ma_orders_table = 'ma_orders'
    ma_employee_tokens_table = 'ma_employee_tokens'
    ma_work_sessions_table = 'ma_work_sessions'
    ma_aggregations_table = 'ma_aggregations'

    # Список всех SQL-команд для создания и обновления схемы
    sql_commands = [
        # === Блок из manual-aggregation-app ===
        # Эта часть не имеет зависимостей от других таблиц, можно оставить в начале.
        sql.SQL('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {ma_orders} ( id SERIAL PRIMARY KEY,
                                                      client_name VARCHAR(255) NOT NULL,
                                                      created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                                      status VARCHAR(50) NOT NULL DEFAULT 'new',
                                                      aggregation_levels JSONB,
                                                      employee_count INTEGER NOT NULL,
                                                      set_capacity INTEGER );
        """).format(ma_orders=sql.Identifier(ma_orders_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {ma_tokens} ( id SERIAL PRIMARY KEY,
                                                      order_id INTEGER NOT NULL REFERENCES {ma_orders}(id) ON DELETE CASCADE,
                                                      access_token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
                                                      is_active BOOLEAN NOT NULL DEFAULT true,
                                                      created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                                      last_login TIMESTAMP WITH TIME ZONE,
                                                      employee_name VARCHAR(255) );
        """).format(ma_tokens=sql.Identifier(ma_employee_tokens_table), ma_orders=sql.Identifier(ma_orders_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {ma_sessions} ( id SERIAL PRIMARY KEY,
                                                        employee_token_id INTEGER NOT NULL REFERENCES {ma_tokens}(id) ON DELETE CASCADE,
                                                        employee_name VARCHAR(255),
                                                        order_id INTEGER,
                                                        start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                                        end_time TIMESTAMP WITH TIME ZONE,
                                                        workstation_id VARCHAR(100) );
        """).format(ma_sessions=sql.Identifier(ma_work_sessions_table), ma_tokens=sql.Identifier(ma_employee_tokens_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {ma_aggregations} ( id BIGSERIAL PRIMARY KEY,
                                                            order_id INTEGER NOT NULL REFERENCES {ma_orders}(id) ON DELETE CASCADE,
                                                            employee_token_id INTEGER NOT NULL REFERENCES {ma_tokens}(id) ON DELETE CASCADE,
                                                            work_session_id INTEGER REFERENCES {ma_sessions}(id) ON DELETE SET NULL,
                                                            child_code VARCHAR(255) NOT NULL,
                                                            child_type VARCHAR(50) NOT NULL,
                                                            parent_code VARCHAR(255) NOT NULL,
                                                            parent_type VARCHAR(50) NOT NULL,
                                                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP );
        """).format(
            ma_aggregations=sql.Identifier(ma_aggregations_table),
            ma_orders=sql.Identifier(ma_orders_table),
            ma_tokens=sql.Identifier(ma_employee_tokens_table),
            ma_sessions=sql.Identifier(ma_work_sessions_table)
        ),
        # Команда для обратной совместимости, если в старой базе было такое ограничение
        sql.SQL("ALTER TABLE {ma_aggregations} DROP CONSTRAINT IF EXISTS ma_aggregations_child_code_parent_code_key;").format(ma_aggregations=sql.Identifier(ma_aggregations_table)),

        # === Блок из dmkod-integration-app (независимые таблицы) ===
        # Таблица dmkod_product_groups должна быть создана ДО таблицы orders, т.к. orders на нее ссылается.
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {pg_table} ( id SERIAL PRIMARY KEY,
                                                     group_name VARCHAR(100) NOT NULL UNIQUE,
                                                     display_name VARCHAR(255) NOT NULL,
                                                     fias_required BOOLEAN NOT NULL DEFAULT FALSE,
                                                     created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                                     code_template TEXT,
                                                     dm_template TEXT );
        """).format(pg_table=sql.Identifier(product_groups_table)),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_pg_group_name ON {pg_table}(group_name);").format(pg_table=sql.Identifier(product_groups_table)),

        # === Блок из datamatrix-app ===
        # Эти таблицы также независимы или зависят только друг от друга, но не от dmkod.
        # Их нужно создать до таблиц, которые на них ссылаются (items, aggregation_tasks и т.д.).
        sql.SQL("CREATE TABLE IF NOT EXISTS {counters} ( counter_name VARCHAR(50) PRIMARY KEY, current_value BIGINT NOT NULL );").format(counters=sql.Identifier(system_counters_table)),
        sql.SQL("INSERT INTO {counters} (counter_name, current_value) VALUES ('sscc_id', 93) ON CONFLICT (counter_name) DO NOTHING;").format(counters=sql.Identifier(system_counters_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {users} ( id SERIAL PRIMARY KEY,
                                                  username VARCHAR(80) UNIQUE NOT NULL,
                                                  password_hash VARCHAR(255) NOT NULL,
                                                  is_admin BOOLEAN DEFAULT FALSE NOT NULL,
                                                  is_active BOOLEAN DEFAULT TRUE NOT NULL );
        """).format(users=sql.Identifier(users_table)),
        # Добавляем колонку для обратной совместимости
        sql.SQL("ALTER TABLE {users} ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL;").format(users=sql.Identifier(users_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {products} ( gtin VARCHAR(14) PRIMARY KEY,
                                                     name VARCHAR(255) NOT NULL,
                                                     description_1 TEXT,
                                                     description_2 TEXT,
                                                     description_3 TEXT,
                                                     created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
        """).format(products=sql.Identifier(products_table)),

        # Теперь можно создавать таблицу orders, т.к. dmkod_product_groups уже существует.
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {orders} ( id SERIAL PRIMARY KEY,
                                                   client_name VARCHAR(100) NOT NULL,
                                                   order_date DATE NOT NULL DEFAULT CURRENT_DATE,
                                                   status VARCHAR(50) DEFAULT 'new',
                                                   notes TEXT,
                                                   created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                                   -- Колонки из dmkod-integration-app
                                                   fias_code VARCHAR(36),
                                                   api_status VARCHAR(36),
                                                   api_order_id INTEGER,
                                                   participant_id INTEGER,
                                                   product_group_id INTEGER REFERENCES {pg_table}(id) );
        """).format(orders=sql.Identifier(orders_table), pg_table=sql.Identifier(product_groups_table)),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_orders_client_name ON {orders}(client_name);").format(orders=sql.Identifier(orders_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {packages} ( id SERIAL PRIMARY KEY,
                                                     sscc VARCHAR(18) NOT NULL UNIQUE,
                                                     owner VARCHAR(100) NOT NULL,
                                                     level SMALLINT NOT NULL CHECK (level > 0),
                                                     parent_id INTEGER REFERENCES {packages}(id) ON DELETE SET NULL,
                                                     parent_sscc VARCHAR(18),
                                                     created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
        """).format(packages=sql.Identifier(packages_table)),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_packages_parent_id ON {packages}(parent_id);").format(packages=sql.Identifier(packages_table)),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_packages_owner ON {packages}(owner);").format(packages=sql.Identifier(packages_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {items} ( datamatrix VARCHAR(255) PRIMARY KEY,
                                                  gtin VARCHAR(14) NOT NULL REFERENCES {products}(gtin) ON UPDATE CASCADE,
                                                  serial VARCHAR(100) NOT NULL,
                                                  crypto_part_91 VARCHAR(100),
                                                  crypto_part_92 VARCHAR(255),
                                                  crypto_part_93 VARCHAR(100),
                                                  code_8005 VARCHAR(4),
                                                  package_id INTEGER REFERENCES {packages}(id) ON DELETE SET NULL,
                                                  order_id INTEGER NOT NULL REFERENCES {orders}(id) ON DELETE CASCADE,
                                                  tirage_number VARCHAR(50),
                                                  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
        """).format(
            items=sql.Identifier(items_table),
            products=sql.Identifier(products_table),
            packages=sql.Identifier(packages_table),
            orders=sql.Identifier(orders_table)
        ),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_items_gtin ON {items}(gtin);").format(items=sql.Identifier(items_table)),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_items_order_id ON {items}(order_id);").format(items=sql.Identifier(items_table)),

        # Таблицы, зависящие от orders
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {agg_tasks} ( id SERIAL PRIMARY KEY,
                                                      order_id INTEGER NOT NULL REFERENCES {orders}(id) ON DELETE CASCADE,
                                                      container_id VARCHAR(100) NOT NULL,
                                                      gtin VARCHAR(14) NOT NULL,
                                                      sscc VARCHAR(18) NOT NULL UNIQUE,
                                                      owner VARCHAR(100) NOT NULL,
                                                      status VARCHAR(50) DEFAULT 'pending',
                                                      created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
        """).format(
            agg_tasks=sql.Identifier(aggregation_tasks_table),
            orders=sql.Identifier(orders_table)
        ),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {agg_details} ( id SERIAL PRIMARY KEY,
                                                        order_id INTEGER NOT NULL REFERENCES {orders}(id) ON DELETE CASCADE,
                                                        gtin VARCHAR(14) NOT NULL,
                                                        api_id INTEGER,
                                                        dm_quantity INTEGER NOT NULL,
                                                        aggregation_level SMALLINT NOT NULL DEFAULT 0,
                                                        production_date DATE,
                                                        expiry_date DATE,
                                                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                                        api_codes_json JSONB );
        """).format(
            agg_details=sql.Identifier(aggregation_details_table),
            orders=sql.Identifier(orders_table)
        ),
        sql.SQL("CREATE INDEX IF NOT EXISTS idx_agg_details_order_id ON {agg_details}(order_id);").format(agg_details=sql.Identifier(aggregation_details_table)),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {order_files} ( id SERIAL PRIMARY KEY,
                                                        order_id INTEGER NOT NULL REFERENCES {orders}(id) ON DELETE CASCADE,
                                                        filename VARCHAR(255) NOT NULL,
                                                        file_data BYTEA NOT NULL,
                                                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
        """).format(
            order_files=sql.Identifier(order_files_table),
            orders=sql.Identifier(orders_table)
        ),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {delta_table} ( id SERIAL PRIMARY KEY,
                                                        order_id INTEGER NOT NULL REFERENCES {orders}(id) ON DELETE CASCADE,
                                                        printrun_id INTEGER,
                                                        utilisation_upload_id INTEGER,
                                                        codes_json JSONB,
                                                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() );
        """).format(
            delta_table=sql.Identifier(delta_result_table),
            orders=sql.Identifier(orders_table)
        ),
        
        # === Блок комментариев к таблицам (опционально, но полезно) ===
        sql.SQL("COMMENT ON TABLE {table} IS 'Справочник товаров (номенклатура)';").format(table=sql.Identifier(products_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Заказы на маркировку';").format(table=sql.Identifier(orders_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Транспортные упаковки всех уровней (короба, паллеты)';").format(table=sql.Identifier(packages_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Индивидуальные экземпляры товаров с DataMatrix';").format(table=sql.Identifier(items_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Задания на агрегацию и печать SSCC';").format(table=sql.Identifier(aggregation_tasks_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Пользователи системы (для веб-доступа)';").format(table=sql.Identifier(users_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Таблица для хранения системных счетчиков (напр. SSCC)';").format(table=sql.Identifier(system_counters_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Справочник товарных групп для ДМкод';").format(table=sql.Identifier(product_groups_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Детализация задания на агрегацию для ДМкод';").format(table=sql.Identifier(aggregation_details_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Оригинальные файлы заказов от клиентов для ДМкод';").format(table=sql.Identifier(order_files_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Результаты обработки для системы Дельта';").format(table=sql.Identifier(delta_result_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Заказы для ручной агрегации';").format(table=sql.Identifier(ma_orders_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Пропуска доступа сотрудников для ручной агрегации';").format(table=sql.Identifier(ma_employee_tokens_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Рабочие сессии сотрудников ручной агрегации';").format(table=sql.Identifier(ma_work_sessions_table)),
        sql.SQL("COMMENT ON TABLE {table} IS 'Хранит иерархию вложений для ручной агрегации';").format(table=sql.Identifier(ma_aggregations_table)),
    ]
    
    # === Блок для новых таблиц портала (префикс ap_) ===
    ap_tables_commands = [
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS ap_workplaces (
                id SERIAL PRIMARY KEY,
                warehouse_name VARCHAR(255) NOT NULL,
                workplace_number INTEGER NOT NULL,
                access_token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE (warehouse_name, workplace_number)
            );
        """),
        # Добавляем создание таблицы для шаблонов этикеток
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS label_templates (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                template_json JSONB NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """),
        sql.SQL("COMMENT ON TABLE label_templates IS 'Шаблоны макетов этикеток в формате JSON';"),
        # --- НОВАЯ ТАБЛИЦА ДЛЯ ИЗОБРАЖЕНИЙ ---
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS ap_images (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                image_data BYTEA NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """),
        sql.SQL("COMMENT ON TABLE ap_images IS 'Хранилище изображений (логотипов) для макетов этикеток';"),

        # --- НОВЫЙ БЛОК: Таблицы для уведомлений о поставке ---
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS ap_supply_notifications (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                planned_arrival_date DATE,
                status VARCHAR(50) NOT NULL DEFAULT 'new',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """),
        sql.SQL("COMMENT ON TABLE ap_supply_notifications IS 'Уведомления о поставке';"),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS ap_supply_notification_files (
                id SERIAL PRIMARY KEY,
                notification_id INTEGER NOT NULL REFERENCES ap_supply_notifications(id) ON DELETE CASCADE,
                file_type VARCHAR(50) NOT NULL, -- 'supplier' или 'formalized'
                filename VARCHAR(255) NOT NULL,
                file_data BYTEA NOT NULL,
                uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """),
        sql.SQL("COMMENT ON TABLE ap_supply_notification_files IS 'Файлы, приложенные к уведомлениям о поставке';"),

        sql.SQL("""
            CREATE TABLE IF NOT EXISTS ap_supply_notification_details (
                id SERIAL PRIMARY KEY,
                notification_id INTEGER NOT NULL REFERENCES ap_supply_notifications(id) ON DELETE CASCADE,
                gtin VARCHAR(100),
                product_name TEXT,
                quantity INTEGER,
                aggregation TEXT,
                production_date DATE,
                expiry_date DATE
            );
        """),
        # --- ИСПРАВЛЕНИЕ: Явно добавляем колонку, если она отсутствует, для обратной совместимости ---
        sql.SQL("ALTER TABLE ap_supply_notification_details ADD COLUMN IF NOT EXISTS aggregation TEXT;"),

        sql.SQL("COMMENT ON TABLE ap_supply_notification_details IS 'Детализированное содержимое формализованного уведомления о поставке';"),

        # --- НОВЫЙ БЛОК: Таблица для сценариев маркировки ---
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS ap_marking_scenarios (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                scenario_data JSONB NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """),
        sql.SQL("""
            CREATE OR REPLACE FUNCTION trigger_set_timestamp()
            RETURNS TRIGGER AS $$
            BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
            $$ LANGUAGE plpgsql;
        """),
        sql.SQL("DROP TRIGGER IF EXISTS set_timestamp ON ap_marking_scenarios;"), # Сначала удаляем старый, если есть
        sql.SQL("""
            CREATE TRIGGER set_timestamp
            BEFORE UPDATE ON ap_marking_scenarios
            FOR EACH ROW
            EXECUTE PROCEDURE trigger_set_timestamp();
        """),
        sql.SQL("COMMENT ON TABLE ap_marking_scenarios IS 'Справочник сценариев маркировки';"),

    ]



    # === Блок для таблицы видимости приложений (из init_visibility.py) ===
    visibility_commands = [
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS app_visibility (
                app_name VARCHAR(100) PRIMARY KEY,
                visibility_rule TEXT NOT NULL
            );
        """),
        # Заполняем начальными данными
        sql.SQL("INSERT INTO app_visibility (app_name, visibility_rule) VALUES ('dmkod-integration-app', 'admin') ON CONFLICT (app_name) DO NOTHING;"),
        sql.SQL("INSERT INTO app_visibility (app_name, visibility_rule) VALUES ('manual-aggregation-app', 'All') ON CONFLICT (app_name) DO NOTHING;"),
        sql.SQL("INSERT INTO app_visibility (app_name, visibility_rule) VALUES ('datamatrix-app', 'All') ON CONFLICT (app_name) DO NOTHING;"),
    ]

    # Объединяем все команды
    all_commands = sql_commands + ap_tables_commands + visibility_commands

    try:
        with conn.cursor() as cur:
            logging.info("Начинаю обновление схемы базы данных клиента...")
            for i, command in enumerate(all_commands):
                try:
                    # Для отладки можно распечатать команду
                    # logging.debug(f"Выполняю команду {i+1}/{len(sql_commands)}: {command.as_string(cur.connection)}")
                    cur.execute(command)
                except psycopg2.Error as e:
                    # Логируем ошибку, но не прерываем выполнение, т.к. некоторые ошибки могут быть ожидаемы
                    # (например, попытка удалить несуществующий constraint)
                    logging.warning(f"Не удалось выполнить команду {i+1}. Ошибка: {e}. Продолжаю выполнение...")
                    # Важно откатить транзакцию, чтобы можно было продолжить
                    conn.rollback()
            
            logging.info("Обновление схемы базы данных клиента успешно завершено.")
        conn.commit()
        return True
    except (Exception, psycopg2.DatabaseError) as error:
        logging.error(f"Критическая ошибка при обновлении схемы клиента: {error}")
        conn.rollback()
        return False


if __name__ == '__main__':
    # Этот блок для демонстрации и ручного тестирования.
    # Он не будет выполняться при импорте функции update_client_db_schema.
    import os
    from dotenv import load_dotenv

    # Загружаем .env из корня desktop-app для получения тестовых данных
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    dotenv_path = os.path.join(project_root, '.env')
    load_dotenv(dotenv_path=dotenv_path)

    # --- ВАЖНО: ---
    # Для теста нужно указать данные для подключения к ТЕСТОВОЙ КЛИЕНТСКОЙ БАЗЕ.
    # Не используйте здесь данные от tilda_db.
    # Эти переменные нужно добавить в ваш .env файл для тестирования.
    TEST_CLIENT_DB_HOST = os.getenv("TEST_CLIENT_DB_HOST", "localhost")
    TEST_CLIENT_DB_PORT = os.getenv("TEST_CLIENT_DB_PORT", 5432)
    TEST_CLIENT_DB_NAME = os.getenv("TEST_CLIENT_DB_NAME")
    TEST_CLIENT_DB_USER = os.getenv("TEST_CLIENT_DB_USER")
    TEST_CLIENT_DB_PASSWORD = os.getenv("TEST_CLIENT_DB_PASSWORD")
    # Переменная для содержимого сертификата (можно вставить прямо в .env)
    TEST_CLIENT_DB_SSL_CERT = os.getenv("TEST_CLIENT_DB_SSL_CERT")

    if not TEST_CLIENT_DB_NAME:
        print("Для тестирования скрипта необходимо задать переменные TEST_CLIENT_DB_* в .env файле.")
    else:
        test_conn = None
        temp_cert_file = None
        try:
            print(f"Подключаюсь к тестовой базе '{TEST_CLIENT_DB_NAME}' на {TEST_CLIENT_DB_HOST}...")
            
            ssl_params = {}
            if TEST_CLIENT_DB_SSL_CERT:
                import tempfile
                print("Найден SSL сертификат, создаю временный файл...")
                with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                    fp.write(TEST_CLIENT_DB_SSL_CERT)
                    temp_cert_file = fp.name
                ssl_params = {'sslmode': 'verify-full', 'sslrootcert': temp_cert_file}
                print(f"Используется временный SSL-сертификат: {temp_cert_file}")

            test_conn = psycopg2.connect(
                host=TEST_CLIENT_DB_HOST,
                port=TEST_CLIENT_DB_PORT,
                dbname=TEST_CLIENT_DB_NAME,
                user=TEST_CLIENT_DB_USER,
                password=TEST_CLIENT_DB_PASSWORD,
                **ssl_params
            )
            print("Подключение к тестовой базе успешно установлено.")

            # Настраиваем логирование для вывода в консоль
            logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
            
            # Запускаем основную функцию
            success = update_client_db_schema(test_conn)
            
            if success:
                print("\nСкрипт успешно выполнен.")
            else:
                print("\nСкрипт завершился с ошибками. Проверьте лог.")

        except psycopg2.OperationalError as e:
            print(f"\nОшибка подключения к тестовой базе данных: {e}")
            print("Убедитесь, что база данных доступна и учетные данные в .env верны.")
        except Exception as e:
            print(f"\nПроизошла непредвиденная ошибка: {e}")
        finally:
            if test_conn:
                test_conn.close()
                print("Соединение с тестовой базой закрыто.")
            if temp_cert_file and os.path.exists(temp_cert_file):
                os.remove(temp_cert_file)
                print("Временный файл сертификата удален.")