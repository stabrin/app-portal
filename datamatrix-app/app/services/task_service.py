import os
import pandas as pd
import csv
from io import StringIO
from app.db import get_db_connection
from app.utils import upsert_data_to_db

def process_aggregation_task_file(order_id: int, file_stream, owner_name: str) -> list:
    """
    ОБНОВЛЕННАЯ ВЕРСИЯ: Обрабатывает CSV файл с заданием на агрегацию,
    ожидая колонки container_id, gtin, sscc.
    """
    logs = []
    logs.append(f"Начало обработки файла с заданием на агрегацию для Заказа №{order_id}.")
    
    try:
        # Автоопределение разделителя
        file_content = file_stream.read().decode('utf-8')
        try:
            dialect = csv.Sniffer().sniff(file_content[:2048])
            delimiter = dialect.delimiter
            logs.append(f"Автоматически определен разделитель: '{delimiter}'")
        except csv.Error:
            logs.append("Не удалось определить разделитель, используется стандартный: ','")
            delimiter = ','

        # Читаем CSV с правильным разделителем
        file_as_string_io = StringIO(file_content)
        df = pd.read_csv(file_as_string_io, dtype=str, sep=delimiter)
        logs.append(f"Файл успешно прочитан, найдено {len(df)} строк.")
        
        # --- ИЗМЕНЕННАЯ ЛОГИКА ---
        
        # Переименовываем вашу колонку "quantity" в "container_id" для единообразия
        # Это делает код устойчивым, даже если клиент пришлет файл со старым заголовком
        if 'quantity' in df.columns and 'container_id' not in df.columns:
             df.rename(columns={'quantity': 'container_id'}, inplace=True)

        # Очищаем имена колонок от пробелов
        df.columns = df.columns.str.strip()
        
        # Проверяем наличие обязательных колонок
        required_cols = {'container_id', 'gtin', 'sscc'}
        if not required_cols.issubset(df.columns):
            logs.append(f"ОШИБКА: В файле отсутствуют обязательные колонки. Требуются: {list(required_cols)}")
            logs.append(f"Найденные колонки: {list(df.columns)}")
            return logs
            
        # Оставляем только нужные колонки и удаляем строки с пустыми значениями в них
        df = df[list(required_cols)].copy()
        df.dropna(inplace=True)
        
        # Валидация данных
        df['sscc'] = df['sscc'].astype(str).str.strip()
        df['gtin'] = df['gtin'].astype(str).str.strip()
        df['container_id'] = df['container_id'].astype(str).str.strip()
        
        # Отфильтровываем некорректные строки (только по gtin и sscc)
        initial_count = len(df)
        df = df[(df['sscc'].str.len() == 18) & (df['sscc'].str.isdigit())]
        df = df[(df['gtin'].str.len() == 14) & (df['gtin'].str.isdigit())]
        # Валидация для container_id не нужна, т.к. это текстовое поле
        
        if len(df) < initial_count:
            logs.append(f"ПРЕДУПРЕЖДЕНИЕ: {initial_count - len(df)} строк были отфильтрованы из-за некорректного формата SSCC или GTIN.")

        if df.empty:
            logs.append("ОШИБКА: В файле не найдено ни одной корректной строки с заданием.")
            return logs

        # Добавляем системные поля
        df['order_id'] = order_id
        df['owner'] = owner_name
        df['status'] = 'pending'

        # Загружаем в базу
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Используем UPSERT по SSCC, чтобы можно было перезагружать файл
            upsert_data_to_db(cur, 'TABLE_AGGREGATION_TASKS', df, 'sscc')
        conn.commit()
        conn.close()

        logs.append(f"Успешно загружено/обновлено {len(df)} заданий на агрегацию.")
        
    except UnicodeDecodeError:
        logs.append("ОШИБКА: Не удалось прочитать файл. Возможно, он имеет неверную кодировку (требуется UTF-8).")
    except Exception as e:
        logs.append(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")

    return logs