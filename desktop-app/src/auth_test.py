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
sys.path.insert(0, project_root)

from db_connector import get_main_db_connection
# Импортируем наши новые классы интерфейсов
from supervisor_ui import SupervisorWindow
from admin_ui import AdminWindow


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

    def _verify_login(self):
        login = self.login_entry.get()
        password = self.password_entry.get()

        if not login or not password:
            messagebox.showerror("Ошибка", "Логин и пароль не могут быть пустыми.", parent=self)
            return

        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    # Изменяем запрос, чтобы через LEFT JOIN получить имя базы данных клиента
                    query = """
                        SELECT u.name, u.password_hash, u.role, c.db_name
                        FROM users u
                        LEFT JOIN clients c ON u.client_id = c.id
                        WHERE u.login = %s AND (u.role = 'супервизор' OR u.role = 'администратор')
                    """
                    cur.execute(query, (login,))
                    user_data = cur.fetchone()

            if user_data:
                user_name, hashed_password, user_role, client_db_name = user_data
                if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                    user_info = {"name": user_name, "role": user_role}
                    # Если это администратор, добавляем имя его базы данных
                    if user_role == 'администратор':
                        user_info['db_name'] = client_db_name
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
    Главная функция для запуска изолированного теста авторизации.
    """
    logging.info("Application starting...")
    
    # --- Этап 1: Авторизация ---
    # Создаем временный root для окна входа
    auth_root = tk.Tk()
    auth_root.withdraw()

    user_info_container = {}
    def on_auth_complete(result):
        user_info_container['result'] = result
        auth_root.destroy()

    StandaloneLoginWindow(on_auth_complete)
    auth_root.mainloop()

    user_info = user_info_container.get('result')

    # --- Этап 2: Запуск основного интерфейса ---
    if not user_info:
        logging.info("Login failed or cancelled. Exiting application.")
        return
    
    role = user_info.get("role")
    if role == 'супервизор':
        app = SupervisorWindow(user_info)
        app.mainloop()
    elif role == 'администратор':
        app = AdminWindow(user_info)
        app.mainloop()
    else:
        logging.error(f"Unknown user role '{role}'. Cannot start application.")
        messagebox.showerror("Критическая ошибка", f"Неизвестная роль пользователя: {role}")

    logging.info("Application finished.")

if __name__ == "__main__":
    main()