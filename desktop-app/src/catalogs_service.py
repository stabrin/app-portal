# src/catalogs_service.py

import json
import logging
import psycopg2
import pandas as pd
from psycopg2.extras import RealDictCursor, execute_values
from .api_service import ApiService

logger = logging.getLogger(__name__)

class CatalogsService:
    """
    Сервис для управления логикой вкладки "Справочники".
    """
    def __init__(self, user_info, db_connection_func):
        """
        Инициализирует сервис.
        :param user_info: Словарь с информацией о пользователе.
        :param db_connection_func: Функция, возвращающая подключение к БД клиента.
        """
        self.api_service = ApiService(user_info)
        self.get_db_connection = db_connection_func
        self._ensure_images_table_exists()

    def get_participants_catalog(self):
        """Получает справочник участников, используя ApiService."""
        logger.info("Запрос справочника участников через CatalogsService.")
        return self.api_service.get_participants()

    # --- Методы для товарных групп ---

    def get_product_groups(self):
        """Возвращает список товарных групп из БД клиента."""
        logger.info("Запрос справочника товарных групп из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, group_name, display_name, fias_required, code_template, dm_template FROM dmkod_product_groups ORDER BY display_name")
                return cur.fetchall()

    def upsert_product_group(self, group_data: dict):
        """Добавляет или обновляет товарную группу."""
        group_id = group_data.get('id')
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                if group_id: # Обновление
                    cur.execute("""
                        UPDATE dmkod_product_groups SET group_name=%s, display_name=%s, fias_required=%s, code_template=%s, dm_template=%s
                        WHERE id=%s
                    """, (group_data['group_name'], group_data['display_name'], group_data['fias_required'], group_data['code_template'], group_data['dm_template'], group_id))
                else: # Вставка
                    cur.execute("""
                        INSERT INTO dmkod_product_groups (group_name, display_name, fias_required, code_template, dm_template)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (group_data['group_name'], group_data['display_name'], group_data['fias_required'], group_data['code_template'], group_data['dm_template']))
            conn.commit()

    def delete_product_group(self, group_id: int):
        """Удаляет товарную группу по ID."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dmkod_product_groups WHERE id = %s", (group_id,))
            conn.commit()

    def get_product_groups_template(self):
        """Возвращает шаблон для импорта товарных групп."""
        return pd.DataFrame(columns=['id', 'group_name', 'display_name', 'fias_required', 'code_template', 'dm_template'])

    def process_product_groups_import(self, df: pd.DataFrame):
        """Обрабатывает импорт товарных групп из DataFrame."""
        with self.get_db_connection() as conn:
            df = df.where(pd.notna(df), None)

            with conn.cursor() as cur:
                # --- НОВАЯ ЛОГИКА: Используем UPSERT для атомарного обновления и вставки ---
                # Убираем строки без ID, так как для UPSERT по ID они не нужны и вызовут ошибку.
                df = df[pd.to_numeric(df['id'], errors='coerce').notna()].copy()
                if df.empty:
                    logger.warning("Нет данных с 'id' для импорта товарных групп.")
                    return

                upsert_query = """
                    INSERT INTO dmkod_product_groups (id, group_name, display_name, fias_required, code_template, dm_template)
                    VALUES %s
                    ON CONFLICT (id) DO UPDATE SET
                        group_name = EXCLUDED.group_name,
                        display_name = EXCLUDED.display_name,
                        fias_required = EXCLUDED.fias_required,
                        code_template = EXCLUDED.code_template,
                        dm_template = EXCLUDED.dm_template;
                """
                data_tuples = [tuple(x) for x in df[['id', 'group_name', 'display_name', 'fias_required', 'code_template', 'dm_template']].to_numpy()]
                logger.info(f"Подготовлено к импорту (UPSERT) {len(data_tuples)} товарных групп. Первые 5: {data_tuples[:5]}")
                execute_values(cur, upsert_query, data_tuples)
                logger.info(f"Выполнен execute_values для импорта {cur.rowcount} товарных групп.")
            conn.commit()

    # --- Методы для товаров ---

    def get_products(self):
        """Возвращает список товаров (номенклатуры) из БД клиента."""
        logger.info("Запрос справочника товаров из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT gtin, name, description_1, description_2, description_3 FROM products ORDER BY name")
                return cur.fetchall()

    def upsert_product(self, product_data: dict):
        """Добавляет или обновляет товар."""
        logger.debug(f"Попытка UPSERT для товара с GTIN: {product_data.get('gtin')}. Данные: {product_data}")
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # --- ИСПРАВЛЕНИЕ: Явная проверка на существование перед действием ---
                # Это надежнее, чем полагаться на ON CONFLICT, особенно когда
                # ключ может быть изменен в UI (хотя мы это и заблокировали).
                cur.execute("SELECT 1 FROM products WHERE gtin = %s", (product_data['gtin'],))
                exists = cur.fetchone()
                if exists:  # Если GTIN найден, обновляем запись
                    logger.debug(f"Товар с GTIN {product_data['gtin']} существует. Выполняется UPDATE.")
                    cur.execute("""
                        UPDATE products SET name=%s, description_1=%s, description_2=%s, description_3=%s
                        WHERE gtin=%s
                    """, (product_data['name'], product_data.get('description_1'), product_data.get('description_2'), product_data.get('description_3'), product_data['gtin']))
                else:  # Если GTIN не найден, создаем новую запись
                    logger.debug(f"Товар с GTIN {product_data['gtin']} не найден. Выполняется INSERT.")
                    cur.execute("""
                        INSERT INTO products (gtin, name, description_1, description_2, description_3)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (product_data['gtin'], product_data['name'], product_data.get('description_1'), product_data.get('description_2'), product_data.get('description_3')))
            conn.commit()

    def delete_product(self, gtin: str):
        """Удаляет товар по GTIN."""
        logger.debug(f"Попытка удаления товара с GTIN: {gtin}")
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE gtin = %s", (gtin,))
            conn.commit()
        logger.info(f"Товар с GTIN {gtin} успешно удален.")

    def get_products_template(self):
        """Возвращает шаблон для импорта товаров."""
        return pd.DataFrame(columns=['gtin', 'name', 'description_1', 'description_2', 'description_3'])

    def process_products_import(self, df: pd.DataFrame):
        """Обрабатывает импорт товаров из DataFrame в режиме UPSERT."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # --- ИСПРАВЛЕНИЕ: Заменяем NaN на None, чтобы избежать ошибок при вставке в БД ---
                # Это гарантирует, что пустые ячейки в Excel будут преобразованы в NULL в базе данных.
                df = df.where(pd.notna(df), None)

                # --- НОВАЯ ЛОГИКА: Используем UPSERT для атомарного обновления и вставки ---
                data_tuples = [tuple(x) for x in df[['gtin', 'name', 'description_1', 'description_2', 'description_3']].to_numpy()]
                
                # Формируем запрос UPSERT (INSERT ... ON CONFLICT ... DO UPDATE)
                upsert_query = """
                    INSERT INTO products (gtin, name, description_1, description_2, description_3)
                    VALUES %s
                    ON CONFLICT (gtin) DO UPDATE SET
                        name = EXCLUDED.name,
                        description_1 = EXCLUDED.description_1,
                        description_2 = EXCLUDED.description_2,
                        description_3 = EXCLUDED.description_3;
                """
                logger.info(f"Подготовлено к импорту (UPSERT) {len(data_tuples)} товаров. Первые 5: {data_tuples[:5]}")
                execute_values(cur, upsert_query, data_tuples)
                logger.info(f"Выполнен execute_values для импорта {cur.rowcount} товаров.")
            conn.commit()

    # --- Методы для сценариев маркировки ---

    def get_marking_scenarios(self):
        """Возвращает список сценариев маркировки из БД клиента."""
        logger.info("Запрос справочника сценариев маркировки из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, name, scenario_data FROM ap_marking_scenarios ORDER BY name")
                return cur.fetchall()

    def upsert_marking_scenario(self, scenario_data: dict):
        """Добавляет или обновляет сценарий маркировки."""
        scenario_id = scenario_data.get('id')
        # Убедимся, что scenario_data - это JSON-строка
        data_json = json.dumps(scenario_data.get('scenario_data', {}))

        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                if scenario_id: # Обновление
                    cur.execute("""
                        UPDATE ap_marking_scenarios SET name=%s, scenario_data=%s
                        WHERE id=%s
                    """, (scenario_data['name'], data_json, scenario_id))
                else: # Вставка
                    cur.execute("""
                        INSERT INTO ap_marking_scenarios (name, scenario_data)
                        VALUES (%s, %s)
                    """, (scenario_data['name'], data_json))
            conn.commit()

    def delete_marking_scenario(self, scenario_id: int):
        """Удаляет сценарий маркировки по ID."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ap_marking_scenarios WHERE id = %s", (scenario_id,))
            conn.commit()

    def get_marking_scenarios_template(self):
        """Возвращает шаблон для импорта сценариев."""
        return pd.DataFrame(columns=['id', 'name', 'scenario_data'])

    def process_marking_scenarios_import(self, df: pd.DataFrame):
        """Обрабатывает импорт сценариев из DataFrame."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Используем ON CONFLICT для UPSERT
                upsert_query = """
                    INSERT INTO ap_marking_scenarios (id, name, scenario_data)
                    VALUES %s
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        scenario_data = EXCLUDED.scenario_data;
                """
                # Убедимся, что scenario_data это валидный JSON
                df['scenario_data'] = df['scenario_data'].apply(lambda x: json.dumps(x) if isinstance(x, dict) else x)
                data_tuples = [tuple(x) for x in df[['id', 'name', 'scenario_data']].to_numpy()]
                execute_values(cur, upsert_query, data_tuples)
            conn.commit()

    # --- Методы для локального справочника клиентов ---

    def get_local_clients(self):
        """Возвращает список локальных клиентов из БД."""
        logger.info("Запрос локального справочника клиентов из БД.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, name, inn FROM ap_clients ORDER BY name")
                return cur.fetchall()

    def upsert_local_client(self, client_data: dict):
        """Добавляет или обновляет локального клиента."""
        client_id = client_data.get('id')
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                if client_id: # Обновление
                    cur.execute("""
                        UPDATE ap_clients SET name=%s, inn=%s
                        WHERE id=%s
                    """, (client_data['name'], client_data.get('inn'), client_id))
                else: # Вставка
                    cur.execute("""
                        INSERT INTO ap_clients (name, inn)
                        VALUES (%s, %s)
                    """, (client_data['name'], client_data.get('inn')))
            conn.commit()

    def delete_local_client(self, client_id: int):
        """Удаляет локального клиента по ID."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ap_clients WHERE id = %s", (client_id,))
            conn.commit()

    def get_local_clients_template(self):
        """Возвращает шаблон для импорта локальных клиентов."""
        return pd.DataFrame(columns=['id', 'name', 'inn'])

    def process_local_clients_import(self, df: pd.DataFrame):
        """Обрабатывает импорт локальных клиентов из DataFrame."""
        with self.get_db_connection() as conn:
            # --- ИСПРАВЛЕНИЕ: Заменяем NaN на None, чтобы избежать ошибок при вставке в БД ---
            # Это гарантирует, что пустые ячейки в Excel будут преобразованы в NULL в базе данных.
            df = df.where(pd.notna(df), None)

            # --- НОВАЯ ЛОГИКА: Разделяем на вставку и обновление для детального логирования ---
            with conn.cursor() as cur:
                # Разделяем данные на те, что с ID (для обновления) и без (для вставки)
                update_df = df[pd.to_numeric(df['id'], errors='coerce').notna()].copy()
                insert_df = df[pd.to_numeric(df['id'], errors='coerce').isna()].copy()

                # Обновляем существующие записи
                if not update_df.empty:
                    update_df['id'] = update_df['id'].astype(int) # Приводим ID к целочисленному типу
                    update_tuples = [tuple(x) for x in update_df[['name', 'inn', 'id']].to_numpy()]
                    update_query = "UPDATE ap_clients SET name=%s, inn=%s WHERE id=%s"
                    logger.info(f"Подготовлено к обновлению {len(update_tuples)} записей. Первые 5: {update_tuples[:5]}")
                    cur.executemany(update_query, update_tuples)
                    logger.info(f"Выполнен executemany для обновления {cur.rowcount} записей.")

                # Вставляем новые записи
                if not insert_df.empty:
                    # Для вставки убираем столбец 'id', так как он будет сгенерирован автоматически
                    insert_tuples = [tuple(x) for x in insert_df[['name', 'inn']].to_numpy()]
                    insert_query = "INSERT INTO ap_clients (name, inn) VALUES %s"
                    logger.info(f"Подготовлено к вставке {len(insert_tuples)} новых записей. Первые 5: {insert_tuples[:5]}")
                    execute_values(cur, insert_query, insert_tuples)
                    logger.info(f"Выполнен execute_values для вставки {cur.rowcount} новых записей.")
            conn.commit()

    # --- Методы для макетов печати ---

    def get_print_layouts(self):
        """Возвращает список макетов печати из БД клиента."""
        logger.info("Запрос справочника макетов печати из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Проверяем, существует ли таблица перед выполнением запроса
                cur.execute("SELECT to_regclass('public.label_templates')")
                if cur.fetchone()['to_regclass'] is None:
                    logger.warning("Таблица 'label_templates' не найдена. Создание таблицы...")
                    cur.execute("""
                        CREATE TABLE label_templates (
                            name TEXT NOT NULL PRIMARY KEY,
                            template_json JSONB,
                            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                        )
                    """)
                    conn.commit()
                    logger.info("Таблица 'label_templates' успешно создана.")
                    return [] # Возвращаем пустой список, так как таблица только что создана
                
                cur.execute("SELECT name, template_json FROM label_templates ORDER BY name")
                rows = cur.fetchall()
                logger.debug(f"Найдено {len(rows)} макетов в БД. Данные: {rows}")
                
                layouts = []
                for row in rows:
                    layout = None
                    try:
                        layout_data = row['template_json']
                        if isinstance(layout_data, str):
                            layout = json.loads(layout_data)
                        else:
                            layout = layout_data
                        
                        # Дополнительная проверка, что layout это словарь
                        if not isinstance(layout, dict):
                            logger.warning(f"Данные для макета '{row['name']}' не являются словарем. Пропуск.")
                            layout = None

                    except (json.JSONDecodeError, TypeError) as e:
                        logger.error(f"Ошибка декодирования JSON для макета '{row['name']}': {e}")
                        layout = None # Явно обнуляем в случае ошибки
                    
                    if not layout:
                        # Если layout пустой или произошла ошибка, создаем заглушку
                        layout = {
                            'name': row['name'],
                            'width_mm': '?',
                            'height_mm': '?',
                            'objects': [],
                            '_is_invalid': True # Флаг для UI
                        }

                    # 'id' и 'name' берем из имени строки в любом случае
                    layout['id'] = row['name']
                    layout['name'] = row['name']
                    layouts.append(layout)
                
                logger.debug(f"Возвращено {len(layouts)} обработанных макетов.")
                return layouts

    def upsert_print_layout(self, layout_data: dict):
        """Добавляет или обновляет макет печати."""
        layout_name = layout_data.get('name')
        if not layout_name:
            raise ValueError("Имя макета не может быть пустым.")
        
        # Убираем 'id', так как он не является частью JSON
        template_to_save = {k: v for k, v in layout_data.items() if k != 'id'}

        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO label_templates (name, template_json, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (name) DO UPDATE SET
                        template_json = EXCLUDED.template_json,
                        updated_at = NOW();
                """, (layout_name, json.dumps(template_to_save)))
            conn.commit()

    def delete_print_layout(self, layout_name: str):
        """Удаляет макет печати по имени."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM label_templates WHERE name = %s", (layout_name,))
            conn.commit()

    # --- Методы для изображений в макетах ---

    def _ensure_images_table_exists(self):
        """Проверяет и при необходимости создает таблицу для хранения изображений."""
        pass # Логика перенесена в printing_service для централизации

    def upload_image(self, name: str, data: bytes):
        """Загружает или обновляет изображение в БД."""
        if not name:
            raise ValueError("Имя изображения не может быть пустым.")
        
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ap_images (name, image_data)
                    VALUES (%s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        image_data = EXCLUDED.image_data;
                """, (name, psycopg2.Binary(data)))
            conn.commit()
        logger.info(f"Изображение '{name}' успешно загружено.")

    # --- НОВЫЙ БЛОК: Методы для сопоставления кодов ---

    def get_product_mappings(self):
        """Возвращает список всех сопоставлений кодов товаров."""
        logger.info("Запрос справочника сопоставлений кодов из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Используем LEFT JOIN, чтобы получить и глобальные сопоставления (где client_id IS NULL)
                cur.execute("""
                    SELECT 
                        pcm.id, pcm.gtin, pcm.mapped_code, pcm.mapped_code_type, pcm.client_id,
                        ac.name as client_name
                    FROM product_code_mappings pcm
                    LEFT JOIN ap_clients ac ON pcm.client_id = ac.id
                    ORDER BY pcm.gtin, pcm.mapped_code;
                """)
                return cur.fetchall()

    def get_mapping_by_id(self, mapping_id: int):
        """Возвращает одну запись сопоставления по ее ID."""
        logger.info(f"Запрос сопоставления с ID: {mapping_id}")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM product_code_mappings WHERE id = %s", (mapping_id,))
                return cur.fetchone()

    def upsert_product_mapping(self, mapping_data: dict):
        """Добавляет или обновляет сопоставление кодов."""
        mapping_id = mapping_data.get('id')
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                if mapping_id: # Обновление
                    cur.execute("""
                        UPDATE product_code_mappings SET 
                            gtin=%s, mapped_code=%s, mapped_code_type=%s, client_id=%s
                        WHERE id=%s
                    """, (mapping_data['gtin'], mapping_data['mapped_code'], mapping_data['mapped_code_type'], mapping_data.get('client_id'), mapping_id))
                else: # Вставка
                    cur.execute("""
                        INSERT INTO product_code_mappings (gtin, mapped_code, mapped_code_type, client_id)
                        VALUES (%s, %s, %s, %s)
                    """, (mapping_data['gtin'], mapping_data['mapped_code'], mapping_data['mapped_code_type'], mapping_data.get('client_id')))
            conn.commit()

    def delete_product_mapping(self, mapping_id: int):
        """Удаляет сопоставление по ID."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM product_code_mappings WHERE id = %s", (mapping_id,))
            conn.commit()

    # --- КОНЕЦ НОВОГО БЛОКА ---


    def get_image_names(self):
        """Возвращает список имен всех изображений из БД."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM ap_images ORDER BY name")
                return [row[0] for row in cur.fetchall()]

    