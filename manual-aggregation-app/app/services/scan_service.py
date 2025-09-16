import json
from datetime import datetime
from app.db import get_db_connection
from typing import Optional
import redis
from psycopg2.extras import RealDictCursor, execute_values

import re

from .state_service import state_manager
from .order_service import get_trained_code_model, get_erroneous_sets, build_and_save_model_and_samples

# --- Управляющие команды ---
CMD_COMPLETE_UNIT = "CMD_COMPLETE_UNIT"  # Завершить текущую единицу (набор/короб)
CMD_CANCEL_UNIT = "CMD_CANCEL_UNIT"      # Отменить сборку текущей единицы
CMD_LOGOUT = "CMD_LOGOUT"                # Выйти из системы (дублирует бейдж)
# --- НОВЫЕ КОМАНДЫ ---
CMD_ENTER_CORRECTION_MODE = "CMD_ENTER_CORRECTION_MODE"
CMD_EXIT_CORRECTION_MODE = "CMD_EXIT_CORRECTION_MODE"

# Символ-разделитель групп в коде DataMatrix, непечатаемый (ASCII 29)
GS_SEPARATOR = '\x1d'

def _is_sscc(code: str) -> bool:
    """Проверяет, является ли код кодом SSCC (18 цифр)."""
    return code.isdigit() and len(code) == 18

def _get_senior_token_record(order_id: int) -> Optional[RealDictCursor]:
    """Получает запись о токене старшего смены (первый созданный для заказа)."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, access_token FROM ma_employee_tokens WHERE order_id = %s ORDER BY id ASC LIMIT 1",
                (order_id,)
            )
            return cur.fetchone()
    except Exception as e:
        print(f"ОШИБКА в _get_senior_token_record: {e}")
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
    is_command = scanned_code in [CMD_COMPLETE_UNIT, CMD_CANCEL_UNIT, CMD_LOGOUT, CMD_ENTER_CORRECTION_MODE, CMD_EXIT_CORRECTION_MODE]

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
        self.senior_token_record = None # Ленивая загрузка записи о токене старшего
        
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
        
        # --- НОВЫЙ БЛОК: ПРОВЕРКА И ПРОВЕДЕНИЕ ОБУЧЕНИЯ ---
        is_trained = state_manager.is_order_trained(self.order['id'])
        if not is_trained:
            if not self._is_senior_by_token_id():
                return {
                    "status": "error",
                    "message": "Система не обучена. Для начала работы старший смены должен отсканировать 3 образцовых набора.",
                    "session": self.session,
                    "order_status": "NEEDS_TRAINING" # Флаг для UI
                }
            # Текущий пользователь - старший смены, и система не обучена. Запускаем процесс обучения.
            return self._handle_training_scan(scanned_code)

        status = self.session.get('status')

        # --- Обработка состояния блокировки (высший приоритет) ---
        if status == 'LOCKED':
            return self._handle_unlock(scanned_code)

        # --- Вход/выход из режима коррекции ---
        if scanned_code == CMD_ENTER_CORRECTION_MODE:
            self.session['status'] = 'AWAITING_SENIOR_FOR_CORRECTION'
            self._save_state()
            return self._build_success_response("Вход в режим коррекции: ожидание сканирования пропуска старшего смены.")
        
        if scanned_code == CMD_EXIT_CORRECTION_MODE:
            # Проверяем, активен ли режим, чтобы не показывать это сообщение без надобности
            order_mode, _ = state_manager.get_correction_mode_status(self.order['id'])
            if order_mode == 'CORRECTION':
                self.session['status'] = 'AWAITING_SENIOR_FOR_EXIT_CORRECTION'
                self._save_state()
                return self._build_success_response("Выход из режима коррекции: ожидание сканирования пропуска старшего смены.")
            else:
                # Не блокируем, просто информируем
                return self._build_success_response("Режим коррекции не активен.")

        # --- Обработка состояний, ожидающих скана старшего ---
        if status == 'AWAITING_SENIOR_FOR_CORRECTION':
            return self._activate_correction_mode(scanned_code) # scanned_code is the senior badge
        
        if status == 'AWAITING_SENIOR_FOR_EXIT_CORRECTION':
            return self._deactivate_correction_mode(scanned_code) # scanned_code is the senior badge

        # --- Проверка глобального режима коррекции для заказа ---
        order_mode, correction_stats = state_manager.get_correction_mode_status(self.order['id'])
        if order_mode == 'CORRECTION':
            result = self._handle_correction_scan(scanned_code)
            # Добавляем актуальную статистику к ответу для UI
            _, result['correction_stats'] = state_manager.get_correction_mode_status(self.order['id'])
            return result

        # --- Обработка команд (стандартный режим) ---
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

    def _is_senior_by_token_id(self) -> bool:
        """Проверяет, является ли текущий сотрудник старшим смены."""
        if self.senior_token_record is None:
            self.senior_token_record = _get_senior_token_record(self.order['id'])
        
        if not self.senior_token_record:
            return False # Не удалось определить старшего
        
        return self.employee_token_id == self.senior_token_record['id']

    def _is_senior(self, scanned_badge: str) -> bool:
        """Проверяет, является ли отсканированный пропуск пропуском старшего."""
        if self.senior_token_record is None:
            self.senior_token_record = _get_senior_token_record(self.order['id'])
        
        if not self.senior_token_record:
            return False

        return scanned_badge == self.senior_token_record['access_token']

    def _activate_correction_mode(self, senior_badge: str):
        """Активирует режим коррекции после проверки пропуска старшего."""
        if not self._is_senior(senior_badge):
            # Сбрасываем состояние ожидания, но не блокируем систему
            self.session['status'] = 'IDLE'
            self._save_state()
            return self._build_error_response("Ошибка: Только старший смены может активировать режим коррекции.")

        erroneous_sets = get_erroneous_sets(self.order['id'])
        state_manager.start_correction_mode(self.order['id'], erroneous_sets)
        
        # Сбрасываем состояние текущего пользователя в IDLE
        self.session = self._get_initial_state()
        self._save_state()

        if not erroneous_sets:
            return self._build_success_response("Режим коррекции активирован. Ошибочных наборов в заказе не найдено.")
        else:
            return self._build_success_response(f"Режим коррекции активирован. Найдено {len(erroneous_sets)} ошибочных наборов. Начинайте сканирование.")

    def _deactivate_correction_mode(self, senior_badge: str):
        """Деактивирует режим коррекции после проверки пропуска старшего."""
        if not self._is_senior(senior_badge):
            self.session['status'] = 'IDLE'
            self._save_state()
            return self._build_error_response("Ошибка: Только старший смены может деактивировать режим коррекции.")

        state_manager.stop_correction_mode(self.order['id'])
        self.session = self._get_initial_state()
        self._save_state()
        return self._build_success_response("Режим коррекции деактивирован. Система возвращена в штатный режим работы.")

    def _process_completed_training_sample(self):
        """Вызывается после завершения сбора одного образца."""
        payload = self.session['payload']
        samples_collected = payload['samples_collected']
        num_samples_needed = 3
        sample_num = len(samples_collected)

        if sample_num < num_samples_needed:
            self._save_state()
            return self._build_success_response(f"Образец {sample_num} из {num_samples_needed} сохранен. Начинайте сборку следующего.")
        else:
            # Собрали все 3 образца, запускаем обучение
            result = build_and_save_model_and_samples(
                self.order['id'],
                self.employee_token_id,
                self.work_session_id,
                samples_collected
            )
            if result['success']:
                self.session = self._get_initial_state() # Сброс состояния после обучения
                self._save_state()
                model = result['model']
                product_prefixes_str = ", ".join(model['product_prefixes'])
                set_prefixes_str = ", ".join(model['set_prefixes'])
                response = self._build_success_response(
                    f"Обучение успешно завершено!\n"
                    f"Префиксы товаров: {product_prefixes_str}\n"
                    f"Префиксы наборов: {set_prefixes_str}\n"
                    f"Система готова к работе."
                )
                # Убираем флаг для UI
                response['order_status'] = 'OPERATIONAL'
                return response
            else:
                # Обучение не удалось, сбрасываем прогресс
                self.session['payload']['samples_collected'] = []
                self.session['payload']['current_sample_items'] = []
                self._save_state()
                return self._build_error_response(result['message'])

    def _handle_training_scan(self, scanned_code: str):
        """Обрабатывает сканирование в режиме обучения системы."""
        if self.session.get('status') != 'TRAINING':
            # Инициализация режима обучения
            self.session['status'] = 'TRAINING'
            self.session['payload'] = {'samples_collected': [], 'current_sample_items': []}
        
        # --- НОВАЯ ЛОГИКА: Сброс обучения по команде ---
        if scanned_code == CMD_CANCEL_UNIT:
            self.session['payload'] = {'samples_collected': [], 'current_sample_items': []}
            self._save_state()
            return self._build_success_response("Обучение сброшено. Начните сборку образцов заново.")

        payload = self.session['payload']
        samples_collected = payload['samples_collected']
        current_items = payload['current_sample_items']
        num_samples_needed = 3
        sample_num = len(samples_collected) + 1

        is_valid, error_message = self._validate_data_code(scanned_code)
        if not is_valid: return self._build_error_response(error_message)

        if scanned_code == CMD_COMPLETE_UNIT:
            if len(current_items) < 2: return self._build_error_response("Для завершения образца нужно отсканировать хотя бы один товар и код самого набора.")
            parent_code = current_items.pop()
            # Проверяем, что код набора не совпадает с одним из товаров
            if parent_code in current_items:
                current_items.append(parent_code) # Возвращаем состояние как было
                return self._build_error_response("Логическая ошибка: Код набора не может совпадать с кодом одного из товаров в этом же наборе.")

            samples_collected.append({'parent_code': parent_code, 'items': current_items})
            payload['current_sample_items'] = []
            return self._process_completed_training_sample()

        set_capacity = self.order.get('set_capacity')
        if set_capacity and len(current_items) == set_capacity:
            parent_code = scanned_code
            # Проверяем, что код набора не совпадает с одним из товаров
            if parent_code in current_items:
                return self._build_error_response("Логическая ошибка: Код набора не может совпадать с кодом одного из товаров в этом же наборе.")
            samples_collected.append({'parent_code': parent_code, 'items': current_items})
            payload['current_sample_items'] = []
            return self._process_completed_training_sample()

        if scanned_code in current_items: return self._build_error_response("Этот код уже был отсканирован в текущем образце.")
        current_items.append(scanned_code)
        self._save_state()
        return self._build_success_response(f"Обучение (образец {sample_num}/{num_samples_needed}): отсканировано товаров: {len(current_items)}.")

    def _handle_correction_scan(self, scanned_code: str):
        """Обрабатывает сканирование в режиме коррекции."""
        redis = state_manager.redis_client
        order_id = self.order['id']
        pending_key = f"correction:pending_confirm:{self.employee_token_id}"
        sets_to_check_key = f"correction:sets_to_check:{order_id}"

        pending_code = redis.get(pending_key)

        if pending_code:
            return self._handle_correction_confirmation(scanned_code, pending_code)
        else:
            is_in_error_list = redis.sismember(sets_to_check_key, scanned_code)
            if is_in_error_list:
                redis.set(pending_key, scanned_code, ex=60) # Ожидаем подтверждения 60 сек
                return self._build_error_response(f"ВНИМАНИЕ: Набор '{scanned_code}' в списке ошибок. Отложите его и отсканируйте еще раз для подтверждения.")
            else:
                return self._handle_correction_ok_scan(scanned_code)

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
        if not self._is_senior(scanned_code):
            # Эта ошибка не должна блокировать систему повторно
            return self._build_error_response("Неверный код. Сканируйте пропуск старшего смены для разблокировки.")

        # Восстанавливаем состояние до блокировки
        self.session['status'] = self.session.pop('previous_status', 'IDLE')
        self.session['payload'] = self.session.pop('previous_payload', self._get_initial_state()['payload'])
        self._save_state()
        return self._build_success_response("Система разблокирована старшим смены. Последняя операция отменена. Можно продолжать работу.")

    def _handle_correction_confirmation(self, scanned_code, pending_code):
        """Обрабатывает второй скан в режиме коррекции (подтверждение)."""
        redis = state_manager.redis_client
        order_id = self.order['id']
        pending_key = f"correction:pending_confirm:{self.employee_token_id}"

        if scanned_code == pending_code:
            pipe = redis.pipeline()
            pipe.srem(f"correction:sets_to_check:{order_id}", scanned_code)
            pipe.sadd(f"correction:scanned_error:{order_id}", scanned_code)
            pipe.delete(pending_key)
            pipe.execute()
            return self._build_success_response(f"Подтверждено: набор '{scanned_code}' помечен как исправленный.")
        else:
            redis.delete(pending_key)
            return self._build_error_response(f"Ошибка подтверждения. Ожидался повторный скан '{pending_code}', но получен '{scanned_code}'. Попробуйте снова.")

    def _handle_correction_ok_scan(self, scanned_code):
        """Обрабатывает скан корректного набора в режиме коррекции."""
        state_manager.redis_client.sadd(f"correction:scanned_ok:{self.order['id']}", scanned_code)
        return self._build_success_response(f"OK: Набор '{scanned_code}' не в списке ошибок.")

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
        # --- 1. ПРОВЕРКА ВАЛИДНОСТИ КОДА ---
        is_valid, error_message = self._validate_data_code(scanned_code)
        if not is_valid:
            return self._build_error_response(error_message)

        unit_type = self.session['payload']['current_unit'].get('type')
        current_items = self.session['payload']['current_unit']['items']

        # --- 2. ПРОВЕРКА НА АВТОЗАВЕРШЕНИЕ (ПРИОРИТЕТ) ---
        # Если сканируется SSCC для короба, это завершение.
        if unit_type == 'box' and _is_sscc(scanned_code):
            return self._complete_unit(parent_code=scanned_code)
        
        # Если достигнута вместимость набора, этот скан - завершение.
        if unit_type == 'set':
            set_capacity = self.order.get('set_capacity')
            if set_capacity and len(current_items) == set_capacity:
                return self._complete_unit(parent_code=scanned_code)

        # --- 3. ЛОГИЧЕСКИЕ ПРОВЕРКИ ДЛЯ ДОБАВЛЯЕМОГО ЭЛЕМЕНТА ---
        # Теперь, когда мы знаем, что это не завершающий код, а вложение,
        # проверяем его на логическую корректность.
        if unit_type == 'set':
            model = get_trained_code_model(self.order['id'])
            if model.get('set_prefixes') and scanned_code and len(scanned_code) >= 16:
                if scanned_code[:16] in model['set_prefixes']:
                    return self._build_error_response("Логическая ошибка: Попытка вложить код набора в другой набор.")

        # --- 4. ДРУГИЕ ПРОВЕРКИ ---
        # Защита от сканирования лишних товаров в набор с фиксированной вместимостью.
        if unit_type == 'set':
            set_capacity = self.order.get('set_capacity')
            if set_capacity and len(current_items) > set_capacity:
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

        # --- 5. ДОБАВЛЕНИЕ ЭЛЕМЕНТА ---
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

        # --- НОВАЯ ЛОГИЧЕСКАЯ ПРОВЕРКА: Набор нельзя закрывать кодом товара ---
        unit_type = unit.get('type')
        if unit_type == 'set':
            model = get_trained_code_model(self.order['id'])
            if model.get('product_prefixes') and parent_code and len(parent_code) >= 16:
                if parent_code[:16] in model['product_prefixes']:
                    return self._build_error_response("Логическая ошибка: Попытка закрыть набор кодом, определенным как товар.")

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