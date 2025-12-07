import json
import logging
import random
import string
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
        Получает список всех задач, опционально фильтруя по статусу,
        включая имя клиента из связанного заказа.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT
                        pt.id,
                        pt.order_id,
                        o.client_name,
                        pt.task_type AS type,
                        pt.status,
                        pt.created_at,
                        pt.settings_json
                    FROM
                        production_tasks pt
                    JOIN
                        orders o ON pt.order_id = o.id
                """
                if status:
                    query += " WHERE pt.status = %s"
                    cur.execute(query + " ORDER BY pt.created_at DESC", (status,))
                else:
                    cur.execute(query + " ORDER BY pt.created_at DESC")
                return cur.fetchall()

    def get_task(self, task_id):
        """Получает детали одной задачи."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM production_tasks WHERE id = %s", (task_id,))
                return cur.fetchone()

    def get_task_by_order_id(self, order_id):
        """Получает детали задачи по ID заказа."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM production_tasks WHERE order_id = %s", (order_id,))
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
                    INSERT INTO production_tasks (order_id, task_type, status, settings_json)
                    VALUES (%s, %s, 'new', %s)
                    RETURNING id;
                    """,
                    (order_id, task_type, json.dumps(settings))
                )
                new_id = cur.fetchone()[0]
            conn.commit()
            logging.info(f"Создана новая задача #{new_id} для заказа #{order_id}.")
            return new_id

    def generate_employee_passes(self, task_id, employee_count):
        """
        Создает уникальные коды доступа (пропуски) для сотрудников, привязанные к задаче.
        При повторном вызове перезатирает старые пропуски для данной задачи.
        """
        generated_codes = []
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Удаляем старые пропуски для этой задачи, чтобы избежать дублей
                cur.execute("DELETE FROM task_employees WHERE task_id = %s", (task_id,))
                logging.info(f"Удалены старые пропуски для задачи #{task_id}.")

                # 2. Генерируем и вставляем новые
                for _ in range(employee_count):
                    # --- ИЗМЕНЕНИЕ: Генерируем короткий 8-значный код вместо длинного GUID ---
                    # Этого более чем достаточно для уникальности и делает штрихкод короче.
                    while True:
                        try:
                            chars = string.ascii_uppercase + string.digits
                            access_code = ''.join(random.choices(chars, k=8))
                            cur.execute(
                                """
                                INSERT INTO task_employees (task_id, access_code)
                                VALUES (%s, %s)
                                """,
                                (task_id, access_code)
                            )
                            generated_codes.append(access_code)
                            break # Выходим из цикла, если вставка прошла успешно
                        except psycopg2.IntegrityError: # Перехватываем ошибку уникальности
                            logging.warning(f"Сгенерирован дублирующийся код '{access_code}'. Повторная генерация...")
            conn.commit()
        
        logging.info(f"Сгенерировано {len(generated_codes)} новых пропусков для задачи #{task_id}.")
        return generated_codes

    def get_employee_passes_details(self, task_id):
        """
        Получает все данные, необходимые для печати пропусков для задачи.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Получаем информацию о задаче и связанном заказе
                cur.execute("""
                    SELECT
                        o.client_name,
                        o.notes AS container_number
                    FROM production_tasks pt
                    JOIN orders o ON pt.order_id = o.id
                    WHERE pt.id = %s
                """, (task_id,))
                order_info = cur.fetchone()

                if not order_info:
                    return None

                # 2. Получаем все коды доступа для этой задачи
                cur.execute("""
                    SELECT access_code
                    FROM task_employees
                    WHERE task_id = %s
                """, (task_id,))
                passes = cur.fetchall()
                
                order_info['passes'] = [p['access_code'] for p in passes]
                return order_info
