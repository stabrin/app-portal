# src/order_service.py

import logging
import psycopg2
from psycopg2.extras import RealDictCursor

class OrderService:
    """
    Сервис для управления заказами.
    Инкапсулирует всю логику работы с таблицей 'orders' и связанными с ней данными.
    """

    def __init__(self, db_connection_func):
        """
        Инициализирует сервис.
        :param db_connection_func: Функция, возвращающая активное подключение к БД клиента.
        """
        self.get_db_connection = db_connection_func

    def get_orders(self, is_archive=False):
        """Получает список заказов (активных или архивных)."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                status_filter = "status LIKE 'Архив%%'" if is_archive else "status NOT LIKE 'Архив%%'"
                query = f"SELECT id, client_name, order_date, status, notes, api_status FROM orders WHERE {status_filter} ORDER BY id DESC"
                cur.execute(query)
                return cur.fetchall()

    def archive_order(self, order_id, current_status):
        """Перемещает заказ в архив и, если есть, связанное с ним уведомление."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                new_status = f"Архив_{current_status}"
                cur.execute("UPDATE orders SET status = %s WHERE id = %s RETURNING notification_id", (new_status, order_id))
                result = cur.fetchone()
                notification_id = result[0] if result else None
                logging.info(f"Заказ ID {order_id} перемещен в архив. Связанное уведомление ID: {notification_id}")

                if notification_id:
                    cur.execute("UPDATE ap_supply_notifications SET status = 'В архиве' WHERE id = %s", (notification_id,))
                    logging.info(f"Статус уведомления о поставке ID {notification_id} также изменен на 'В архиве'.")
            conn.commit()

    def get_order_scenario(self, order_id):
        """Получает данные сценария для указанного заказа."""
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT s.scenario_data FROM orders o
                    JOIN ap_marking_scenarios s ON o.scenario_id = s.id
                    WHERE o.id = %s
                """, (order_id,))
                result = cur.fetchone()
        return result['scenario_data'] if result else {}