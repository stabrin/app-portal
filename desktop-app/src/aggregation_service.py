import os
import re
import logging
from typing import Optional, Dict, Any
import codecs
import pandas as pd
from psycopg2.extras import RealDictCursor
from psycopg2 import sql
import psycopg2

from .db_connector import get_client_db_connection
from .utils import upsert_data_to_db # Импортируем утилиту
from .sscc_service import generate_sscc, read_and_increment_counter # Импортируем централизованные функции

# Константа-разделитель для кодов DataMatrix
GS_SEPARATOR = '\x1d'

logger = logging.getLogger(__name__)

# --- Логика из datamatrix-app/app/services/tobacco_service.py ---

def parse_tobacco_dm(dm_string: str) -> Optional[dict]:
    """
    Парсит строку DataMatrix для табачной продукции.
    Возвращает словарь с данными или None, если строка не соответствует формату.
    """
    cleaned_dm = re.sub(r'[\x00-\x1c\x1e-\x1f\x7f]', '', dm_string).strip()

    if len(cleaned_dm) != 29:
        return {"error": "InvalidLength", "length": len(cleaned_dm), "original_string": dm_string[:40]}

    gtin = cleaned_dm[0:14]
    serial = cleaned_dm[14:21]
    code8005 = cleaned_dm[21:25]
    internal_93 = cleaned_dm[25:29]
    
    return {
        'datamatrix': cleaned_dm,
        'gtin': gtin,
        'serial': serial,
        'code_8005': code8005,
        'crypto_part_93': internal_93,
        'crypto_part_91': '',
        'crypto_part_92': ''
    }

# --- Логика из dmkod-integration-app/app/routes.py ---

def parse_datamatrix(dm_string: str) -> dict:
    """Разбирает (парсит) строку DataMatrix на составные части."""
    result = {
        'datamatrix': dm_string, 'gtin': '', 'serial': '',
        'crypto_part_91': '', 'crypto_part_92': '', 'crypto_part_93': ''
    }
    cleaned_dm = dm_string.replace(' ', '\x1d').strip()
    parts = cleaned_dm.split(GS_SEPARATOR)
    if len(parts) > 0:
        main_part = parts.pop(0)
        if main_part.startswith('01'):
            result['gtin'] = main_part[2:16]
            serial_part = main_part[16:]
            if serial_part.startswith('21'):
                result['serial'] = serial_part[2:].split(GS_SEPARATOR)[0]
    for part in parts:
        if not part: continue
        if part.startswith('91'): result['crypto_part_91'] = part[2:]
        elif part.startswith('92'): result['crypto_part_92'] = part[2:]
        elif part.startswith('93'): result['crypto_part_93'] = part[2:]
    return result

def create_bartender_views(user_info: Dict[str, Any], order_id: int) -> dict:
    """
    Создает или обновляет представления (VIEW) для Bartender.
    Перенесено из PrintingService для лучшей организации кода.
    """
    logging.info(f"Начало создания представлений для заказа ID: {order_id}")
    conn = None
    try:
        # Используем get_client_db_connection из текущего модуля
        with get_client_db_connection(user_info) as conn, conn.cursor() as cur:
            # 1. Получаем информацию о заказе и формируем имена
            cur.execute("SELECT client_name FROM orders WHERE id = %s", (order_id,))
            order_info = cur.fetchone()
            if not order_info:
                logging.error(f"Заказ с ID {order_id} не найден в БД при создании представлений.")
                return {"success": False, "message": f"Заказ с ID {order_id} не найден."}
            client_name = order_info[0]

            # Очистка имен для SQL
            base_view_name_str = f"{client_name}_{order_id}"
            sanitized_name = re.sub(r'[^\w]', '_', base_view_name_str)
            sanitized_name = re.sub(r'_+', '_', sanitized_name).strip('_')
            
            base_view_name = sql.Identifier(sanitized_name)
            sscc_view_name = sql.Identifier(f"{sanitized_name}_sscc")

            logging.debug(f"Сгенерированы имена представлений: {base_view_name.string}, {sscc_view_name.string}")

            # 2. Используем CREATE OR REPLACE VIEW для атомарного обновления
            logging.debug("Создание/обновление основного представления...")
            main_view_query = sql.SQL("""
            CREATE OR REPLACE VIEW {view_name} AS
            SELECT
                o.client_name, o.order_date, i.datamatrix, i.gtin, i.serial,
                i.code_8005, i.crypto_part_91, i.crypto_part_92, i.crypto_part_93,
                i.tirage_number, i.package_id, p.name AS product_name,
                p.description_1, p.description_2, p.description_3
            FROM items i
            JOIN orders o ON i.order_id = o.id
            LEFT JOIN products p ON i.gtin = p.gtin
            WHERE i.order_id = {order_id};
            """).format(
                view_name=base_view_name,
                order_id=sql.Literal(order_id)
            )
            cur.execute(main_view_query)
            logging.debug("Основное представление успешно создано/обновлено.")
            
            logging.debug("Создание/обновление SSCC-представления...")
            
            # 3. Проверяем наличие агрегации
            cur.execute(
                sql.SQL("SELECT 1 FROM items WHERE order_id = %s AND package_id IS NOT NULL LIMIT 1"),
                (order_id,)
            )
            aggregation_exists = cur.fetchone() is not None
            logging.debug(f"Проверка наличия агрегации для заказа {order_id}: {aggregation_exists}")

            # 4. Создаем второе представление для SSCC
            if aggregation_exists:
                sscc_view_query = sql.SQL("""
                CREATE OR REPLACE VIEW {view_name} AS
                WITH RECURSIVE package_hierarchy AS (
                    SELECT
                        p.id as base_box_id, p.id as package_id, p.level, p.sscc, p.parent_id
                    FROM packages p
                    WHERE p.level = 1 AND p.id IN (
                        SELECT DISTINCT i.package_id
                        FROM items i
                        WHERE i.order_id = {order_id} AND i.package_id IS NOT NULL
                    )
                    UNION ALL
                    SELECT ph.base_box_id, p_parent.id as package_id, p_parent.level, p_parent.sscc, p_parent.parent_id
                    FROM package_hierarchy ph JOIN packages p_parent ON ph.parent_id = p_parent.id
                ),
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
                SELECT * FROM boxes_view;
                """).format(
                    view_name=sscc_view_name,
                    order_id=sql.Literal(order_id)
                )
            else:
                # Пустое представление, если агрегации нет
                sscc_view_query = sql.SQL("""
                CREATE OR REPLACE VIEW {view_name} AS
                SELECT NULL::integer AS id_level_1, NULL::varchar AS sscc_level_1, NULL::integer AS id_level_2,
                       NULL::varchar AS sscc_level_2, NULL::integer AS id_level_3, NULL::varchar AS sscc_level_3
                WHERE 1=0;
                """).format(view_name=sscc_view_name)

            cur.execute(sscc_view_query)
            logging.debug("SSCC-представление успешно создано/обновлено.")

        conn.commit()
        logging.info(f"Представления для заказа №{order_id} успешно созданы/обновлены.")
        return {"success": True, "message": f"Представления для заказа №{order_id} успешно созданы/обновлены."}
    except Exception as e:
        logging.error(f"Ошибка при создании представлений для заказа {order_id}: {e}", exc_info=True)
        if conn: conn.rollback()
        return {"success": False, "message": f"Ошибка при создании представлений: {e}"}

def run_import_from_dmkod(user_info: dict, order_id: int) -> list:
    """
    Выполняет импорт кодов из JSON-поля в dmkod_aggregation_details и их агрегацию.
    Адаптировано из datamatrix-app.
    """
    logs = [f"Запуск импорта кодов из БД для Заказа №{order_id}..."]
    logger.info(f"run_import_from_dmkod: Начало для order_id={order_id}")
    conn = None

    try:
        with get_client_db_connection(user_info) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Получаем все строки детализации с кодами для этого заказа
            cur.execute("""
                SELECT gtin, api_codes_json, aggregation_level, api_id
                FROM dmkod_aggregation_details WHERE order_id = %s AND api_codes_json IS NOT NULL
            """, (order_id,))
            all_details_with_codes = cur.fetchall()

            logger.debug(f"Найдено {len(all_details_with_codes)} строк с кодами для обработки.")

            if not all_details_with_codes:
                raise ValueError("Не найдено записей с кодами для импорта в базе данных.")

            # --- НОВАЯ ЛОГИКА: Обрабатываем каждый тираж отдельно ---
            for detail in all_details_with_codes:
                api_id = detail['api_id']
                gtin = detail['gtin']
                aggregation_level = detail['aggregation_level']
                codes_from_json = detail.get('api_codes_json', {}).get('codes', [])

                logs.append(f"\n--- Обработка тиража ID: {api_id} (GTIN: {gtin}) ---")
                logger.info(f"Обработка тиража api_id={api_id}, gtin={gtin}, {len(codes_from_json)} кодов.")

                if not codes_from_json:
                    logs.append("  -> В записи нет кодов для обработки. Пропускаю.")
                    continue

                # Проверяем, существуют ли коды из ЭТОГО тиража в таблице items
                cur.execute("SELECT 1 FROM items WHERE datamatrix = ANY(%s) LIMIT 1", (codes_from_json,))
                if cur.fetchone():
                    logs.append("  -> ИНФО: Коды из этого тиража уже были загружены ранее. Пропускаю.")
                    logger.info(f"Тираж {api_id} пропущен, так как коды уже есть в таблице items.")
                    continue

                # Собираем данные для DataFrame только для текущего тиража
                current_tirage_dm_data = []
                for dm_string in codes_from_json:
                    parsed_data = parse_datamatrix(dm_string)
                    if not parsed_data.get('gtin'):
                        logs.append(f"  -> Пропущен код: не удалось распознать GTIN в '{dm_string[:30]}...'.")
                        continue
                    parsed_data['order_id'] = order_id
                    parsed_data['tirage_number'] = str(api_id)
                    current_tirage_dm_data.append(parsed_data)

                if not current_tirage_dm_data:
                    logs.append("  -> В тираже не найдено корректных кодов DataMatrix для обработки.")
                    continue
                logger.debug(f"Спарсено {len(current_tirage_dm_data)} кодов для тиража {api_id}.")

                items_df = pd.DataFrame(current_tirage_dm_data)
                logs.append(f"  -> Подготовлено к загрузке {len(items_df)} кодов.")

                # Проверка и создание GTIN в справочнике
                cur.execute("SELECT gtin FROM products WHERE gtin = %s", (gtin,))
                if not cur.fetchone():
                    logs.append(f"  -> GTIN {gtin} не найден в справочнике. Создаю заглушку...")
                    logger.info(f"GTIN {gtin} не найден в products. Создается запись-заглушка.")
                    new_products_df = pd.DataFrame([{'gtin': gtin, 'name': f'Товар (GTIN: {gtin})'}])
                    upsert_data_to_db(cur, 'products', new_products_df, 'gtin')

                # Агрегация
                all_packages = []
                items_df['package_id'] = None
                agg_level_int = int(aggregation_level) if pd.notna(aggregation_level) else 0

                if agg_level_int > 0:
                    logs.append(f"  -> Начинаю агрегацию с шагом {agg_level_int} шт. в коробе.")
                    logger.info(f"Агрегация для тиража {api_id} с уровнем {agg_level_int}.")
                    item_indices = items_df.index.tolist()
                    step = agg_level_int
                    for i in range(0, len(item_indices), step):
                        chunk_indices = item_indices[i:i + step]
                        box_id, warning, gcp_for_sscc = read_and_increment_counter(cur, 'sscc_id')
                        if warning and warning not in logs: logs.append(warning)
                        items_df.loc[chunk_indices, 'package_id'] = box_id
                        _, full_sscc = generate_sscc(box_id, gcp_for_sscc)
                        all_packages.append({'id': box_id, 'sscc': full_sscc, 'owner': 'wed-ug', 'level': 1, 'parent_id': None})
                    logger.debug(f"Создано {len(all_packages)} пакетов для тиража {api_id}.")
                else:
                    logs.append("  -> Агрегация для тиража пропущена, т.к. кол-во в коробе не задано.")
                    logger.info(f"Агрегация для тиража {api_id} пропущена (aggregation_level={aggregation_level}).")

                # Сохранение результатов для текущего тиража
                if all_packages:
                    packages_df = pd.DataFrame(all_packages)
                    logs.append(f"  -> Загружаю {len(packages_df)} упаковок...")
                    logger.info(f"Вызов upsert_data_to_db для {len(packages_df)} упаковок.")
                    upsert_data_to_db(cur, 'packages', packages_df, 'id')

                logs.append(f"  -> Загружаю {len(items_df)} товаров...")
                logger.info(f"Вызов upsert_data_to_db для {len(items_df)} товаров.")
                upsert_data_to_db(cur, 'items', items_df, 'datamatrix')

            conn.commit()
            logger.info(f"run_import_from_dmkod: Транзакция для order_id={order_id} успешно закоммичена.")

        logs.append("\nПроцесс импорта и агрегации успешно завершен!")
    except Exception as e:
        logger.error(f"Ошибка в run_import_from_dmkod для order_id={order_id}: {e}", exc_info=True)
        if conn: conn.rollback()
        logs.append(f"\nКРИТИЧЕСКАЯ ОШИБКА: {e}")
        logs.append("Все изменения в базе данных отменены.")
    finally:
        if conn: conn.close()

    return logs

def run_aggregation_process_desktop(user_info: dict, order_id: int, filepaths: list, dm_type: str, aggregation_mode: str, level1_qty: int) -> list:
    """
    Основная функция, которая выполняет весь процесс, включая агрегацию.
    Адаптировано из datamatrix-app для десктопного приложения.
    """
    logs = [f"Запуск обработки для Заказа №{order_id}..."]
    
    if not filepaths:
        logs.append("ОШИБКА: Не было передано ни одного файла для обработки.")
        return logs

    all_dm_data = []
    total_lines_processed = 0
    total_lines_skipped = 0
    
    file_counter = 1 
    for file_path in filepaths:
        original_filename = os.path.basename(file_path)
        logs.append(f"--- Читаю файл №{file_counter}: {original_filename} (Тип кодов: {dm_type}) ---")
        
        tirazh_num = str(file_counter) # В десктопной версии номер тиража - это просто порядковый номер файла
        
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
                    if parsed_data is None or parsed_data.get("error"):
                        error_msg = parsed_data.get("error", "неверный формат") if parsed_data else "неверная длина"
                        logs.append(f"  -> Пропущена строка {line_num} (табак): {error_msg}")
                        total_lines_skipped += 1
                        continue
                else: # 'standard'
                    parsed_data = parse_datamatrix(dm_string)

                if not parsed_data.get('gtin'):
                    logs.append(f"  -> Пропущена строка {line_num}: не удалось распознать GTIN.")
                    total_lines_skipped += 1
                    continue
                    
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

    if not all_dm_data:
        logs.append("Не найдено корректных кодов DataMatrix в файлах.")
        return logs
        
    items_df = pd.DataFrame(all_dm_data)
    logs.append(f"\nВсего найдено и разобрано {len(items_df)} кодов DataMatrix.")
    logs.append("Проверяю, не были ли эти коды обработаны ранее...")
    
    conn = None
    try:
        with get_client_db_connection(user_info) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Проверка на существование кодов
                dm_to_check = tuple(items_df['datamatrix'].unique())
                if dm_to_check:
                    cur.execute("SELECT datamatrix, order_id FROM items WHERE datamatrix IN %s", (dm_to_check,))
                    existing_codes = cur.fetchall()
                    if existing_codes:
                        error_msg = "\nОШИБКА: Обнаружены коды, которые уже были обработаны в других заказах. Процесс прерван. Список кодов и их заказов:\n"
                        for code in existing_codes:
                            error_msg += f"  - Код (последние 10 символов): ...{code['datamatrix'][-10:]}, уже в заказе: {code['order_id']}\n"
                        logs.append(error_msg)
                        return logs
                
                logs.append("Проверка на дубликаты пройдена успешно. Ранее обработанных кодов не найдено.")
                unique_gtins_in_upload = items_df['gtin'].unique()
                logs.append(f"\nПроверяю наличие {len(unique_gtins_in_upload)} уникальных GTIN в справочнике...")
                gtins_tuple = tuple(unique_gtins_in_upload)
                if not gtins_tuple:
                     existing_gtins = set()
                else:
                    cur.execute("SELECT gtin FROM products WHERE gtin IN %s", (gtins_tuple,))
                    existing_gtins = {row['gtin'] for row in cur.fetchall()}
                
                new_gtins = [gtin for gtin in unique_gtins_in_upload if gtin not in existing_gtins]
                
                if new_gtins:
                    logs.append(f"Найдено {len(new_gtins)} новых GTIN. Создаю для них заглушки...")
                    new_products_data = [{'gtin': gtin, 'name': f'Товар (GTIN: {gtin})'} for gtin in new_gtins]
                    new_products_df = pd.DataFrame(new_products_data)
                    upsert_data_to_db(cur, 'products', new_products_df, 'gtin')
                else:
                    logs.append("Все GTIN из загрузки уже есть в справочнике.")

                packages_df = pd.DataFrame()
                if aggregation_mode == 'level1':
                    logs.append(f"\nНачинаю агрегацию...")
                    all_packages = []
                    items_df['package_id'] = None
                    
                    for gtin, group in items_df.groupby('gtin'):
                        logs.append(f"--- Агрегирую GTIN: {gtin} ({len(group)} шт.) в короба ---")
                        item_indices = group.index.tolist()
                        for i in range(0, len(item_indices), level1_qty):
                            chunk_indices = item_indices[i:i + level1_qty]
                            box_id, warning, gcp_for_sscc = read_and_increment_counter(cur, 'sscc_id')
                            if warning and warning not in logs:
                                logs.append(warning)
                            items_df.loc[chunk_indices, 'package_id'] = box_id
                            _, full_sscc = generate_sscc(box_id, gcp_for_sscc)
                            all_packages.append({'id': box_id, 'sscc': full_sscc, 'owner': 'file_upload', 'level': 1, 'parent_id': None})
                            logs.append(f"  -> Создан короб (ID: {box_id}, SSCC: {full_sscc}) для {len(chunk_indices)} шт.")
                    packages_df = pd.DataFrame(all_packages)
                
                if not packages_df.empty:
                    logs.append(f"\nЗагружаю {len(packages_df)} упаковок в 'packages'...")
                    upsert_data_to_db(cur, 'packages', packages_df, 'id')
                
                logs.append(f"Загружаю {len(items_df)} товаров в 'items'...")
                upsert_data_to_db(cur, 'items', items_df, 'datamatrix')
                
                logs.append("\nОбновляю статус заказа на 'completed'...")
                cur.execute("UPDATE orders SET status = 'completed' WHERE id = %s", (order_id,))

            conn.commit()
            logs.append("\nПроцесс успешно завершен! Данные и счетчик в БД обновлены.")

    except Exception as e:
        if conn: conn.rollback()
        logs.append(f"\nКРИТИЧЕСКАЯ ОШИБКА: {e}")
        logs.append("Все изменения в базе данных отменены.")
    
    return logs