# src/auth_test.py

import tkinter as tk
from tkinter import messagebox
import os
import sys
import logging
import bcrypt
import traceback

# --- Настройка путей для импорта ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

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
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [auth_test] - %(message)s', # Добавим маркер для логов
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class StandaloneLoginWindow(tk.Tk):
    """
    Автономное, самодостаточное окно входа.
    После завершения работы (успех, ошибка, закрытие) вызывает callback и самоуничтожается.
    """
    def __init__(self, on_complete_callback):
        super().__init__()
        self.on_complete_callback = on_complete_callback

        self.title("Тест Авторизации")
        self.resizable(False, False)

        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind('<Return>', lambda event: self._verify_login())

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
        login = self.login_entry.get()
        password = self.password_entry.get()

        if not login or not password:
            messagebox.showerror("Ошибка", "Логин и пароль не могут быть пустыми.", parent=self)
            return

        # --- НОВАЯ ЛОГИКА: Заранее читаем содержимое SSL-сертификата ---
        # Это необходимо, чтобы передать его в конфигурацию для QR-кодов.
        ssl_cert_content = None
        try:
            app_portal_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            cert_path = os.path.join(app_portal_root, 'secrets', 'postgres', 'server.crt')
            if os.path.exists(cert_path):
                with open(cert_path, 'r', encoding='utf-8') as f:
                    ssl_cert_content = f.read()
            else:
                logging.warning(f"Файл сертификата не найден по пути: {cert_path}")
        except Exception as e:
            logging.error(f"Ошибка при чтении файла SSL-сертификата: {e}")

        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    # Изменяем запрос, чтобы через LEFT JOIN получить имя базы данных клиента
                    # и все данные для подключения к ней.
                    query = """
                        SELECT u.name, u.password_hash, u.role, u.client_id,
                               c.db_name, c.db_host, c.db_port, c.db_user, c.db_password, c.db_ssl_cert
                        FROM users u
                        LEFT JOIN clients c ON u.client_id = c.id
                        WHERE u.login = %s AND (u.role = 'супервизор' OR u.role = 'администратор')
                    """
                    cur.execute(query, (login,))
                    user_data = cur.fetchone()

            if user_data:
                (user_name, hashed_password, user_role, client_id,
                 db_name, db_host, db_port, db_user, db_password, db_ssl_cert) = user_data

                if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                    user_info = {"name": user_name, "role": user_role}
                    # Если это администратор, добавляем всю информацию о его клиенте
                    # --- ИСПРАВЛЕНИЕ: Читаем SSL-сертификат из активного соединения ---
                    if user_role == 'администратор':
                        user_info['client_id'] = client_id
                        user_info['client_db_config'] = {
                            "db_name": db_name, "db_host": db_host, "db_port": db_port,
                            "db_user": db_user, "db_password": db_password,
                            "db_ssl_cert": ssl_cert_content # Используем прочитанное содержимое
                        }
                    self.on_complete_callback(user_info) # Сначала вызываем callback
                    self.destroy() # Затем уничтожаем окно
                else:
                    messagebox.showerror("Ошибка", "Неверный пароль.", parent=self)
                    # Не закрываем окно, даем пользователю еще попытку
            else:
                messagebox.showerror("Ошибка", "Пользователь не найден или не имеет прав доступа.", parent=self)

        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка авторизации: {e}\n{error_details}")
            messagebox.showerror("Критическая ошибка", f"Ошибка подключения к базе данных.\nПодробности в app.log.", parent=self)
            self.on_complete_callback(None) # Сообщаем о провале
            self.destroy() # И закрываемся

    def _on_closing(self):
        """При закрытии окна вызываем callback с None."""
        self.on_complete_callback(None)

def main():
    """
    Главная функция для запуска приложения.
    """
    logging.info("Application starting...")
    
    # --- Этап 1: Авторизация ---
    user_info_container = {}
    def on_auth_complete(result):
        """Callback, который сохраняет результат и уничтожает окно входа."""
        user_info_container['result'] = result
        # Не нужно вызывать destroy() здесь, так как окно само себя уничтожает

    # Создаем и запускаем окно входа.
    # Его mainloop будет блокировать выполнение до закрытия окна.
    login_app = StandaloneLoginWindow(on_auth_complete)
    login_app.mainloop()

    user_info = user_info_container.get('result')

    # --- Этап 2: Запуск основного интерфейса ---
    if not user_info:
        logging.info("Login failed or cancelled. Exiting application.")
        return
    
    log_message = f"Login successful. User: {user_info['name']}, Role: {user_info['role']}"
    if user_info.get('client_db_config'):
        log_message += f", Client DB: {user_info['client_db_config'].get('db_name')}"
    logging.info(log_message)

    role = user_info.get("role")
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