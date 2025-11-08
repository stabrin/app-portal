import os
import re
from typing import Optional

# Константа-разделитель для кодов DataMatrix
GS_SEPARATOR = '\x1d'

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

# --- Логика из datamatrix-app/app/services/sscc_service.py ---

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

def generate_sscc(sscc_id: int, gcp: str) -> tuple[str, str]:
    """Вспомогательная функция для генерации SSCC."""
    if not gcp:
        raise ValueError("GCP (Global Company Prefix) не задан.")

    if len(gcp) > 16:
        raise ValueError(f"Некорректная длина GCP '{gcp}' ({len(gcp)} символов).")

    serial_number_length = 16 - len(gcp)
    serial_number_capacity = 10 ** serial_number_length

    extension_digit = (sscc_id // serial_number_capacity) % 10
    serial_number = sscc_id % serial_number_capacity
    serial_part = str(serial_number).zfill(serial_number_length)
    base_sscc = str(extension_digit) + gcp + serial_part
    check_digit = calculate_sscc_check_digit(base_sscc)
    full_sscc = base_sscc + str(check_digit)
    return base_sscc, full_sscc

def read_and_increment_counter(cursor, counter_name: str, increment_by: int = 1) -> tuple[int, Optional[str], str]:
    """
    Атомарно читает и увеличивает счетчик в БД.
    Возвращает (новое_значение, сообщение_с_предупреждением | None, используемый_gcp).
    """
    cursor.execute(
        "SELECT current_value FROM system_counters WHERE counter_name = %s FOR UPDATE;",
        (counter_name,)
    )
    current_value = cursor.fetchone()[0]
    new_value = current_value + increment_by
    
    warning_message = None
    gcp_to_use = os.getenv('SSCC_GCP_1', '')

    cursor.execute(
        "UPDATE system_counters SET current_value = %s WHERE counter_name = %s;",
        (new_value, counter_name)
    )
    
    return new_value, warning_message, gcp_to_use