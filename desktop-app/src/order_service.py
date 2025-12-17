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

    def save_order_changes(self, order_id: int, updates: list, notes: str):
        """Сохраняет изменения в детализации и комментарии к заказу в одной транзакции."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Обновляем комментарий в основной таблице orders
                cur.execute("UPDATE orders SET notes = %s WHERE id = %s", (notes, order_id))
                
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
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT notes FROM orders WHERE id = %s", (order_id,))
                order_info = cur.fetchone()
                
                cur.execute(
                    "SELECT api_codes_json, production_date, expiry_date FROM dmkod_aggregation_details WHERE order_id = %s AND api_codes_json IS NOT NULL",
                    (order_id,)
                )
                details_to_process = cur.fetchall()

        if not details_to_process:
            return None, None # Нет данных для экспорта

        all_rows = []
        for detail in details_to_process:
            codes = detail.get('api_codes_json', {}).get('codes', [])
            prod_date = detail.get('production_date')
            exp_date = detail.get('expiry_date')

            life_time_months = ''
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
        """Обрабатывает CSV-файл от 'Дельта', обновляет базу данных."""
        df = pd.read_csv(filepath, sep='\t', dtype={'Barcode': str, 'BoxSSCC': str, 'PaletSSCC': str})
        df.columns = df.columns.str.strip()
        required_columns = ['DataMatrix', 'Barcode', 'StartDate', 'EndDate', 'BoxSSCC', 'PaletSSCC']
        if not all(col in df.columns for col in required_columns):
            raise ValueError(f'В файле отсутствуют необходимые колонки. Ожидаются: {", ".join(required_columns)}.')

        df['Barcode'] = df['Barcode'].apply(lambda x: '0' + str(x) if isinstance(x, str) and len(x) == 13 else x)
        df['BoxSSCC'] = df['BoxSSCC'].str[-18:]
        df['PaletSSCC'] = df['PaletSSCC'].str[-18:]
        df['StartDate'] = pd.to_datetime(df['StartDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')
        df['EndDate'] = pd.to_datetime(df['EndDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')
        
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
                    upsert_data_to_db(cur, 'packages', all_packages_df, 'sscc')
                    
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
                upsert_data_to_db(cur, 'items', items_to_upload, 'datamatrix')

                # 3. Подготовка данных для delta_result
                df_for_json = df.copy()
                df_for_json.rename(columns={'Barcode': 'gtin', 'StartDate': 'production_date', 'EndDate': 'expiration_date'}, inplace=True)
                df_for_json['gtin'] = df_for_json['gtin'].astype(str)
                
                cur.execute("SELECT gtin, api_id FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL", (order_id,))
                gtin_to_printrun_map = {str(row['gtin']): row['api_id'] for row in cur.fetchall()}

                if not gtin_to_printrun_map:
                    raise Exception("Не удалось найти ID тиражей (api_id) в деталях заказа. Убедитесь, что тиражи созданы в API.")

                df_for_json['printrun_id'] = df_for_json['gtin'].map(gtin_to_printrun_map)
                if df_for_json['printrun_id'].isnull().any():
                    unmapped_gtins = df_for_json[df_for_json['printrun_id'].isnull()]['gtin'].unique()
                    raise ValueError(f"Ошибка: Для GTIN(ов) {list(unmapped_gtins)} из файла не найден соответствующий ID тиража в заказе.")

                grouped_for_api = df_for_json.groupby(['printrun_id', 'production_date', 'expiration_date']).agg({'DataMatrix': list}).reset_index()
                
                grouped_for_api['codes_json'] = [
                    json.dumps({
                        "include": [{"code": code.replace('\x1d', '')} for code in row.DataMatrix],
                        "attributes": { "production_date": str(row.production_date), "expiration_date": str(row.expiration_date) }
                    })
                    for row in grouped_for_api.itertuples()
                ]
                grouped_for_api['order_id'] = order_id
                grouped_for_api['printrun_id'] = grouped_for_api['printrun_id'].astype(int)
                grouped_for_api['production_date'] = pd.to_datetime(grouped_for_api['production_date']).dt.date

                delta_result_df = grouped_for_api[['order_id', 'printrun_id', 'production_date', 'codes_json']]
                upsert_data_to_db(cur, 'delta_result', delta_result_df, ['order_id', 'printrun_id', 'production_date'])

            conn.commit()

    def get_declarator_report_data(self, order_id: int):
        """Формирует и возвращает DataFrame для отчета декларанта."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT notes FROM orders WHERE id = %s", (order_id,))
                order_info = cur.fetchone()
                
                query = """
                    WITH RECURSIVE base_data AS (
                        SELECT i.datamatrix, i.gtin, i.package_id, p.name AS product_name, p.description_1, p.description_2, p.description_3
                        FROM items i LEFT JOIN products p ON i.gtin = p.gtin
                        WHERE i.order_id = %(order_id)s
                    ), package_hierarchy AS (
                        SELECT p.id as base_box_id, p.id as package_id, p.level, p.sscc, p.parent_id
                        FROM packages p WHERE p.level = 1 AND p.id IN (SELECT DISTINCT package_id FROM base_data WHERE package_id IS NOT NULL)
                        UNION ALL
                        SELECT ph.base_box_id, p_parent.id as package_id, p_parent.level, p_parent.sscc, p_parent.parent_id
                        FROM package_hierarchy ph JOIN packages p_parent ON ph.parent_id = p_parent.id
                    ), sscc_data AS (
                        SELECT base_box_id AS id_level_1, MAX(CASE WHEN level = 1 THEN sscc END) AS sscc_level_1, MAX(CASE WHEN level = 2 THEN sscc END) AS sscc_level_2, MAX(CASE WHEN level = 3 THEN sscc END) AS sscc_level_3
                        FROM package_hierarchy GROUP BY base_box_id
                    )
                    SELECT b.datamatrix, b.gtin, SUBSTRING(b.datamatrix for 24) AS dm_part_24, SUBSTRING(b.datamatrix for 31) AS dm_part_31, s.sscc_level_1, s.sscc_level_2, s.sscc_level_3, b.product_name, b.description_1, b.description_2, b.description_3
                    FROM base_data b LEFT JOIN sscc_data s ON b.package_id = s.id_level_1 ORDER BY b.datamatrix;
                """
                cur.execute(query, {'order_id': order_id})
                report_data = cur.fetchall()
        
        if not report_data:
            return None, None

        df = pd.DataFrame(report_data)
        df = df.applymap(lambda val: val.replace('\x1d', ' ') if isinstance(val, str) else val)
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
