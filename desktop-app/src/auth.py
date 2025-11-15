# src/auth_test.py

import tkinter as tk
from tkinter import messagebox
import os
import sys
import logging
import bcrypt
import traceback
import requests
import base64
import configparser

# --- Настройка путей для импорта ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# --- ИСПРАВЛЕНИЕ: Загружаем переменные окружения в самом начале ---
from dotenv import load_dotenv
dotenv_path = os.path.join(project_root, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    logging.warning(f"Файл .env не найден по пути: {dotenv_path}. Переменные окружения могут быть не установлены.")

# --- НАДЁЖНОЕ РЕШЕНИЕ ПРОБЛЕМЫ С ВИРТУАЛЬНЫМ ОКРУЖЕНИЕМ ---
# Принудительно добавляем путь к библиотекам виртуального окружения.
# Это гарантирует, что все зависимости (pylibdmtx, qrcode и т.д.) будут найдены,
# даже если приложение запускается не из активированной консоли.
venv_site_packages = os.path.join(project_root, '.venv', 'Lib', 'site-packages')
if os.path.isdir(venv_site_packages) and venv_site_packages not in sys.path:
    sys.path.insert(0, venv_site_packages)
# --- КОНЕЦ РЕШЕНИЯ ---

# --- ИСПРАВЛЕНИЕ: Используем относительные импорты ---
from .db_connector import get_main_db_connection
# Импортируем наши новые классы интерфейсов
from .supervisor_ui import SupervisorWindow
from .admin_ui import AdminWindow


# --- Настройка логирования (копируем из main_window.py) ---
log_file_path = os.path.join(project_root, 'app.log')
logging.basicConfig(
    level=logging.DEBUG, # Изменено на DEBUG для более подробного логирования
    format='%(asctime)s - %(levelname)s - [auth_test] - %(message)s', # Добавим маркер для логов
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class StandaloneLoginWindow(tk.Toplevel):
    """
    Автономное, самодостаточное окно входа.
    После завершения работы (успех, ошибка, закрытие) вызывает callback и самоуничтожается.
    """
    def __init__(self, on_complete_callback):
        super().__init__()
        self.on_complete_callback = on_complete_callback

        # --- ИЗМЕНЕНИЕ: Динамический заголовок окна и делаем его модальным ---
        config_path = os.path.join(project_root, 'config.ini')
        self.title(f"{config_path} Вход | Путь для config.ini:")
        self.resizable(False, False)

        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind('<Return>', lambda event: self._verify_login())

        self.transient(self.master) # Окно будет поверх главного
        self.grab_set() # Модальное поведение

        self._create_widgets()

    def _create_widgets(self):
        frame = tk.Frame(self, padx=20, pady=10)
        frame.pack()
        tk.Label(frame, text="Логин:").grid(row=0, column=0, sticky="w", pady=5)
        self.login_entry = tk.Entry(frame, width=30)
        self.login_entry.grid(row=0, column=1, pady=5)
        self.login_entry.focus_set()
        tk.Label(frame, text="Пароль:").grid(row=1, column=0, sticky="w", pady=5)
        self.password_entry = tk.Entry(frame, width=30, show="*")
        self.password_entry.grid(row=1, column=1, pady=5)
        tk.Button(frame, text="Войти", command=self._verify_login).grid(row=2, columnspan=2, pady=10)
        
        # --- НОВАЯ КНОПКА ---
        # Добавляем кнопку для входа по QR-коду.
        # Пока она не выполняет никаких действий, но готова для будущей реализации.
        tk.Button(frame, text="Войти по QR-коду", command=self._login_with_qr).grid(row=3, columnspan=2, pady=(0, 10))

    def _login_with_qr(self):
        messagebox.showinfo("В разработке", "Функция входа по QR-коду находится в разработке.", parent=self)
        
    def _verify_login(self):
        logging.debug("Начало процесса верификации логина.")
        login = self.login_entry.get()
        password = self.password_entry.get()

        if not login or not password:
            messagebox.showerror("Ошибка", "Логин и пароль не могут быть пустыми.", parent=self)
            return
        
        # --- НОВАЯ ЛОГИКА: Проверка на локальный режим ---
        config_path = os.path.join(project_root, 'config.ini')
        cert_path = os.path.join(project_root, 'cert.pem')
        
        if os.path.exists(config_path):
            logging.info("Обнаружен файл config.ini. Запуск в локальном режиме.")
            self._local_auth(login, password, config_path, cert_path)
        else:
            logging.info(f"Файл config.ini не найден по пути '{config_path}'. Запуск в стандартном (онлайн) режиме.")
            self._online_auth(login, password)

    def _local_auth(self, login, password, config_path, cert_path):
        """Аутентификация в локальном режиме по файлу конфигурации."""
        try:
            # 1. Чтение config.ini
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            db_section = config['database']

            # 2. Расшифровка пароля
            def xor_cipher(data, key):
                return bytes([c ^ ord(k) for c, k in zip(data, key * (len(data) // len(key) + 1))]).decode('utf-8')
            
            encryption_key = "TildaKodSecretKey"
            encrypted_password_b64 = db_section.get('password')
            # Сначала декодируем из Base64, а затем расшифровываем
            encrypted_bytes = base64.b64decode(encrypted_password_b64)
            decrypted_password = xor_cipher(encrypted_bytes, encryption_key)

            # 3. Формирование конфигурации для подключения
            client_db_config = {
                "db_name": db_section.get('dbname'),
                "db_host": db_section.get('host'),
                "db_port": db_section.getint('port'),
                "db_user": db_section.get('user'),
                "db_password": decrypted_password,
                "db_ssl_cert": None,
                "id": 0 # ID клиента неизвестен в этом режиме
            }

            if os.path.exists(cert_path):
                with open(cert_path, 'r', encoding='utf-8') as f:
                    client_db_config["db_ssl_cert"] = f.read()

            # 4. Подключение к БД клиента и проверка пользователя
            from .db_connector import get_client_db_connection
            user_info_for_connection = {'client_db_config': client_db_config}

            with get_client_db_connection(user_info_for_connection) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT username, password_hash, is_active FROM public.users WHERE username = %s AND is_active = TRUE", (login,))
                    user_data = cur.fetchone()

            if user_data:
                user_name, hashed_password, is_admin = user_data
                if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                    if not is_admin:
                        messagebox.showerror("Ошибка", "Для локального входа требуются права администратора.", parent=self)
                        return

                    user_info = {
                        "name": user_name,
                        "role": "администратор",
                        "client_id": 0,
                        "client_db_config": client_db_config,
                        "client_api_config": {} # API недоступно в локальном режиме
                    }
                    logging.info(f"Локальная аутентификация для '{login}' прошла успешно.")
                    self.on_complete_callback(user_info)
                    self.destroy()
                else:
                    messagebox.showerror("Ошибка", "Неверный пароль.", parent=self)
            else:
                messagebox.showerror("Ошибка", "Пользователь не найден или неактивен в базе данных клиента.", parent=self)
        
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка локальной авторизации: {e}\n{error_details}")
            messagebox.showerror("Критическая ошибка", f"Ошибка при локальной авторизации: {e}\nПодробности в app.log.", parent=self)
            self.on_complete_callback(None) # Сообщаем о провале
            self.destroy() # И закрываемся

    def _online_auth(self, login, password):
        """Аутентификация в стандартном (онлайн) режиме через главную БД."""
        try:
            logging.debug("Попытка подключения к главной базе данных для получения данных пользователя...")
            with get_main_db_connection() as conn:
                logging.debug("Успешное подключение к главной базе данных.")
                with conn.cursor() as cur:
                    logging.debug("Выполнение запроса для получения данных пользователя, клиента и API.")
                    cur.execute("""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name='clients' AND column_name='local_server_address';
                    """)
                    has_new_columns = cur.fetchone() is not None
                    
                    local_server_fields = "c.local_server_address, c.local_server_port" if has_new_columns else "NULL, NULL"

                    query = """
                        SELECT u.name, u.password_hash, u.role, u.client_id,
                               c.db_name, c.db_host, c.db_port, c.db_user, c.db_password, 
                               c.db_ssl_cert, c.api_base_url, c.api_email, c.api_password,
                               {}
                        FROM users u
                        LEFT JOIN clients c ON u.client_id = c.id
                        WHERE u.login = %s AND u.is_active = TRUE AND (u.role = 'супервизор' OR u.role = 'администратор')
                    """.format(local_server_fields)
                    cur.execute(query, (login,))
                    user_data = cur.fetchone()
                    logging.debug(f"Запрос выполнен. Результат: {'Данные получены' if user_data else 'Пользователь не найден'}.")

            if user_data:
                (user_name, hashed_password, user_role, client_id, db_name, db_host, 
                 db_port, db_user, db_password, db_ssl_cert, api_base_url, 
                 api_email, api_password, local_server_address, local_server_port) = user_data

                if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                    user_info = {"name": user_name, "role": user_role}
                    logging.info(f"Пароль верен. Пользователь '{login}' успешно аутентифицирован. Роль: {user_role}.")

                    if user_role == 'администратор':
                        try:
                            logging.info("Попытка получить токен API для администратора...")
                            if not api_base_url:
                                raise ValueError("API_BASE_URL не настроен.")
                            token_url = f"{api_base_url.rstrip('/')}/user/token"
                            api_credentials = {"email": api_email, "password": api_password}
                            response = requests.get(token_url, json=api_credentials, timeout=10)
                            response.raise_for_status()
                            tokens = response.json()
                            user_info['api_access_token'] = tokens.get('access')
                            user_info['api_refresh_token'] = tokens.get('refresh')
                            logging.info("Токен API успешно получен.")
                        except Exception as e:
                            logging.error(f"Аутентификация в API провалена: {e}")
                            messagebox.showwarning("Предупреждение API", f"Не удалось получить токен API: {e}", parent=self)

                        user_info['client_id'] = client_id
                        user_info['client_db_config'] = {
                            "db_name": db_name, "db_host": db_host, "db_port": db_port,
                            "db_user": db_user, "db_password": db_password, "db_ssl_cert": db_ssl_cert, "id": client_id,
                            "local_server_address": local_server_address, "local_server_port": local_server_port
                        }
                        user_info['client_api_config'] = {
                            "api_base_url": api_base_url, "api_email": api_email, "api_password": api_password
                        }

                    self.on_complete_callback(user_info)
                    self.destroy()
                else:
                    messagebox.showerror("Ошибка", "Неверный пароль.", parent=self)
            else:
                messagebox.showerror("Ошибка", "Пользователь не найден или не имеет прав доступа.", parent=self)

        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка онлайн авторизации: {e}\n{error_details}")
            messagebox.showerror("Критическая ошибка", f"Ошибка подключения к базе данных.\nПодробности в app.log.", parent=self)
            self.on_complete_callback(None)
            self.destroy()

    def _on_closing(self):
        """При закрытии окна вызываем callback с None."""
        self.on_complete_callback(None)

def main():
    """
    Главная функция для запуска приложения.
    """
    logging.info("Application starting...")
    
    # --- Этап 1: Создание корневого окна и установка глобальных привязок ---
    # Создаем невидимое корневое окно. Оно будет служить основой для всего приложения.
    root = tk.Tk()
    root.withdraw() # Скрываем его

    try:
        # Устанавливаем глобальные привязки для Copy/Paste в русской раскладке
        # 46 - это keycode для 'C', 47 - для 'V' в Tkinter на Windows
        root.bind_class("Entry", "<Control-KeyPress-46>", lambda event: event.widget.event_generate("<<Copy>>"))
        root.bind_class("Text", "<Control-KeyPress-46>", lambda event: event.widget.event_generate("<<Copy>>"))
        root.bind_class("Entry", "<Control-KeyPress-47>", lambda event: event.widget.event_generate("<<Paste>>"))
        root.bind_class("Text", "<Control-KeyPress-47>", lambda event: event.widget.event_generate("<<Paste>>"))
        logging.info("Глобальные привязки для Copy/Paste успешно установлены.")
    except Exception as e:
        logging.error(f"Не удалось установить глобальные привязки для Copy/Paste: {e}")

    # --- Этап 2: Авторизация ---
    user_info_container = {}
    def on_auth_complete(result):
        """Callback, который сохраняет результат и позволяет основному циклу завершиться."""
        user_info_container['result'] = result
        root.quit() # Выходим из mainloop корневого окна

    # Создаем окно входа как дочернее от скрытого корневого окна
    login_app = StandaloneLoginWindow(on_auth_complete)
    root.mainloop() # Запускаем главный цикл. Он будет ждать, пока login_app не вызовет root.quit()

    user_info = user_info_container.get('result')

    # --- Этап 3: Запуск основного интерфейса или выход ---
    if not user_info:
        logging.info("Login failed or cancelled. Exiting application.")
        root.destroy() # Уничтожаем скрытое корневое окно перед выходом
        return
    
    log_message = f"Login successful. User: {user_info['name']}, Role: {user_info['role']}"
    if user_info.get('client_db_config'):
        log_message += f", Client DB: {user_info['client_db_config'].get('db_name')}"
    logging.info(log_message)

    role = user_info.get("role")
    root.destroy() # Уничтожаем старое корневое окно, т.к. главные окна создают свое.
    app = None
    if role == 'супервизор':
        app = SupervisorWindow(user_info)
    elif role == 'администратор':
        app = AdminWindow(user_info)
    
    if app:
        logging.info(f"Starting main application window for role: {role}")
        app.mainloop()
    else:
        logging.error(f"Unknown user role '{role}'. Cannot start application.")
        messagebox.showerror("Критическая ошибка", f"Неизвестная роль пользователя: {role}")

    logging.info("Application finished.")

if __name__ == "__main__":
    main()