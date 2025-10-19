# src/main_window.py

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os
import logging
import traceback
import time
import socket
import psycopg2
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

class SshTunnelProcess:
    """
    Контекстный менеджер для управления SSH-туннелем через системный процесс ssh.exe.
    """
    def __init__(self, ssh_host, ssh_port, ssh_user, ssh_key, remote_host, remote_port):
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.remote_host = remote_host
        self.remote_port = remote_port
        
        self.local_host = '127.0.0.1'
        self.local_port = self._get_free_port()
        self.process = None

    def _get_free_port(self):
        """Находит свободный TCP-порт для туннеля."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def __enter__(self):
        """Запускает SSH-туннель в фоновом процессе."""
        ssh_executable = 'ssh'
        # На Windows лучше указать полный путь к системному ssh.exe
        if sys.platform == "win32":
            ssh_executable = r'C:\Windows\System32\OpenSSH\ssh.exe'

        tunnel_command = [
            ssh_executable,
            '-N',  # Не выполнять удаленную команду
            '-L', f'{self.local_host}:{self.local_port}:{self.remote_host}:{self.remote_port}',
            '-p', str(self.ssh_port),
            '-i', self.ssh_key,
            f'{self.ssh_user}@{self.ssh_host}',
            '-o', 'StrictHostKeyChecking=no', # Автоматически принимать ключ хоста
            '-o', 'ExitOnForwardFailure=yes'  # Выйти, если не удалось создать туннель
        ]
        
        logging.info(f"Запуск SSH-туннеля командой: {' '.join(tunnel_command)}")
        
        # Для Windows, чтобы окно консоли не появлялось
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Перенаправляем stderr в лог, чтобы видеть ошибки от ssh.exe
        self.process = subprocess.Popen(
            tunnel_command, 
            startupinfo=startupinfo,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        time.sleep(2)  # Даем время на установку соединения
        
        # Проверяем, не завершился ли процесс с ошибкой
        if self.process.poll() is not None:
            error_output = self.process.stderr.read()
            logging.error(f"Процесс ssh.exe завершился с ошибкой: {error_output.strip()}")
            raise ConnectionError(f"Не удалось запустить SSH-туннель. Ошибка: {error_output.strip()}")
            
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Завершает процесс SSH-туннеля."""
        if self.process:
            logging.info("Закрытие SSH-туннеля...")
            self.process.terminate()
            self.process.wait()
            logging.info("SSH-туннель закрыт.")

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

        # Запускаем скрипт в новом окне консоли (кросс-платформенный способ)
        if sys.platform == "win32":
            subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            # Для Linux/macOS потребуется терминал, например xterm
            subprocess.Popen(['xterm', '-e'] + command)

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

        with SshTunnelProcess(
            ssh_host=os.getenv("SSH_HOST"),
            ssh_port=int(os.getenv("SSH_PORT", 22)),
            ssh_user=os.getenv("SSH_USER"),
            ssh_key=ssh_key_path,
            remote_host=os.getenv("DB_HOST"),
            remote_port=int(os.getenv("DB_PORT"))
        ) as tunnel:
            logging.info(f"SSH-туннель успешно создан. Локальный порт: {tunnel.local_port}")
            
            # 2. Проверяем доступность постгреса
            logging.info("Шаг 2: Проверка подключения к PostgreSQL через туннель...")
            with psycopg2.connect(
                dbname=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                host=tunnel.local_host,
                port=tunnel.local_port,
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
        with SshTunnelProcess(
            ssh_host=os.getenv("SSH_HOST"),
            ssh_port=int(os.getenv("SSH_PORT", 22)),
            ssh_user=os.getenv("SSH_USER"),
            ssh_key=ssh_key_path,
            remote_host=os.getenv("DB_HOST"),
            remote_port=int(os.getenv("DB_PORT"))
        ) as tunnel:
            logging.info(f"SSH туннель успешно создан. Локальный порт: {tunnel.local_port}")
            # Подключаемся к БД через локальный порт туннеля
            with psycopg2.connect(
                dbname=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                host=tunnel.local_host,
                port=tunnel.local_port
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

def open_print_management_window():
    """
    Открывает окно для управления печатью: выбор принтера, просмотр размеров бумаги и тестовая печать.
    """
    try:
        import win32print
        import win32ui
        from pywintypes import error as pywin_error
    except ImportError:
        messagebox.showerror("Ошибка", "Библиотека 'pywin32' не установлена.\nПожалуйста, установите ее командой: pip install pywin32")
        return

    # --- Создание окна ---
    print_window = tk.Toplevel(root)
    print_window.title("Управление печатью")
    print_window.geometry("500x400")
    print_window.transient(root) # Окно будет поверх главного
    print_window.grab_set() # Модальное поведение

    # --- Функции для работы с принтерами ---
    def load_printers():
        """Загружает список установленных принтеров в выпадающий список."""
        try:
            printers = [printer[2] for printer in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL, None, 1)]
            printer_combobox['values'] = printers
            if printers:
                printer_combobox.current(0)
                load_paper_sizes()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить список принтеров:\n{e}", parent=print_window)

    def load_paper_sizes(*args):
        """Загружает размеры бумаги для выбранного принтера."""
        printer_name = printer_combobox.get()
        if not printer_name:
            return
        
        paper_listbox.delete(0, tk.END)
        try:
            # Получаем дескриптор локального сервера печати, передавая None в OpenPrinter
            h_server = win32print.OpenPrinter(None)
            try:
                # Запрашиваем ВСЕ формы, зарегистрированные в системе.
                # Это позволяет увидеть все кастомные форматы.
                forms = win32print.EnumForms(h_server)
                for form in forms:
                    # Возвращаем фильтрацию по префиксу "Tilda_"
                    if form['Name'].startswith('Tilda_'):
                        paper_listbox.insert(tk.END, form['Name'])
            finally:
                win32print.ClosePrinter(h_server) # Обязательно закрываем дескриптор
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка при получении системных форматов бумаги: {e}\n{error_details}")
            messagebox.showerror("Ошибка", f"Не удалось получить размеры бумаги.\nПодробности в файле app.log", parent=print_window)

    def print_test_page():
        """Отправляет простую текстовую строку на выбранный принтер."""
        printer_name = printer_combobox.get()
        if not printer_name:
            messagebox.showwarning("Внимание", "Пожалуйста, выберите принтер.", parent=print_window)
            return

        # --- НОВЫЙ, БОЛЕЕ НАДЕЖНЫЙ МЕТОД ПЕЧАТИ ЧЕРЕЗ GDI ---
        try:
            # 1. Получаем хендл принтера, не запрашивая избыточных прав администратора.
            # Это решает проблему "Отказано в доступе".
            # Мы передаем None, чтобы использовать права доступа по умолчанию для текущего пользователя.
            h_printer = win32print.OpenPrinter(printer_name, None)

            # 2. Создаем контекст устройства (DC) для этого принтера
            # Это как "холст", на котором мы будем рисовать
            dc = win32ui.CreateDC()
            dc.CreatePrinterDC(printer_name)

            try:
                # 3. Начинаем документ
                dc.StartDoc("Тестовая страница из 'ТильдаКод'")
                
                # 4. Начинаем страницу
                dc.StartPage()

                # 5. Настраиваем шрифт
                font_data = {
                    'name': 'Arial', # Используем стандартный шрифт
                    'height': 20, # Уменьшаем размер для маленькой этикетки
                    'weight': 400, # Нормальный вес
                    'charset': 204, # Явно указываем кириллический набор символов
                }
                font = win32ui.CreateFont(font_data)
                dc.SelectObject(font)

                # 6. "Рисуем" текст на странице
                # Координаты (x, y) в точках (dots) от левого верхнего угла.
                # Уменьшаем координаты для термопринтеров и небольших этикеток,
                # чтобы текст не выходил за пределы области печати.
                dc.TextOut(10, 10, "Тестовая печать")
                dc.TextOut(10, 40, "из 'ТильдаКод'")

                # 7. Завершаем страницу и документ
                dc.EndPage()
                dc.EndDoc()

                messagebox.showinfo("Успех", f"Тестовая страница отправлена на принтер '{printer_name}'.", parent=print_window)

            finally:
                dc.DeleteDC() # Очищаем контекст устройства
                win32print.ClosePrinter(h_printer) # Закрываем принтер
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Общая ошибка печати: {e}\n{error_details}")
            messagebox.showerror("Ошибка печати", f"Не удалось напечатать тестовую страницу.\nПодробности в файле app.log", parent=print_window)

    # --- Виджеты окна ---
    main_frame = ttk.Frame(print_window, padding="10")
    main_frame.pack(expand=True, fill="both")

    ttk.Label(main_frame, text="Выберите принтер:").pack(fill="x", pady=2)
    printer_combobox = ttk.Combobox(main_frame, state="readonly")
    printer_combobox.pack(fill="x", pady=2)
    printer_combobox.bind("<<ComboboxSelected>>", load_paper_sizes)

    ttk.Label(main_frame, text="Поддерживаемые размеры бумаги:").pack(fill="x", pady=(10, 2))
    paper_listbox = tk.Listbox(main_frame, height=10)
    paper_listbox.pack(expand=True, fill="both", pady=2)

    ttk.Button(main_frame, text="Напечатать тестовую страницу", command=print_test_page).pack(fill="x", pady=(10, 2))

    # --- Первоначальная загрузка данных ---
    load_printers()


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

# -- Меню "Печать" --
print_menu = tk.Menu(menubar, tearoff=0)
print_menu.add_command(label="Управление печатью", command=open_print_management_window)
menubar.add_cascade(label="Печать", menu=print_menu)

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
