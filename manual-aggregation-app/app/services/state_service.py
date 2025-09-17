import redis
import json
import os
from typing import Optional, Dict, Any

# --- Константы состояний ---
# Сотрудник свободен и ожидает сканирования первого товара/короба для новой операции.
STATUS_IDLE = 'IDLE' 
# Сотрудник сканирует товары для вложения в набор.
STATUS_AGGREGATING_SET = 'AGGREGATING_SET'
# Сотрудник сканирует товары/наборы для вложения в короб.
STATUS_AGGREGATING_BOX = 'AGGREGATING_BOX'
# Сотрудник приписан к паллете и сканирует короба для нее.
STATUS_ASSIGNED_TO_PALLET = 'ASSIGNED_TO_PALLET'
# Сотрудник приписан к контейнеру и сканирует паллеты для него.
STATUS_ASSIGNED_TO_CONTAINER = 'ASSIGNED_TO_CONTAINER'


class EmployeeStateManager:
    """
    Управляет состоянием сотрудников в Redis.
    Гарантирует, что состояние доступно для всех воркеров приложения.
    """
    def __init__(self):
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=0,
            decode_responses=True # <-- Важно для работы со строками
        )

    def _get_key(self, token_id: int) -> str:
        """Генерирует ключ для Redis."""
        return f"employee_state:{token_id}"

    def _get_lock_key(self, token_id: int) -> str:
        """Генерирует ключ для блокировки сессии."""
        return f"session_lock:{token_id}"

    def get_state(self, token_id: int) -> Optional[Dict[str, Any]]:
        """Получает текущее состояние сотрудника из Redis."""
        key = self._get_key(token_id)
        state_json = self.redis_client.get(key)
        if state_json:
            return json.loads(state_json)
        return None

    def set_state(self, token_id: int, status: str, payload: Dict = None, ex_seconds: int = 1800):
        """
        Устанавливает новое состояние для сотрудника.
        ex_seconds: время жизни ключа (30 минут), чтобы не хранить старые сессии вечно.
        """
        key = self._get_key(token_id)
        state = {
            'status': status,
            'payload': payload or {}
        }
        self.redis_client.set(key, json.dumps(state), ex=ex_seconds)

    def update_payload(self, token_id: int, new_data: Dict):
        """Обновляет payload в существующем состоянии."""
        state = self.get_state(token_id)
        if state:
            state['payload'].update(new_data)
            self.set_state(token_id, state['status'], state['payload'])

    def clear_state(self, token_id: int):
        """Полностью удаляет состояние сотрудника и снимает блокировку сессии."""
        state_key = self._get_key(token_id)
        lock_key = self._get_lock_key(token_id)
        self.redis_client.delete(state_key, lock_key)

    def acquire_session_lock(self, token_id: int, ex_seconds: int = 1800) -> bool:
        """
        Пытается установить блокировку для сессии сотрудника.
        Возвращает True, если блокировка установлена, и False, если она уже существует.
        """
        lock_key = self._get_lock_key(token_id)
        # nx=True означает "set only if key does not exist"
        # Мы просто храним '1' как значение, важен сам факт наличия ключа.
        return self.redis_client.set(lock_key, "1", ex=ex_seconds, nx=True)

    def reset_order_state(self, order_id: int, token_ids: list[int]):
        """
        Полностью сбрасывает состояние заказа в Redis:
        - Удаляет обученную модель.
        - Удаляет все активные сессии и блокировки для переданных ID токенов.
        """
        # 1. Удаляем обученную модель
        model_key = f"order_model:{order_id}"
        
        # 2. Собираем все ключи для удаления
        keys_to_delete = [model_key]
        for token_id in token_ids:
            keys_to_delete.append(self._get_key(token_id)) # employee_state:<id>
            keys_to_delete.append(self._get_lock_key(token_id)) # session_lock:<id>
        
        # 3. Удаляем все ключи одной командой, если они есть
        if keys_to_delete:
            self.redis_client.delete(*keys_to_delete)

    def is_order_trained(self, order_id: int) -> bool:
        """Проверяет, существует ли обученная модель для заказа."""
        model_key = f"order_model:{order_id}"
        cached_data = self.redis_client.get(model_key)
        if not cached_data:
            return False
        try:
            model = json.loads(cached_data)
            # Модель считается обученной, если она существует и флаг успешности установлен
            return model.get('learning_successful', False)
        except json.JSONDecodeError:
            return False

    def get_trained_model(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Получает обученную модель для заказа из Redis."""
        model_key = f"order_model:{order_id}"
        cached_data = self.redis_client.get(model_key)
        if cached_data:
            try:
                model = json.loads(cached_data)
                # Преобразуем списки обратно в множества для быстрой проверки
                model['product_prefixes'] = set(model.get('product_prefixes', []))
                model['set_prefixes'] = set(model.get('set_prefixes', []))
                return model
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    def save_trained_model(self, order_id: int, model: dict):
        """Сохраняет обученную модель в Redis без ограничения по времени."""
        model_key = f"order_model:{order_id}"
        # Преобразуем множества в списки для JSON-сериализации
        model_to_cache = model.copy()
        model_to_cache['product_prefixes'] = list(model.get('product_prefixes', set()))
        model_to_cache['set_prefixes'] = list(model.get('set_prefixes', set()))
        
        self.redis_client.set(model_key, json.dumps(model_to_cache))

    def get_correction_mode_status(self, order_id: int, employee_token_id: Optional[int] = None) -> tuple[Optional[str], Optional[dict]]:
        """
        Проверяет, активен ли режим коррекции для заказа, и возвращает его статус и статистику.
        Если передан employee_token_id, также возвращает список наборов, ожидающих подтверждения удаления.
        """
        mode_key = f"order_mode:{order_id}"
        mode = self.redis_client.get(mode_key)
        
        if mode != 'CORRECTION':
            return None, None
        
        # Если режим активен, собираем статистику
        pipe = self.redis_client.pipeline()
        pipe.scard(f"correction:sets_to_check:{order_id}")
        pipe.scard(f"correction:scanned_ok:{order_id}")
        pipe.scard(f"correction:scanned_error:{order_id}")
        if employee_token_id:
            # Новый ключ для хранения кодов, ожидающих подтверждения удаления
            pending_removal_key = f"correction:pending_removal:{employee_token_id}"
            pipe.smembers(pending_removal_key)

        results = pipe.execute()
        
        stats = {
            "to_check": results[0],
            "scanned_ok": results[1],
            "scanned_error": results[2],
            "pending_removal": sorted(list(results[3])) if employee_token_id and len(results) > 3 else []
        }
        return 'CORRECTION', stats

    def start_correction_mode(self, order_id: int, erroneous_sets: list):
        """Активирует режим коррекции для заказа."""
        mode_key = f"order_mode:{order_id}"
        sets_to_check_key = f"correction:sets_to_check:{order_id}"
        # Ключи pending_removal создаются и удаляются динамически для каждого юзера
        scanned_ok_key = f"correction:scanned_ok:{order_id}"
        scanned_error_key = f"correction:scanned_error:{order_id}"

        # Очищаем старые данные на всякий случай
        self.redis_client.delete(sets_to_check_key, scanned_ok_key, scanned_error_key)

        if erroneous_sets:
            self.redis_client.sadd(sets_to_check_key, *erroneous_sets)
        
        self.redis_client.set(mode_key, 'CORRECTION', ex=43200) # 12 часов

    def stop_correction_mode(self, order_id: int):
        """Деактивирует режим коррекции и очищает все связанные данные."""
        from .order_service import get_token_ids_for_order # Локальный импорт для избежания циклической зависимости

        # 1. Собираем все ключи, связанные напрямую с заказом
        keys_to_delete = self.redis_client.keys(f"correction:*:{order_id}")
        keys_to_delete.append(f"order_mode:{order_id}")

        # 2. Собираем все персональные ключи сотрудников, работавших с этим заказом
        token_ids = get_token_ids_for_order(order_id)
        for token_id in token_ids:
            keys_to_delete.append(f"correction:pending_removal:{token_id}")

        if keys_to_delete:
            self.redis_client.delete(*keys_to_delete) # Удаляем все одним запросом

# Создаем один экземпляр, который будет использоваться во всем приложении
state_manager = EmployeeStateManager()