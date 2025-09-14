# manual-aggregation-app/app/services/report_service.py
from collections import defaultdict
from app.db import get_db_connection
import pandas as pd
import io
import psycopg2.extras

def get_aggregation_report_for_order(order_id: int) -> dict:
    """
    Формирует структурированный отчет по агрегации для конкретного заказа.
    Возвращает древовидную структуру вложений и сводку по сотрудникам.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Получаем все связи для данного заказа, включая ID сотрудника
            cur.execute(
                """
                SELECT 
                    agg.parent_code, 
                    agg.parent_type, 
                    agg.child_code, 
                    agg.child_type,
                    COALESCE(
                        ws.employee_name, 
                        tok.employee_name, 
                        'ID ' || tok.id::text
                    ) as employee_name
                FROM ma_aggregations as agg
                LEFT JOIN ma_employee_tokens as tok ON agg.employee_token_id = tok.id
                LEFT JOIN ma_work_sessions as ws ON agg.work_session_id = ws.id
                WHERE agg.order_id = %s
                ORDER BY agg.parent_code, agg.child_code;
                """,
                (order_id,)
            )
            rows = cur.fetchall()

        if not rows:
            return {"order_id": order_id, "tree": None, "employee_summary": None}

        # Используем defaultdict для удобного группирования
        tree = defaultdict(lambda: {'type': '', 'children': []})
        # Словарь для подсчета уникальных упаковок по каждому сотруднику
        employee_summary = defaultdict(set)

        for row in rows:
            parent_code = row['parent_code']
            employee_name = row['employee_name']
            
            # Собираем дерево
            tree[parent_code]['type'] = row['parent_type']
            tree[parent_code]['children'].append({
                "code": row['child_code'],
                "type": row['child_type']
            })
            
            # Собираем данные для сводки: добавляем код упаковки в set сотрудника
            employee_summary[employee_name].add(parent_code)

        # Преобразуем сеты в количество уникальных упаковок
        final_summary = {emp_name: len(parent_codes) for emp_name, parent_codes in employee_summary.items()}
            
        return {"order_id": order_id, "tree": dict(tree), "employee_summary": final_summary}

    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в get_aggregation_report_for_order: {e}")
        return {"order_id": order_id, "tree": None, "employee_summary": None, "error": str(e)}
    finally:
        if conn:
            conn.close()

def generate_aggregation_excel_report(order_id: int) -> io.BytesIO:
    """
    Создает Excel-отчет со всеми агрегациями для заказа.
    Колонки с кодами форматируются как текст.
    """
    conn = None
    try:
        conn = get_db_connection()
        # Выбираем все необходимые поля для отчета
        query = """
            SELECT 
                agg.id,
                agg.parent_code,
                agg.parent_type,
                agg.child_code,
                agg.child_type,
                COALESCE(
                    ws.employee_name, 
                    tok.employee_name, 
                    'ID ' || agg.employee_token_id::text
                ) as employee_name,
                agg.created_at
            FROM ma_aggregations as agg
            LEFT JOIN ma_employee_tokens as tok ON agg.employee_token_id = tok.id
            LEFT JOIN ma_work_sessions as ws ON agg.work_session_id = ws.id
            WHERE agg.order_id = %s
            ORDER BY agg.id;
        """
        # Используем pandas для удобного чтения из SQL и записи в Excel
        df = pd.read_sql_query(query, conn, params=(order_id,))

        # Переименовываем колонки для понятности в отчете
        df.rename(columns={
            'id': 'ID Записи',
            'parent_code': 'Код упаковки (родитель)',
            'parent_type': 'Тип упаковки',
            'child_code': 'Код вложения (потомок)',
            'child_type': 'Тип вложения',
            'employee_name': 'Сотрудник',
            'created_at': 'Время операции'
        }, inplace=True)

        # --- ИСПРАВЛЕНИЕ ---
        # Убираем информацию о временной зоне из колонки с датой,
        # так как библиотека для записи в Excel ее не поддерживает.
        if 'Время операции' in df.columns:
            df['Время операции'] = df['Время операции'].dt.tz_localize(None)

        # Создаем Excel файл в памяти
        output_buffer = io.BytesIO()
        with pd.ExcelWriter(output_buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name=f'Отчет по заказу {order_id}')
            
            workbook = writer.book
            worksheet = writer.sheets[f'Отчет по заказу {order_id}']
            text_format = workbook.add_format({'num_format': '@'})
            
            worksheet.set_column('B:B', 30, text_format) 
            worksheet.set_column('D:D', 30, text_format)
            worksheet.autofit()

        output_buffer.seek(0)
        return output_buffer

    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в generate_aggregation_excel_report: {e}")
        raise
    finally:
        if conn:
            conn.close()