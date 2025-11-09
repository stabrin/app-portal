import os
import re
import logging
from typing import Optional
import pandas as pd
from psycopg2.extras import RealDictCursor

from .printing_service import PrintingService # Для получения подключения к БД
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

def run_import_from_dmkod(user_info: dict, order_id: int) -> list:
    """
    Выполняет импорт кодов из JSON-поля в dmkod_aggregation_details и их агрегацию.
    Адаптировано из datamatrix-app.
    """
    logs = [f"Запуск импорта кодов из БД для Заказа №{order_id}..."]
    logger.info(f"run_import_from_dmkod: Начало для order_id={order_id}")
    conn = None

    try:
        conn = PrintingService._get_client_db_connection(user_info)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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