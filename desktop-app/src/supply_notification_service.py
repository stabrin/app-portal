# src/supply_notification_service.py

import logging
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import io
import json
from dateutil.relativedelta import relativedelta

class SupplyNotificationService:
    """
    Сервис для управления уведомлениями о поставке.
    """

    def __init__(self, db_connection_func):
        """
        Инициализирует сервис.
        :param db_connection_func: Функция, возвращающая активное подключение к БД клиента.
        """
        self.get_db_connection = db_connection_func

    def get_notifications_with_counts(self):
        """Возвращает список уведомлений, не находящихся в архиве, с подсчетом позиций и ДМ."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Основной запрос для получения уведомлений
                cur.execute("""
                    SELECT 
                        id, scenario_name, client_name, product_groups, 
                        planned_arrival_date, vehicle_number, status, comments
                    FROM ap_supply_notifications
                    WHERE status NOT IN ('В работе', 'В архиве')
                    ORDER BY planned_arrival_date ASC NULLS LAST, id DESC
                """)
                notifications = cur.fetchall()

                # Запрос для подсчета позиций и ДМ
                cur.execute("""
                    SELECT 
                        notification_id,
                        COUNT(DISTINCT gtin) as positions_count,
                        SUM(quantity) as dm_count
                    FROM ap_supply_notification_details
                    GROUP BY notification_id
                """)
                counts = {row['notification_id']: row for row in cur.fetchall()}

                # Объединяем данные
                for n in notifications:
                    count_data = counts.get(n['id'])
                    if count_data:
                        n['positions_count'] = count_data['positions_count']
                        n['dm_count'] = int(count_data['dm_count']) # Убедимся, что это int
                    else:
                        n['positions_count'] = 0
                        n['dm_count'] = 0
                return notifications

    def get_arrival_summary(self):
        """
        Возвращает сгруппированную сводку по поставкам на ближайшие 4 дня.
        """
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                WITH details_agg AS (
                    -- Сначала агрегируем детализацию, чтобы избежать дублирования при JOIN
                    SELECT
                        notification_id,
                        COUNT(DISTINCT gtin) as positions_count,
                        SUM(quantity) as dm_count
                    FROM ap_supply_notification_details
                    GROUP BY notification_id
                )
                SELECT
                    n.client_name,
                    -- Агрегация для СЕГОДНЯ (d0)
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE THEN 1 ELSE 0 END), 0) as d0_ув,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE THEN d.positions_count ELSE 0 END), 0) as d0_поз,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE THEN d.dm_count ELSE 0 END), 0) as d0_дм,
                    -- Агрегация для ЗАВТРА (d1)
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 1 THEN 1 ELSE 0 END), 0) as d1_ув,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 1 THEN d.positions_count ELSE 0 END), 0) as d1_поз,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 1 THEN d.dm_count ELSE 0 END), 0) as d1_дм,
                    -- Агрегация для ПОСЛЕЗАВТРА (d2)
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 2 THEN 1 ELSE 0 END), 0) as d2_ув,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 2 THEN d.positions_count ELSE 0 END), 0) as d2_поз,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 2 THEN d.dm_count ELSE 0 END), 0) as d2_дм,
                    -- Агрегация для +3 ДНЯ (d3)
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 3 THEN 1 ELSE 0 END), 0) as d3_ув,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 3 THEN d.positions_count ELSE 0 END), 0) as d3_поз,
                    COALESCE(SUM(CASE WHEN n.planned_arrival_date = CURRENT_DATE + 3 THEN d.dm_count ELSE 0 END), 0) as d3_дм
                FROM
                    ap_supply_notifications n
                LEFT JOIN details_agg d ON n.id = d.notification_id
                WHERE
                    n.planned_arrival_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '3 days'
                    AND n.status NOT IN ('В работе', 'В архиве')
                GROUP BY
                    n.client_name
                ORDER BY
                    n.client_name;
                """
                cur.execute(query)
                summary = cur.fetchall()
                return summary

    def create_notification(self, data):
        """Создает новое уведомление о поставке."""
        logging.info(f"Создание нового уведомления с данными: {data}")
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ap_supply_notifications (
                        scenario_id, scenario_name, client_api_id, client_local_id, client_name,
                        product_groups, planned_arrival_date, vehicle_number, comments, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Проект')
                    RETURNING id
                    """,
                    (
                        data['scenario_id'], data['scenario_name'], data.get('client_api_id'),
                        data.get('client_local_id'), data['client_name'],
                        json.dumps(data['product_groups']), data['planned_arrival_date'],
                        data['vehicle_number'], data['comments']
                    )
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                logging.info(f"Уведомление успешно создано с ID: {new_id}")
                return new_id

    def update_notification(self, notification_id, data):
        """Обновляет существующее уведомление."""
        with self.get_db_connection() as conn:
            logging.info(f"Обновление уведомления ID: {notification_id} с данными: {data}")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ap_supply_notifications SET
                        product_groups = %s,
                        planned_arrival_date = %s,
                        vehicle_number = %s,
                        comments = %s
                    WHERE id = %s
                    """,
                    (
                        json.dumps(data['product_groups']), data['planned_arrival_date'],
                        data['vehicle_number'], data['comments'], notification_id
                    )
                )
            conn.commit()
            logging.info(f"Уведомление ID: {notification_id} успешно обновлено.")

    def get_notification_by_id(self, notification_id):
        """Получает одно уведомление по ID."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM ap_supply_notifications WHERE id = %s", (notification_id,))
                return cur.fetchone()

    def get_notification_files(self, notification_id):
        """Возвращает список файлов для указанного уведомления."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, filename, file_type, uploaded_at FROM ap_supply_notification_files WHERE notification_id = %s ORDER BY uploaded_at DESC",
                    (notification_id,)
                )
                return cur.fetchall()

    def add_notification_file(self, notification_id, filename, file_data, file_type):
        """Добавляет файл к уведомлению."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ap_supply_notification_files (notification_id, filename, file_data, file_type) VALUES (%s, %s, %s, %s)",
                    (notification_id, filename, file_data, file_type)
                )
            conn.commit()

    def delete_notification_file(self, file_id):
        """Удаляет файл."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ap_supply_notification_files WHERE id = %s", (file_id,))
            conn.commit()

    def get_formalization_template(self):
        """Возвращает шаблон для формализации в виде DataFrame."""
        return pd.DataFrame(columns=['GTIN', 'Кол-во', 'Агрегация', 'Дата производства', 'Срок годности', 'Окончание срока годности'])

    def process_formalized_file(self, notification_id, file_data):
        """Обрабатывает загруженный файл с детализацией."""
        # --- ИЗМЕНЕНИЕ: Читаем все колонки как строки, чтобы избежать авто-преобразования pandas ---
        df = pd.read_excel(io.BytesIO(file_data), dtype=str, engine='openpyxl')
        df.columns = [col.strip().lower() for col in df.columns]

        details_to_insert = []
        for _, row in df.iterrows():
            # 1. Дата производства: если пустая, ставим текущую
            prod_date = pd.to_datetime(row.get('дата производства'), errors='coerce')
            if pd.isna(prod_date):
                prod_date = pd.Timestamp.now()

            # 2. Срок годности и дата окончания
            exp_date = pd.to_datetime(row.get('окончание срока годности'), errors='coerce')
            shelf_life_months = pd.to_numeric(row.get('срок годности'), errors='coerce')

            # Приоритетное правило: если есть "Срок годности", считаем "Окончание срока годности"
            if pd.notna(shelf_life_months):
                exp_date = prod_date + relativedelta(months=int(shelf_life_months))
            # Вторичное правило: если есть "Окончание срока годности", а "Срок годности" пуст, считаем его
            elif pd.notna(exp_date):
                delta = relativedelta(exp_date, prod_date)
                shelf_life_months = delta.years * 12 + delta.months

            # 3. Кол-во и Агрегация с надежным преобразованием в число
            quantity = pd.to_numeric(row.get('кол-во'), errors='coerce')
            if pd.isna(quantity) or quantity > 1000000:
                quantity = 0 # Ставим 0, если не число или больше лимита

            aggregation = pd.to_numeric(row.get('агрегация'), errors='coerce')
            if pd.isna(aggregation):
                aggregation = 0 # Ставим 0, если не число

            details_to_insert.append((
                notification_id,
                str(row.get('gtin', '')),
                int(quantity),
                int(aggregation),
                prod_date.date(),
                int(shelf_life_months) if pd.notna(shelf_life_months) else None,
                exp_date.date() if pd.notna(exp_date) else None
            ))

        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ap_supply_notification_details WHERE notification_id = %s", (notification_id,))
                
                from psycopg2.extras import execute_values
                insert_query = """
                    INSERT INTO ap_supply_notification_details 
                    (notification_id, gtin, quantity, aggregation, production_date, shelf_life_months, expiry_date)
                    VALUES %s
                """
                if details_to_insert:
                    execute_values(cur, insert_query, details_to_insert)
                
                cur.execute("UPDATE ap_supply_notifications SET status = 'Ожидание' WHERE id = %s", (notification_id,))
            conn.commit()
        return len(details_to_insert)

    def get_notification_details(self, notification_id):
        """Возвращает детализированные строки для уведомления."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, gtin, quantity, aggregation, production_date, shelf_life_months, expiry_date FROM ap_supply_notification_details WHERE notification_id = %s ORDER BY id",
                    (notification_id,)
                )
                return cur.fetchall()

    def save_notification_details(self, details_data):
        """Массово обновляет строки детализации."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                from psycopg2.extras import execute_values
                update_query = """
                    UPDATE ap_supply_notification_details SET
                        gtin = data.gtin,
                        quantity = data.quantity,
                        aggregation = data.aggregation,
                        production_date = data.production_date::date,
                        shelf_life_months = data.shelf_life_months,
                        expiry_date = data.expiry_date::date
                    FROM (VALUES %s) AS data(id, gtin, quantity, aggregation, production_date, shelf_life_months, expiry_date)
                    WHERE ap_supply_notification_details.id = data.id;
                """
                execute_values(cur, update_query, details_data)
            conn.commit()

    def archive_notification(self, notification_id):
        """Перемещает уведомление в архив."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE ap_supply_notifications SET status = 'В архиве' WHERE id = %s", (notification_id,))
            conn.commit()