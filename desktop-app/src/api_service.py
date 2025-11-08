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
        # --- ИЗМЕНЕНИЕ: Получаем URL из конфигурации клиента, а не из .env ---
        api_config = self.user_info.get('client_api_config', {})
        self.api_base_url = api_config.get('api_base_url')

        if not self.api_base_url:
            raise ValueError("URL для подключения к API не найден в конфигурации пользователя.")

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

    def create_order(self, payload: dict):
        """Создает заказ в API ДМкод."""
        logger.info(f"Отправка запроса на создание заказа в API. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/order/create"
            headers = self._get_auth_headers()
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            logger.info(f"Заказ успешно создан в API. Ответ: {response.json()}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании заказа в API: {e}", exc_info=True)
            raise

    def create_suborder_request(self, payload: dict):
        """Создает запрос на коды (suborder) в API ДМкод."""
        logger.info(f"Отправка запроса на создание suborder. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/suborders/create"
            headers = self._get_auth_headers()
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            logger.info(f"Запрос на коды успешно отправлен. Ответ: {response.json()}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании запроса на коды: {e}", exc_info=True)
            raise