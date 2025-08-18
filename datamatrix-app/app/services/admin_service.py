import os
import psycopg2
from app.db import get_db_connection
from psycopg2 import sql
from app.services.view_service import sanitize_view_name # Импортируем из соседнего сервиса

def delete_order_completely(order_id: int) -> dict:
    """
    Полностью удаляет заказ и все связанные с ним данные.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            orders_table = sql.Identifier(os.getenv('TABLE_ORDERS', 'orders'))
            
            cur.execute(f"SELECT client_name FROM {orders_table.string} WHERE id = %s", (order_id,))
            order_info = cur.fetchone()
            if order_info:
                client_name = order_info[0]
                base_view_name_str = f"{client_name}_{order_id}"
                sscc_view_name_str = f"{base_view_name_str}_sscc"
                base_view_name = sql.Identifier(sanitize_view_name(base_view_name_str))
                sscc_view_name = sql.Identifier(sanitize_view_name(sscc_view_name_str))
                
                cur.execute(sql.SQL("DROP VIEW IF EXISTS {view};").format(view=sscc_view_name))
                cur.execute(sql.SQL("DROP VIEW IF EXISTS {view};").format(view=base_view_name))

            cur.execute(sql.SQL("DELETE FROM {table} WHERE id = %s").format(table=orders_table), (order_id,))
            
            if cur.rowcount == 0:
                return {"success": False, "message": f"Заказ с ID {order_id} не найден."}

            conn.commit()
            return {"success": True, "message": f"Заказ №{order_id} и все связанные данные удалены."}
    except Exception as e:
        if conn: conn.rollback()
        return {"success": False, "message": f"Ошибка при удалении заказа: {e}"}
    finally:
        if conn: conn.close()
        
def get_tirages_for_order(order_id: int) -> list:
    """Возвращает список тиражей для указанного заказа."""
    conn = get_db_connection()
    items_table = os.getenv('TABLE_ITEMS', 'items')
    with conn.cursor() as cur:
        # Группируем по тиражу и gtin, чтобы получить сводную информацию
        query = f"""
            SELECT 
                tirage_number,
                gtin,
                COUNT(datamatrix) as dm_count,
                -- Проверяем, есть ли хотя бы одна запись с package_id
                BOOL_OR(package_id IS NOT NULL) as has_aggregation
            FROM {items_table}
            WHERE order_id = %s
            GROUP BY tirage_number, gtin
            ORDER BY tirage_number, gtin;
        """
        cur.execute(query, (order_id,))
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    conn.close()


def delete_tirages_from_order(order_id: int, tirages_to_delete: list) -> dict:
    """
    Удаляет выбранные тиражи (комбинации tirage_number и gtin) из заказа.
    """
    if not tirages_to_delete:
        return {"success": False, "message": "Не выбрано ни одного тиража для удаления."}
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            items_table = sql.Identifier(os.getenv('TABLE_ITEMS', 'items'))
            
            # Удаляем записи из items, соответствующие выбранным тиражам и gtin
            # Мы не можем использовать execute_values для DELETE, поэтому делаем цикл
            deleted_count = 0
            for item in tirages_to_delete:
                tirage_number, gtin = item.split('|')
                cur.execute(
                    sql.SQL("DELETE FROM {table} WHERE order_id = %s AND tirage_number = %s AND gtin = %s").format(table=items_table),
                    (order_id, tirage_number, gtin)
                )
                deleted_count += cur.rowcount

            conn.commit()
            return {"success": True, "message": f"Успешно удалено {deleted_count} записей DataMatrix из указанных тиражей."}
    except Exception as e:
        if conn: conn.rollback()
        return {"success": False, "message": f"Произошла ошибка при удалении тиражей: {e}"}
    finally:
        if conn: conn.close()