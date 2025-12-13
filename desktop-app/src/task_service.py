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

    def _populate_datamatrix_pool(self, task_id, conn):
        """
        Наполняет пул кодов DataMatrix для задачи.
        Вызывается при переводе задачи в статус 'in_progress'.
        Все операции выполняются с переданным соединением 'conn'.
        """
        logging.info(f"Начало наполнения пула DataMatrix для задачи #{task_id}.")
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                # 1. Получить order_id для данной задачи
                cur.execute("SELECT order_id FROM production_tasks WHERE id = %s", (task_id,))
                task_data = cur.fetchone()
                if not task_data:
                    logging.error(f"Задача #{task_id} не найдена. Наполнение пула прервано.")
                    raise ValueError(f"Задача #{task_id} не найдена.")
                order_id = task_data['order_id']

                # 2. Получить ID сценария из заказа
                cur.execute("SELECT scenario_id FROM orders WHERE id = %s", (order_id,))
                order_data = cur.fetchone()
                scenario_id = order_data['scenario_id'] if order_data and order_data.get('scenario_id') else None

                if not scenario_id:
                    logging.warning(f"Для заказа #{order_id} не указан ID сценария. Пул не будет наполнен.")
                    return

                # 3. Получить детали сценария и извлечь dm_source
                cur.execute("SELECT scenario_data FROM public.ap_marking_scenarios WHERE id = %s", (scenario_id,))
                scenario_data_row = cur.fetchone()
                scenario_data = scenario_data_row['scenario_data'] if scenario_data_row and scenario_data_row.get('scenario_data') else {}
                
                if isinstance(scenario_data, str):
                    try:
                        scenario_data = json.loads(scenario_data)
                    except json.JSONDecodeError:
                        scenario_data = {}

                dm_source = scenario_data.get('dm_source')
                logging.info(f"Для заказа #{order_id} (сценарий ID: {scenario_id}) источник КМ: '{dm_source}'")

                codes_to_insert = []
                # 4. Извлечь коды в зависимости от источника
                if dm_source == 'Заказ в ДМ.Код':
                    cur.execute("SELECT api_codes_json FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                    all_details = cur.fetchall()
                    for detail in all_details:
                        if detail['api_codes_json']:
                            codes_to_insert.extend(detail['api_codes_json'])
                elif dm_source == 'Файлы клиента (csv, txt)':
                    cur.execute("SELECT datamatrix FROM items WHERE order_id = %s AND datamatrix IS NOT NULL", (order_id,))
                    fetched_codes = cur.fetchall()
                    codes_to_insert.extend([row['datamatrix'] for row in fetched_codes])
                else:
                    logging.warning(f"Неизвестный или неподдерживаемый dm_source ('{dm_source}') для задачи #{task_id}. Пул не будет наполнен.")
                    return # Не считаем это ошибкой, просто выходим

                if not codes_to_insert:
                    logging.warning(f"Не найдено кодов для наполнения пула для задачи #{task_id}.")
                    return

                logging.info(f"Найдено {len(codes_to_insert)} кодов для задачи #{task_id}.")

                # 5. Очистить старые записи для этой задачи (для идемпотентности)
                cur.execute("DELETE FROM task_datamatrix_pool WHERE task_id = %s", (task_id,))
                logging.info(f"Старые записи в пуле для задачи #{task_id} удалены.")

                # 6. Вставить новые коды
                from psycopg2.extras import execute_values
                insert_data = [(task_id, code, 'available') for code in set(codes_to_insert)] # Используем set для удаления дублей
                
                execute_values(
                    cur,
                    "INSERT INTO task_datamatrix_pool (task_id, datamatrix, status) VALUES %s",
                    insert_data
                )
                
                logging.info(f"Успешно вставлено {len(insert_data)} кодов в task_datamatrix_pool для задачи #{task_id}.")

            except Exception as e:
                logging.error(f"Критическая ошибка при наполнении пула DataMatrix для задачи #{task_id}: {e}")
                raise # Перевыбрасываем исключение, чтобы транзакция откатилась

    def update_task_status(self, task_id, status):
        """Обновляет статус задачи и запускает связанные процессы."""
        with self._get_connection() as conn:
            # Все операции выполняются в одной транзакции
            with conn.cursor() as cur:
                cur.execute("UPDATE production_tasks SET status = %s WHERE id = %s", (status, task_id))
            
            # Если задача переводится в работу, наполняем пул кодов
            if status == 'in_progress':
                self._populate_datamatrix_pool(task_id, conn)

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
