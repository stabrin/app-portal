import requests
import json
import os
from dotenv import load_dotenv

class ApiClient:
    """Простой клиент для взаимодействия с API dmkod.ru."""
    def __init__(self):
        # Просто вызываем load_dotenv(). Он автоматически найдет .env файл,
        # если скрипт запускается из корневой папки проекта.
        # Это более надежный и простой способ.
        load_dotenv()
        
        self.base_url = os.getenv("API_BASE_URL")
        if not self.base_url:
            # Улучшаем сообщение об ошибке
            raise ValueError(
                "Переменная API_BASE_URL не найдена. "
                "Убедитесь, что файл .env находится в корне проекта "
                "и вы запускаете скрипт из корневой папки проекта."
            )
        self.auth_headers = self._get_auth_headers()

    def _get_auth_headers(self):
        """Читает токен и формирует заголовки для авторизации."""
        try:
            token_file_path = os.path.join(os.path.dirname(__file__), 'api_tokens.json')
            with open(token_file_path, 'r', encoding='utf-8') as f:
                tokens = json.load(f)
            access_token = tokens.get('access')
            if not access_token:
                raise ValueError("Ключ 'access' не найден в файле api_tokens.json.")
            return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        except Exception as e:
            raise IOError(f"Ошибка при чтении токена: {e}") from e

    def get(self, endpoint, headers=None):
        """Выполняет GET-запрос и возвращает объект ответа."""
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        # Объединяем заголовки авторизации с дополнительными, если они есть
        final_headers = {**self.auth_headers, **(headers or {})}
        response = requests.get(url, headers=final_headers)
        response.raise_for_status()  # Вызовет ошибку для статусов 4xx/5xx
        return response