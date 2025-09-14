import json
from datetime import datetime
from app.db import get_db_connection
from typing import Optional
import redis
from psycopg2.extras import RealDictCursor, execute_values

import re

from .state_service import state_manager

# --- Управляющие команды ---
CMD_COMPLETE_UNIT = "CMD_COMPLETE_UNIT"  # Завершить текущую единицу (набор/короб)
CMD_CANCEL_UNIT = "CMD_CANCEL_UNIT"      # Отменить сборку текущей единицы
CMD_LOGOUT = "CMD_LOGOUT"                # Выйти из системы (дублирует бейдж)

# Символ-разделитель групп в коде DataMatrix, непечатаемый (ASCII 29)
GS_SEPARATOR = '\x1d'

def _is_sscc(code: str) -> bool:
    """Проверяет, является ли код кодом SSCC (18 цифр)."""
    return code.isdigit() and len(code) == 18

def _get_senior_token(order_id: int) -> Optional[str]:
    """Получает токен доступа старшего смены (первый созданный для заказа)."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT access_token FROM ma_employee_tokens WHERE order_id = %s ORDER BY id ASC LIMIT 1",
                (order_id,)
            )
            result = cur.fetchone()
            return str(result[0]) if result else None
    except Exception as e:
        print(f"ОШИБКА в _get_senior_token: {e}")
        return None
    finally:
        if conn:
            conn.close()

def process_scan(work_session_id: int, order_info: dict, scanned_code: str) -> dict:
    """
    Основная точка входа для обработки сканирования.
    Создает экземпляр ScanProcessor, обрабатывает код и возвращает результат.
    """
    # --- Предварительная обработка кода ---
    # Определяем, является ли отсканированный код командой.
    is_command = scanned_code in [CMD_COMPLETE_UNIT, CMD_CANCEL_UNIT, CMD_LOGOUT]

    # Если это не команда, то это код данных (DM, SSCC).
    # Некоторые сканеры заменяют непечатаемый символ GS (ASCII 29) на пробел.
    # Ранее здесь была замена пробелов на GS, но это могло приводить к ошибкам,
    # если в коде были легитимные пробелы. Теперь мы полностью полагаемся на
    # новую логику фронтенда, которая гарантированно присылает корректный символ GS (\x1d).
    # .strip() убирает случайные пробелы/переводы строк по краям, которые может добавить сканер.
    if not is_command:
        scanned_code = scanned_code.strip()

    try:
        processor = ScanProcessor(work_session_id, order_info)
        result = processor.process(scanned_code)
        return result
    except redis.exceptions.ConnectionError as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к Redis. {e}")
        # Возвращаем стандартизированный ответ об ошибке, который будет корректно обработан на фронтенде
        return {
            "status": "error", 
            "message": "Критическая ошибка: Сервис состояний недоступен. Обратитесь к администратору.",
            "session": None # Сессии нет, т.к. Redis не работает
        }
    except Exception as e:
        # Перехватываем ЛЮБУЮ другую непредвиденную ошибку, чтобы избежать падения сервера (ошибка 500)
        import traceback
        print(f"НЕПРЕДВИДЕННАЯ ОШИБКА в process_scan: {e}\n{traceback.format_exc()}")
        # Возвращаем пользователю общее, но информативное сообщение об ошибке
        return {
            "status": "error",
            "message": f"Произошла внутренняя ошибка сервера. Пожалуйста, сообщите администратору. (Тип ошибки: {type(e).__name__})",
            "session": None # Не можем доверять состоянию сессии в случае ошибки
        }

class ScanProcessor:
    def __init__(self, work_session_id, order_info):
        self.work_session_id = work_session_id
        self.order = order_info
        self.senior_token = None # Ленивая загрузка токена старшего
        
        self.employee_token_id = self._get_token_id_from_session()
        if not self.employee_token_id:
            raise ValueError("Критическая ошибка: не удалось определить пропуск по текущей рабочей сессии.")

        # Получаем сессию из Redis по ID пропуска (состояние привязано к пропуску)
        self.session = state_manager.get_state(self.employee_token_id)
        if not self.session:
            # Если сессии нет, создаем начальное состояние и сохраняем его
            self.session = self._get_initial_state()
            self._save_state()

    def _get_token_id_from_session(self) -> Optional[int]:
        """Получает ID физического пропуска (employee_token_id) из рабочей сессии."""
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT employee_token_id FROM ma_work_sessions WHERE id = %s;", (self.work_session_id,))
                result = cur.fetchone()
                return result[0] if result else None
        except Exception as e:
            print(f"ОШИБКА в _get_token_id_from_session: {e}")
            return None
        finally:
            if conn:
                conn.close()


    def _validate_data_code(self, code: str) -> tuple[bool, str]:
        """
        Проверяет код на валидность (отсутствие кириллицы, мусорных символов).
        Возвращает (is_valid, error_message).
        """
        # 1. Проверка на кириллицу - самая частая ошибка из-за раскладки.
        if re.search('[а-яА-Я]', code):
            return False, "Ошибка: Код содержит кириллические символы. Проверьте раскладку клавиатуры."

        # 2. Проверка на наличие "мусорных" непечатаемых символов.
        # Разрешаем только сам GS (29), Tab (9), LF (10), CR (13).
        for char in code:
            char_code = ord(char)
            if char_code < 32 and char_code not in [9, 10, 13, 29]:
                return False, f"Ошибка: Код содержит недопустимый управляющий символ (код {char_code}). Возможно, произошел сбой сканера."

        # Если все проверки пройдены
        return True, ""

    def _get_initial_state(self):
        """Определяет начальное состояние на основе настроек заказа."""
        # Используем `or []` для обработки случая, когда из БД приходит None
        # вместо отсутствующего ключа. Это делает код более устойчивым.
        levels = self.order.get('aggregation_levels') or []
        next_step = levels[0] if levels else None
        
        return {
            "status": "IDLE",
            "payload": {
                "current_unit": {
                    "type": None,
                    "items": []
                },
                "next_step": next_step
            }
        }

    def _save_state(self):
        """Сохраняет текущее состояние сессии в Redis."""
        state_manager.set_state(
            self.employee_token_id, 
            self.session['status'], 
            self.session['payload']
        )

    def _is_code_already_used_as_child(self, code: str) -> bool:
        """Проверяет, был ли код использован как вложение (child) в этом заказе."""
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM ma_aggregations WHERE child_code = %s AND order_id = %s LIMIT 1;",
                    (code, self.order['id'])
                )
                return cur.fetchone() is not None
        except Exception as e:
            print(f"ОШИБКА в _is_code_already_used_as_child: {e}")
            # В случае ошибки БД безопаснее считать, что код уже используется,
            # чтобы предотвратить запись некорректных данных.
            return True
        finally:
            if conn:
                conn.close()

    def _is_code_already_used_as_parent(self, code: str) -> bool:
        """Проверяет, был ли код использован как упаковка (parent) в этом заказе."""
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM ma_aggregations WHERE parent_code = %s AND order_id = %s LIMIT 1;",
                    (code, self.order['id'])
                )
                return cur.fetchone() is not None
        except Exception as e:
            print(f"ОШИБКА в _is_code_already_used_as_parent: {e}")
            return True
        finally:
            if conn:
                conn.close()

    def process(self, scanned_code):
        """Главный метод обработки сканирования."""
        status = self.session.get('status')

        # --- Обработка состояния блокировки ---
        if status == 'LOCKED':
            result = self._handle_unlock(scanned_code)
            return result

        # --- Обработка команд ---
        if scanned_code == CMD_LOGOUT:
            # Команда на выход. Возвращаем специальный ответ,
            # который фронтенд обработает и выполнит редирект.
            return {
                "status": "command",
                "command": "logout",
                "message": "Завершение смены...",
                "session": self.session
            }

        if scanned_code == CMD_CANCEL_UNIT:
            # Если мы в процессе сборки - отменяем текущую операцию в сессии.
            if status != 'IDLE':
                result = self._handle_cancel()
                self._save_state()
                return result
            # Если мы в состоянии IDLE - отменяем последнюю сохраненную в БД операцию.
            else:
                result = self._handle_undo_last_save()
                # Состояние не меняется и не сохраняется, т.к. мы уже в IDLE.
                return result

        if scanned_code == CMD_COMPLETE_UNIT:
            result = self._complete_unit_from_command()
            # Состояние сохраняется внутри, т.к. там есть ветвление
            return result

        # --- Обработка кодов данных ---
        if status == 'IDLE':
            result = self._start_new_unit(scanned_code)
        elif status in ['AGGREGATING_SET', 'AGGREGATING_BOX']:
            result = self._add_to_unit(scanned_code)
        else:
            result = self._build_error_response("Неожиданное действие. Система находится в неизвестном состоянии.")

        # --- Новая логика: блокировка при ошибке ---
        if result.get("status") == "error":
            self._lock_system()
            result['message'] += " \n\nСИСТЕМА ЗАБЛОКИРОВАНА. Требуется сканирование пропуска старшего смены."
        
        self._save_state()
        return result

    def _lock_system(self):
        """Переводит систему в состояние блокировки."""
        if self.session.get('status') == 'LOCKED':
            return # Уже заблокировано

        # Сохраняем состояние до блокировки, чтобы можно было к нему вернуться
        self.session['previous_status'] = self.session.get('status', 'IDLE')
        self.session['previous_payload'] = self.session.get('payload', {})
        self.session['status'] = 'LOCKED'
        # Не сбрасываем payload, чтобы можно было его восстановить при разблокировке

    def _handle_unlock(self, scanned_code: str):
        """Обрабатывает попытку разблокировки системы."""
        if self.senior_token is None:
            self.senior_token = _get_senior_token(self.order['id'])

        if not self.senior_token:
            # Эта ошибка не должна блокировать систему повторно
            return {
                "status": "error",
                "message": "Критическая ошибка: не удалось определить старшего смены для этого заказа. Обратитесь к администратору.",
                "session": self.session
            }

        if scanned_code == self.senior_token:
            # Восстанавливаем состояние до блокировки
            self.session['status'] = self.session.pop('previous_status', 'IDLE')
            self.session['payload'] = self.session.pop('previous_payload', self._get_initial_state()['payload'])
            self._save_state()
            return self._build_success_response("Система разблокирована старшим смены. Последняя операция отменена. Можно продолжать работу.")
        else:
            # Не сохраняем состояние, чтобы не сбросить previous_status
            return self._build_error_response("Неверный код. Сканируйте пропуск старшего смены для разблокировки.")

    def _start_new_unit(self, first_code):
        """Начинает сборку новой единицы (набора или короба)."""
        # --- НОВАЯ ПРОВЕРКА ВАЛИДНОСТИ КОДА ---
        is_valid, error_message = self._validate_data_code(first_code)
        if not is_valid:
            # Эта ошибка заблокирует систему, требуя вмешательства старшего
            return self._build_error_response(error_message)

        # ПРОВЕРКА: не был ли этот код уже использован как вложение
        if self._is_code_already_used_as_child(first_code):
            return self._build_error_response(f"Ошибка: Код уже числится как вложенный в другую упаковку.")

        unit_type = self.session['payload']['next_step']
        if not unit_type:
            return self._build_error_response("В заказе не определены этапы агрегации.")

        self.session['status'] = f"AGGREGATING_{unit_type.upper()}"
        self.session['payload']['current_unit']['type'] = unit_type
        self.session['payload']['current_unit']['items'].append(first_code)
        
        return self._build_success_response(f"Начата сборка '{unit_type}'. Отсканировано товаров: 1.")

    def _add_to_unit(self, scanned_code):
        """Добавляет код в текущую единицу."""
        # --- НОВАЯ ПРОВЕРКА ВАЛИДНОСТИ КОДА ---
        is_valid, error_message = self._validate_data_code(scanned_code)
        if not is_valid:
            # Эта ошибка заблокирует систему, требуя вмешательства старшего
            return self._build_error_response(error_message)

        unit_type = self.session['payload']['current_unit'].get('type')

        # --- НОВАЯ ЛОГИКА: Завершение короба по SSCC ---
        if unit_type == 'box' and _is_sscc(scanned_code):
            return self._complete_unit(parent_code=scanned_code)

        current_items = self.session['payload']['current_unit']['items']

        # --- НОВАЯ ЛОГИКА: Автозавершение набора по заданной вместимости ---
        if unit_type == 'set':
            set_capacity = self.order.get('set_capacity')
            if set_capacity:
                # Если количество отсканированных товаров равно вместимости набора,
                # то текущий сканируемый код считается кодом самого набора и завершает операцию.
                if len(current_items) == set_capacity:
                    return self._complete_unit(parent_code=scanned_code)

                # Защита от сканирования лишних товаров в набор.
                if len(current_items) > set_capacity:
                    return self._build_error_response(
                        f"Ошибка: Превышена вместимость набора ({set_capacity} шт.). "
                        f"Отсканируйте код самого набора для завершения или отмените операцию."
                    )

        # Проверка на дубликат в текущей операции
        if scanned_code in current_items:
            return self._build_error_response("Этот код уже был отсканирован в текущей операции.")

        # ПРОВЕРКА: не был ли этот код уже использован как вложение в ДРУГОЙ упаковке
        if self._is_code_already_used_as_child(scanned_code):
            return self._build_error_response(f"Ошибка: Код уже числится как вложенный в другую упаковку.")

        self.session['payload']['current_unit']['items'].append(scanned_code)
        count = len(self.session['payload']['current_unit']['items'])
        return self._build_success_response(f"Товар добавлен. Отсканировано товаров: {count}.")

    def _complete_unit_from_command(self):
        """Обрабатывает команду завершения (CMD_COMPLETE_UNIT)."""
        unit = self.session['payload']['current_unit']
        if len(unit['items']) < 2:
            return self._build_error_response("Нельзя завершить операцию. Нужно отсканировать хотя бы один товар и код упаковки.")

        # Последний отсканированный код - это родитель (упаковка)
        parent_code = unit['items'].pop()
        return self._complete_unit(parent_code)

    def _complete_unit(self, parent_code: str):
        """
        Универсальный метод завершения юнита. Сохраняет данные в БД.
        Вызывается либо по команде, либо по сканированию SSCC.
        """
        unit = self.session['payload']['current_unit']
        child_items = unit['items']

        if not child_items:
            return self._build_error_response("Ошибка: Не отсканировано ни одного вложения для этой упаковки.")

        # ПРОВЕРКА: не была ли эта упаковка уже зарегистрирована
        if self._is_code_already_used_as_parent(parent_code):
            # Состояние не меняем, чтобы пользователь мог отменить операцию или попробовать другой код
            return self._build_error_response(f"Ошибка: Упаковка с кодом {parent_code} уже зарегистрирована в системе.")
        
        # Сохранение в БД
        if not self._save_aggregation(parent_code, child_items):
            # Состояние не меняем, чтобы не потерять данные сканирования
            return self._build_error_response("Ошибка сохранения в базу данных. Попробуйте снова.")

        # Сброс состояния для следующей операции
        message = f"Успешно сохранено: {unit['type']} с кодом {parent_code} ({len(child_items)} вложений)."
        self.session = self._get_initial_state()
        self._save_state()
        
        return self._build_success_response(message)

    def _handle_cancel(self):
        """Отменяет текущую операцию."""
        self.session = self._get_initial_state()
        self._save_state()
        response = self._build_success_response("Текущая операция отменена.")
        return response

    def _handle_undo_last_save(self):
        """Находит и удаляет последнюю сохраненную этим сотрудником упаковку."""
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                # 1. Найти parent_code последней операции этого сотрудника в этом заказе
                cur.execute(
                    """
                    SELECT parent_code FROM ma_aggregations
                    WHERE employee_token_id = %s AND order_id = %s
                    ORDER BY id DESC
                    LIMIT 1;
                    """,
                    (self.employee_token_id, self.order['id'])
                )
                result = cur.fetchone()
                if not result:
                    return self._build_success_response("Нет сохраненных операций для отмены.")

                last_parent_code = result[0]

                # 2. Удалить все записи, связанные с этим parent_code
                cur.execute(
                    "DELETE FROM ma_aggregations WHERE parent_code = %s AND order_id = %s;",
                    (last_parent_code, self.order['id'])
                )
                deleted_count = cur.rowcount
            conn.commit()
            return self._build_success_response(f"Последняя сохраненная упаковка ({last_parent_code}) и ее {deleted_count} вложений были удалены. Можете сканировать заново.")
        except Exception as e:
            if conn: conn.rollback()
            print(f"ОШИБКА в _handle_undo_last_save: {e}")
            return self._build_error_response("Ошибка базы данных при отмене последней операции.")
        finally:
            if conn: conn.close()

    def _save_aggregation(self, parent_code, child_items):
        """Сохраняет пачку записей в ma_aggregations."""
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                parent_type = self.session['payload']['current_unit']['type']
                
                # --- Улучшенная логика определения типа вложения ---
                hierarchy = ['product', 'set', 'box', 'pallet']
                child_type = 'unknown' # Значение по умолчанию
                try:
                    parent_index = hierarchy.index(parent_type)
                    if parent_index > 0:
                        # Тип вложения - это предыдущий уровень в иерархии
                        child_type = hierarchy[parent_index - 1]
                except ValueError:
                    # parent_type не найден в иерархии, оставляем 'unknown'
                    print(f"ПРЕДУПРЕЖДЕНИЕ: Неизвестный тип родителя '{parent_type}' в иерархии.")
                
                args_list = []
                for child_code in child_items:
                    args_list.append((self.order['id'], self.employee_token_id, self.work_session_id, child_code, child_type, parent_code, parent_type))
                
                # Используем execute_values для быстрой вставки
                execute_values(
                    cur,
                    "INSERT INTO ma_aggregations (order_id, employee_token_id, work_session_id, child_code, child_type, parent_code, parent_type) VALUES %s",
                    args_list
                )
            conn.commit()
            return True
        except Exception as e:
            if conn: conn.rollback()
            print(f"ОШИБКА в _save_aggregation: {e}")
            return False
        finally:
            if conn: conn.close()

    def _build_success_response(self, message):
        return {"status": "success", "message": message, "session": self.session}
    
    def _build_error_response(self, message):
        return {"status": "error", "message": message, "session": self.session}