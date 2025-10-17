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
        "SELECT current_value FROM system_counters WHERE counter_name = %s FOR UPDATE;",
        (counter_name,)
    )
    current_value = cursor.fetchone()[0]
    new_value = current_value + increment_by

    warning_message = None
    gcp_to_use = ''
    # Проверяем только счетчик SSCC, чтобы не влиять на другие возможные счетчики
    if counter_name == 'sscc_id':
        # --- Новая логика выбора GCP ---
        gcp1 = os.getenv('SSCC_GCP_1', '')
        gcp2 = os.getenv('SSCC_GCP_2', '')
        primary_limit = int(os.getenv('SSCC_PRIMARY_GCP_LIMIT', '9900000'))

        gcp_to_use = gcp1 if new_value < primary_limit else gcp2

        serial_number_length = 16 - len(gcp_to_use)
        serial_number_capacity = 10 ** serial_number_length
        # Общая емкость теперь 10 (0-9) * 10^N
        sscc_total_capacity = 10 * serial_number_capacity
        
        warning_percent = int(os.getenv('SSCC_WARNING_PERCENT', '80'))
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
        "UPDATE system_counters SET current_value = %s WHERE counter_name = %s;",
        (new_value, counter_name)
    )
    
    return new_value, warning_message, gcp_to_use