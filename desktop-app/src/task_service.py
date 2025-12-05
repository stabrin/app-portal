import json
import logging
from .db_connector import get_client_db_connection
from psycopg2.extras import RealDictCursor

class TaskService:
    """
    Сервисный слой для инкапсуляции бизнес-логики, связанной с производственными задачами.
    """
    def __init__(self, user_info):
        self.user_info = user_info

    def _get_connection(self):
        """Возвращает соединение с БД клиента."""
        return get_client_db_connection(self.user_info)

    def get_tasks(self, status=None):
        """
        Получает список всех задач, опционально фильтруя по статусу.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if status:
                    cur.execute("SELECT * FROM production_tasks WHERE status = %s ORDER BY created_at DESC", (status,))
                else:
                    cur.execute("SELECT * FROM production_tasks ORDER BY created_at DESC")
                return cur.fetchall()

    def get_task(self, task_id):
        """Получает детали одной задачи."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM production_tasks WHERE id = %s", (task_id,))
                return cur.fetchone()

    def update_task_status(self, task_id, status):
        """Обновляет статус задачи."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE production_tasks SET status = %s WHERE id = %s", (status, task_id))
            conn.commit()
            logging.info(f"Статус задачи #{task_id} обновлен на '{status}'.")

    def update_task_settings(self, task_id, settings):
        """Обновляет настройки задачи."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE production_tasks SET settings_json = %s WHERE id = %s", (json.dumps(settings), task_id))
            conn.commit()
            logging.info(f"Настройки задачи #{task_id} обновлены.")

    def create_task(self, order_id, task_type, settings):
        """Создает новую производственную задачу."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO production_tasks (order_id, type, status, settings_json)
                    VALUES (%s, %s, 'new', %s)
                    RETURNING id;
                    """,
                    (order_id, task_type, json.dumps(settings))
                )
                new_id = cur.fetchone()[0]
            conn.commit()
            logging.info(f"Создана новая задача #{new_id} для заказа #{order_id}.")
            return new_id
