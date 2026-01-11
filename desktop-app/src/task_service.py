import json
import logging
import random
import string
import psycopg2
from .db_connector import get_client_db_connection
from psycopg2.extras import RealDictCursor
from typing import Optional, Dict, Any

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

    def get_task_by_employee_pass(self, access_code, operator_name, workstation_id):
        """
        Проверяет код-пропуск, СОХРАНЯЕТ ФИО ОПЕРАТОРА, и возвращает
        всю необходимую информацию о задаче для начала работы.
        Также создает новую рабочую сессию.
        """
        with self._get_connection() as conn:
            # Все операции в одной транзакции
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                try:
                    # 1. Найти сотрудника и связанную задачу по коду-пропуску
                    cur.execute(
                        """
                        SELECT te.id, tet.task_id, te.employee_token_id
                        FROM task_employees te
                        JOIN task_employee_tokens tet ON te.employee_token_id = tet.id
                        WHERE te.access_code = %s
                        """,
                        (access_code,)
                    )
                    employee_data = cur.fetchone()

                    if not employee_data:
                        return {'is_valid': False, 'error': 'Код-пропуск не найден'}

                    employee_id = employee_data['id']
                    employee_token_id = employee_data['employee_token_id']
                    task_id = employee_data['task_id']

                    # 2. Обновить ФИО сотрудника
                    cur.execute(
                        "UPDATE task_employee_tokens SET employee_name = %s WHERE id = %s",
                        (operator_name, employee_token_id)
                    )
                    logging.info(f"Сохранено ФИО '{operator_name}' для токена #{employee_token_id}")

                    # 3. Получить основную информацию о задаче и заказе
                    cur.execute(
                        """
                        SELECT
                           pt.id AS task_id,
                           pt.task_type,
                           pt.status,
                           pt.settings_json,
                           o.id AS order_id,
                           o.client_name,
                           CASE
                                WHEN o.client_api_id IS NOT NULL THEN 'api_' || o.client_api_id::text || '_' || o.client_name
                                WHEN o.client_local_id IS NOT NULL THEN 'local_' || o.client_local_id::text || '_' || o.client_name
                               ELSE NULL
                           END as client_id
                       FROM
                           production_tasks pt
                       JOIN
                           orders o ON pt.order_id = o.id
                       WHERE
                           pt.id = %s
                       """,
                        (task_id,)
                    )
                    task_info = cur.fetchone()

                    if not task_info:
                        return {'is_valid': False, 'error': 'Задача, связанная с пропуском, не найдена'}
                    
                    order_id = task_info['order_id']

                    # 4. Проверить, что задача в статусе 'in_progress'
                    if task_info['status'] != 'in_progress':
                        return {'is_valid': False, 'error': f"Задача не находится в статусе 'in_progress' (текущий статус: {task_info['status']})"}

                    # 5. Стартуем сессию (проверка на дубли внутри)
                    session_id = self.start_session(
                        employee_token_id=employee_token_id,
                        employee_name=operator_name,
                        task_id=task_id,
                        workstation_id=workstation_id,
                        cursor=cur  # Передаем курсор для выполнения в той же транзакции
                    )

                    # 6. Получить список уникальных GTIN для этой задачи
                    cur.execute(
                        "SELECT DISTINCT gtin FROM task_datamatrix_pool WHERE task_id = %s ORDER BY gtin",
                        (task_id,)
                    )
                    gtins_data = cur.fetchall()
                    available_gtins = [row['gtin'] for row in gtins_data]

                    if not available_gtins:
                        # Откатывать сессию не нужно, но нужно вернуть ошибку
                        return {'is_valid': False, 'error': 'В пуле кодов для этой задачи нет доступных GTIN.'}

                    # 7. Собрать результат
                    result = {
                        'is_valid': True,
                        'session_id': session_id,
                        'employee_id': employee_id,
                        'gtins': available_gtins,
                        **task_info # Распаковываем словарь с информацией о задаче
                    }
                    
                    # 8. Зафиксировать изменения в БД
                    conn.commit()

                    return result
                
                except psycopg2.Error as e:
                    conn.rollback()
                    # Возвращаем специфичную ошибку для UI
                    if "SESSION_EXISTS" in str(e):
                        logging.warning(f"Попытка повторного входа с пропуском {access_code}. Активная сессия уже существует.")
                        return {'is_valid': False, 'error': 'Для данного пропуска уже существует активная сессия. Завершите ее перед новым входом.'}
                    logging.error(f"Ошибка при проверке пропуска и создании сессии: {e}")
                    raise

    def start_session(self, employee_token_id, employee_name, task_id, workstation_id, cursor):
        """
        Создает новую рабочую сессию. Проверяет наличие уже активной сессии.
        Использует переданный курсор для выполнения в рамках существующей транзакции.
        """
        # 1. Проверить, нет ли уже активной сессии для этого сотрудника
        cursor.execute(
            "SELECT id FROM task_work_sessions WHERE employee_token_id = %s AND end_time IS NULL",
            (employee_token_id,)
        )
        active_session = cursor.fetchone()
        if active_session:
            # Используем специальное исключение или код ошибки, чтобы обработать в UI
            raise psycopg2.Error("SESSION_EXISTS: Активная сессия для этого сотрудника уже существует.")

        # 2. Создать новую сессию
        cursor.execute(
            """
            INSERT INTO task_work_sessions (employee_token_id, employee_name, task_id, workstation_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
            """,
            (employee_token_id, employee_name, task_id, workstation_id)
        )
        session_id = cursor.fetchone()['id']
        logging.info(f"Создана новая сессия #{session_id} для сотрудника #{employee_token_id} на рабочем месте '{workstation_id}'.")
        return session_id

    def update_session_activity(self, session_id):
        """Обновляет время последней активности для сессии."""
        if not session_id:
            return
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE task_work_sessions SET last_activity = CURRENT_TIMESTAMP WHERE id = %s AND end_time IS NULL",
                        (session_id,)
                    )
                conn.commit()
        except Exception as e:
            # Эту ошибку можно игнорировать, чтобы не прерывать основную операцию
            logging.warning(f"Не удалось обновить активность сессии #{session_id}: {e}")
            
    def close_session(self, session_id):
        """Корректно закрывает рабочую сессию."""
        if not session_id:
            return
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE task_work_sessions SET end_time = CURRENT_TIMESTAMP WHERE id = %s",
                        (session_id,)
                    )
                conn.commit()
            logging.info(f"Сессия #{session_id} была закрыта.")
        except Exception as e:
            logging.error(f"Ошибка при закрытии сессии #{session_id}: {e}")

    def get_active_sessions(self):
        """Возвращает список активных сессий."""
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT id, employee_name, task_id, workstation_id, start_time
                        FROM task_work_sessions
                        WHERE end_time IS NULL
                        ORDER BY start_time DESC
                        """
                    )
                    return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка при получении активных сессий: {e}")
            return []

    def close_inactive_sessions(self):
        """Закрывает все сессии, неактивные более 30 минут."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE task_work_sessions
                        SET end_time = last_activity + INTERVAL '30 minutes'
                        WHERE end_time IS NULL AND last_activity < CURRENT_TIMESTAMP - INTERVAL '30 minutes'
                        RETURNING id;
                        """
                    )
                    closed_sessions = [row[0] for row in cur.fetchall()]
                    if closed_sessions:
                        logging.info(f"Автоматически закрыты неактивные сессии: {closed_sessions}")
                conn.commit()
        except Exception as e:
            logging.error(f"Ошибка при автоматическом закрытии неактивных сессий: {e}")

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

                raw_data = []
                # 4. Извлечь коды и GTIN в зависимости от источника
                if dm_source == 'Заказ в ДМ.Код':
                    cur.execute("SELECT gtin, api_codes_json FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                    all_details = cur.fetchall()
                    for detail in all_details:
                        gtin = detail.get('gtin')
                        codes_json = detail.get('api_codes_json')
                        if not gtin or not codes_json:
                            continue
                        if isinstance(codes_json, str):
                            try:
                                codes_json = json.loads(codes_json)
                            except json.JSONDecodeError:
                                logging.warning(f"Не удалось распарсить JSON с кодами для GTIN {gtin}.")
                                continue
                        codes = codes_json.get('codes', [])
                        for code in codes:
                            raw_data.append({'gtin': gtin, 'datamatrix': code})

                elif dm_source == 'Файлы клиента (csv, txt)':
                    cur.execute("SELECT gtin, datamatrix FROM items WHERE order_id = %s AND datamatrix IS NOT NULL AND gtin IS NOT NULL", (order_id,))
                    fetched_items = cur.fetchall()
                    for item in fetched_items:
                        raw_data.append({'gtin': item['gtin'], 'datamatrix': item['datamatrix']})
                else:
                    logging.warning(f"Неизвестный или неподдерживаемый dm_source ('{dm_source}') для задачи #{task_id}. Пул не будет наполнен.")
                    return

                if not raw_data:
                    logging.warning(f"Не найдено кодов для наполнения пула для задачи #{task_id}.")
                    return

                # Обогащение данных
                unique_gtins = sorted(list(set(item['gtin'] for item in raw_data)))
                gtin_to_product_info = {}
                if unique_gtins:
                    cur.execute("SELECT gtin, name, description_1, description_2, description_3 FROM products WHERE gtin = ANY(%s)", (unique_gtins,))
                    for row in cur.fetchall():
                        gtin_to_product_info[row['gtin']] = row

                insert_data = []
                gtin_counters = {gtin: 0 for gtin in unique_gtins}
                for item in raw_data:
                    gtin = item['gtin']
                    product_info = gtin_to_product_info.get(gtin, {})
                    gtin_index = unique_gtins.index(gtin)
                    dm_index = gtin_counters[gtin]
                    serial_number = f"{task_id}_{gtin_index}_{dm_index}"
                    gtin_counters[gtin] += 1

                    insert_data.append((task_id, gtin, item['datamatrix'], 'available', product_info.get('name'), product_info.get('description_1'), product_info.get('description_2'), product_info.get('description_3'), serial_number))

                if not insert_data:
                    logging.warning(f"Не найдено данных для наполнения пула для задачи #{task_id}.")
                    return

                logging.info(f"Найдено {len(insert_data)} записей для вставки в пул для задачи #{task_id}.")

                # 5. Очистить старые записи для этой задачи (для идемпотентности)
                cur.execute("DELETE FROM task_datamatrix_pool WHERE task_id = %s", (task_id,))
                logging.info(f"Старые записи в пуле для задачи #{task_id} удалены.")

                # 6. Вставить новые записи
                from psycopg2.extras import execute_values
                unique_insert_data = list(set(insert_data)) # Удаляем полные дубликаты (task_id, gtin, code, status)
                
                execute_values(
                    cur,
                    "INSERT INTO task_datamatrix_pool (task_id, gtin, datamatrix, status, name, description_1, description_2, description_3, serial_number) VALUES %s",
                    unique_insert_data
                )
                
                logging.info(f"Успешно вставлено {len(unique_insert_data)} кодов в task_datamatrix_pool для задачи #{task_id}.")

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
                # 1. Удаляем старые пропуски и токены для этой задачи, чтобы избежать дублей
                cur.execute(
                    "DELETE FROM task_employees WHERE employee_token_id IN (SELECT id FROM task_employee_tokens WHERE task_id = %s)",
                    (task_id,)
                )
                cur.execute("DELETE FROM task_employee_tokens WHERE task_id = %s", (task_id,))
                logging.info(f"Удалены старые токены и пропуски для задачи #{task_id}.")

                # 2. Генерируем и вставляем новые токены и пропуски
                for _ in range(employee_count):
                    # Создаем токен
                    cur.execute(
                        "INSERT INTO task_employee_tokens (task_id) VALUES (%s) RETURNING id",
                        (task_id,)
                    )
                    token_id = cur.fetchone()[0]
                    
                    # --- ИЗМЕНЕНИЕ: Генерируем короткий 8-значный код вместо длинного GUID ---
                    # Этого более чем достаточно для уникальности и делает штрихкод короче.
                    while True:
                        try:
                            chars = string.ascii_uppercase + string.digits
                            access_code = ''.join(random.choices(chars, k=8))
                            cur.execute(
                                """
                                INSERT INTO task_employees (employee_token_id, access_code)
                                VALUES (%s, %s)
                                """,
                                (token_id, access_code)
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
                    SELECT te.access_code, tet.employee_name
                    FROM task_employees te
                    JOIN task_employee_tokens tet ON te.employee_token_id = tet.id
                    WHERE tet.task_id = %s
                """, (task_id,))
                passes = cur.fetchall()
                
                order_info['passes'] = passes
                return order_info

    def get_first_datamatrix_for_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает первую доступную запись из task_datamatrix_pool для задачи."""
        with get_client_db_connection(self.user_info) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM task_datamatrix_pool 
                    WHERE task_id = %s AND status = 'available' 
                    ORDER BY id LIMIT 1
                """, (task_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_gtins_for_task(self, task_id: int) -> list[dict[str, Any]]:
        """
        Возвращает список уникальных GTIN и их наименований для указанной задачи.
        Используется для построения диалога сопоставления.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Используем DISTINCT ON (gtin) чтобы получить одну запись для каждого GTIN.
                # ORDER BY gtin, name гарантирует, что мы получим предсказуемый результат,
                # если для одного GTIN есть несколько разных имен (хотя это маловероятно).
                cur.execute(
                    """
                    SELECT DISTINCT ON (gtin) gtin, name
                    FROM task_datamatrix_pool
                    WHERE task_id = %s
                    ORDER BY gtin, name;
                    """,
                    (task_id,)
                )
                return cur.fetchall()
