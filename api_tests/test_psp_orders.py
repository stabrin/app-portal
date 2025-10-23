import requests
import pytest
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Загружаем переменные из .env файла в корне проекта
load_dotenv()

@pytest.fixture(scope="module")
def api_config():
    """
    Фикстура, которая один раз за сессию подготавливает конфигурацию:
    - Загружает базовый URL из .env.
    - Читает актуальный токен доступа из файла api_tokens.json.
    """
    base_url = os.getenv("API_BASE_URL")
    if not base_url:
        pytest.skip("Переменная API_BASE_URL не найдена в .env файле.")

    try:
        current_dir = os.path.dirname(__file__)
        token_file_path = os.path.join(current_dir, 'api_tokens.json')
        with open(token_file_path, 'r', encoding='utf-8') as f:
            tokens = json.load(f)
        access_token = tokens.get('access')
        if not access_token:
            pytest.fail(f"Ключ 'access' не найден в файле {token_file_path}.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        pytest.fail(f"Ошибка при чтении токена: {e}. Запустите get_token.py.")

    return {
        "base_url": base_url,
        "auth_headers": {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
    }

def test_get_orders_for_last_7_days_with_offset(api_config):
    """
    Получает список заказов за последние 7 дней с учетом часового пояса.
    
    Согласно документации, для корректной работы фильтра 'range' необходимо
    указывать 'offset', чтобы API правильно определил временную зону.
    """
    print("\n--- Запускаю тест: получение заказов за последние 7 дней с учетом offset ---")
    base_url = api_config["base_url"]
    auth_headers = api_config["auth_headers"]

    # Формируем заголовки, добавляя фильтр по диапазону и смещение часового пояса
    request_headers = {
        **auth_headers, 
        "range": "last7",
        "offset": "3:00:00"  # Указываем смещение для Москвы (UTC+3)
    }
    
    print(f"Отправка запроса на {base_url}/psp/orders с заголовками: {list(request_headers.keys())}")
    
    response = requests.get(f"{base_url}/psp/orders", headers=request_headers)

    assert response.status_code == 200, (
        f"Ожидался статус-код 200, но получен {response.status_code}. Тело ответа: {response.text}"
    )

    # --- Показываем ответ как сырой текст ---
    raw_text_response = response.text
    print("\n--- Ответ от сервера (сырой текст) ---")
    print(raw_text_response)
    print("------------------------------------")
    # -------------------------------------------

    response_data = response.json()
    assert "orders" in response_data and isinstance(response_data["orders"], list)

    returned_orders = response_data["orders"]
    orders_count = len(returned_orders)
    
    print(f"\n[РЕЗУЛЬТАТ] Найдено заказов за последние 7 дней: {orders_count}")

    if orders_count > 0:
        print("Список полученных заказов (ID и состояние):")
        for order in returned_orders:
            print(f"  - ID: {order.get('order_id')}, Состояние: {order.get('state')}")
    else:
        print("[INFO] Запрос с range=last7 выполнен, но заказов не найдено.")


def test_get_orders_for_specific_date_with_offset(api_config):
    """
    Получает список заказов за конкретную дату с учетом часового пояса.
    Это поможет выяснить, может ли API отдавать заказы за прошлые дни.
    """
    print("\n--- Запускаю тест: получение заказов за КОНКРЕТНУЮ ДАТУ с учетом offset ---")
    base_url = api_config["base_url"]
    auth_headers = api_config["auth_headers"]

    # --- ВАЖНО: УКАЖИ ЗДЕСЬ ДАТУ, ЗА КОТОРУЮ ТЫ ТОЧНО ЗНАЕШЬ, ЧТО ЕСТЬ ЗАКАЗЫ ---
    specific_date = "2025-09-08" # <--- ИЗМЕНИ ЭТУ ДАТУ НА АКТУАЛЬНУЮ ДАТУ С ЗАКАЗАМИ

    # Формируем заголовки, используя 'date' вместо 'range' и 'offset'
    request_headers = {
        **auth_headers, 
        "date": specific_date,
        "offset": "3:00:00"  # Указываем смещение для Москвы (UTC+3)
    }
    
    print(f"Отправка запроса на {base_url}/psp/orders с заголовками: {list(request_headers.keys())}")
    
    response = requests.get(f"{base_url}/psp/orders", headers=request_headers)

    assert response.status_code == 200, (
        f"Ожидался статус-код 200, но получен {response.status_code}. Тело ответа: {response.text}"
    )

    raw_text_response = response.text
    print("\n--- Ответ от сервера (сырой текст) ---")
    print(raw_text_response)
    print("------------------------------------")

    response_data = response.json()
    assert "orders" in response_data and isinstance(response_data["orders"], list)

    returned_orders = response_data["orders"]
    orders_count = len(returned_orders)
    
    print(f"\n[РЕЗУЛЬТАТ] Найдено заказов за {specific_date}: {orders_count}")

    if orders_count > 0:
        print("Список полученных заказов (ID и состояние):")
        for order in returned_orders:
            print(f"  - ID: {order.get('order_id')}, Состояние: {order.get('state')}")
    else:
        print(f"[INFO] Запрос за {specific_date} выполнен, но заказов не найдено.")


# --- НОВЫЙ ДИАГНОСТИЧЕСКИЙ ТЕСТ ---
def test_get_specific_order_by_id(api_config):
    """
    Получает один конкретный заказ по его ID.
    Это самый точный способ проверить, может ли API в принципе найти старый заказ.
    """
    print("\n--- Запускаю тест: получение КОНКРЕТНОГО заказа по ID ---")
    base_url = api_config["base_url"]
    auth_headers = api_config["auth_headers"]

    # --- ВАЖНО: УКАЖИ ЗДЕСЬ ID СТАРОГО ЗАКАЗА, КОТОРЫЙ ТЫ ВИДИШЬ В ЛК ---
    order_id_to_find = 12183 # <--- ИЗМЕНИ ЭТОТ ID НА ID СУЩЕСТВУЮЩЕГО ЗАКАЗА

    # Формируем заголовки, используя только 'order_id'
    request_headers = {
        **auth_headers, 
        "order_id": str(order_id_to_find)
    }
    
    print(f"Отправка запроса на {base_url}/psp/orders с заголовком order_id: {order_id_to_find}")
    
    response = requests.get(f"{base_url}/psp/orders", headers=request_headers)

    assert response.status_code == 200, (
        f"Ожидался статус-код 200, но получен {response.status_code}. Тело ответа: {response.text}"
    )

    raw_text_response = response.text
    print("\n--- Ответ от сервера (сырой текст) ---")
    print(raw_text_response)
    print("------------------------------------")

    response_data = response.json()
    assert "orders" in response_data and isinstance(response_data["orders"], list)

    returned_orders = response_data["orders"]
    orders_count = len(returned_orders)
    
    print(f"\n[РЕЗУЛЬТАТ] Найдено заказов с ID {order_id_to_find}: {orders_count}")

    if orders_count > 0:
        found_order = next((order for order in returned_orders if order.get("order_id") == order_id_to_find), None)
        if found_order:
            print(f"[SUCCESS] Заказ с ID {order_id_to_find} успешно найден!")
            print("Данные заказа:", json.dumps(found_order, indent=2, ensure_ascii=False))
        else:
            print(f"[WARNING] API вернул {orders_count} заказ(ов), но ни один из них не имеет ID {order_id_to_find}.")
    else:
        print(f"[INFO] Запрос для ID {order_id_to_find} выполнен, но заказов не найдено.")

