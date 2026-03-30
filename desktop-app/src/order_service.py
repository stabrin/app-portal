import logging
import re
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dateutil.relativedelta import relativedelta

from .db_connector import get_client_db_connection
from .utils import upsert_data_to_db
from .aggregation_service import run_import_from_dmkod, create_bartender_views, parse_datamatrix
import json

class OrderService:
    """
    Сервисный слой для инкапсуляции бизнес-логики, связанной с заказами.
    """
    def __init__(self, user_info):
        self.user_info = user_info

    def _get_connection(self):
        """Возвращает соединение с БД клиента."""
        return get_client_db_connection(self.user_info)

    def get_orders(self, is_archive: bool):
        """
        Загружает список заказов (активных или архивных) с агрегированной информацией.
        """
        status_filter = "o.status LIKE 'Архив%%'" if is_archive else "o.status NOT LIKE 'Архив%%'"
        query = f"""
            SELECT o.id, o.client_name, o.order_date, o.status, o.notes, o.api_status, s.scenario_data,
                   COUNT(DISTINCT d.gtin) as positions_count,
                   COALESCE(SUM(d.dm_quantity), 0) as dm_count
            FROM orders o
            LEFT JOIN dmkod_aggregation_details d ON o.id = d.order_id
            LEFT JOIN ap_marking_scenarios s ON o.scenario_id = s.id
            WHERE {status_filter}
            GROUP BY o.id, o.client_name, o.order_date, o.status, o.notes, o.api_status, s.scenario_data
            ORDER BY o.id DESC
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                return cur.fetchall()

    def get_order_details(self, order_id: int):
        """Загружает детализацию для конкретного заказа."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM dmkod_aggregation_details WHERE order_id = %s ORDER BY id", (order_id,))
                return cur.fetchall()

    def create_order_from_api_order(self, api_order_data: dict, participants_dict: dict):
        """Создает заказ в БД клиента на основе данных из API.
        
        Args:
            api_order_data: Словарь с данными заказа из API (order_id, participant, products, etc.)
            participants_dict: Словарь {participant_id: {"id": ..., "name": ...}}
        
        Returns:
            int: ID созданного заказа
        """
        from datetime import datetime, timedelta
        
        api_order_id = api_order_data.get('order_id')
        participant_id = api_order_data.get('participant')
        products = api_order_data.get('products', [])
        
        if not api_order_id or not participant_id:
            raise ValueError("api_order_id и participant обязательны.")
        
        # Получаем данные участника из справочника
        participant_info = participants_dict.get(participant_id)
        if not participant_info:
            raise ValueError(f"Участник {participant_id} не найден в справочнике.")
        
        client_name = participant_info.get('name', f"Участник {participant_id}")
        client_api_id = participant_info.get('id', participant_id)
        
        # Формируем даты
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Создаем заказ в таблице orders
                cur.execute("""
                    INSERT INTO orders (
                        client_name, order_date, status, notes, api_status, 
                        api_order_id, scenario_id, client_api_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    client_name,
                    today,
                    "dmkod",
                    "Заказ из АПИ",
                    "Запрос создан",
                    api_order_id,
                    1,  # scenario_id = 1
                    client_api_id
                ))
                
                order_id = cur.fetchone()['id']
                logging.info(f"Создан заказ ID {order_id} из API заказа {api_order_id}.")
                
                # 2. Создаем детализацию для каждого продукта
                if products:
                    details_data = [
                        (
                            order_id,
                            product.get('gtin'),
                            product.get('qty', 0),
                            0,  # aggregation_level
                            today,
                            tomorrow
                        )
                        for product in products
                    ]
                    
                    insert_query = """
                        INSERT INTO dmkod_aggregation_details (
                            order_id, gtin, dm_quantity, aggregation_level, 
                            production_date, expiry_date
                        ) VALUES %s
                    """
                    execute_values(cur, insert_query, details_data)
                    logging.info(f"Добавлено {len(details_data)} товаров в детализацию заказа {order_id}.")
                
                conn.commit()
            
        return order_id

    def save_order_changes(self, order_id: int, updates: list, notes: str, fias_code: str, kpp: str, product_group_id: int = None):
        """Сохраняет изменения в заказе (комментарий, ФИАС, КПП, продуктовая группа) и его детализации в одной транзакции."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Обновляем основные данные заказа
                if product_group_id is not None:
                    cur.execute("UPDATE orders SET notes = %s, fias_code = %s, kpp = %s, product_group_id = %s WHERE id = %s", (notes, fias_code, kpp, product_group_id, order_id))
                else:
                    cur.execute("UPDATE orders SET notes = %s, fias_code = %s, kpp = %s WHERE id = %s", (notes, fias_code, kpp, order_id))
                
                # 2. Обновляем строки в детализации
                for item in updates:
                    cur.execute("""
                        UPDATE dmkod_aggregation_details SET
                            gtin = %s, dm_quantity = %s, aggregation_level = %s,
                            production_date = %s, expiry_date = %s
                        WHERE id = %s
                    """, (
                        item['gtin'], item['dm_quantity'], item['aggregation_level'],
                        item['production_date'] or None, item['expiry_date'] or None,
                        item['id']
                    ))
            conn.commit()

    def import_details_from_excel(self, order_id: int, filepath: str):
        """Импортирует детализацию из Excel-файла, полностью заменяя существующую."""
        df = pd.read_excel(filepath, dtype={'gtin': str})
        df = df.where(pd.notna(df), None)
        df['order_id'] = order_id

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                logging.debug(f"Удаление старой детализации для заказа ID: {order_id}...")
                cur.execute("DELETE FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                
                cols = ['order_id', 'gtin', 'dm_quantity', 'aggregation_level', 'production_date', 'expiry_date']
                df_to_insert = df[[c for c in cols if c in df.columns]]
                
                insert_query = f"""
                    INSERT INTO dmkod_aggregation_details ({", ".join(df_to_insert.columns)}) 
                    VALUES %s
                """
                data_tuples = [tuple(x) for x in df_to_insert.to_numpy()]
                execute_values(cur, insert_query, data_tuples)
            conn.commit()
        return len(df)

    def move_order_to_archive(self, order_id: int):
        """Перемещает заказ в архив, удаляя связанные с ним представления."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT client_name, status FROM orders WHERE id = %s", (order_id,))
                order_info = cur.fetchone()
                if not order_info:
                    raise ValueError(f"Заказ с ID {order_id} не найден.")

                client_name = order_info['client_name']
                current_status = order_info['status']
                
                base_view_name_str = f"{client_name}_{order_id}"
                sanitized_name = re.sub(r'[^\w]', '_', base_view_name_str)
                sanitized_name = re.sub(r'_+', '_', sanitized_name).strip('_')
                
                base_view_name = psycopg2.sql.Identifier(sanitized_name)
                sscc_view_name = psycopg2.sql.Identifier(f"{sanitized_name}_sscc")

                cur.execute(psycopg2.sql.SQL("DROP VIEW IF EXISTS {};").format(sscc_view_name))
                cur.execute(psycopg2.sql.SQL("DROP VIEW IF EXISTS {};").format(base_view_name))

                new_status = f"Архив_{current_status}"
                cur.execute("UPDATE orders SET status = %s WHERE id = %s RETURNING notification_id", (new_status, order_id))
                result = cur.fetchone()
                notification_id = result['notification_id'] if result else None
                if notification_id:
                    cur.execute("UPDATE ap_supply_notifications SET status = 'В архиве' WHERE id = %s", (notification_id,))
            conn.commit()

    def get_order_scenario_data(self, order_id: int):
        """Возвращает данные сценария и ID уведомления для заказа."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT o.notification_id, s.scenario_data FROM orders o JOIN ap_marking_scenarios s ON o.scenario_id = s.id WHERE o.id = %s", (order_id,))
                return cur.fetchone()

    def get_products_for_order(self, order_id: int):
        """Возвращает данные о товарах, связанных с заказом, из справочника."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT DISTINCT gtin FROM dmkod_aggregation_details WHERE order_id = %s AND gtin IS NOT NULL", (order_id,))
                gtins = [row['gtin'] for row in cur.fetchall()]
                if not gtins:
                    return []
                
                cur.execute("SELECT gtin, name, description_1, description_2, description_3 FROM products WHERE gtin = ANY(%s)", (gtins,))
                return cur.fetchall()

    def import_products_from_excel(self, filepath: str):
        """Импортирует (обновляет) данные о товарах из Excel-файла в общий справочник."""
        df = pd.read_excel(filepath, dtype={'gtin': str})
        # --- НОВЫЙ БЛОК: Игнорируем составное поле при импорте ---
        if 'ИНН_GTIN' in df.columns:
            df = df.drop(columns=['ИНН_GTIN'])
            logging.info("Колонка 'ИНН_GTIN' найдена в файле и будет проигнорирована при импорте.")
        # --- КОНЕЦ НОВОГО БЛОКА ---

        logging.debug(f"Прочитано {len(df)} строк из Excel-файла: {filepath}")
        # --- ИСПРАВЛЕНИЕ: Заменяем NaN на None, чтобы избежать ошибок при вставке в БД ---
        # Это гарантирует, что пустые ячейки в Excel будут преобразованы в NULL в базе данных.
        df = df.where(pd.notna(df), None)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # --- ИСПРАВЛЕНИЕ: Аргументы были перепутаны. Правильный порядок: cursor, dataframe, table_name, pk_column ---
                logging.debug(f"Вызов upsert_data_to_db для таблицы 'products'. Тип DataFrame: {type(df)}. Первые 5 строк:\n{df.head().to_string()}")
                upsert_data_to_db(cur, df, 'products', 'gtin')
            conn.commit()
        return len(df)

    def create_bartender_views_for_order(self, order_id: int) -> dict:
        """Выполняет импорт кодов и создает/обновляет представления для Bartender."""
        try:
            # Шаг 1: Импорт кодов
            logging.info(f"OrderService: Запуск run_import_from_dmkod для заказа {order_id}")
            import_logs = run_import_from_dmkod(self.user_info, order_id)
            if any("КРИТИЧЕСКАЯ ОШИБКА" in log for log in import_logs):
                error_message = next((log for log in import_logs if "КРИТИЧЕСКАЯ ОШИБКА" in log), "Неизвестная ошибка импорта.")
                return {"success": False, "message": error_message}
            # Шаг 2: Создание представлений
            logging.info(f"OrderService: Запуск create_bartender_views для заказа {order_id}")
            return create_bartender_views(self.user_info, order_id)
        except Exception as e:
            logging.error(f"OrderService: Критическая ошибка в create_bartender_views_for_order для заказа {order_id}: {e}", exc_info=True)
            return {"success": False, "message": f"Критическая ошибка сервиса: {e}"}

    def export_data_for_external_sw(self, order_id: int):
        """Готовит DataFrame для выгрузки данных в формате 'Дельта'."""
        all_rows = []
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Получаем информацию о заказе и его сценарии
                cur.execute("""
                    SELECT o.notes, s.scenario_data
                    FROM orders o
                    JOIN ap_marking_scenarios s ON o.scenario_id = s.id
                    WHERE o.id = %s
                """, (order_id,))
                order_info = cur.fetchone()
                if not order_info:
                    raise ValueError(f"Заказ с ID {order_id} не найден.")

                dm_source = order_info.get('scenario_data', {}).get('dm_source')

                # 2. В зависимости от источника, выбираем данные из разных таблиц
                if dm_source == 'Файлы клиента (csv, txt)': # Сценарий для кодов от клиента
                    logging.info(f"Экспорт для заказа {order_id}. Источник: Файлы клиента. Запрос к 'items'.")
                    # --- ИЗМЕНЕНИЕ: Добавляем запрос для получения срока годности ---
                    cur.execute(
                        """
                        SELECT 
                            i.datamatrix,
                            d.shelf_life_months
                        FROM items i
                        JOIN orders o ON i.order_id = o.id
                        LEFT JOIN ap_supply_notification_details d ON o.notification_id = d.notification_id AND i.gtin = d.gtin
                        WHERE i.order_id = %s AND i.datamatrix IS NOT NULL
                        """,
                        (order_id,)
                    )
                    codes_from_db = cur.fetchall()
                    for row in codes_from_db:
                        code = row['datamatrix']
                        life_time = row.get('shelf_life_months', '')
                        if not code or len(code) < 16: continue
                        all_rows.append({
                            'DataMatrix': code, 'DataMatrixCode': '', 'Barcode': code[2:16], 'LifeTime': life_time
                        })

                else: # Логика по умолчанию для кодов из API (сценарии "Заказ в ДМ.Код" и "Внешняя система (1С)")
                    logging.info(f"Экспорт для заказа {order_id}. Источник: API. Запрос к 'dmkod_aggregation_details'.")
                    cur.execute(
                        """
                        SELECT 
                            d.api_codes_json, d.production_date, d.expiry_date, snd.shelf_life_months 
                        FROM dmkod_aggregation_details d
                        JOIN orders o ON d.order_id = o.id
                        LEFT JOIN ap_supply_notification_details snd ON o.notification_id = snd.notification_id AND d.gtin = snd.gtin
                        WHERE d.order_id = %s AND d.api_codes_json IS NOT NULL
                        """,
                        (order_id,)
                    )
                    details_to_process = cur.fetchall()
                    for detail in details_to_process:
                        codes = detail.get('api_codes_json', {}).get('codes', [])
                        
                        # --- ИЗМЕНЕНИЕ: Логика определения LifeTime ---
                        life_time_months = ''
                        # 1. Приоритет у поля shelf_life_months
                        if detail.get('shelf_life_months') is not None:
                            life_time_months = detail['shelf_life_months']
                        # 2. Если его нет, считаем по датам (старая логика)
                        else:
                            prod_date = detail.get('production_date')
                            exp_date = detail.get('expiry_date')
                            if prod_date and exp_date:
                                delta = relativedelta(exp_date, prod_date)
                                life_time_months = delta.years * 12 + delta.months

                        for code in codes:
                            if not code or len(code) < 16: continue
                            all_rows.append({
                                'DataMatrix': code,
                                'DataMatrixCode': '',
                                'Barcode': code[2:16],
                                'LifeTime': life_time_months
                            })
        
        if not all_rows:
            return None, None

        df = pd.DataFrame(all_rows)
        report_name = re.sub(r'[^\w]', '_', order_info.get('notes', '') if order_info else '').strip('_')
        
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE orders SET status = 'delta' WHERE id = %s", (order_id,))
            conn.commit()

        return df, report_name

    def import_data_from_external_sw(self, order_id: int, filepath: str):
        """
        Обрабатывает CSV-файл от 'Дельта', обновляет базу данных.
        Принимает путь к файлу.
        """
        df = pd.read_csv(filepath, sep='\t', dtype={'Barcode': str, 'BoxSSCC': str, 'PaletSSCC': str}) # Читаем файл здесь
        df.columns = df.columns.str.strip()
        required_columns = ['DataMatrix', 'Barcode', 'StartDate', 'EndDate', 'BoxSSCC', 'PaletSSCC']
        if not all(col in df.columns for col in required_columns):
            raise ValueError(f'В файле отсутствуют необходимые колонки. Ожидаются: {", ".join(required_columns)}.')

        df['Barcode'] = df['Barcode'].apply(lambda x: '0' + str(x) if isinstance(x, str) and len(x) == 13 else x)
        df['BoxSSCC'] = df['BoxSSCC'].str[-18:]
        df['PaletSSCC'] = df['PaletSSCC'].str[-18:]
        # --- ИСПРАВЛЕНИЕ: Обрабатываем пустые даты ---
        df['StartDate'] = pd.to_datetime(df['StartDate'], format='%Y-%m-%d', errors='coerce').dt.strftime('%Y-%m-%d')
        df['EndDate'] = pd.to_datetime(df['EndDate'], format='%Y-%m-%d', errors='coerce')
        mask = df['EndDate'].notna()
        df.loc[mask, 'EndDate'] = df.loc[mask, 'EndDate'].dt.strftime('%Y-%m-%d')
        df.loc[~mask, 'EndDate'] = None
        logging.debug(f"[Delta Import] After date processing - EndDate unique: {df['EndDate'].unique()}")
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT o.id, o.product_group_id, pg.fias_required, pg.kpp_required, pg.variables_required, o.fias_code, o.kpp
                    FROM orders o
                    LEFT JOIN dmkod_product_groups pg ON o.product_group_id = pg.id
                    WHERE o.id = %s
                """, (order_id,))
                order_info = cur.fetchone()
                if not order_info:
                    raise ValueError(f"Не удалось найти информацию для заказа ID {order_id}")

                # --- ЛОГИРОВАНИЕ: Проверка параметров заказа ---
                logging.debug(
                    f"[Delta Import] order_id={order_id}, product_group_id={order_info.get('product_group_id')}, "
                    f"fias_required={order_info.get('fias_required')}, kpp_required={order_info.get('kpp_required')}, "
                    f"variables_required={order_info.get('variables_required')}"
                )
                # --- КОНЕЦ ЛОГА ---

                # 1. Создание упаковок (короба и паллеты)
                unique_boxes = df[['BoxSSCC']].dropna().drop_duplicates().rename(columns={'BoxSSCC': 'sscc'})
                unique_pallets = df[['PaletSSCC']].dropna().drop_duplicates().rename(columns={'PaletSSCC': 'sscc'})
                
                packages_to_insert = []
                if not unique_boxes.empty:
                    unique_boxes['level'] = 1
                    packages_to_insert.append(unique_boxes)
                if not unique_pallets.empty:
                    unique_pallets['level'] = 2
                    packages_to_insert.append(unique_pallets)

                if packages_to_insert:
                    all_packages_df = pd.concat(packages_to_insert, ignore_index=True)
                    all_packages_df['owner'] = 'delta'
                    
                    box_pallet_map = df[['BoxSSCC', 'PaletSSCC']].dropna().drop_duplicates()
                    box_to_pallet_sscc_map = pd.Series(box_pallet_map.PaletSSCC.values, index=box_pallet_map.BoxSSCC).to_dict()
                    
                    all_packages_df['parent_sscc'] = all_packages_df.apply(
                        lambda row: box_to_pallet_sscc_map.get(row['sscc']) if row['level'] == 1 else None, 
                        axis=1
                    )
                    # --- ИСПРАВЛЕНИЕ: Аргументы dataframe и table_name были перепутаны местами. ---
                    # Правильный порядок: upsert_data_to_db(cursor, dataframe, table_name, pk_column)
                    upsert_data_to_db(cur, all_packages_df, 'packages', 'sscc')
                    
                    cur.execute("""
                        UPDATE packages p_child SET parent_id = p_parent.id
                        FROM packages AS p_parent
                        WHERE p_child.parent_sscc = p_parent.sscc AND p_child.parent_sscc IS NOT NULL;
                    """)
                    cur.execute("UPDATE packages SET parent_sscc = NULL WHERE parent_sscc IS NOT NULL;")

                # 2. Создание товаров (items)
                parsed_dm_data = [parse_datamatrix(dm) for dm in df['DataMatrix']]
                items_df = pd.DataFrame(parsed_dm_data)
                items_df['order_id'] = order_id
                items_df['BoxSSCC'] = df['BoxSSCC']

                box_ssccs_tuple = tuple(df['BoxSSCC'].dropna().unique())
                sscc_to_id_map = {}
                if box_ssccs_tuple:
                    cur.execute("SELECT sscc, id FROM packages WHERE sscc IN %s", (box_ssccs_tuple,))
                    sscc_to_id_map = {row['sscc']: row['id'] for row in cur.fetchall()}
                
                items_df['package_id'] = items_df['BoxSSCC'].map(sscc_to_id_map)
                items_df['package_id'] = items_df['package_id'].astype('object').where(pd.notna(items_df['package_id']), None)
                
                columns_to_save = ['datamatrix', 'gtin', 'serial', 'crypto_part_91', 'crypto_part_92', 'crypto_part_93', 'order_id', 'package_id']
                items_to_upload = items_df[columns_to_save]
                # --- ИСПРАВЛЕНИЕ: Аргументы dataframe и table_name были перепутаны местами. ---
                # Правильный порядок: upsert_data_to_db(cursor, dataframe, table_name, pk_column)
                upsert_data_to_db(cur, items_to_upload, 'items', 'datamatrix')

                # 3. Подготовка данных для delta_result
                df_for_json = df.copy()
                df_for_json.rename(columns={'Barcode': 'gtin', 'StartDate': 'production_date', 'EndDate': 'expiration_date'}, inplace=True)
                # --- ИСПРАВЛЕНИЕ: Приводим NaT к None для корректной группировки ---
                df_for_json['expiration_date'] = df_for_json['expiration_date'].astype('object').where(df_for_json['expiration_date'].notna(), None)
                # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
                
                df_for_json['gtin'] = df_for_json['gtin'].astype(str)
                cur.execute("SELECT gtin, api_id FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL", (order_id,))
                # --- ЛОГИРОВАНИЕ: GTIN из заказа ---
                order_gtins = cur.fetchall()
                logging.debug(f"[Delta Import] GTINs from order {order_id}: {[(r['gtin'], r['api_id']) for r in order_gtins]}")
                # --- КОНЕЦ ЛОГА ---
                gtin_to_printrun_map = {str(row['gtin']): row['api_id'] for row in order_gtins}

                if not gtin_to_printrun_map:
                    raise Exception("Не удалось найти ID тиражей (api_id) в деталях заказа. Убедитесь, что тиражи созданы в API.")

                # Создаем обратную карту: printrun_id -> gtin, чтобы при формировании payload знать GTIN
                printrun_to_gtin_map = {v: k for k, v in gtin_to_printrun_map.items()}

                df_for_json['printrun_id'] = df_for_json['gtin'].map(gtin_to_printrun_map)
                if df_for_json['printrun_id'].isnull().any():
                    unmapped_gtins = df_for_json[df_for_json['printrun_id'].isnull()]['gtin'].unique()
                    raise ValueError(f"Ошибка: Для GTIN(ов) {list(unmapped_gtins)} из файла не найден соответствующий ID тиража в заказе.")

                # Подготавливаем сопоставление GTIN -> description_1 (если в справочнике есть данные)
                # даже если переменные не требуются, это позволяет автоматически добавлять описание при наличии.
                gtin_to_description = {}
                unique_gtins = df_for_json['gtin'].dropna().unique().tolist()
                logging.debug(f"[Delta Import] Unique GTINs from CSV: {unique_gtins}")
                if unique_gtins:
                    cur.execute("SELECT gtin, description_1 FROM products WHERE gtin IN %s", (tuple(unique_gtins),))
                    fetched = cur.fetchall()
                    logging.debug(f"[Delta Import] Fetched from products: {[(r['gtin'], r['description_1'][:50] if r['description_1'] else '') for r in fetched]}")
                    gtin_to_description = {
                        str(row['gtin']): str(row['description_1']) if row.get('description_1') is not None else ''
                        for row in fetched
                    }
                    logging.debug(f"[Delta Import] GTIN->description_1 mapping: {gtin_to_description}")

                logging.debug(f"[Delta Import] df_for_json sample:\n{df_for_json.head().to_string()}")
                grouped_for_api = df_for_json.groupby(['printrun_id', 'production_date', 'expiration_date'], dropna=False).agg({'DataMatrix': list}).reset_index()
                logging.debug(f"[Delta Import] Grouped for API: {len(grouped_for_api)} groups")
                if len(grouped_for_api) == 0:
                    logging.debug(f"[Delta Import] No groups - checking expiration_date types: {df_for_json['expiration_date'].dtype}, unique: {df_for_json['expiration_date'].unique()}")
                
                # --- ИЗМЕНЕНИЕ: Формируем JSON с учетом требований товарной группы ---
                def create_payload(row):
                    attributes = {
                        "production_date": str(row.production_date)
                    }
                    if pd.notna(row.expiration_date):
                        attributes["expiration_date"] = str(row.expiration_date)
                    if order_info.get('fias_required') and order_info.get('fias_code'):
                        attributes['fiasid'] = order_info['fias_code']
                    if order_info.get('kpp_required') and order_info.get('kpp'):
                        attributes['kpp'] = order_info['kpp']

                    codes_list = []
                    gtin_for_row = printrun_to_gtin_map.get(row.printrun_id)

                    for code in row.DataMatrix:
                        cleaned_code = code.replace('\x1d', '')
                        code_obj = {"code": cleaned_code}

                        # Если требуются переменные, парсим их из description_1
                        if order_info.get('variables_required') and gtin_for_row:
                            description = gtin_to_description.get(str(gtin_for_row))
                            if description:
                                try:
                                    # Ожидаем формат "ключ:значение"
                                    key, value = (s.strip() for s in description.split(':', 1))
                                    # Убираем кавычки, если они есть по краям
                                    key = key.strip('"')
                                    value = value.strip('"')
                                    code_obj[key.strip()] = value.strip()
                                    logging.debug(
                                        f"[Delta Import] Added variable to code object: "
                                        f"GTIN={gtin_for_row}, key='{key.strip()}', value='{value.strip()}'"
                                    )
                                except ValueError:
                                    logging.warning(
                                        f"[Delta Import] Некорректный формат переменных в description_1 для GTIN {gtin_for_row}. "
                                        f"Ожидался 'ключ:значение', получено: '{description}'"
                                    )
                        
                        codes_list.append(code_obj)

                    logging.debug(f"[Delta Import] For printrun {row.printrun_id} (GTIN {gtin_for_row}): {len(codes_list)} codes prepared.")

                    return json.dumps({
                        "include": codes_list,
                        "attributes": attributes
                    })


                # Pandas apply может вернуть DataFrame, если функция возвращает Series/словарь.
                # Поэтому формируем список вручную и присваиваем колонку через assign.
                codes_json_list = [create_payload(row) for _, row in grouped_for_api.iterrows()]
                grouped_for_api = grouped_for_api.assign(codes_json=codes_json_list)
                grouped_for_api['order_id'] = order_id
                grouped_for_api['printrun_id'] = grouped_for_api['printrun_id'].astype(int)
                grouped_for_api['production_date'] = pd.to_datetime(grouped_for_api['production_date']).dt.date

                # --- ЛОГИРОВАНИЕ: Показываем как минимум одну сформированную запись для отладки ---
                if not grouped_for_api.empty:
                    sample = grouped_for_api.iloc[0]
                    logging.debug(
                        "[Delta Import] Пример сформированного codes_json (первые 200 символов): %s",
                        str(sample['codes_json'])[:200]
                    )

                delta_result_df = grouped_for_api[['order_id', 'printrun_id', 'production_date', 'codes_json']]
                # --- ИСПРАВЛЕНИЕ: Аргументы dataframe и table_name были перепутаны местами. ---
                # Правильный порядок: upsert_data_to_db(cursor, dataframe, table_name, pk_column)
                upsert_data_to_db(cur, delta_result_df, 'delta_result', ['order_id', 'printrun_id', 'production_date'])

            conn.commit()

    def get_declarator_report_data(self, order_id: int):
        """Формирует и возвращает DataFrame для отчета декларанта."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT notes FROM orders WHERE id = %s", (order_id,))
                order_info = cur.fetchone()
                
                query = """
                        WITH RECURSIVE base_data AS (
                            SELECT 
                                i.datamatrix, 
                                REPLACE(i.datamatrix, CHR(29), '') AS cleaned_datamatrix, 
                                i.gtin, 
                                i.r_id,
                                i.package_id, 
                                p.name AS product_name, 
                                p.description_1, 
                                p.description_2, 
                                p.description_3
                            FROM items i LEFT JOIN products p ON i.gtin = p.gtin
                            WHERE i.order_id = %(order_id)s
                        ),
                        package_hierarchy AS (
                            SELECT 
                                p.id as base_box_id, 
                                p.id as package_id, 
                                p.level, 
                                p.sscc, 
                                p.parent_id
                            FROM packages p WHERE p.level = 1 AND p.id IN (SELECT DISTINCT package_id FROM base_data WHERE package_id IS NOT NULL)
                            UNION ALL
                            SELECT 
                                ph.base_box_id, 
                                p_parent.id as package_id, 
                                p_parent.level, 
                                p_parent.sscc, 
                                p_parent.parent_id
                            FROM package_hierarchy ph JOIN packages p_parent ON ph.parent_id = p_parent.id
                        ),
                        sscc_data AS (
                            SELECT 
                                base_box_id AS id_level_1, 
                                MAX(CASE WHEN level = 1 THEN '00'||sscc END) AS sscc_level_1, 
                                MAX(CASE WHEN level = 2 THEN '00'||sscc END) AS sscc_level_2, 
                                MAX(CASE WHEN level = 3 THEN '00'||sscc END) AS sscc_level_3
                            FROM package_hierarchy GROUP BY base_box_id
                        ),
                        delta_parsed_codes AS (
                            SELECT
                                dr.order_id,
                                (jsonb_array_elements(dr.codes_json->'include')->>'code') AS delta_datamatrix_raw,
                                REPLACE((jsonb_array_elements(dr.codes_json->'include')->>'code'), CHR(29), '') AS delta_datamatrix_cleaned,
                                (dr.codes_json->'attributes'->>'production_date') AS production_date,
                                (dr.codes_json->'attributes'->>'expiration_date') AS expiration_date
                            FROM delta_result dr
                            WHERE dr.order_id = %(order_id)s
                        )
                        SELECT 
                            b.datamatrix, 
                            b.gtin, 
                            b.r_id as "№_pp",
                            SUBSTRING(b.datamatrix for 24) AS dm_part_24, 
                            SUBSTRING(b.datamatrix for 31) AS dm_part_31, 
                            s.sscc_level_1, 
                            s.sscc_level_2, 
                            s.sscc_level_3, 
                            b.product_name, 
                            b.description_1, 
                            b.description_2, 
                            b.description_3,
                            dp.production_date,
                            dp.expiration_date
                        FROM base_data b 
                        LEFT JOIN sscc_data s ON b.package_id = s.id_level_1 
                        LEFT JOIN delta_parsed_codes dp ON b.cleaned_datamatrix = dp.delta_datamatrix_cleaned
                        ORDER BY b.datamatrix;
                        """
                cur.execute(query, {'order_id': order_id})
                report_data = cur.fetchall()
        
        if not report_data:
            return None, None

        df = pd.DataFrame(report_data)
        # --- ИСПРАВЛЕНИЕ: Заменяем устаревший applymap на map ---
        df = df.map(lambda val: val.replace('\x1d', ' ') if isinstance(val, str) else val)
        report_name = re.sub(r'[^\w]', '_', order_info.get('notes', '') if order_info else '').strip('_')
        
        return df, report_name

    def get_all_utilisation_upload_ids(self, order_id: int, order_status: str) -> list:
        """
        Возвращает список всех уникальных `utilisation_upload_id` для заказа.
        """
        all_ids = []
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                if order_status == 'dmkod':
                    cur.execute("SELECT utilisation_upload_id FROM dmkod_aggregation_details WHERE order_id = %s AND utilisation_upload_id IS NOT NULL", (order_id,))
                    all_ids.extend([row[0] for row in cur.fetchall()])
                elif order_status == 'delta':
                    cur.execute("SELECT utilisation_upload_id FROM delta_result WHERE order_id = %s AND utilisation_upload_id IS NOT NULL", (order_id,))
                    all_ids.extend([row[0] for row in cur.fetchall()])
        
        # Возвращаем только уникальные ID
        return list(set(all_ids))

    def get_delta_results_for_upload(self, order_id: int):
        """Возвращает записи из delta_result, которые еще не были выгружены в API."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, printrun_id, codes_json FROM delta_result WHERE order_id = %s AND utilisation_upload_id IS NULL", (order_id,))
                return cur.fetchall()

    def update_delta_result_upload_id(self, result_id: int, upload_id: int):
        """Обновляет utilisation_upload_id для записи в delta_result."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE delta_result SET utilisation_upload_id = %s WHERE id = %s", (upload_id, result_id))
            conn.commit()

    def get_dmkod_details_for_upload(self, order_id: int):
        """Возвращает детали dmkod, которые еще не были выгружены в API."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT d.api_id, d.production_date, d.expiry_date, d.id as detail_id, o.fias_code
                    FROM dmkod_aggregation_details d JOIN orders o ON d.order_id = o.id
                    WHERE d.order_id = %s AND d.api_id IS NOT NULL AND d.utilisation_upload_id IS NULL
                """, (order_id,))
                return cur.fetchall()

    def get_items_for_printing(self, order_id: int):
        """Возвращает список товаров с кодами для печати."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT 
                        i.gtin,
                        i.datamatrix,
                        p.sscc
                    FROM items i
                    LEFT JOIN packages p ON i.package_id = p.id
                    WHERE i.order_id = %s
                    ORDER BY p.sscc, i.datamatrix;
                """
                cur.execute(query, (order_id,))
                return cur.fetchall()

    def get_order_summary(self, order_id: int):
        """
        Возвращает сводную информацию по заказу: имя клиента, количество товаров, заказанных и полученных кодов.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Получаем имя клиента
                cur.execute("SELECT client_name FROM orders WHERE id = %s", (order_id,))
                client_name_result = cur.fetchone()
                if not client_name_result:
                    raise ValueError(f"Заказ с ID {order_id} не найден.")
                
                client_name = client_name_result['client_name']

                # 2. Получаем сводку по кодам
                summary_query = """
                WITH codes_count AS (
                    SELECT COALESCE(SUM(jsonb_array_length(api_codes_json->'codes')), 0) as received_codes
                    FROM dmkod_aggregation_details
                    WHERE order_id = %s
                )
                SELECT 
                    COUNT(DISTINCT gtin) as total_products,
                    SUM(dm_quantity) as ordered_codes,
                    (SELECT received_codes FROM codes_count) as received_codes
                FROM dmkod_aggregation_details WHERE order_id = %s;
                """
                cur.execute(summary_query, (order_id, order_id))
                summary = cur.fetchone()
                summary['client_name'] = client_name
                return summary

    # --- Новые методы для поддержки ApiService ---
    def get_order_by_id(self, order_id: int):
        """Возвращает основные данные заказа по его ID."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
                return cur.fetchone()

    def get_order_for_api_creation(self, order_id: int):
        """Возвращает данные, необходимые для создания заказа в API ДМ.Код."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT o.notes, pg.dm_template, o.client_api_id FROM orders o JOIN dmkod_product_groups pg ON o.product_group_id = pg.id WHERE o.id = %s", (order_id,))
                order_info = cur.fetchone()
                cur.execute("SELECT gtin, dm_quantity FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                products_data = cur.fetchall()
        
        products_df = pd.DataFrame(products_data).groupby('gtin').agg(dm_quantity=('dm_quantity', 'sum')).reset_index()
        order_info['products'] = products_df.to_dict('records')
        return order_info

    def update_order_api_id(self, order_id: int, api_order_id: int):
        """Обновляет api_order_id для заказа."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE orders SET api_order_id = %s WHERE id = %s", (api_order_id, order_id))
            conn.commit()

    def update_order_status(self, order_id: int, status: str):
        """Обновляет текстовый статус (api_status) для заказа."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE orders SET api_status = %s WHERE id = %s", (status, order_id))
            conn.commit()

    def clear_and_sync_printruns(self, order_id: int, gtin_to_run_id: dict):
        """Очищает старые api_id и синхронизирует новые ID активных тиражей из API."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Очищаем все ID для данного заказа
                cur.execute("UPDATE dmkod_aggregation_details SET api_id = NULL WHERE order_id = %s", (order_id,))
                # 2. Обновляем ID для тех, что были найдены в API
                if gtin_to_run_id:
                    for gtin, run_id in gtin_to_run_id.items():
                        cur.execute("UPDATE dmkod_aggregation_details SET api_id = %s WHERE order_id = %s AND gtin = %s", (run_id, order_id, gtin))
            conn.commit()

    def get_details_for_splitting(self, order_id: int, post_processing_mode: str):
        """Возвращает детализацию заказа, сгруппированную или нет, в зависимости от режима постобработки."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if post_processing_mode == "Печать через Bartender":
                    cur.execute("SELECT id, gtin, dm_quantity, api_id FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                else:
                    cur.execute("""
                        SELECT gtin, api_id, SUM(dm_quantity) as dm_quantity
                        FROM dmkod_aggregation_details WHERE order_id = %s
                        GROUP BY gtin, api_id
                    """, (order_id,))
                return cur.fetchall()

    def update_detail_api_id(self, detail_id: int, api_id: int):
        """Обновляет api_id для одной строки детализации по ее ID."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE dmkod_aggregation_details SET api_id = %s WHERE id = %s", (api_id, detail_id))
            conn.commit()

    def update_details_api_id_by_gtin(self, order_id: int, gtin: str, api_id: int):
        """Обновляет api_id для всех строк с указанным GTIN в рамках заказа."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE dmkod_aggregation_details SET api_id = %s WHERE order_id = %s AND gtin = %s", (api_id, order_id, gtin))
            conn.commit()

    def update_detail_utilisation_upload_id(self, detail_id: int, upload_id: int):
        """Обновляет utilisation_upload_id для одной строки детализации по ее ID."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE dmkod_aggregation_details SET utilisation_upload_id = %s WHERE id = %s", (upload_id, detail_id))
            conn.commit()

    def get_details_for_report(self, order_id: int):
        """Возвращает детали, необходимые для создания отчета о нанесении."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, api_id, gtin FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL ORDER BY id",
                    (order_id,)
                )
                return cur.fetchall()

    def get_unique_printrun_ids(self, order_id: int):
        """Возвращает множество уникальных ID тиражей (api_id) для заказа."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT api_id FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL", (order_id,))
                return {item['api_id'] for item in cur.fetchall()}

    def save_downloaded_codes(self, printrun_id: int, codes_json: dict):
        """Сохраняет скачанные коды (в формате JSON) для соответствующего тиража."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE dmkod_aggregation_details SET api_codes_json = %s WHERE api_id = %s",
                    (json.dumps(codes_json), printrun_id)
                )
            conn.commit()

    def create_task_from_order(self, order_id):
        """
        Создает производственную задачу на основе заказа, если сценарий подходит.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Получаем данные заказа и его сценария
                cur.execute("""
                    SELECT o.id, s.scenario_data
                    FROM orders o
                    JOIN ap_marking_scenarios s ON o.scenario_id = s.id
                    WHERE o.id = %s
                """, (order_id,))
                order_data = cur.fetchone()

                if not order_data:
                    raise ValueError(f"Заказ с ID {order_id} не найден.")

                scenario_data = order_data['scenario_data']
                scenario_type = scenario_data.get('type')
                post_processing = scenario_data.get('post_processing')

                # 2. Проверяем условия и формируем задачу
                if scenario_type == 'Ручная агрегация':
                    task_type = 'manual_aggregation'
                    settings_json = {
                        'variant': scenario_data.get('manual_agg_variant'),
                        'clarify_prod_date': scenario_data.get('clarify_prod_date'),
                        'clarify_prod_country': scenario_data.get('clarify_prod_country')
                    }
                elif post_processing == 'Собственный алгоритм':
                    task_type = 'custom_algorithm'
                    settings_json = {
                        'clarify_prod_date': scenario_data.get('clarify_prod_date'),
                        'clarify_prod_country': scenario_data.get('clarify_prod_country')
                    }
                else:
                    # Если сценарий не подходит, просто возвращаем None
                    return None

                # 3. Вставляем новую задачу в production_tasks
                cur.execute("""
                    INSERT INTO production_tasks (order_id, task_type, settings_json, status)
                    VALUES (%s, %s, %s, 'new')
                    RETURNING id;
                """, (order_id, task_type, json.dumps(settings_json)))
                
                task_id = cur.fetchone()['id']
                logging.info(f"Создана производственная задача #{task_id} для заказа #{order_id} с типом '{task_type}'.")
                
                # 4. Обновляем статус заказа
                cur.execute("UPDATE orders SET status = 'task_created' WHERE id = %s", (order_id,))
                logging.info(f"Статус заказа #{order_id} обновлен на 'task_created'.")

            conn.commit()
            return task_id
