import re

def parse_tobacco_dm(dm_string: str) -> dict | None:
    """
    Парсит строку DataMatrix для табачной продукции.
    Возвращает словарь с данными или None, если строка не соответствует формату.
    """
    # Более агрессивная очистка: удаляем все непечатные/управляющие символы, кроме GS (\x1d)
    # Это решает проблему с BOM и другими скрытыми символами.
    cleaned_dm = re.sub(r'[\x00-\x1c\x1e-\x1f\x7f]', '', dm_string).strip()

    # Табачный код имеет строгую длину 29 символов
    if len(cleaned_dm) != 29:
        # Возвращаем не None, а словарь с ошибкой для более детального логгирования
        return {"error": "InvalidLength", "length": len(cleaned_dm), "original_string": dm_string[:40]}

    # Позиционная нарезка строки
    gtin = cleaned_dm[0:14]
    serial = cleaned_dm[14:21]
    code8005 = cleaned_dm[21:25]
    internal_93 = cleaned_dm[25:29]
    
    result = {
        'datamatrix': cleaned_dm,
        'gtin': gtin,
        'serial': serial,
        'code_8005': code8005,
        'crypto_part_93': internal_93,
        'crypto_part_91': '',
        'crypto_part_92': ''
    }
    
    return result