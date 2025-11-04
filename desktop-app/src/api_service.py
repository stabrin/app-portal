# src/api_service.py

import os
import requests
import logging

logger = logging.getLogger(__name__)

class ApiService:
    """
    Сервис для инкапсуляции всех взаимодействий с внешним API ДМкод.
    """
    def __init__(self, user_info):
        """
        Инициализирует сервис с информацией о пользователе, включая токен доступа.
        :param user_info: Словарь с данными пользователя, включая 'api_access_token'.
        """
        self.user_info = user_info
        self.api_base_url = os.getenv('API_BASE_URL')
        if not self.api_base_url:
            raise ValueError("Переменная окружения API_BASE_URL не установлена.")

    def _get_auth_headers(self):
        """Создает заголовок авторизации."""
        access_token = self.user_info.get('api_access_token')
        if not access_token:
            raise ConnectionError("Отсутствует токен доступа к API.")
        return {'Authorization': f'Bearer {access_token}'}

    def get_participants(self):
        """Получает список участников (клиентов) из API."""
        logger.info("Получение списка участников из API...")
        try:
            participants_url = f"{self.api_base_url.rstrip('/')}/psp/participants"
            headers = self._get_auth_headers()
            response = requests.get(participants_url, headers=headers)
            response.raise_for_status()
            return response.json().get('participants', [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось получить список участников из API: {e}", exc_info=True)
            raise