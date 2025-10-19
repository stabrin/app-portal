# src/main_window.py

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os
import logging
import traceback
import psycopg2
from sshtunnel import SSHTunnelForwarder
from psycopg2 import sql
from dotenv import load_dotenv

# --- Настройка логирования ---
# Определяем путь к лог-файлу в корне папки desktop-app
log_file_path = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), 'app.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'), # Запись в файл
        logging.StreamHandler()  # Вывод в консоль
    ]
)

# Глобальная переменная для хранения виджета таблицы, чтобы его можно было удалять
tree = None


def run_db_setup():
    """
    Запускает скрипт setup_database.py в новом окне терминала.
    """
    try:
        # Определяем корневую папку проекта (на уровень выше, чем 'src')
        desktop_app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        script_path = os.path.join(desktop_app_root, 'scripts', 'setup_database.py')

        # Проверяем, существует ли скрипт
        if not os.path.exists(script_path):
            print(f"Ошибка: Скрипт не найден по пути {script_path}")
            # Можно показать ошибку и в GUI
            tk.messagebox.showerror("Ошибка", f"Скрипт не найден:\n{script_path}")
            return

        # sys.executable - это путь к интерпретатору Python, который запустил это приложение
        # (правильный Python из вашего .venv)
        command = [sys.executable, script_path]

        # Запускаем скрипт в новом окне консоли
        subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)

    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Не удалось запустить скрипт 'setup_database.py': {e}\n{error_details}")
        messagebox.showerror("Ошибка запуска", f"Произошла ошибка при запуске скрипта.\nПодробности в файле app.log")

def test_connection():
    """
    Устанавливает SSH-туннель и проверяет доступность PostgreSQL, не получая данных.
    """
    try:
        logging.info("Проверка подключения к удаленной БД...")
        # Загружаем переменные из .env файла
        desktop_app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        dotenv_path = os.path.join(desktop_app_root, '.env')
        if not os.path.exists(dotenv_path):
            messagebox.showerror("Ошибка", f"Файл .env не найден по пути: {dotenv_path}")
            return
        load_dotenv(dotenv_path=dotenv_path)

        # 1. Устанавливаем SSH подключение (туннель)
        logging.info("Шаг 1: Установка SSH-туннеля...")
        ssh_key_path = os.path.join(desktop_app_root, 'keys', os.getenv("SSH_KEY_FILENAME"))
        if not os.path.exists(ssh_key_path):
            raise FileNotFoundError(f"SSH ключ не найден по пути: {ssh_key_path}")

        with SSHTunnelForwarder(
            (os.getenv("SSH_HOST"), int(os.getenv("SSH_PORT", 22))),
            ssh_username=os.getenv("SSH_USER"),
            ssh_pkey=ssh_key_path,
            remote_bind_address=(os.getenv("DB_HOST"), int(os.getenv("DB_PORT"))),
        ) as server:
            logging.info(f"SSH-туннель успешно создан. Локальный порт: {server.local_bind_port}")
            
            # 2. Проверяем доступность постгреса
            logging.info("Шаг 2: Проверка подключения к PostgreSQL через туннель...")
            with psycopg2.connect(
                dbname=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                host=server.local_bind_host,
                port=server.local_bind_port,
                connect_timeout=5  # Таймаут подключения 5 секунд
            ) as conn:
                # Просто проверяем, что соединение активно
                logging.info(f"Соединение с PostgreSQL (версия {conn.server_version}) успешно установлено.")
        
        messagebox.showinfo("Проверка подключения", "Подключение к удаленной базе данных успешно установлено!")

    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Ошибка при проверке подключения: {e}\n{error_details}")
        messagebox.showerror("Ошибка подключения", f"Не удалось подключиться.\nПодробности записаны в файл app.log")

def connect_and_show_orders():
    """
    Подключается к БД, считывает таблицу orders и отображает ее в главном окне.
    """
    global tree
    # Очищаем предыдущую таблицу, если она есть
    if tree:
        tree.destroy()

    try:
        # Загружаем переменные из .env файла в папке desktop-app
        desktop_app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        dotenv_path = os.path.join(desktop_app_root, '.env')
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path=dotenv_path)
        else:
            messagebox.showerror("Ошибка", f"Файл .env не найден по пути: {dotenv_path}")
            return

        orders = []

        def get_orders_from_db(connection):
            """Внутренняя функция для выполнения запроса и получения данных."""
            with connection.cursor() as cur:
                # Используем имя таблицы из .env для консистентности
                orders_table = os.getenv('TABLE_ORDERS', 'orders')
                query = sql.SQL("SELECT id, client_name, status, created_at FROM {} ORDER BY id DESC;").format(sql.Identifier(orders_table))
                cur.execute(query)
                return cur.fetchall()

        logging.info("Попытка подключения к удаленной БД через SSH-туннель...")
        
        # Путь к ключу. Предполагаем, что папка 'keys' находится рядом с .env
        ssh_key_path = os.path.join(desktop_app_root, 'keys', os.getenv("SSH_KEY_FILENAME"))
        if not os.path.exists(ssh_key_path):
            raise FileNotFoundError(f"SSH ключ не найден по пути: {ssh_key_path}")

        # Создаем SSH туннель
        with SSHTunnelForwarder(
            (os.getenv("SSH_HOST"), int(os.getenv("SSH_PORT", 22))),
            ssh_username=os.getenv("SSH_USER"),
            ssh_pkey=ssh_key_path,
            remote_bind_address=(os.getenv("DB_HOST"), int(os.getenv("DB_PORT"))),
            # local_bind_address=('127.0.0.1', 6543) # Можно указать явно или дать выбрать свободный порт
        ) as server:
            logging.info(f"SSH туннель успешно создан. Локальный порт: {server.local_bind_port}")
            # Подключаемся к БД через локальный порт туннеля
            with psycopg2.connect(
                dbname=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                host=server.local_bind_host,
                port=server.local_bind_port
            ) as conn:
                orders = get_orders_from_db(conn)
            logging.info("Данные из удаленной БД успешно получены.")

        # Создаем Treeview для отображения данных
        columns = ('id', 'client_name', 'status', 'created_at')
        tree = ttk.Treeview(root, columns=columns, show='headings')

        # Определяем заголовки
        tree.heading('id', text='ID')
        tree.heading('client_name', text='Клиент')
        tree.heading('status', text='Статус')
        tree.heading('created_at', text='Дата создания')

        # Настраиваем ширину колонок
        tree.column('id', width=50, anchor=tk.CENTER)
        tree.column('client_name', width=200)
        tree.column('status', width=100, anchor=tk.CENTER)
        tree.column('created_at', width=150)

        # Добавляем данные в таблицу
        for order in orders:
            # Форматируем дату для красивого отображения
            formatted_date = order[3].strftime('%Y-%m-%d %H:%M:%S') if order[3] else ''
            tree.insert('', tk.END, values=(order[0], order[1], order[2], formatted_date))

        # Размещаем таблицу в окне
        tree.pack(expand=True, fill='both')

    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Ошибка при подключении к БД или получении данных: {e}\n{error_details}")
        messagebox.showerror("Ошибка подключения к БД", f"Не удалось получить данные.\nПодробности записаны в файл app.log")


# 1. Создаем главное окно приложения
root = tk.Tk()

# 2. Устанавливаем заголовок окна
root.title("ТильдаКод")

# 3. Устанавливаем начальный размер окна (ширина x высота)
root.geometry("600x400")

# 4. Создаем главное меню
menubar = tk.Menu(root)
root.config(menu=menubar)

# -- Меню "Файл" --
file_menu = tk.Menu(menubar, tearoff=0)
file_menu.add_command(label="Инициализация БД", command=run_db_setup)
file_menu.add_separator()
file_menu.add_command(label="Выход", command=root.quit)
menubar.add_cascade(label="Файл", menu=file_menu)

# -- Меню "База данных" --
db_menu = tk.Menu(menubar, tearoff=0)
db_menu.add_command(label="Проверить подключение", command=test_connection)
db_menu.add_separator()
db_menu.add_command(label="Показать заказы", command=connect_and_show_orders)
menubar.add_cascade(label="База данных", menu=db_menu)

# -- Меню "Справка" --
help_menu = tk.Menu(menubar, tearoff=0)
help_menu.add_command(label="О программе") # Пока без функции
menubar.add_cascade(label="Справка", menu=help_menu)

# 6. Запускаем главный цикл приложения.
#    Окно будет отображаться и ждать действий пользователя, пока его не закроют.
root.mainloop()
