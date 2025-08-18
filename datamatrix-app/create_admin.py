import os
import psycopg2
from getpass import getpass
from dotenv import load_dotenv
from bcrypt import hashpw, gensalt

def create_admin_user():
    """Скрипт для создания пользователя-администратора."""
    load_dotenv()
    
    print("--- Создание нового пользователя ---")
    username = input("Введите имя пользователя (username): ").strip()
    # getpass скрывает ввод пароля
    password = getpass("Введите пароль: ")
    password_confirm = getpass("Повторите пароль: ")

    if not username or not password:
        print("Ошибка: Имя пользователя и пароль не могут быть пустыми.")
        return

    if password != password_confirm:
        print("Ошибка: Пароли не совпадают.")
        return

    # Хэшируем пароль
    password_hash = hashpw(password.encode('utf-8'), gensalt()).decode('utf-8')
    
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'), database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD')
        )
        with conn.cursor() as cur:
            # Вставляем нового пользователя
            cur.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s)",
                (username, password_hash, True) # Первый созданный пользователь будет админом
            )
            conn.commit()
            print(f"Пользователь '{username}' успешно создан!")
    except psycopg2.errors.UniqueViolation:
        print(f"Ошибка: Пользователь с именем '{username}' уже существует.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    create_admin_user()