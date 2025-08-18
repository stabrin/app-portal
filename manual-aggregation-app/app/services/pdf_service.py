# manual-aggregation-app/app/services/pdf_service.py
import qrcode
import base64
import io
from flask import render_template
from weasyprint import HTML
from .scan_service import CMD_COMPLETE_UNIT, CMD_CANCEL_UNIT, CMD_LOGOUT

def generate_tokens_pdf(order_data: dict, tokens: list) -> bytes:
    """
    Генерирует PDF-файл с QR-кодами для сотрудников.
    Каждый QR-код - на отдельном листе 60x80 мм.
    """
    labels_data = []
    for i, token_record in enumerate(tokens, 1):
        # 1. Создаем QR-код в памяти
        qr_img = qrcode.make(token_record['access_token'])
        
        # 2. Сохраняем изображение в байтовый буфер
        buffer = io.BytesIO()
        qr_img.save(buffer, format="PNG")
        
        # 3. Кодируем в Base64, чтобы вставить прямо в HTML
        qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        labels_data.append({
            "client_name": order_data['client_name'],
            "order_id": order_data['id'],
            "pass_number": i,
            "qr_base64": qr_base64
        })

    # 4. Рендерим HTML-шаблон с нашими данными
    html_out = render_template("pdf/label_template.html", labels=labels_data)
    
    # 5. Превращаем HTML в PDF и возвращаем байты
    return HTML(string=html_out).write_pdf()

def generate_control_codes_pdf() -> bytes:
    """Генерирует PDF-файл с универсальными управляющими QR-кодами."""
    
    # Список всех управляющих кодов и их описаний
    control_codes = [
        {"code": CMD_COMPLETE_UNIT, "title": "ЗАВЕРШИТЬ ЮНИТ", "description": "Сохраняет собранный набор или короб. Сканировать ПОСЛЕ сканирования всех товаров и кода самого набора/короба."},
        {"code": CMD_CANCEL_UNIT, "title": "ОТМЕНИТЬ ЮНИТ", "description": "Отменяет текущую сборку набора или короба. Все отсканированные товары в рамках этой операции будут сброшены."},
        {"code": CMD_LOGOUT, "title": "ЗАВЕРШИТЬ СМЕНУ", "description": "Осуществляет выход из системы. Вся незавершенная работа (несохраненный набор/короб) будет потеряна."},
        # Сюда можно будет добавлять коды для паллет, контейнеров и т.д.
        # {"code": "CMD_JOIN_PALLET_01", "title": "РАБОТАТЬ НАД ПАЛЛЕТОМ 1", "description": "..."}
    ]

    labels_data = []
    for code_info in control_codes:
        qr_img = qrcode.make(code_info['code'])
        buffer = io.BytesIO()
        qr_img.save(buffer, format="PNG")
        qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        labels_data.append({**code_info, "qr_base64": qr_base64})

    html_out = render_template("pdf/control_codes_template.html", labels=labels_data)
    
    return HTML(string=html_out).write_pdf()