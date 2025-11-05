# src/catalogs_service.py

import logging
import pandas as pd
from psycopg2.extras import RealDictCursor, execute_values
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

    # --- Методы для товарных групп ---

    def get_product_groups(self):
        """Возвращает список товарных групп из БД клиента."""
        logger.info("Запрос справочника товарных групп из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, group_name, display_name, fias_required, code_template, dm_template FROM dmkod_product_groups ORDER BY display_name")
                return cur.fetchall()

    def upsert_product_group(self, group_data: dict):
        """Добавляет или обновляет товарную группу."""
        group_id = group_data.get('id')
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                if group_id: # Обновление
                    cur.execute("""
                        UPDATE dmkod_product_groups SET group_name=%s, display_name=%s, fias_required=%s, code_template=%s, dm_template=%s
                        WHERE id=%s
                    """, (group_data['group_name'], group_data['display_name'], group_data['fias_required'], group_data['code_template'], group_data['dm_template'], group_id))
                else: # Вставка
                    cur.execute("""
                        INSERT INTO dmkod_product_groups (group_name, display_name, fias_required, code_template, dm_template)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (group_data['group_name'], group_data['display_name'], group_data['fias_required'], group_data['code_template'], group_data['dm_template']))
            conn.commit()

    def delete_product_group(self, group_id: int):
        """Удаляет товарную группу по ID."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dmkod_product_groups WHERE id = %s", (group_id,))
            conn.commit()

    def get_product_groups_template(self):
        """Возвращает шаблон для импорта товарных групп."""
        return pd.DataFrame(columns=['id', 'group_name', 'display_name', 'fias_required', 'code_template', 'dm_template'])

    def process_product_groups_import(self, df: pd.DataFrame):
        """Обрабатывает импорт товарных групп из DataFrame."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Разделяем данные на те, что с ID (для обновления) и без (для вставки)
                update_df = df[pd.to_numeric(df['id'], errors='coerce').notna()]
                insert_df = df[pd.to_numeric(df['id'], errors='coerce').isna()]

                # Обновляем существующие
                if not update_df.empty:
                    update_tuples = [tuple(x) for x in update_df[['group_name', 'display_name', 'fias_required', 'code_template', 'dm_template', 'id']].to_numpy()]
                    update_query = "UPDATE dmkod_product_groups SET group_name=%s, display_name=%s, fias_required=%s, code_template=%s, dm_template=%s WHERE id=%s"
                    cur.executemany(update_query, update_tuples)

                # Вставляем новые
                if not insert_df.empty:
                    insert_tuples = [tuple(x) for x in insert_df[['group_name', 'display_name', 'fias_required', 'code_template', 'dm_template']].to_numpy()]
                    insert_query = "INSERT INTO dmkod_product_groups (group_name, display_name, fias_required, code_template, dm_template) VALUES %s"
                    execute_values(cur, insert_query, insert_tuples)
            conn.commit()

    # --- Методы для товаров ---

    def get_products(self):
        """Возвращает список товаров (номенклатуры) из БД клиента."""
        logger.info("Запрос справочника товаров из БД клиента.")
        with self.get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT gtin, name, description_1, description_2, description_3 FROM products ORDER BY name")
                return cur.fetchall()

    def upsert_product(self, product_data: dict):
        """Добавляет или обновляет товар."""
        logger.debug(f"Попытка UPSERT для товара с GTIN: {product_data.get('gtin')}. Данные: {product_data}")
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # --- ИСПРАВЛЕНИЕ: Явная проверка на существование перед действием ---
                # Это надежнее, чем полагаться на ON CONFLICT, особенно когда
                # ключ может быть изменен в UI (хотя мы это и заблокировали).
                cur.execute("SELECT 1 FROM products WHERE gtin = %s", (product_data['gtin'],))
                exists = cur.fetchone()
                if exists:  # Если GTIN найден, обновляем запись
                    logger.debug(f"Товар с GTIN {product_data['gtin']} существует. Выполняется UPDATE.")
                    cur.execute("""
                        UPDATE products SET name=%s, description_1=%s, description_2=%s, description_3=%s
                        WHERE gtin=%s
                    """, (product_data['name'], product_data.get('description_1'), product_data.get('description_2'), product_data.get('description_3'), product_data['gtin']))
                else:  # Если GTIN не найден, создаем новую запись
                    logger.debug(f"Товар с GTIN {product_data['gtin']} не найден. Выполняется INSERT.")
                    cur.execute("""
                        INSERT INTO products (gtin, name, description_1, description_2, description_3)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (product_data['gtin'], product_data['name'], product_data.get('description_1'), product_data.get('description_2'), product_data.get('description_3')))
            conn.commit()

    def delete_product(self, gtin: str):
        """Удаляет товар по GTIN."""
        logger.debug(f"Попытка удаления товара с GTIN: {gtin}")
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE gtin = %s", (gtin,))
            conn.commit()
        logger.info(f"Товар с GTIN {gtin} успешно удален.")

    def get_products_template(self):
        """Возвращает шаблон для импорта товаров."""
        return pd.DataFrame(columns=['gtin', 'name', 'description_1', 'description_2', 'description_3'])

    def process_products_import(self, df: pd.DataFrame):
        """Обрабатывает импорт товаров из DataFrame в режиме UPSERT."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Готовим данные для execute_values
                data_tuples = [tuple(x) for x in df[['gtin', 'name', 'description_1', 'description_2', 'description_3']].to_numpy()]
                
                # Формируем запрос UPSERT
                upsert_query = """
                    INSERT INTO products (gtin, name, description_1, description_2, description_3)
                    VALUES %s
                    ON CONFLICT (gtin) DO UPDATE SET
                        name = EXCLUDED.name,
                        description_1 = EXCLUDED.description_1,
                        description_2 = EXCLUDED.description_2,
                        description_3 = EXCLUDED.description_3;
                """
                execute_values(cur, upsert_query, data_tuples)
            conn.commit()