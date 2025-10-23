import os
import requests
import json
from dotenv import load_dotenv

# Загружаем переменные из .env файла, находящегося в той же папке
print("Загрузка переменных из файла .env...")
load_dotenv()

# --- НАСТРОЙКИ ---
# Получаем базовый URL API и учетные данные из переменных окружения.
# Если переменная не задана, os.getenv вернет None.
BASE_URL = os.getenv("API_BASE_URL")
API_EMAIL = os.getenv("API_EMAIL")
API_PASSWORD = os.getenv("API_PASSWORD")

# Эндпоинт для получения токена
TOKEN_ENDPOINT = "/user/token" # Замените, если путь другой

def get_api_token():
    """
    Отправляет запрос на получение access и refresh токенов и сохраняет их в файл.
    """
    # 1. Проверяем, что все необходимые переменные загружены из .env
    if not all([BASE_URL, API_EMAIL, API_PASSWORD]):
        print("\n❌ Ошибка: Не все переменные заданы в файле .env.")
        print("   Убедитесь, что в файле api_tests/.env есть API_BASE_URL, API_EMAIL и API_PASSWORD.")
        return

    # 2. Формируем полный URL и тело запроса
    url = f"{BASE_URL.rstrip('/')}{TOKEN_ENDPOINT}"
    payload = {
        "email": API_EMAIL,
        "password": API_PASSWORD
    }

    print(f"\nОтправка GET-запроса на: {url}")
    
    try:
        # 3. Отправляем GET-запрос с данными в теле (нестандартно, но требуется API).
        # Предыдущие ошибки 400 и 405 указывают, что это единственно верный способ.
        response = requests.get(url, json=payload)
        
        # 4. Анализируем ответ
#        print(f"Фактический URL запроса: {response.url}")

        if response.status_code == 200:
            token_data = response.json()
            print("\n✅ Токены успешно получены!")
            # Красиво выводим полученный JSON
            print(json.dumps(token_data, indent=2))
            
            # Сохраняем токены в файл для дальнейшего использования другими скриптами
            with open("api_tokens.json", "w") as f:
                json.dump(token_data, f, indent=2)
            print("\nТокены сохранены в файл 'api_tokens.json'")

        else:
            print(f"\n❌ Ошибка получения токена. Статус-код: {response.status_code}")
            print(f"Ответ сервера: {response.text}")

    except requests.exceptions.RequestException as e:
        print(f"\n❌ Критическая ошибка при выполнении запроса: {e}")

if __name__ == "__main__":
    get_api_token()

