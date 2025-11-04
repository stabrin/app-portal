# src/catalogs_service.py

import logging
from .api_service import ApiService

logger = logging.getLogger(__name__)

class CatalogsService:
    """
    Сервис для управления логикой вкладки "Справочники".
    """
    def __init__(self, user_info):
        """
        Инициализирует сервис.
        :param user_info: Словарь с информацией о пользователе.
        """
        self.api_service = ApiService(user_info)

    def get_participants_catalog(self):
        """Получает справочник участников, используя ApiService."""
        logger.info("Запрос справочника участников через CatalogsService.")
        return self.api_service.get_participants()