from flask import Blueprint

# 1. Определяем Blueprint. Назовем его 'dmkod_bp' для примера.
#    Замените 'dmkod_app' на уникальное имя для вашего blueprint.
dmkod_bp = Blueprint('dmkod_app', __name__, template_folder='templates')

# 2. Создаем тестовый роут.
#    Из-за url_prefix='/dmkod' в __init__.py, итоговый URL будет /dmkod/
@dmkod_bp.route('/')
def index():
    return "<h1>Приложение 'ДМкод интеграция' работает!</h1>"