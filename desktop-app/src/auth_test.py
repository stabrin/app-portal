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

class StandaloneLoginWindow(tk.Toplevel):
    """
    Автономное окно входа, которое после завершения вызывает callback
    и передает в него результат (словарь с данными пользователя или None).
    """
    def __init__(self, parent, on_complete_callback):
        super().__init__(parent)
        self.on_complete_callback = on_complete_callback

        self.title("Тест Авторизации")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind('<Return>', lambda event: self._verify_login())

        self._create_widgets()
        self.lift()
        self.focus_force()

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
                    cur.execute("SELECT name, password_hash, role FROM users WHERE login = %s AND (role = 'супервизор' OR role = 'администратор')", (login,))
                    user_data = cur.fetchone()

            if user_data:
                user_name, hashed_password, user_role = user_data
                if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                    user_info = {"name": user_name, "role": user_role}
                    self.on_complete_callback(user_info)
                else:
                    messagebox.showerror("Ошибка", "Неверный пароль.", parent=self)
                    # Не вызываем callback, чтобы процесс не завершился
            else:
                messagebox.showerror("Ошибка", "Пользователь не найден или не имеет прав доступа.", parent=self)

        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка авторизации: {e}\n{error_details}")
            messagebox.showerror("Критическая ошибка", f"Ошибка подключения к базе данных.\nПодробности в app.log.", parent=self)
            self.on_complete_callback(None) # Завершаем при критической ошибке

    def _on_closing(self):
        """При закрытии окна вызываем callback с None."""
        self.on_complete_callback(None)

def main():
    """
    Главная функция для запуска изолированного теста авторизации.
    """
    logging.info("Запущен изолированный тест авторизации.")
    
    # Создаем временный корневой элемент, который будет невидим.
    # Он нужен только для того, чтобы на его основе создать Toplevel.
    root = tk.Tk()
    root.withdraw()

    def on_auth_complete(user_info):
        """Callback, который будет вызван окном входа."""
        if user_info:
            logging.info(f"Тест пройден. Успешный вход. Пользователь: {user_info['name']}, Роль: {user_info['role']}")
        else:
            logging.info("Тест завершен. Вход не выполнен (окно закрыто или ошибка).")
        
        # Уничтожаем корневое окно, что приводит к завершению скрипта.
        root.destroy()

    # Создаем и ждем завершения работы окна входа.
    login_window = StandaloneLoginWindow(root, on_auth_complete)
    root.mainloop()

if __name__ == "__main__":
    main()