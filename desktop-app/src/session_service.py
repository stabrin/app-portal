# desktop-app/src/session_service.py
import logging
import socket
from datetime import timedelta

class SessionService:
    """
    Сервис для управления рабочими сессиями сотрудников.
    """
    def __init__(self, db_connector):
        self.db = db_connector
        self.session_timeout_minutes = 30

    def start_session(self, employee_token_id, employee_name, task_id):
        """
        Начинает новую рабочую сессию для сотрудника.

        1. Очищает старые, "зависшие" сессии.
        2. Проверяет, нет ли уже активной сессии для данного сотрудника.
        3. Создает новую запись о сессии.

        :param employee_token_id: ID пропуска сотрудника.
        :param employee_name: Имя сотрудника.
        :param task_id: ID задачи, к которой привязан сотрудник.
        :return: ID новой сессии.
        :raises ValueError: Если для сотрудника уже существует активная сессия.
        """
        workstation_id = socket.gethostname()
        logging.info(f"Попытка начать сессию для сотрудника ID {employee_token_id} на {workstation_id}.")

        self._cleanup_stale_sessions()

        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                # Проверка на существующую активную сессию
                cur.execute(
                    """
                    SELECT id, workstation_id FROM ma_work_sessions
                    WHERE employee_token_id = %s AND end_time IS NULL;
                    """,
                    (employee_token_id,)
                )
                existing_session = cur.fetchone()
                if existing_session:
                    session_id, ws_id = existing_session
                    error_msg = (f"Для этого сотрудника уже есть активная сессия (ID: {session_id}) "
                                 f"на рабочей станции '{ws_id}'. Завершите ту сессию перед началом новой.")
                    logging.warning(f"Не удалось начать сессию для токена {employee_token_id}: {error_msg}")
                    raise ValueError(error_msg)

                # Создание новой сессии
                cur.execute(
                    """
                    INSERT INTO ma_work_sessions (employee_token_id, employee_name, order_id, workstation_id)
                    VALUES (%s, %s, %s, %s) RETURNING id;
                    """,
                    (employee_token_id, employee_name, task_id, workstation_id)
                )
                new_session_id = cur.fetchone()[0]
                conn.commit()
                logging.info(f"Начата новая сессия ID {new_session_id} для сотрудника '{employee_name}' (токен ID {employee_token_id}).")
                return new_session_id

    def end_session(self, session_id):
        """
        Завершает указанную рабочую сессию.
        Проставляет время окончания.

        :param session_id: ID сессии для завершения.
        """
        if not session_id:
            logging.warning("Попытка завершить сессию с пустым ID.")
            return

        logging.info(f"Завершение сессии ID {session_id}.")
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ma_work_sessions
                    SET end_time = CURRENT_TIMESTAMP
                    WHERE id = %s AND end_time IS NULL;
                    """,
                    (session_id,)
                )
                conn.commit()
                if cur.rowcount == 0:
                    logging.warning(f"Сессия ID {session_id} не найдена или уже была завершена.")
                else:
                    logging.info(f"Сессия ID {session_id} успешно завершена.")

    def touch_session(self, session_id):
        """
        Обновляет время последней активности для сессии.
        "Касание" сессии, чтобы предотвратить ее автоматическое завершение.

        :param session_id: ID активной сессии.
        """
        if not session_id:
            return

        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ma_work_sessions
                    SET last_activity = CURRENT_TIMESTAMP
                    WHERE id = %s AND end_time IS NULL;
                    """,
                    (session_id,)
                )
                conn.commit()

    def _cleanup_stale_sessions(self):
        """
        Автоматически завершает сессии, которые были неактивны дольше таймаута.
        Время окончания (end_time) устанавливается равным времени последней активности (last_activity).
        """
        logging.info("Запуск очистки устаревших сессий...")
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                timeout_interval = timedelta(minutes=self.session_timeout_minutes)
                cur.execute(
                    """
                    UPDATE ma_work_sessions
                    SET end_time = last_activity
                    WHERE end_time IS NULL AND last_activity < (CURRENT_TIMESTAMP - %s);
                    """,
                    (timeout_interval,)
                )
                stale_sessions_count = cur.rowcount
                conn.commit()
                if stale_sessions_count > 0:
                    logging.info(f"Автоматически завершено {stale_sessions_count} устаревших сессий.")
                else:
                    logging.info("Устаревших сессий не найдено.")
