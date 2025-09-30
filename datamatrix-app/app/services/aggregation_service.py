import os
import re
from pathlib import Path
from datetime import date
import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values
from werkzeug.utils import secure_filename
import codecs
from app.services.tobacco_service import parse_tobacco_dm
from app.db import get_db_connection
from app.utils import upsert_data_to_db

# --- КОНСТАНТЫ И НАСТРОЙКИ ---
GCP = '466941739999'
OWNER_NAME = 'wed-ug'
GS_SEPARATOR = '\x1d'
SERIAL_NUMBER_LENGTH = 16 - len(GCP)
SERIAL_NUMBER_CAPACITY = 10 ** SERIAL_NUMBER_LENGTH
# НОВЫЕ КОНСТАНТЫ ДЛЯ ПРЕДУПРЕЖДЕНИЯ
SSCC_TOTAL_CAPACITY = 9 * SERIAL_NUMBER_CAPACITY  # 9 * 10000 = 90000
SSCC_WARNING_THRESHOLD = SSCC_TOTAL_CAPACITY - 10000 # Порог срабатывания = 80000

# --- ФУНКЦИИ-ПОМОЩНИКИ ---

def analyze_filename(filename: str) -> dict:
    """
    Разбирает имя файла. Если начинается с "Тираж_", извлекает номер.
    Иначе возвращает номер тиража '0'.
    """
    pattern = re.compile(r"^Тираж_(\d+).*")
    match = pattern.match(filename)
    if match:
        return {"tirazh_number": match.group(1)}
    return {"tirazh_number": "0"}

def parse_datamatrix(dm_string: str) -> dict:
    """Разбирает (парсит) строку DataMatrix на составные части."""
    result = {
        'datamatrix': dm_string, 'gtin': '', 'serial': '',
        'crypto_part_91': '', 'crypto_part_92': '', 'crypto_part_93': ''
    }
    cleaned_dm = dm_string.replace(' ', GS_SEPARATOR).strip()
    parts = cleaned_dm.split(GS_SEPARATOR)
    if len(parts) > 0:
        main_part = parts.pop(0)
        if main_part.startswith('01'):
            result['gtin'] = main_part[2:16]
            serial_part = main_part[16:]
            if serial_part.startswith('21'):
                result['serial'] = serial_part[2:]
    for part in parts:
        if not part: continue
        if part.startswith('91'): result['crypto_part_91'] = part[2:]
        elif part.startswith('92'): result['crypto_part_92'] = part[2:]
        elif part.startswith('93'): result['crypto_part_93'] = part[2:]
    return result

def calculate_sscc_check_digit(base_sscc: str) -> int:
    """Вычисляет контрольную цифру для 17-значного SSCC по алгоритму GS1."""
    if len(base_sscc) != 17:
        raise ValueError("База для SSCC должна содержать 17 цифр")
    total_sum = 0
    for i, digit_char in enumerate(reversed(base_sscc)):
        digit = int(digit_char)
        if i % 2 == 0:
            total_sum += digit * 3
        else:
            total_sum += digit * 1
    return (10 - (total_sum % 10)) % 10

def generate_sscc(sscc_id: int) -> tuple[str, str]:
    """Вспомогательная функция для генерации SSCC."""
    extension_digit = (sscc_id // SERIAL_NUMBER_CAPACITY) % 9 + 1
    serial_number = sscc_id % SERIAL_NUMBER_CAPACITY
    serial_part = str(serial_number).zfill(SERIAL_NUMBER_LENGTH)
    base_sscc = str(extension_digit) + GCP + serial_part
    check_digit = calculate_sscc_check_digit(base_sscc)
    full_sscc = base_sscc + str(check_digit)
    return base_sscc, full_sscc

def read_and_increment_counter(cursor, counter_name: str, increment_by: int = 1) -> tuple[int, str | None]:
    """
    Атомарно читает и увеличивает счетчик в БД.
    Возвращает кортеж (новое_значение, сообщение_с_предупреждением | None).
    """
    cursor.execute(
        "SELECT current_value FROM system_counters WHERE counter_name = %s FOR UPDATE;",
        (counter_name,)
    )
    current_value = cursor.fetchone()[0]
    new_value = current_value + increment_by
    
    warning_message = None
    # Проверяем только счетчик SSCC, чтобы не влиять на другие возможные счетчики
    if counter_name == 'sscc_id':
        if new_value >= SSCC_TOTAL_CAPACITY:
            # Это уже не предупреждение, а критическая ошибка. Останавливаем процесс.
            raise ValueError(
                f"КРИТИЧЕСКАЯ ОШИБКА: Счетчик SSCC ИСЧЕРПАН (id={new_value})!  Нужно менять GCP "
                f"Дальнейшая генерация приведет к дубликатам кодов."
                f"Пожалуйста, свяжитесь с администратором системы. !!!"
            )
        elif new_value >= SSCC_WARNING_THRESHOLD:
            remaining = SSCC_TOTAL_CAPACITY - new_value
            warning_message = (
                f"!!! ВНИМАНИЕ: Ресурс счетчика SSCC подходит к концу. Нужно менять GCP "
                f"Текущее значение: {new_value} из {SSCC_TOTAL_CAPACITY}. "
                f"Осталось уникальных кодов: {remaining}. "
                f"Пожалуйста, свяжитесь с администратором системы. !!!"
            )

    cursor.execute(
        "UPDATE system_counters SET current_value = %s WHERE counter_name = %s;",
        (new_value, counter_name)
    )
    
    return new_value, warning_message

# --- ОСНОВНАЯ СЕРВИСНАЯ ФУНКЦИЯ ---

def run_aggregation_process(order_id: int, files: list, dm_type: str, aggregation_mode: str, level1_qty: int, level2_qty: int, level3_qty: int) -> list:
    """Основная функция, которая выполняет весь процесс, включая многоуровневую агрегацию."""
    logs = []
    logs.append(f"Запуск обработки для Заказа №{order_id}...")
    
    upload_folder = Path('/app/uploads')
    upload_folder.mkdir(exist_ok=True)
    saved_files_paths_with_original_names = []
    
    for file in files:
        if file and file.filename:
            original_filename = file.filename
            safe_filename = secure_filename(file.filename)
            if not safe_filename: safe_filename = "unnamed_file"
            save_path = upload_folder  / f"{order_id}_{safe_filename}" # Добавляем префикс заказа для уникальности
            file.save(save_path)
            saved_files_paths_with_original_names.append((save_path, original_filename))
            logs.append(f"Файл '{original_filename}' успешно загружен.")
    
    if not saved_files_paths_with_original_names:
        logs.append("ОШИБКА: Не было передано ни одного файла для обработки.")
        return logs

    all_dm_data = []
    total_lines_processed = 0
    total_lines_skipped = 0
    
    file_counter = 1 
    for file_path, original_filename in saved_files_paths_with_original_names:
        logs.append(f"--- Читаю файл №{file_counter}: {original_filename} (Тип кодов: {dm_type}) ---")
        
        file_info = analyze_filename(original_filename)
        if file_info['tirazh_number'] != '0':
            tirazh_num = file_info['tirazh_number']
            logs.append(f"  -> Номер тиража определен из имени файла: {tirazh_num}")
        else:
            tirazh_num = str(file_counter)
            logs.append(f"  -> Номер тиража присвоен по порядку: {tirazh_num}")
        
        try:
            lines = []
            if dm_type == 'tobacco':
                logs.append("  -> Использую специфический метод чтения для табачных кодов (codecs.open).")
                with codecs.open(file_path, 'r', encoding='utf-8-sig') as f:
                    lines = f.readlines()
            else: # 'standard'
                logs.append("  -> Использую стандартный метод чтения (open).")
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            
            logs.append(f"  [Отладка] Файл прочитан. Общее количество полученных строк: {len(lines)}")

            for line_num, line in enumerate(lines, 1):
                dm_string = line.strip()
                if not dm_string:
                    continue
                
                total_lines_processed += 1

                if dm_type == 'tobacco':
                    parsed_data = parse_tobacco_dm(dm_string)
                    # Добавим более надежную проверку
                    if parsed_data is None or parsed_data.get("error"):
                        error_msg = parsed_data.get("error", "неверный формат") if parsed_data else "неверная длина"
                        logs.append(f"  -> Пропущена строка {line_num} (табак): {error_msg}")
                        total_lines_skipped += 1
                        continue
                else: # 'standard'
                    parsed_data = parse_datamatrix(dm_string)

                # Эта проверка теперь общая для всех типов DM
                if not parsed_data.get('gtin'):
                    logs.append(f"  -> Пропущена строка {line_num}: не удалось распознать GTIN.")
                    total_lines_skipped += 1
                    continue
                    
                # Этот блок тоже становится общим
                parsed_data['order_id'] = order_id
                parsed_data['tirage_number'] = tirazh_num
                all_dm_data.append(parsed_data)
        
        except UnicodeDecodeError:
            logs.append(f"ОШИБКА: Файл '{original_filename}' имеет неверную кодировку (не UTF-8). Файл пропущен.")
            continue
        except Exception as e:
            logs.append(f"ОШИБКА при чтении файла '{original_filename}': {e}. Файл пропущен.")
            continue
            
        file_counter += 1

    saved_file_paths = [item[0] for item in saved_files_paths_with_original_names]

    if not all_dm_data:
        logs.append("Не найдено корректных кодов DataMatrix в файлах.")
        for path in saved_file_paths:
            try: os.remove(path)
            except OSError: pass
        return logs
        
    items_df = pd.DataFrame(all_dm_data)
    logs.append(f"\nВсего найдено и разобрано {len(items_df)} кодов DataMatrix.")
    logs.append("Проверяю, не были ли эти коды обработаны ранее...")
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # --- НОВОЕ: Получаем начальный статус заказа ---
            orders_table_for_status = os.getenv('TABLE_ORDERS')
            cur.execute(f"SELECT status FROM {orders_table_for_status} WHERE id = %s", (order_id,))
            status_result = cur.fetchone()
            if not status_result:
                logs.append(f"КРИТИЧЕСКАЯ ОШИБКА: Заказ с ID {order_id} не найден.")
                if conn: conn.close()
                return logs
            initial_order_status = status_result[0]

            # Проверка на существование кодов
            dm_to_check = tuple(items_df['datamatrix'].unique())
            if dm_to_check:
                items_table = os.getenv('TABLE_ITEMS')
                cur.execute(f"SELECT datamatrix, order_id FROM {items_table} WHERE datamatrix IN %s", (dm_to_check,))
                existing_codes = cur.fetchall()
                if existing_codes:
                    # Если найдены существующие коды, прерываем процесс
                    error_msg = "\nОШИБКА: Обнаружены коды, которые уже были обработаны в других заказах. Процесс прерван. Список кодов и их заказов:\n"
                    for code, old_order_id in existing_codes:
                        error_msg += f"  - Код (последние 10 символов): ...{code[-10:]}, уже в заказе: {old_order_id}\n"
                    logs.append(error_msg)
                    # Важно вернуть логи и прервать выполнение, закрыв соединение и удалив файлы
                    if conn: conn.close()
                    for path in saved_file_paths:
                        try: os.remove(path)
                        except OSError: pass
                    return logs
            
            logs.append("Проверка на дубликаты пройдена успешно. Ранее обработанных кодов не найдено.")
            unique_gtins_in_upload = items_df['gtin'].unique()
            logs.append(f"\nПроверяю наличие {len(unique_gtins_in_upload)} уникальных GTIN в справочнике...")
            gtins_tuple = tuple(unique_gtins_in_upload)
            if not gtins_tuple:
                 existing_gtins = set()
            else:
                products_table = os.getenv('TABLE_PRODUCTS')
                cur.execute(f"SELECT gtin FROM {products_table} WHERE gtin IN %s", (gtins_tuple,))
                existing_gtins = {row[0] for row in cur.fetchall()}
            
            new_gtins = [gtin for gtin in unique_gtins_in_upload if gtin not in existing_gtins]
            
            if new_gtins:
                logs.append(f"Найдено {len(new_gtins)} новых GTIN. Создаю для них заглушки...")
                new_products_data = [{'gtin': gtin, 'name': f'Товар (GTIN: {gtin})'} for gtin in new_gtins]
                new_products_df = pd.DataFrame(new_products_data)
                upsert_data_to_db(cur, 'TABLE_PRODUCTS', new_products_df, 'gtin')
            else:
                logs.append("Все GTIN из загрузки уже есть в справочнике.")

            packages_df = pd.DataFrame()
            if aggregation_mode in ['level1', 'level2', 'level3']:
                logs.append(f"\nНачинаю агрегацию...")
                all_packages = []
                items_df['package_id'] = None
                
                for gtin, group in items_df.groupby('gtin'):
                    logs.append(f"--- Агрегирую GTIN: {gtin} ({len(group)} шт.) в короба ---")
                    item_indices = group.index.tolist()
                    for i in range(0, len(item_indices), level1_qty):
                        chunk_indices = item_indices[i:i + level1_qty]
                        box_id, warning = read_and_increment_counter(cur, 'sscc_id')
                        if warning and warning not in logs:
                            logs.append(warning)
                        items_df.loc[chunk_indices, 'package_id'] = box_id
                        _, full_sscc = generate_sscc(box_id)
                        all_packages.append({'id': box_id, 'sscc': full_sscc, 'owner': OWNER_NAME, 'level': 1, 'parent_id': None})
                        logs.append(f"  -> Создан короб (ID: {box_id}, SSCC: {full_sscc}) для {len(chunk_indices)} шт.")
                
                packages_df = pd.DataFrame(all_packages)

                if aggregation_mode in ['level2', 'level3']:
                    logs.append("\n--- Создаю паллеты (уровень 2) ---")
                    all_box_ids = packages_df[packages_df['level'] == 1]['id'].tolist()
                    for i in range(0, len(all_box_ids), level2_qty):
                        boxes_on_pallet_ids = all_box_ids[i:i + level2_qty]
                        pallet_id, warning = read_and_increment_counter(cur, 'sscc_id')
                        if warning and warning not in logs:
                            logs.append(warning)
                        packages_df.loc[packages_df['id'].isin(boxes_on_pallet_ids), 'parent_id'] = pallet_id
                        _, full_sscc = generate_sscc(pallet_id)
                        pallet_record = pd.DataFrame([{'id': pallet_id, 'sscc': full_sscc, 'owner': OWNER_NAME, 'level': 2, 'parent_id': None}])
                        packages_df = pd.concat([packages_df, pallet_record], ignore_index=True)
                        logs.append(f"  -> Создана паллета (ID: {pallet_id}, SSCC: {full_sscc}) для {len(boxes_on_pallet_ids)} коробов.")

                if aggregation_mode == 'level3':
                    logs.append("\n--- Создаю контейнеры (уровень 3) ---")
                    all_pallet_ids = packages_df[packages_df['level'] == 2]['id'].tolist()
                    for i in range(0, len(all_pallet_ids), level3_qty):
                        pallets_in_container_ids = all_pallet_ids[i:i + level3_qty]
                        container_id, warning = read_and_increment_counter(cur, 'sscc_id')
                        if warning and warning not in logs:
                            logs.append(warning)
                        packages_df.loc[packages_df['id'].isin(pallets_in_container_ids), 'parent_id'] = container_id
                        _, full_sscc = generate_sscc(container_id)
                        container_record = pd.DataFrame([{'id': container_id, 'sscc': full_sscc, 'owner': OWNER_NAME, 'level': 3, 'parent_id': None}])
                        packages_df = pd.concat([packages_df, container_record], ignore_index=True)
                        logs.append(f"  -> Создан контейнер (ID: {container_id}, SSCC: {full_sscc}) для {len(pallets_in_container_ids)} паллет.")
            else:
                items_df['package_id'] = None
                logs.append("\nАгрегация не требуется.")
            
            if not packages_df.empty:
                logs.append(f"\nЗагружаю {len(packages_df)} упаковок в 'TABLE_PACKAGES'...")
                upsert_data_to_db(cur, 'TABLE_PACKAGES', packages_df, 'id')
            
            logs.append(f"Загружаю {len(items_df)} товаров в 'TABLE_ITEMS'...")
            upsert_data_to_db(cur, 'TABLE_ITEMS', items_df, 'datamatrix')
            
            # --- ИЗМЕНЕННАЯ ЛОГИКА: Обновляем статус, только если он не 'dmkod' ---
            if initial_order_status != 'dmkod':
                logs.append("\nОбновляю статус заказа на 'completed'...")
                orders_table = os.getenv('TABLE_ORDERS')
                cur.execute(f"UPDATE {orders_table} SET status = 'completed' WHERE id = %s", (order_id,))
            else:
                logs.append("\nСтатус заказа 'dmkod' не изменен, так как обработка идет из модуля интеграции.")

            conn.commit()
            logs.append("\nПроцесс успешно завершен! Данные и счетчик в БД обновлены.")

    except Exception as e:
        if conn: conn.rollback()
        logs.append(f"\nКРИТИЧЕСКАЯ ОШИБКА: {e}")
        logs.append("Все изменения в базе данных отменены.")
    finally:
            if conn: conn.close()
            for path in saved_file_paths:
                # Проверяем, существует ли файл, прежде чем пытаться его удалить
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError as e:
                        # Эта ошибка теперь маловероятна, но оставим обработку на всякий случай
                        logs.append(f"Не удалось удалить временный файл {path}: {e}")        
    return logs