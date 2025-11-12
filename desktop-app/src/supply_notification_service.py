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
                    WHERE status NOT IN ('В работе', 'В архиве', 'Заказ создан')
                    ORDER BY planned_arrival_date ASC NULLS LAST, id ASC
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
                WITH daily_client_stats AS (
                    -- Сначала агрегируем данные по каждому клиенту и дню
                    SELECT
                        n.client_name,
                        n.planned_arrival_date,
                        COUNT(DISTINCT n.id) as notifications_count,
                        COUNT(DISTINCT CASE WHEN d.gtin IS NOT NULL AND d.gtin != '' THEN d.gtin END) as positions_count,
                        COALESCE(SUM(d.quantity), 0) as dm_count
                    FROM
                        ap_supply_notifications n
                    LEFT JOIN 
                        ap_supply_notification_details d ON n.id = d.notification_id
                    WHERE
                        n.planned_arrival_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '3 days'
                        AND n.status NOT IN ('В работе', 'В архиве')
                    GROUP BY
                        n.client_name, n.planned_arrival_date
                )
                SELECT
                    client_name,
                    -- Агрегация для СЕГОДНЯ (d0)
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE THEN notifications_count ELSE 0 END) as d0_ув,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE THEN positions_count ELSE 0 END) as d0_поз,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE THEN dm_count ELSE 0 END) as d0_дм,
                    -- Агрегация для ЗАВТРА (d1)
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 1 THEN notifications_count ELSE 0 END) as d1_ув,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 1 THEN positions_count ELSE 0 END) as d1_поз,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 1 THEN dm_count ELSE 0 END) as d1_дм,
                    -- Агрегация для ПОСЛЕЗАВТРА (d2)
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 2 THEN notifications_count ELSE 0 END) as d2_ув,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 2 THEN positions_count ELSE 0 END) as d2_поз,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 2 THEN dm_count ELSE 0 END) as d2_дм,
                    -- Агрегация для +3 ДНЯ (d3)
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 3 THEN notifications_count ELSE 0 END) as d3_ув,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 3 THEN positions_count ELSE 0 END) as d3_поз,
                    SUM(CASE WHEN planned_arrival_date = CURRENT_DATE + 3 THEN dm_count ELSE 0 END) as d3_дм
                FROM daily_client_stats
                GROUP BY
                    client_name
                ORDER BY
                    client_name;
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

    def create_order_from_notification(self, notification_id: int):
        """
        Создает или обновляет заказ в таблице 'orders' на основе данных из уведомления о поставке.
        Возвращает кортеж (bool, str) - успех и сообщение.
        """
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Получаем данные уведомления и сценария
                cur.execute("""
                    SELECT 
                        n.id, n.product_groups, n.scenario_id, n.client_api_id, n.client_local_id,
                        n.client_name, n.vehicle_number,
                        s.scenario_data
                    FROM ap_supply_notifications n
                    JOIN ap_marking_scenarios s ON n.scenario_id = s.id
                    WHERE n.id = %s
                """, (notification_id,))
                notification = cur.fetchone()

                if not notification:
                    return False, f"Уведомление с ID {notification_id} не найдено."

                # 2. Проверяем количество товарных групп
                product_groups = notification['product_groups']
                if not product_groups or len(product_groups) > 1:
                    msg = f"Разделите это уведомление на {len(product_groups)} и после этого создайте отдельные заказы для каждой товарной группы."
                    return False, msg

                # 3. Проверяем тип сценария и формируем статус
                scenario_data = notification['scenario_data']
                if scenario_data.get('type') == 'Ручная агрегация':
                    return False, "Создание заказа для сценария 'Ручная агрегация' находится в процессе реализации."
                
                status = 'dmkod' if scenario_data.get('dm_source') == 'Заказ в ДМ.Код' else 'new'
                product_group_id = product_groups[0].get('id')

                # 4. Проверяем, существует ли уже заказ для этого уведомления
                cur.execute("SELECT id FROM orders WHERE notification_id = %s", (notification_id,))
                existing_order = cur.fetchone()

                if existing_order:
                    # ОБНОВЛЕНИЕ СУЩЕСТВУЮЩЕГО ЗАКАЗА
                    order_id = existing_order['id']
                    cur.execute("""
                        UPDATE orders SET
                            client_api_id = %s, client_local_id = %s, client_name = %s, scenario_id = %s,
                            order_date = CURRENT_DATE, notes = %s, status = %s, product_group_id = %s
                        WHERE id = %s;
                    """, (
                        notification['client_api_id'], notification['client_local_id'], notification['client_name'],
                        notification['scenario_id'], notification['vehicle_number'], status, product_group_id,
                        order_id
                    ))
                    # Удаляем старую детализацию перед вставкой новой
                    cur.execute("DELETE FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                    logging.info(f"Обновлен существующий заказ ID {order_id} из уведомления ID {notification_id}. Старая детализация удалена.")
                    message = f"Заказ №{order_id} успешно обновлен на основе уведомления."
                else:
                    # СОЗДАНИЕ НОВОГО ЗАКАЗА
                    cur.execute("""
                        INSERT INTO orders (
                            client_api_id, client_local_id, client_name, scenario_id, notification_id, 
                            order_date, notes, status, product_group_id
                        ) VALUES (%s, %s, %s, %s, %s, CURRENT_DATE, %s, %s, %s)
                        RETURNING id;
                    """, (
                        notification['client_api_id'], notification['client_local_id'], notification['client_name'],
                        notification['scenario_id'], notification['id'], notification['vehicle_number'],
                        status, product_group_id
                    ))
                    order_id = cur.fetchone()['id']
                    logging.info(f"Создан новый заказ с ID {order_id} из уведомления ID {notification_id}.")
                    message = f"Заказ №{order_id} успешно создан на основе уведомления."

                # 5. Переносим детализацию (общая логика для создания и обновления)
                cur.execute("""
                    SELECT gtin, quantity, aggregation, production_date, expiry_date 
                    FROM ap_supply_notification_details 
                    WHERE notification_id = %s
                """, (notification_id,))
                details = cur.fetchall()

                if details:
                    from psycopg2.extras import execute_values
                    details_to_insert = [
                        (order_id, d['gtin'], d['quantity'], d.get('aggregation', 0), d['production_date'], d['expiry_date'])
                        for d in details
                    ]
                    insert_query = """
                        INSERT INTO dmkod_aggregation_details (order_id, gtin, dm_quantity, aggregation_level, production_date, expiry_date)
                        VALUES %s
                    """
                    execute_values(cur, insert_query, details_to_insert)
                    logging.info(f"Перенесено {len(details_to_insert)} строк детализации в заказ ID {order_id}.")

                # 6. Обновляем статус самого уведомления
                cur.execute("UPDATE ap_supply_notifications SET status = 'Заказ создан' WHERE id = %s", (notification_id,))
                logging.info(f"Статус уведомления ID {notification_id} обновлен на 'Заказ создан'.")

            conn.commit()
            return True, message

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

    def get_file_content(self, file_id):
        """
        Возвращает содержимое (в байтах) и имя файла по его ID.
        """
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT filename, file_data FROM ap_supply_notification_files WHERE id = %s",
                    (file_id,)
                )
                file_record = cur.fetchone()
                if not file_record:
                    raise FileNotFoundError(f"Файл с ID {file_id} не найден в базе данных.")
                return file_record['file_data'], file_record['filename']

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