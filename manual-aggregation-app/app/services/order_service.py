# manual-aggregation-app/app/services/order_service.py
import os
import json
import psycopg2
import re
import math
from typing import Optional
from app.db import get_db_connection
from .state_service import state_manager

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
                "INSERT INTO ma_orders (client_name, employee_count, aggregation_levels, set_capacity, status) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                (client_name, employee_count, levels_json, set_capacity, 'new')
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

def update_order(order_id: int, client_name: str, aggregation_levels: list, new_employee_count: int, set_capacity: Optional[int], status: str) -> dict:
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
                "UPDATE ma_orders SET client_name = %s, aggregation_levels = %s, employee_count = %s, set_capacity = %s, status = %s WHERE id = %s;",
                (client_name, levels_json, new_employee_count, set_capacity, status, order_id)
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
            cur.execute("SELECT access_token FROM ma_employee_tokens WHERE order_id = %s ORDER BY id ASC;", (order_id,))
            return cur.fetchall()
    return []

def get_token_ids_for_order(order_id: int) -> list[int]:
    """Получает все ID токенов для указанного заказа."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM ma_employee_tokens WHERE order_id = %s ORDER BY id ASC;", (order_id,))
            # fetchall returns a list of tuples, e.g., [(1,), (2,)]
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_token_ids_for_order: {e}")
        return []
    finally:
        if conn: conn.close()

def create_work_session(access_token: str, employee_name: str) -> Optional[int]:
    """
    Создает новую рабочую сессию для сотрудника и возвращает ее ID.
    Проверяет, что для данного пропуска нет другой активной сессии.
    """
    from .state_service import state_manager
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. Найти ID токена по его значению
            cur.execute("SELECT id FROM ma_employee_tokens WHERE access_token = %s;", (access_token,))
            token_record = cur.fetchone()
            if not token_record:
                return None # Токен не найден

            employee_token_id = token_record[0]

            # 2. Проверяем, нет ли уже активной сессии для этого пропуска
            if not state_manager.acquire_session_lock(employee_token_id):
                # Не удалось получить блокировку, значит, сессия уже активна.
                print(f"ПРЕДУПРЕЖДЕНИЕ: Попытка входа по пропуску ID {employee_token_id}, у которого уже есть активная сессия.")
                # Возвращаем None, чтобы сигнализировать об ошибке входа.
                # В вызывающем коде (в routes) это должно обрабатываться как ошибка "Сессия уже активна".
                return None

            # 3. Создать новую запись в ma_work_sessions
            cur.execute(
                "INSERT INTO ma_work_sessions (employee_token_id, employee_name) VALUES (%s, %s) RETURNING id;",
                (employee_token_id, employee_name)
            )
            session_id = cur.fetchone()[0]

            # 4. Опционально: если у токена еще нет имени, запишем его в первый раз как "основное".
            cur.execute("UPDATE ma_employee_tokens SET employee_name = %s WHERE id = %s AND (employee_name IS NULL OR employee_name = '');", (employee_name, employee_token_id))

        conn.commit()
        return session_id
    except Exception as e:
        if conn: conn.rollback()
        print(f"КРИТИЧЕСКАЯ ОШИБКА в create_work_session: {e}")
        return None
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

def _check_code_validity(code: str) -> dict:
    """Проверяет код из БД на валидность и возвращает статус."""
    if not isinstance(code, str):
        return {'is_valid': False, 'reason': 'Не является строкой'}

    if re.search('[а-яА-Я]', code):
        return {'is_valid': False, 'reason': 'Кириллица в коде'}

    for char in code:
        char_code = ord(char)
        # Разрешаем только сам GS (29), Tab (9), LF (10), CR (13).
        if char_code < 32 and char_code not in [9, 10, 13, 29]:
            return {'is_valid': False, 'reason': f'Недопустимый символ (код {char_code})'}
    
    return {'is_valid': True, 'reason': 'OK'}

class AggregationResult(list):
    """Кастомный класс для возврата результатов агрегации вместе со сводкой и пагинацией."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.summary = {
            'total_sets': 0,
            'error_sets': 0,
        }
        self.pagination = None

def _get_code_model_for_aggregations(aggregations: list) -> dict:
    """
    Анализирует предоставленный список агрегаций и возвращает модель префиксов.
    Работает ТОЛЬКО на основе образцовых наборов.
    """
    from collections import Counter

    # --- "Обучение" на основе первых образцовых наборов ---
    identified_product_prefixes = set()
    identified_set_prefixes = set()
    learning_successful = False

    # 1. Группируем записи по родительским наборам
    sets_by_parent = {}
    for agg in aggregations:
        if agg.get('parent_type') == 'set':
            parent_code = agg['parent_code']
            if parent_code not in sets_by_parent:
                # Сохраняем первую запись (с мин. ID), чтобы знать "возраст" набора
                sets_by_parent[parent_code] = {'id': agg['id'], 'children': []}
            sets_by_parent[parent_code]['children'].append(agg['child_code'])
    
    # 2. Сортируем наборы по ID их первой записи, чтобы найти самые ранние
    sorted_parents = sorted(sets_by_parent.items(), key=lambda item: item[1]['id'])
    
    # 3. Берем до 3 первых наборов как образцы для обучения
    exemplar_sets = sorted_parents[:3]

    if exemplar_sets:
        temp_set_prefixes = set()
        temp_product_prefixes = set()
        for parent_code, data in exemplar_sets:
            if parent_code and len(parent_code) >= 16:
                temp_set_prefixes.add(parent_code[:16])
            for child_code in data['children']:
                if child_code and len(child_code) >= 16:
                    temp_product_prefixes.add(child_code[:16])
        
        # 4. Если префиксы товаров и наборов не пересекаются, считаем обучение успешным
        if temp_set_prefixes and temp_product_prefixes and not (temp_set_prefixes & temp_product_prefixes):
            identified_set_prefixes = temp_set_prefixes
            identified_product_prefixes = temp_product_prefixes
            learning_successful = True

    return {
        'product_prefixes': identified_product_prefixes,
        'set_prefixes': identified_set_prefixes,
        'learning_successful': learning_successful
    }

def get_trained_code_model(order_id: int) -> dict:
    """Обертка для получения модели из state_manager."""
    return state_manager.get_trained_model(order_id)

def build_and_save_model_and_samples(order_id: int, employee_token_id: int, work_session_id: int, samples: list) -> dict:
    """
    Строит модель на основе предоставленных образцов, сохраняет модель в Redis
    и сами образцы в БД.
    `samples` is a list of dicts: [{'parent_code': '...', 'items': ['...', '...']}]
    """
    if len(samples) < 3:
        return {'success': False, 'message': 'Недостаточно образцов для обучения.'}

    # 1. Формируем "псевдо-агрегации" для анализа
    pseudo_aggregations = []
    pseudo_id = 1
    for sample in samples:
        parent_code = sample['parent_code']
        for child_code in sample['items']:
            pseudo_aggregations.append({
                'id': pseudo_id,
                'parent_code': parent_code,
                'parent_type': 'set',
                'child_code': child_code,
                'child_type': 'product' # Предполагаем, что в наборе - товары
            })
            pseudo_id += 1
    
    # 2. Строим модель
    model = _get_code_model_for_aggregations(pseudo_aggregations)

    if not model.get('learning_successful'):
        return {
            'success': False,
            'message': 'Ошибка обучения: не удалось однозначно определить товары и наборы. Возможно, их префиксы пересекаются. Соберите образцы заново.'
        }

    # 3. Сохраняем модель в Redis
    state_manager.save_trained_model(order_id, model)

    # 4. Сохраняем сами образцы в базу данных
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values
            args_list = []
            for agg in pseudo_aggregations:
                args_list.append((order_id, employee_token_id, work_session_id, agg['child_code'], agg['child_type'], agg['parent_code'], agg['parent_type']))
            
            execute_values(
                cur,
                "INSERT INTO ma_aggregations (order_id, employee_token_id, work_session_id, child_code, child_type, parent_code, parent_type) VALUES %s",
                args_list
            )
        conn.commit()
    except Exception as e:
        if conn: conn.rollback()
        print(f"КРИТИЧЕСКАЯ ОШИБКА при сохранении образцов в БД: {e}")
        return {'success': False, 'message': 'Критическая ошибка: не удалось сохранить образцы в базу данных.'}
    finally:
        if conn: conn.close()

    return {'success': True, 'model': model}

def get_erroneous_sets(order_id: int) -> list[str]:
    """
    Анализирует все агрегации заказа и возвращает список кодов родительских
    наборов ('set'), в которых есть хотя бы одна ошибка.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Получаем все записи для анализа
            cur.execute(
                "SELECT id, parent_code, parent_type, child_code FROM ma_aggregations WHERE order_id = %s ORDER BY id ASC;",
                (order_id,)
            )
            aggregations = cur.fetchall()
        
        if not aggregations:
            return []

        # Используем обученную модель, если она есть, для консистентности проверок
        trained_model = state_manager.get_trained_model(order_id)
        if trained_model and trained_model.get('learning_successful'):
            code_model = trained_model
        else:
            code_model = _get_code_model_for_aggregations(aggregations)

        identified_product_prefixes = code_model['product_prefixes']
        identified_set_prefixes = code_model['set_prefixes']

        sets_with_errors = set()
        for agg in aggregations:
            child_validity = _check_code_validity(agg.get('child_code'))
            parent_validity = _check_code_validity(agg.get('parent_code'))
            has_error = False

            if agg.get('parent_type') == 'set':
                parent_code = agg.get('parent_code')
                child_code = agg.get('child_code')

                if not parent_validity['is_valid'] or (parent_code and len(parent_code) >= 16 and parent_code[:16] in identified_product_prefixes):
                    has_error = True
                
                if not child_validity['is_valid'] or (child_code and len(child_code) >= 16 and child_code[:16] in identified_set_prefixes):
                    has_error = True
            
            if has_error and agg.get('parent_type') == 'set':
                sets_with_errors.add(agg['parent_code'])
        
        return list(sets_with_errors)
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_erroneous_sets: {e}")
        return []
    finally:
        if conn: conn.close()

class AggregationResult(list):
    """Кастомный класс для возврата результатов агрегации вместе со сводкой и пагинацией."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.summary = {
            'total_sets': 0,
            'error_sets': 0,
        }
        self.pagination = None

def get_aggregations_for_order(order_id: int, page: int = 1, per_page: int = 1000) -> AggregationResult:
    """
    Получает все записи об агрегации для указанного заказа,
    включая имя сотрудника, проверку валидности кодов, сортировку по ошибкам и пагинацию.
    
    Использует механизм "обучения" на первых 3-х наборах для определения
    эталонных префиксов товаров и упаковок.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    agg.id,
                    agg.parent_code,
                    agg.parent_type,
                    agg.child_code,
                    agg.child_type,
                    COALESCE(
                        ws.employee_name, -- 1. Имя из новой таблицы сессий (приоритет)
                        tok.employee_name, -- 2. Имя из старой таблицы токенов (для обратной совместимости)
                        'ID ' || agg.employee_token_id::text -- 3. Запасной вариант
                    ) as employee_name,
                    agg.created_at
                FROM ma_aggregations as agg
                LEFT JOIN ma_work_sessions as ws ON agg.work_session_id = ws.id
                LEFT JOIN ma_employee_tokens as tok ON agg.employee_token_id = tok.id
                WHERE agg.order_id = %s
                ORDER BY agg.id ASC;
                """,
                (order_id,)
            )
            aggregations = cur.fetchall()
            if not aggregations:
                return AggregationResult()

            # --- Анализ и получение модели кодов ---
            trained_model = state_manager.get_trained_model(order_id)
            if trained_model and trained_model.get('learning_successful'):
                code_model = trained_model
            else:
                code_model = _get_code_model_for_aggregations(aggregations)

            identified_product_prefixes = code_model['product_prefixes']
            identified_set_prefixes = code_model['set_prefixes']

            # --- Проверка каждой записи на физическую и логическую корректность ---
            for agg in aggregations:
                agg['child_validity'] = _check_code_validity(agg.get('child_code'))
                agg['parent_validity'] = _check_code_validity(agg.get('parent_code'))

                if agg.get('parent_type') == 'set':
                    parent_code = agg.get('parent_code')
                    child_code = agg.get('child_code')

                    if parent_code and len(parent_code) >= 16 and agg['parent_validity']['is_valid']:
                        if parent_code[:16] in identified_product_prefixes:
                            agg['parent_validity']['is_valid'] = False
                            agg['parent_validity']['reason'] = 'Логическая ошибка: Набор закрыт кодом товара.'

                    if child_code and len(child_code) >= 16 and agg['child_validity']['is_valid']:
                        if child_code[:16] in identified_set_prefixes:
                            agg['child_validity']['is_valid'] = False
                            agg['child_validity']['reason'] = 'Логическая ошибка: В набор вложен другой набор.'
                
                # Добавляем флаг ошибки для упрощения сортировки
                agg['has_error'] = not (agg['child_validity']['is_valid'] and agg['parent_validity']['is_valid'])

            # --- Расчет итоговой сводки по наборам (на основе всех данных) ---
            sets_with_errors = set()
            all_sets = set()
            for agg in aggregations:
                if agg.get('parent_type') == 'set':
                    all_sets.add(agg['parent_code'])
                    if agg['has_error']:
                        sets_with_errors.add(agg['parent_code'])

            # --- Сортировка: сначала ошибочные, затем по ID в обратном порядке ---
            aggregations.sort(key=lambda x: (x.get('has_error', False), x['id']), reverse=True)

            # --- Пагинация ---
            total_items = len(aggregations)
            start_index = (page - 1) * per_page
            end_index = start_index + per_page
            paginated_aggregations = aggregations[start_index:end_index]

            # --- Формирование финального результата ---
            result = AggregationResult(paginated_aggregations)
            result.summary['total_sets'] = len(all_sets)
            result.summary['error_sets'] = len(sets_with_errors)
            result.pagination = {
                'page': page,
                'per_page': per_page,
                'total_items': total_items,
                'total_pages': math.ceil(total_items / per_page) if per_page > 0 else 0
            }
            return result
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_aggregations_for_order: {e}")
        return AggregationResult()
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