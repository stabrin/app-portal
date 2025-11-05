# src/catalogs_service.py

import logging
from psycopg2.extras import RealDictCursor
from .api_service import ApiService

logger = logging.getLogger(__name__)

class CatalogsService:
    """
    Сервис для управления логикой вкладки "Справочники".
    """
    def __init__(self, user_info, db_connection_func):
        """
        Инициализирует сервис.
        :param user_info: Словарь с информацией о пользователе.
        :param db_connection_func: Функция, возвращающая подключение к БД клиента.
        """
        self.api_service = ApiService(user_info)
        self.get_db_connection = db_connection_func

    def get_participants_catalog(self):
        """Получает справочник участников, используя ApiService."""
        logger.info("Запрос справочника участников через CatalogsService.")
        return self.api_service.get_participants()

    def get_product_groups(self):
        """Возвращает список товарных групп из БД клиента."""
        logger.info("Запрос справочника товарных групп из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, group_name, display_name FROM dmkod_product_groups ORDER BY display_name")
                return cur.fetchall()

    def get_products(self):
        """Возвращает список товаров (номенклатуры) из БД клиента."""
        logger.info("Запрос справочника товаров из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT gtin, name FROM products ORDER BY name")
                return cur.fetchall()