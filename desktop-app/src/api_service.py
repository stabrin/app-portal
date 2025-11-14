# src/api_service.py

import functools
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

    def _refresh_token(self):
        """Обновляет access и refresh токены, используя текущий refresh токен."""
        refresh_token = self.user_info.get('api_refresh_token')
        if not refresh_token:
            logger.error("Refresh token не найден. Невозможно обновить токен доступа.")
            raise ConnectionError("Refresh token отсутствует.")

        logger.info("Токен доступа истек или невалиден. Попытка обновления...")
        try:
            url = f"{self.api_base_url.rstrip('/')}/user/token/refresh"
            response = requests.post(url, json={'refresh': refresh_token})
            response.raise_for_status()
            
            new_tokens = response.json()
            self.user_info['api_access_token'] = new_tokens['access']
            self.user_info['api_refresh_token'] = new_tokens['refresh']
            
            logger.info("Токены успешно обновлены.")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось обновить токен: {e}", exc_info=True)
            # Если обновление не удалось, возможно, refresh-токен тоже истек.
            # В этом случае нужно будет перелогиниться.
            raise ConnectionError("Не удалось обновить токен. Требуется повторная авторизация.") from e

    def _api_request(self, method, url, **kwargs):
        """Обертка для всех API-запросов с автоматическим обновлением токена."""
        try:
            headers = self._get_auth_headers()
            response = requests.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401: # Unauthorized
                self._refresh_token()
                headers = self._get_auth_headers() # Получаем новые заголовки
                return requests.request(method, url, headers=headers, **kwargs)
            raise # Перебрасываем другие HTTP ошибки

    def get_participants(self):
        """Получает список участников (клиентов) из API."""
        logger.info("Получение списка участников из API...")
        try:
            participants_url = f"{self.api_base_url.rstrip('/')}/psp/participants"
            response = self._api_request('get', participants_url)
            return response.json().get('participants', [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось получить список участников из API: {e}", exc_info=True)
            raise

    def create_order(self, payload: dict):
        """Создает заказ в API ДМкод."""
        logger.info(f"Отправка запроса на создание заказа в API. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/order/create"
            response = self._api_request('post', url, json=payload, timeout=30)
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
            response = self._api_request('post', url, json=payload, timeout=30)
            logger.info(f"Запрос на коды успешно отправлен. Ответ: {response.json()}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании запроса на коды: {e}", exc_info=True)
            raise

    def get_order_details(self, api_order_id: int):
        """Получает детали заказа из API."""
        logger.info(f"Запрос деталей заказа ID {api_order_id} из API.")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/orders"
            # GET-запрос с телом в JSON
            response = self._api_request('get', url, json={"order_id": api_order_id}, timeout=30)
            logger.info(f"Детали заказа {api_order_id} успешно получены.")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении деталей заказа {api_order_id}: {e}", exc_info=True)
            raise

    def create_printrun(self, payload: dict):
        """Создает тираж (printrun) в API."""
        logger.info(f"Отправка запроса на создание тиража. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/printrun/create"
            response = self._api_request('post', url, json=payload, timeout=30)
            logger.info(f"Тираж успешно создан. Ответ: {response.json()}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании тиража: {e}", exc_info=True)
            raise

    def create_printrun_json(self, payload: dict):
        """Запрашивает подготовку JSON-файла с кодами для тиража."""
        logger.info(f"Отправка запроса на подготовку JSON для тиража. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/printrun/json/create"
            response = self._api_request('post', url, json=payload, timeout=30)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе JSON для тиража: {e}", exc_info=True)
            raise

    def download_printrun_json(self, payload: dict):
        """Скачивает готовый JSON-файл с кодами для тиража."""
        logger.info(f"Отправка запроса на скачивание кодов для тиража. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/printrun/json/download"
            response = self._api_request('get', url, json=payload, timeout=60)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при скачивании кодов для тиража: {e}", exc_info=True)
            raise

    def upload_utilisation_data(self, payload: dict):
        """
        Отправляет сведения об использовании кодов (атрибуция, агрегация).
        Адаптировано из dmkod-integration-app.
        """
        logger.info(f"Отправка сведений об использовании. Payload: {payload}")
        import json
        try:
            # --- ИСПРАВЛЕНИЕ: Гарантируем, что payload всегда является словарем ---
            # Если payload - это строка, загружаем ее как JSON.
            # Это решает проблему "can only concatenate str (not "int") to str" при повторных вызовах.
            if isinstance(payload, str):
                payload_dict = json.loads(payload)
            else:
                payload_dict = payload

            # --- ИЗМЕНЕНИЕ: Используем единый эндпоинт согласно вашему требованию ---
            url = f"{self.api_base_url.rstrip('/')}/psp/utilisation/upload"
            # Используем параметр `json`, который автоматически кодирует словарь в JSON
            # и устанавливает правильный заголовок 'Content-Type: application/json'.
            response = self._api_request('post', url, json=payload_dict, timeout=240)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при отправке сведений об использовании: {e}", exc_info=True)
            raise

    def create_utilisation_report(self, payload: dict):
        """
        Отправляет запрос на создание отчета об использовании (нанесении).
        """
        logger.info(f"Отправка запроса на создание отчета. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/utilisation/report/create"
            # Увеличиваем таймаут, так как операция может быть долгой
            response = self._api_request('post', url, json=payload, timeout=120)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании отчета об использовании: {e}", exc_info=True)
            raise