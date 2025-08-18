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

    def get_state(self, token_id: int) -> Optional[Dict[str, Any]]:
        """Получает текущее состояние сотрудника из Redis."""
        key = self._get_key(token_id)
        state_json = self.redis_client.get(key)
        if state_json:
            return json.loads(state_json)
        return None

    def set_state(self, token_id: int, status: str, payload: Dict = None, ex_seconds: int = 43200):
        """
        Устанавливает новое состояние для сотрудника.
        ex_seconds: время жизни ключа (12 часов), чтобы не хранить старые сессии вечно.
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
        """Полностью удаляет состояние сотрудника."""
        key = self._get_key(token_id)
        self.redis_client.delete(key)

# Создаем один экземпляр, который будет использоваться во всем приложении
state_manager = EmployeeStateManager()