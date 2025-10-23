import json
from api_client import ApiClient  # Импортируем наш клиент

def get_and_process_participants():
    """
    Получает список участников, преобразует ответ в словарь
    и выводит информацию.
    """
    try:
        client = ApiClient()
        print(f"\nВыполнение GET запроса на {client.base_url}/psp/participants...")
        
        # Выполняем запрос через клиент
        response = client.get("psp/participants")
        
        # Преобразуем JSON-ответ в словарь Python
        data_dict = response.json()
        
        print("\n✅ Список участников успешно получен и преобразован в словарь!")
        
        # Теперь работаем с данными как со словарем
        if 'participants' in data_dict and isinstance(data_dict['participants'], list):
            participants_list = data_dict['participants']
            print(f"Всего найдено участников: {len(participants_list)}")
            
            if participants_list:
                print("\nДанные первого участника:")
                first_participant = participants_list[10]
                # Выводим данные, обращаясь по ключам
                print(f"  ID: {first_participant.get('id')}, Имя: {first_participant.get('name')}, ИНН: {first_participant.get('inn')}, Дата окончания доверености: {first_participant.get('poa_validity_end')}")
        else:
            print("В ответе от API не найден ожидаемый список 'participants'.")

    except Exception as e:
        print(f"\n❌ Произошла ошибка: {e}")

if __name__ == "__main__":
    get_and_process_participants()