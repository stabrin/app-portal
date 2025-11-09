import os


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
    """
    Вспомогательная функция для генерации SSCC.
    Теперь принимает GCP как аргумент.
    """
    if not gcp:
        raise ValueError("GCP (Global Company Prefix) не задан в конфигурации.")

    # --- НОВАЯ ЗАЩИТНАЯ ПРОВЕРКА ---
    if len(gcp) > 16:
        raise ValueError(f"Некорректная длина GCP '{gcp}' ({len(gcp)} символов). Длина префикса не должна превышать 16 символов.")

    serial_number_length = 16 - len(gcp)
    serial_number_capacity = 10 ** serial_number_length

    # Цифра расширения теперь от 0 до 9
    extension_digit = (sscc_id // serial_number_capacity) % 10
    serial_number = sscc_id % serial_number_capacity
    serial_part = str(serial_number).zfill(serial_number_length)
    base_sscc = str(extension_digit) + gcp + serial_part
    check_digit = calculate_sscc_check_digit(base_sscc)
    full_sscc = base_sscc + str(check_digit)
    return base_sscc, full_sscc

def read_and_increment_counter(cursor, counter_name: str, increment_by: int = 1) -> tuple[int, str | None, str]:
    """
    Атомарно читает и увеличивает счетчик в БД.
    Возвращает кортеж (новое_значение, сообщение_с_предупреждением | None, используемый_gcp).
    """
    cursor.execute(
        "SELECT current_value FROM public.system_counters WHERE counter_name = %s FOR UPDATE;",
        (counter_name,)
    )
    current_value = cursor.fetchone()['current_value']
    new_value = current_value + increment_by

    warning_message = None
    gcp_to_use = '' # Инициализируем gcp_to_use
    # Проверяем только счетчик SSCC, чтобы не влиять на другие возможные счетчики
    if counter_name == 'sscc_id':
        # --- НОВАЯ ЛОГИКА: Получаем настройки из таблицы ap_settings ---
        cursor.execute("SELECT setting_key, setting_value FROM public.ap_settings WHERE setting_key IN ('SSCC_GCP_1', 'SSCC_GCP_2', 'SSCC_PRIMARY_GCP_LIMIT', 'SSCC_WARNING_PERCENT')")
        settings_from_db = {row['setting_key']: row['setting_value'] for row in cursor.fetchall()}

        # Используем значения из БД или значения по умолчанию
        gcp1 = settings_from_db.get('SSCC_GCP_1', '')
        gcp2 = settings_from_db.get('SSCC_GCP_2', '')
        try:
            primary_limit = int(settings_from_db.get('SSCC_PRIMARY_GCP_LIMIT', '9900000'))
        except (ValueError, TypeError):
            primary_limit = 9900000

        try:
            warning_percent = int(settings_from_db.get('SSCC_WARNING_PERCENT', '80'))
        except (ValueError, TypeError):
            warning_percent = 80

        gcp_to_use = gcp1 if new_value < primary_limit else gcp2

        serial_number_length = 16 - len(gcp_to_use)
        serial_number_capacity = 10 ** serial_number_length
        # Общая емкость теперь 10 (0-9) * 10^N
        sscc_total_capacity = 10 * serial_number_capacity
        
        sscc_warning_threshold = int(sscc_total_capacity * (warning_percent / 100))

        if new_value >= sscc_total_capacity:
            # Это уже не предупреждение, а критическая ошибка. Останавливаем процесс.
            error_msg = f"КРИТИЧЕСКАЯ ОШИБКА: Счетчик SSCC для GCP '{gcp_to_use}' ИСЧЕРПАН (id={new_value})! "
            error_msg += "Дальнейшая генерация приведет к дубликатам кодов. Обратитесь к администратору."
            raise ValueError(error_msg)
        elif new_value >= sscc_warning_threshold:
            remaining = sscc_total_capacity - new_value
            warning_message = (
                f"!!! ВНИМАНИЕ: Ресурс счетчика SSCC для GCP '{gcp_to_use}' подходит к концу (заполнено более {warning_percent}%). "
                f"Текущее значение: {new_value} из {sscc_total_capacity}. "
                f"Осталось уникальных кодов: {remaining}. Пора планировать смену GCP."
            )

    cursor.execute(
        "UPDATE public.system_counters SET current_value = %s WHERE counter_name = %s;",
        (new_value, counter_name)
    )
    
    return new_value, warning_message, gcp_to_use