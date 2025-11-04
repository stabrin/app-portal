# src/supply_notification_service.py

import logging
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
        return pd.DataFrame(columns=['gtin', 'product_name', 'quantity'])

    def process_formalized_file(self, notification_id, file_data):
        """
        Обрабатывает загруженный формализованный файл, очищает старые детали
        и загружает новые.
        """
        try:
            df = pd.read_excel(io.BytesIO(file_data))
            # Проверяем наличие обязательных колонок
            required_cols = {'gtin', 'product_name', 'quantity'}
            if not required_cols.issubset(df.columns):
                raise ValueError(f"Отсутствуют обязательные колонки. Требуются: {', '.join(required_cols)}")

            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Удаляем старые детали для этого уведомления
                    cur.execute("DELETE FROM ap_supply_notification_details WHERE notification_id = %s", (notification_id,))
                    logging.info(f"Старые детали для уведомления {notification_id} удалены.")

                    # 2. Загружаем новые детали
                    for _, row in df.iterrows():
                        cur.execute(
                            """
                            INSERT INTO ap_supply_notification_details (notification_id, gtin, product_name, quantity)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (notification_id, str(row['gtin']), str(row['product_name']), int(row['quantity']))
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
                    "SELECT id, gtin, product_name, quantity FROM ap_supply_notification_details WHERE notification_id = %s ORDER BY id",
                    (notification_id,)
                )
                return cur.fetchall()

    def update_notification_detail(self, detail_id, gtin, product_name, quantity):
        """Обновляет одну строку в деталях уведомления."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ap_supply_notification_details SET gtin = %s, product_name = %s, quantity = %s WHERE id = %s",
                    (gtin, product_name, quantity, detail_id)
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