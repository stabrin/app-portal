# manual-aggregation-app/app/services/order_service.py
import os
import json
import psycopg2
from typing import Optional
from app.db import get_db_connection

def create_new_order(client_name: str, aggregation_levels: list, employee_count: int, set_capacity: Optional[int]) -> dict:
    if not client_name.strip():
        return {"success": False, "message": "Название клиента не может быть пустым."}
    if not isinstance(employee_count, int) or employee_count <= 0:
        return {"success": False, "message": "Количество сотрудников должно быть целым числом больше нуля."}

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            levels_json = json.dumps(aggregation_levels)
            cur.execute(
                "INSERT INTO ma_orders (client_name, employee_count, aggregation_levels, set_capacity) VALUES (%s, %s, %s, %s) RETURNING id;",
                (client_name, employee_count, levels_json, set_capacity)
            )
            order_id = cur.fetchone()[0]
            generated_tokens = []
            for _ in range(employee_count):
                cur.execute("INSERT INTO ma_employee_tokens (order_id) VALUES (%s) RETURNING access_token;", (order_id,))
                token = cur.fetchone()[0]
                generated_tokens.append(str(token))
            conn.commit()
            return {"success": True, "message": f"Заказ №{order_id} успешно создан.", "order_id": order_id, "tokens": generated_tokens}
    except Exception as e:
        if conn: conn.rollback()
        print(f"КРИТИЧЕСКАЯ ОШИБКА в create_new_order: {e}")
        return {"success": False, "message": f"Произошла ошибка базы данных: {e}"}
    finally:
        if conn: conn.close()

def get_all_orders() -> list:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, client_name, created_at, status, employee_count FROM ma_orders ORDER BY created_at DESC;")
            return cur.fetchall()
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_all_orders: {e}")
        return []
    finally:
        if conn: conn.close()

def get_order_by_id(order_id: int):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ma_orders WHERE id = %s;", (order_id,))
            order = cur.fetchone()
            # psycopg2 > 2.8 с RealDictCursor автоматически парсит JSONB, дополнительная обработка не нужна.
            return order
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_order_by_id для заказа ID {order_id}: {e}")
        return None
    finally:
        if conn: conn.close()

def update_order(order_id: int, client_name: str, aggregation_levels: list, new_employee_count: int, set_capacity: Optional[int]) -> dict:
    if not client_name.strip():
        return {"success": False, "message": "Название клиента не может быть пустым."}
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT employee_count FROM ma_orders WHERE id = %s;", (order_id,))
            current_count_tuple = cur.fetchone()
            if not current_count_tuple:
                return {"success": False, "message": f"Заказ №{order_id} не найден."}
            current_count = current_count_tuple[0]

            if new_employee_count < current_count:
                return {"success": False, "message": "Ошибка: Количество сотрудников нельзя уменьшить."}
            
            levels_json = json.dumps(aggregation_levels)
            cur.execute(
                "UPDATE ma_orders SET client_name = %s, aggregation_levels = %s, employee_count = %s, set_capacity = %s WHERE id = %s;",
                (client_name, levels_json, new_employee_count, set_capacity, order_id)
            )

            tokens_to_add = new_employee_count - current_count
            if tokens_to_add > 0:
                for _ in range(tokens_to_add):
                    cur.execute("INSERT INTO ma_employee_tokens (order_id) VALUES (%s);", (order_id,))
                message = f"Заказ №{order_id} обновлен. Добавлено {tokens_to_add} новых сотрудников."
            else:
                message = f"Заказ №{order_id} успешно обновлен."
            
            conn.commit()
            return {"success": True, "message": message}
    except Exception as e:
        if conn: conn.rollback()
        return {"success": False, "message": f"Произошла ошибка базы данных при обновлении: {e}"}
    finally:
        if conn: conn.close()
            
def get_tokens_for_order(order_id: int) -> list:
    """Получает все токены для указанного заказа."""
    conn = get_db_connection()
    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT access_token FROM ma_employee_tokens WHERE order_id = %s;", (order_id,))
            return cur.fetchall()
    return []

def assign_name_to_token(access_token: str, employee_name: str) -> bool:
    """Присваивает имя сотрудника токену доступа."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ma_employee_tokens SET employee_name = %s WHERE access_token = %s;",
                (employee_name, access_token)
            )
            # Проверяем, была ли обновлена хотя бы одна строка
            success = cur.rowcount > 0
        conn.commit()
        return success
    except Exception as e:
        if conn: conn.rollback()
        print(f"КРИТИЧЕСКАЯ ОШИБКА в assign_name_to_token: {e}")
        return False
    finally:
        if conn: conn.close()

def get_token_details_by_id(token_id: int):
    """Получает детали токена по его ID, включая имя сотрудника."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ma_employee_tokens WHERE id = %s;", (token_id,))
            return cur.fetchone()
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_token_details_by_id: {e}")
        return None
    finally:
        if conn: conn.close()

def delete_order_completely(order_id: int) -> dict:
    """
    Полностью удаляет заказ и все связанные с ним данные (токены, агрегации).
    Использует каскадное удаление в БД.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ma_orders WHERE id = %s;", (order_id,))
            # Проверяем, была ли удалена хотя бы одна строка
            if cur.rowcount == 0:
                return {"success": False, "message": f"Заказ №{order_id} не найден."}
        conn.commit()
        return {"success": True, "message": f"Заказ №{order_id} и все связанные данные были успешно удалены."}
    except Exception as e:
        if conn: conn.rollback()
        return {"success": False, "message": f"Ошибка при удалении заказа: {e}"}
    finally:
        if conn: conn.close()

def get_aggregations_for_order(order_id: int) -> list:
    """Получает все записи об агрегации для указанного заказа."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, parent_code, parent_type, child_code, child_type, employee_token_id, created_at
                FROM ma_aggregations
                WHERE order_id = %s
                ORDER BY id DESC;
                """,
                (order_id,)
            )
            return cur.fetchall()
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_aggregations_for_order: {e}")
        return []
    finally:
        if conn: conn.close()

def delete_aggregations_by_ids(aggregation_ids: list) -> dict:
    """Удаляет записи об агрегации по списку их ID."""
    if not aggregation_ids:
        return {"success": False, "message": "Не выбрано ни одной записи для удаления."}
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            int_ids = [int(i) for i in aggregation_ids]
            cur.execute("DELETE FROM ma_aggregations WHERE id = ANY(%s);", (int_ids,))
            deleted_count = cur.rowcount
        conn.commit()
        return {"success": True, "message": f"Успешно удалено {deleted_count} записей."}
    except (Exception, ValueError) as e:
        if conn: conn.rollback()
        return {"success": False, "message": f"Ошибка при удалении записей: {e}"}
    finally:
        if conn: conn.close()