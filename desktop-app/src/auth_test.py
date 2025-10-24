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
                    if user_role == 'администратор':
                        user_info['client_id'] = client_id
                        user_info['client_db_config'] = {
                            "db_name": db_name, "db_host": db_host, "db_port": db_port,
                            "db_user": db_user, "db_password": db_password, "db_ssl_cert": db_ssl_cert
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
    Главная функция для запуска изолированного теста авторизации.
    """
    logging.info("Запущен изолированный тест авторизации.")
    
    def on_auth_complete(user_info):
        """Callback, который будет вызван окном входа перед его закрытием."""
        if user_info:
            log_message = f"Тест пройден. Успешный вход. Пользователь: {user_info['name']}, Роль: {user_info['role']}"
            client_config = user_info.get('client_db_config')
            if client_config:
                log_message += f", База данных клиента: {client_config.get('db_name')}"
            logging.info(log_message)
        else:
            logging.info("Тест завершен. Вход не выполнен (окно закрыто или ошибка).")

    # Создаем экземпляр нашего главного окна.
    # Передаем ему функцию, которую он вызовет, когда закончит свою работу.
    app = StandaloneLoginWindow(on_auth_complete)
    
    # Запускаем главный цикл приложения.
    # Скрипт будет "висеть" здесь, пока окно `app` не будет уничтожено.
    app.mainloop()

if __name__ == "__main__":
    main()