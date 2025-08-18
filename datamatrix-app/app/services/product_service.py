import os
import io
import pandas as pd
from app.db import get_db_connection
from app.utils import upsert_data_to_db

def get_all_products():
    """Возвращает список всех продуктов из справочника в виде словарей."""
    conn = get_db_connection()
    products_table = os.getenv('TABLE_PRODUCTS')
    # Используем with conn, чтобы соединение закрывалось автоматически
    with conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {products_table} ORDER BY name")
            # Преобразуем кортежи в словари для удобства
            columns = [desc[0] for desc in cur.description]
            products = [dict(zip(columns, row)) for row in cur.fetchall()]
    return products

def add_product(gtin: str, name: str, desc1: str, desc2: str, desc3: str) -> dict:
    """
    Добавляет или обновляет один продукт, предварительно проверив длину GTIN.
    Возвращает словарь с результатом операции.
    """
    # --- НОВАЯ ПРОВЕРКА ---
    if len(str(gtin).strip()) != 14:
        return {"success": False, "message": f"Ошибка: GTIN '{gtin}' должен содержать ровно 14 символов."}

    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                product_df = pd.DataFrame([{
                    'gtin': str(gtin).strip(), # Убираем лишние пробелы на всякий случай
                    'name': name,
                    'description_1': desc1,
                    'description_2': desc2,
                    'description_3': desc3
                }])
                upsert_data_to_db(cur, 'TABLE_PRODUCTS', product_df, 'gtin')
        
        return {"success": True, "message": f"Товар с GTIN {gtin} был успешно добавлен/обновлен."}
    except Exception as e:
        # Логируем ошибку на сервере для отладки
        print(f"ERROR in add_product: {e}") 
        return {"success": False, "message": "Произошла ошибка при записи в базу данных."}


def generate_excel_template() -> io.BytesIO:
    """Создает Excel-файл шаблона в памяти и возвращает его."""
    headers = ['gtin', 'name', 'description_1', 'description_2', 'description_3']
    df = pd.DataFrame(columns=headers)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Products')
        workbook  = writer.book
        worksheet = writer.sheets['Products']
        text_format = workbook.add_format({'num_format': '@'})
        worksheet.set_column('A:E', 25, text_format)
    output.seek(0)
    return output

def process_excel_upload(file_stream) -> dict:
    """
    Обрабатывает загруженный Excel-файл, проверяет все GTIN и добавляет/обновляет продукты.
    Возвращает словарь с результатом операции.
    """
    try:
        df = pd.read_excel(file_stream, dtype=str)
        
        required_cols = ['gtin', 'name']
        if not all(col in df.columns for col in required_cols):
            return {"success": False, "message": "Ошибка: в файле отсутствуют обязательные колонки 'gtin' и/или 'name'."}
            
        for col in ['description_1', 'description_2', 'description_3']:
            if col not in df.columns:
                df[col] = ''
        df.fillna('', inplace=True)
        
        df_to_upload = df[['gtin', 'name', 'description_1', 'description_2', 'description_3']]

        # --- НОВАЯ ПРОВЕРКА ДЛИНЫ GTIN ДЛЯ ВСЕХ СТРОК ---
        df_to_upload['gtin'] = df_to_upload['gtin'].str.strip()
        invalid_gtins = df_to_upload[df_to_upload['gtin'].str.len() != 14]
        
        if not invalid_gtins.empty:
            error_message = "Ошибка: Найдены GTIN с некорректной длиной (не 14 символов). Загрузка отменена. Проблемные строки:\n"
            for index, row in invalid_gtins.head(5).iterrows(): # Показываем первые 5 ошибок
                error_message += f" - Строка {index + 2}: GTIN '{row['gtin']}'\n"
            return {"success": False, "message": error_message}
        # --- КОНЕЦ ПРОВЕРКИ ---

        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                upsert_data_to_db(cur, 'TABLE_PRODUCTS', df_to_upload, 'gtin')
        
        return {"success": True, "message": f"Успешно обработано {len(df_to_upload)} записей."}

    except Exception as e:
        print(f"ERROR in process_excel_upload: {e}") 
        return {"success": False, "message": f"Произошла критическая ошибка при обработке файла: {e}"}