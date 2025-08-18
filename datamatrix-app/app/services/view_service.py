import os
import re
import io
import pandas as pd
import psycopg2
from app.db import get_db_connection
from psycopg2 import sql

def sanitize_view_name(name: str) -> str:
    """Очищает имя для использования в SQL-объектах (VIEW)."""
    name = re.sub(r'[^\w]', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_')

def create_bartender_views(order_id: int) -> dict:
    """
    Создает представления (VIEW) для Bartender для указанного заказа.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. Получаем информацию о заказе и формируем имена
            orders_table_name_str = os.getenv('TABLE_ORDERS', 'orders')
            cur.execute(f"SELECT client_name FROM {orders_table_name_str} WHERE id = %s", (order_id,))
            order_info = cur.fetchone()
            if not order_info:
                return {"success": False, "message": f"Заказ с ID {order_id} не найден."}
            client_name = order_info[0]

            base_view_name_str = f"{client_name}_{order_id}"
            sscc_view_name_str = f"{base_view_name_str}_sscc"
            
            base_view_name = sql.Identifier(sanitize_view_name(base_view_name_str))
            sscc_view_name = sql.Identifier(sanitize_view_name(sscc_view_name_str))

            items_table = sql.Identifier(os.getenv('TABLE_ITEMS', 'items'))
            products_table = sql.Identifier(os.getenv('TABLE_PRODUCTS', 'products'))
            packages_table = sql.Identifier(os.getenv('TABLE_PACKAGES', 'packages'))
            orders_table = sql.Identifier(os.getenv('TABLE_ORDERS', 'orders'))

            # 2. Удаляем старые представления
            cur.execute(sql.SQL("DROP VIEW IF EXISTS {view};").format(view=sscc_view_name))
            cur.execute(sql.SQL("DROP VIEW IF EXISTS {view};").format(view=base_view_name))

            # 3. Создаем основное представление (без изменений)
            main_view_query = sql.SQL("""
            CREATE VIEW {view_name} AS
            SELECT
                o.client_name, o.order_date, i.datamatrix, i.gtin, i.serial,
                i.code_8005, i.crypto_part_91, i.crypto_part_92, i.crypto_part_93,
                i.tirage_number, i.package_id, p.name AS product_name,
                p.description_1, p.description_2, p.description_3,
                p.created_at AS product_created_at
            FROM {items} i
            JOIN {orders} o ON i.order_id = o.id
            LEFT JOIN {products} p ON i.gtin = p.gtin
            WHERE i.order_id = {order_id};
            """).format(
                view_name=base_view_name,
                items=items_table,
                orders=orders_table,
                products=products_table,
                order_id=sql.Literal(order_id)
            )
            cur.execute(main_view_query)
            
            # 4. Проверяем наличие агрегации
            cur.execute(
                sql.SQL("SELECT 1 FROM {items} WHERE order_id = %s AND package_id IS NOT NULL LIMIT 1").format(items=items_table),
                (order_id,)
            )
            aggregation_exists = cur.fetchone() is not None
            
            # 5. Создаем второе представление для SSCC (Вариант B)
            if aggregation_exists:
                # --- ФИНАЛЬНЫЙ ЗАПРОС для SSCC View ---
                sscc_view_query = sql.SQL("""
                CREATE VIEW {view_name} AS
                -- Блок 1: Основные данные по коробам (level 1)
                WITH RECURSIVE package_hierarchy AS (
                    SELECT
                        p.id as base_box_id, p.id as package_id, p.level, p.sscc, p.parent_id
                    FROM {packages} p
                    WHERE p.level = 1 AND p.id IN (
                        SELECT DISTINCT i.package_id
                        FROM {items} i
                        WHERE i.order_id = {order_id} AND i.package_id IS NOT NULL
                    )
                    UNION ALL
                    SELECT ph.base_box_id, p_parent.id as package_id, p_parent.level, p_parent.sscc, p_parent.parent_id
                    FROM package_hierarchy ph JOIN {packages} p_parent ON ph.parent_id = p_parent.id
                ),
                -- Таблица с развернутыми данными для коробов
                boxes_view AS (
                    SELECT
                        base_box_id AS id_level_1,
                        MAX(CASE WHEN level = 1 THEN sscc END) AS sscc_level_1,
                        MAX(CASE WHEN level = 2 THEN package_id END) AS id_level_2,
                        MAX(CASE WHEN level = 2 THEN sscc END) AS sscc_level_2,
                        MAX(CASE WHEN level = 3 THEN package_id END) AS id_level_3,
                        MAX(CASE WHEN level = 3 THEN sscc END) AS sscc_level_3
                    FROM package_hierarchy
                    GROUP BY base_box_id
                )
                -- 1. Выбираем все строки для коробов
                SELECT * FROM boxes_view

                UNION ALL

                -- 2. Добавляем уникальные строки для паллет (level 2)
                SELECT
                    NULL::integer AS id_level_1,
                    sscc_level_2 AS sscc_level_1, -- SSCC паллеты становится основным
                    id_level_2,
                    sscc_level_2,
                    id_level_3,
                    sscc_level_3
                FROM boxes_view
                WHERE id_level_2 IS NOT NULL
                GROUP BY id_level_2, sscc_level_2, id_level_3, sscc_level_3 -- Убираем дубликаты паллет

                UNION ALL

                -- 3. Добавляем уникальные строки для контейнеров (level 3)
                SELECT
                    NULL::integer AS id_level_1,
                    sscc_level_3 AS sscc_level_1, -- SSCC контейнера становится основным
                    NULL::integer AS id_level_2,
                    NULL::varchar AS sscc_level_2,
                    id_level_3,
                    sscc_level_3
                FROM boxes_view
                WHERE id_level_3 IS NOT NULL
                GROUP BY id_level_3, sscc_level_3; -- Убираем дубликаты контейнеров
                """).format(
                    view_name=sscc_view_name,
                    items=items_table,
                    packages=packages_table,
                    order_id=sql.Literal(order_id)
                )
            else:
                # Пустое представление, если агрегации нет
                sscc_view_query = sql.SQL("""
                CREATE VIEW {view_name} AS
                SELECT
                    NULL::integer AS id_level_1,
                    NULL::varchar AS sscc_level_1,
                    NULL::integer AS id_level_2,
                    NULL::varchar AS sscc_level_2,
                    NULL::integer AS id_level_3,
                    NULL::varchar AS sscc_level_3
                WHERE 1=0; -- Гарантирует пустой результат, но с правильной структурой
                """).format(
                    view_name=sscc_view_name
                )

            cur.execute(sscc_view_query)
                
            message = f"Успешно созданы/обновлены представления: '{base_view_name.string}' и '{sscc_view_name.string}'."
            
            conn.commit()
            return {"success": True, "message": message}
            
    except Exception as e:
        if conn: conn.rollback()
        return {"success": False, "message": f"Произошла ошибка при создании представлений: {e}"}
    finally:
        if conn: conn.close()

def generate_declarator_report(order_id: int):
    """
    Генерирует Excel-отчет для декларанта на основе представлений заказа.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            orders_table = os.getenv('TABLE_ORDERS', 'orders')
            cur.execute(f"SELECT client_name FROM {orders_table} WHERE id = %s", (order_id,))
            order_info = cur.fetchone()
            if not order_info:
                raise ValueError(f"Заказ с ID {order_id} не найден.")
            
            client_name = order_info[0]
            base_view_name_str = f"{client_name}_{order_id}"
            sscc_view_name_str = f"{base_view_name_str}_sscc"
            
            base_view_name = sanitize_view_name(base_view_name_str)
            sscc_view_name = sanitize_view_name(sscc_view_name_str)

            query = sql.SQL("""
            SELECT
                b.datamatrix,
                b.gtin,
                SUBSTRING(b.datamatrix for 24) AS dm_part_24,
                SUBSTRING(b.datamatrix for 31) AS dm_part_31,
                s.sscc_level_1,
                s.sscc_level_2,
                s.sscc_level_3
            FROM {base_view} b
            LEFT JOIN {sscc_view} s ON b.package_id = s.id_level_1
            ORDER BY b.datamatrix;
            """).format(
                base_view=sql.Identifier(base_view_name),
                sscc_view=sql.Identifier(sscc_view_name)
            )

            final_query_string = query.as_string(conn)
            df = pd.read_sql(final_query_string, conn)

            # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
            # Очищаем DataFrame от недопустимых для Excel символов.
            # Мы применяем эту функцию ко всем ячейкам DataFrame.
            def clean_illegal_chars(val):
                if isinstance(val, str):
                    # Заменяем символ \x1d (GS) на пробел.
                    # Можно также заменить на '' для полного удаления.
                    return val.replace('\x1d', ' ')
                return val

            df = df.applymap(clean_illegal_chars)
            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name=f'Order_{order_id}_Report')
            
            output.seek(0)
            return {"success": True, "buffer": output, "filename": f"declarator_report_order_{order_id}.xlsx"}

    except psycopg2.errors.UndefinedTable:
        return {"success": False, "message": "Ошибка: Представления для этого заказа еще не созданы. Сначала создайте VIEW для Bartender."}
    except Exception as e:
        return {"success": False, "message": f"Произошла ошибка при формировании отчета: {e}"}
    finally:
        if conn: conn.close()