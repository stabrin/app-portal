# src/supply_notification_service.py

import logging
from dateutil.relativedelta import relativedelta
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import io

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

    def get_all_notifications(self):
        """Возвращает список всех уведомлений о поставке."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM ap_supply_notifications ORDER BY created_at DESC")
                return cur.fetchall()

    def create_notification(self, name, planned_arrival_date):
        """Создает новое уведомление о поставке."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ap_supply_notifications (name, planned_arrival_date, status) VALUES (%s, %s, 'new') RETURNING id",
                    (name, planned_arrival_date)
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                return new_id

    def get_notification_files(self, notification_id):
        """Возвращает список файлов для указанного уведомления."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, filename, file_type, uploaded_at FROM ap_supply_notification_files WHERE notification_id = %s",
                    (notification_id,)
                )
                return cur.fetchall()

    def add_file(self, notification_id, filename, file_data, file_type):
        """Добавляет файл к уведомлению."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ap_supply_notification_files (notification_id, filename, file_data, file_type) VALUES (%s, %s, %s, %s)",
                    (notification_id, filename, file_data, file_type)
                )
                # Обновляем статус уведомления
                if file_type == 'supplier':
                    cur.execute("UPDATE ap_supply_notifications SET status = 'files_uploaded' WHERE id = %s", (notification_id,))
                elif file_type == 'formalized':
                    cur.execute("UPDATE ap_supply_notifications SET status = 'formalized' WHERE id = %s", (notification_id,))
                conn.commit()

    def delete_file(self, file_id):
        """Удаляет файл."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ap_supply_notification_files WHERE id = %s", (file_id,))
                conn.commit()

    def get_formalization_template(self):
        """Возвращает шаблон для формализации в виде DataFrame."""
        return pd.DataFrame(columns=['GTIN', 'Кол-во', 'Агрегация', 'Дата производства', 'Срок годности', 'Окончание срока годности'])

    def process_formalized_file(self, notification_id, file_data):
        """
        Обрабатывает загруженный формализованный файл, очищает старые детали
        и загружает новые.
        """
        try:
            # Указываем, что колонка GTIN всегда должна читаться как текст
            df = pd.read_excel(io.BytesIO(file_data), dtype={'GTIN': str})
            # Приводим названия колонок к нижнему регистру для удобства
            df.columns = [col.strip().lower() for col in df.columns]

            # Проверяем наличие обязательных колонок
            required_cols = {'gtin', 'кол-во'}
            if not required_cols.issubset(df.columns):
                raise ValueError(f"Отсутствуют обязательные колонки. Требуются: 'GTIN', 'Кол-во'")

            details_to_insert = []
            for _, row in df.iterrows():
                # Преобразуем даты, игнорируя ошибки
                prod_date = pd.to_datetime(row.get('дата производства'), errors='coerce')
                exp_date = pd.to_datetime(row.get('окончание срока годности'), errors='coerce')
                # Срок годности в месяцах
                shelf_life_months = pd.to_numeric(row.get('срок годности'), errors='coerce')

                # Логика расчета дат
                if pd.notna(prod_date) and pd.notna(shelf_life_months) and pd.isna(exp_date):
                    exp_date = prod_date + relativedelta(months=int(shelf_life_months))
                
                # Собираем данные для вставки
                details_to_insert.append({
                    'notification_id': notification_id,
                    'gtin': str(row.get('gtin', '')),
                    'quantity': int(row.get('кол-во', 0)),
                    'aggregation': str(row.get('агрегация', '')),
                    'production_date': None if pd.isna(prod_date) else prod_date.date(),
                    'expiry_date': None if pd.isna(exp_date) else exp_date.date(),
                    # product_name пока не используется в шаблоне, но оставим поле в таблице
                    'product_name': str(row.get('наименование', '')) 
                })

            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Удаляем старые детали для этого уведомления
                    cur.execute("DELETE FROM ap_supply_notification_details WHERE notification_id = %s", (notification_id,))
                    logging.info(f"Старые детали для уведомления {notification_id} удалены.")
 
                    # 2. Загружаем новые детали (массовая вставка)
                    from psycopg2.extras import execute_values
                    insert_query = """
                        INSERT INTO ap_supply_notification_details (notification_id, gtin, quantity, aggregation, production_date, expiry_date, product_name)
                        VALUES %s
                    """
                    data_tuples = [(d['notification_id'], d['gtin'], d['quantity'], d['aggregation'], d['production_date'], d['expiry_date'], d['product_name']) for d in details_to_insert]
                    if data_tuples:
                        cur.execute(
                            execute_values(cur, insert_query, data_tuples)
                        )
                    logging.info(f"Загружено {len(df)} новых строк деталей для уведомления {notification_id}.")
                conn.commit() 
            return len(df)
        except Exception as e:
            logging.error(f"Ошибка обработки формализованного файла: {e}")
            raise

    def get_notification_details(self, notification_id):
        """Возвращает детализированные строки для уведомления."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM ap_supply_notification_details WHERE notification_id = %s ORDER BY id",
                    (notification_id,)
                )
                return cur.fetchall()

    def update_notification_detail(self, detail_id, gtin, quantity, aggregation, production_date, expiry_date):
        """
        Обновляет одну строку в деталях уведомления.
        Даты должны приходить в формате 'YYYY-MM-DD' или быть None/пустой строкой.
        """
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Преобразуем пустые строки в None для корректной вставки в БД
                prod_date_or_null = production_date if production_date else None
                exp_date_or_null = expiry_date if expiry_date else None

                cur.execute(
                    """UPDATE ap_supply_notification_details 
                       SET gtin = %s, quantity = %s, aggregation = %s, production_date = %s, expiry_date = %s 
                       WHERE id = %s""",
                    (gtin, quantity, aggregation, prod_date_or_null, exp_date_or_null, detail_id)
                )
                conn.commit()

    def update_notification_name(self, notification_id, new_name):
        """Обновляет имя уведомления."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ap_supply_notifications SET name = %s WHERE id = %s",
                    (new_name, notification_id)
                )
                conn.commit()
    def update_arrival_date(self, notification_id, new_date):
        """Обновляет планируемую дату прибытия."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ap_supply_notifications SET planned_arrival_date = %s WHERE id = %s",
                    (new_date, notification_id)
                )
                conn.commit()

    def delete_notification(self, notification_id):
        """Полностью удаляет уведомление и все связанные с ним данные."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Каскадное удаление настроено в БД, поэтому достаточно удалить основную запись
                cur.execute("DELETE FROM ap_supply_notifications WHERE id = %s", (notification_id,))
                conn.commit()