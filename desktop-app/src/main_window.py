# src/main_window.py

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os
import psycopg2
from dotenv import load_dotenv

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
        print(f"Не удалось запустить скрипт: {e}")
        messagebox.showerror("Ошибка запуска", f"Не удалось запустить скрипт:\n{e}")

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

        # Подключаемся к БД
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST_LOCAL", "localhost"),
            port=os.getenv("DB_PORT")
        )
        with conn.cursor() as cur:
            cur.execute("SELECT id, client_name, status, created_at FROM orders ORDER BY id DESC;")
            orders = cur.fetchall()
        conn.close()

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
        messagebox.showerror("Ошибка подключения к БД", f"Не удалось получить данные:\n{e}")


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
db_menu.add_command(label="Подключиться к БД", command=connect_and_show_orders)
menubar.add_cascade(label="База данных", menu=db_menu)

# -- Меню "Справка" --
help_menu = tk.Menu(menubar, tearoff=0)
help_menu.add_command(label="О программе") # Пока без функции
menubar.add_cascade(label="Справка", menu=help_menu)

# 6. Запускаем главный цикл приложения.
#    Окно будет отображаться и ждать действий пользователя, пока его не закроют.
root.mainloop()
