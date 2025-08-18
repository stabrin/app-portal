from flask import Blueprint, request, jsonify
from flask_login import current_user, login_required

from .services.scan_service import process_scan
from .services.order_service import get_order_by_id

api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route('/scan', methods=['POST'])
@login_required 
def handle_scan():
    """Обрабатывает AJAX-запросы от сканера сотрудника."""
    try:
        if getattr(current_user, 'role', None) != 'employee':
            return jsonify({"status": "error", "message": "Доступ запрещен"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "Пустой запрос или неверный Content-Type"}), 400
        
        scanned_code = data.get('scanned_code')
        if not scanned_code:
            return jsonify({"status": "error", "message": "Пустой код"}), 400

        # Извлекаем чистый ID из user.id, который имеет формат 'employee:123'
        token_id = current_user.id.split(':', 1)[1]
        order_id = current_user.data.get('order_id')
        
        # Получаем актуальные данные заказа, т.к. они могли измениться с момента входа
        order_info = get_order_by_id(order_id)
        if not order_info:
            return jsonify({"status": "error", "message": f"Заказ {order_id} не найден."}), 404

        # Вызываем основную бизнес-логику
        result = process_scan(
            employee_token_id=int(token_id), # ИСПРАВЛЕНО: правильное имя аргумента
            order_info=order_info,
            scanned_code=scanned_code
        )

        return jsonify(result)

    except Exception as e:
        # Этот блок перехватит любую ошибку, которая произошла выше,
        # и предотвратит ответ 500, вернув корректный JSON.
        import traceback
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА в API-эндпоинте handle_scan: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({
            "status": "error",
            "message": f"Критическая ошибка на сервере: {type(e).__name__}. Обратитесь к администратору.",
            "session": None
        }), 500