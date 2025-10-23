# src/main_window.py

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os
import logging

# --- РЕШЕНИЕ ПРОБЛЕМЫ С ИМПОРТОМ ---
# Добавляем корневую папку проекта в пути Python, чтобы импорты из `core` работали.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

import traceback
import psycopg2
from psycopg2 import sql
import bcrypt
from dotenv import load_dotenv
# Импортируем наши новые компоненты
from db_connector import get_main_db_connection
from scripts.setup_client_database import update_client_db_schema

# --- Загрузка переменных окружения ---
# Делаем это один раз при старте приложения
load_dotenv(os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), '.env'))

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
    Проверяет SSL-подключение к базе данных PostgreSQL.
    """
    try:
        logging.info("Проверка SSL-подключения к удаленной БД...")
        
        # Находим путь к сертификату сервера
        app_portal_root = os.path.abspath(os.path.join(project_root, '..'))
        cert_path = os.path.join(app_portal_root, 'secrets', 'postgres', 'server.crt')
        if not os.path.exists(cert_path):
            raise FileNotFoundError(f"Сертификат сервера не найден по пути: {cert_path}")

        # Для проверки подключения используем системную базу 'postgres', которая всегда существует.
        # Это делает проверку независимой от наличия базы 'portal_db'.
        with psycopg2.connect(
            dbname='postgres', # Подключаемся к системной базе
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            connect_timeout=5,
            sslmode='verify-full',
            sslrootcert=cert_path
        ) as conn:
            ssl_info = conn.get_dsn_parameters().get('sslmode')
            logging.info(f"SSL-соединение с PostgreSQL (версия {conn.server_version}) успешно установлено. Режим: {ssl_info}")
        
        messagebox.showinfo("Проверка подключения", "SSL-подключение к серверу PostgreSQL успешно установлено!")

    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Ошибка при проверке подключения: {e}\n{error_details}")
        messagebox.showerror("Ошибка подключения", f"Не удалось подключиться.\nПодробности записаны в файл app.log")

def connect_and_show_orders():
    """
    Подключается к БД, считывает таблицу orders и отображает ее в главном окне.
    """
    global tree
    if tree:
        tree.destroy()

    try:
        logging.info("Попытка подключения к удаленной БД через SSL...")
        
        with get_main_db_connection() as conn:
            with conn.cursor() as cur:
                orders_table = os.getenv('TABLE_ORDERS', 'orders')
                query = sql.SQL("SELECT id, client_name, status, created_at FROM {} ORDER BY id DESC;").format(sql.Identifier(orders_table))
                cur.execute(query)
                orders = cur.fetchall()
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
        import win32con # Добавляем импорт модуля с константами
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

    # Словарь для хранения размеров бумаги: {'ИмяФормы': (ширина_мм, высота_мм)}
    paper_sizes_data = {}

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
        paper_sizes_data.clear()
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
                        name = form['Name']
                        # Размеры хранятся в 1/1000 мм, переводим в мм
                        width_mm = form['Size']['cx'] / 1000.0
                        height_mm = form['Size']['cy'] / 1000.0
                        paper_sizes_data[name] = (width_mm, height_mm)
                        paper_listbox.insert(tk.END, f"{name} ({width_mm} x {height_mm} мм)")
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
        
        # Получаем имя выбранной бумаги из Listbox
        selected_indices = paper_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Внимание", "Пожалуйста, выберите размер бумаги.", parent=print_window)
            return
        
        # --- ИСПРАВЛЕНИЕ: Более надежное извлечение имени формата ---
        # Проблема: .split(' ')[0] не работает, если в имени формата есть пробел (например, "Tilda_58 40").
        # Решение: Находим позицию скобки '(', которая отделяет имя от размеров,
        # и берем всё, что находится до нее, убирая лишние пробелы по краям.
        full_listbox_string = paper_listbox.get(selected_indices[0])
        separator_pos = full_listbox_string.find(' (')
        selected_paper_name = full_listbox_string[:separator_pos].strip() if separator_pos != -1 else full_listbox_string.strip()

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

                # 6. Масштабирование и позиционирование текста
                # Получаем разрешение принтера (точек на дюйм)
                dpi_x = dc.GetDeviceCaps(88) # LOGPIXELSX
                dpi_y = dc.GetDeviceCaps(90) # LOGPIXELSY
                
                # Переводим дюймы в миллиметры
                dots_per_mm_x = dpi_x / 25.4
                dots_per_mm_y = dpi_y / 25.4

                # Получаем размеры бумаги в мм из нашего словаря
                paper_width_mm, paper_height_mm = paper_sizes_data[selected_paper_name]

                # Считаем размеры бумаги в точках (пикселях)
                paper_width_dots = int(paper_width_mm * dots_per_mm_x)
                paper_height_dots = int(paper_height_mm * dots_per_mm_y)

                # Текст для печати
                line1 = "Тестовая печать"
                line2 = "из 'ТильдаКод'"

                # Устанавливаем выравнивание по центру
                dc.SetTextAlign(win32con.TA_CENTER | win32con.TA_TOP)

                # "Рисуем" текст, позиционируя его по центру ширины этикетки
                # и с небольшим отступом сверху
                dc.TextOut(paper_width_dots // 2, 10, line1)
                dc.TextOut(paper_width_dots // 2, 40, line2)

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

def open_clients_management_window():
    """Открывает окно для управления клиентами и пользователями."""
    
    def load_clients():
        """Загружает список клиентов из БД в Treeview."""
        for i in clients_tree.get_children():
            clients_tree.delete(i)
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, db_host, created_at FROM clients ORDER BY name;")
                    for row in cur.fetchall():
                        clients_tree.insert('', 'end', values=row)
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка загрузки клиентов: {e}\n{error_details}")
            messagebox.showerror("Ошибка", "Не удалось загрузить список клиентов.", parent=clients_window)

    def on_client_select(event):
        """При выборе клиента загружает список его пользователей."""
        selected_item = clients_tree.focus()
        if not selected_item:
            return
        client_id = clients_tree.item(selected_item)['values'][0]
        load_users(client_id)

    def load_users(client_id):
        """Загружает пользователей для указанного клиента."""
        for i in users_tree.get_children():
            users_tree.delete(i)
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, login, role, is_active FROM users WHERE client_id = %s ORDER BY name;", (client_id,))
                    for row in cur.fetchall():
                        users_tree.insert('', 'end', values=row)
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка загрузки пользователей: {e}\n{error_details}")
            messagebox.showerror("Ошибка", "Не удалось загрузить список пользователей.", parent=clients_window)

    def open_client_editor(client_id=None):
        """Открывает окно для добавления или редактирования клиента."""
        editor_window = tk.Toplevel(clients_window)
        editor_window.title("Редактор клиента")
        editor_window.grab_set()

        main_editor_frame = ttk.Frame(editor_window, padding="10")
        main_editor_frame.pack(fill=tk.BOTH, expand=True)

        # Фрейм для полей ввода данных клиента
        client_data_frame = ttk.LabelFrame(main_editor_frame, text="Данные клиента")
        client_data_frame.pack(fill=tk.X, pady=5)

        entries = {}
        # Поля "Имя" и все поля для подключения к БД
        fields = ["Имя", "DB Хост", "DB Порт", "DB Имя", "DB Пользователь", "DB Пароль"]

        for i, field in enumerate(fields):
            ttk.Label(client_data_frame, text=field + ":").grid(row=i, column=0, padx=5, pady=2, sticky='w')
            entry = ttk.Entry(client_data_frame, width=40)
            entry.grid(row=i, column=1, padx=5, pady=2, sticky='ew')
            entries[field] = entry
        client_data_frame.columnconfigure(1, weight=1)

        # Поле для SSL сертификата
        cert_frame = ttk.LabelFrame(main_editor_frame, text="SSL-сертификат для подключения к БД клиента")
        cert_frame.pack(fill=tk.X, pady=5)
        ssl_cert_text = tk.Text(cert_frame, height=8, width=80)
        ssl_cert_text.pack(fill=tk.X, expand=True, padx=5, pady=5)

        def run_client_db_setup():
            """
            Запускает скрипт инициализации/обновления для базы данных клиента.
            """
            if not client_id:
                messagebox.showwarning("Внимание", "Сначала сохраните клиента.", parent=editor_window)
                return

            if not messagebox.askyesno("Подтверждение", "Вы уверены, что хотите инициализировать/обновить схему для базы данных этого клиента?\n\nСуществующие данные не будут удалены, но будут созданы недостающие таблицы.", parent=editor_window):
                return

            client_conn = None
            temp_cert_file = None
            try:
                # 1. Получаем данные для подключения к БД клиента
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT db_host, db_port, db_name, db_user, db_password, db_ssl_cert FROM clients WHERE id = %s", (client_id,))
                        db_data = cur.fetchone()
                
                if not db_data:
                    raise ValueError("Не удалось найти данные для подключения к БД клиента.")

                db_host, db_port, db_name, db_user, db_password, db_ssl_cert = db_data

                # 2. Создаем временный файл для сертификата, если он есть
                ssl_params = {}
                if db_ssl_cert:
                    import tempfile
                    # Создаем временный файл с суффиксом .crt
                    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                        fp.write(db_ssl_cert)
                        temp_cert_file = fp.name
                    ssl_params = {'sslmode': 'verify-full', 'sslrootcert': temp_cert_file}
                    logging.info(f"Используется временный SSL-сертификат: {temp_cert_file}")

                # 3. Подключаемся к БД клиента
                logging.info(f"Подключаюсь к базе клиента '{db_name}' на {db_host}...")
                client_conn = psycopg2.connect(host=db_host, port=db_port, dbname=db_name, user=db_user, password=db_password, **ssl_params)

                # 4. Запускаем функцию обновления схемы
                if update_client_db_schema(client_conn):
                    messagebox.showinfo("Успех", "Схема базы данных клиента успешно обновлена.", parent=editor_window)
                else:
                    messagebox.showerror("Ошибка", "Произошла ошибка при обновлении схемы. Подробности в app.log.", parent=editor_window)

            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось выполнить инициализацию: {e}", parent=editor_window)
            finally:
                if client_conn: client_conn.close()
                if temp_cert_file and os.path.exists(temp_cert_file): os.remove(temp_cert_file)

        def sync_user_with_client_db(user_login, password_hash, is_admin, is_active):
            """Синхронизирует данные пользователя с базой данных клиента."""
            client_conn = None
            temp_cert_file = None
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT db_host, db_port, db_name, db_user, db_password, db_ssl_cert FROM clients WHERE id = %s", (client_id,))
                        db_data = cur.fetchone()
                if not db_data: raise ValueError("Данные клиента не найдены.")

                db_host, db_port, db_name, db_user, db_password, db_ssl_cert = db_data
                ssl_params = {}
                if db_ssl_cert:
                    import tempfile
                    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                        fp.write(db_ssl_cert)
                        temp_cert_file = fp.name
                    ssl_params = {'sslmode': 'verify-full', 'sslrootcert': temp_cert_file}

                client_conn = psycopg2.connect(host=db_host, port=db_port, dbname=db_name, user=db_user, password=db_password, **ssl_params)
                with client_conn.cursor() as cur:
                    # Используем INSERT ... ON CONFLICT для создания или обновления пользователя
                    query = sql.SQL("""
                        INSERT INTO users (username, password_hash, is_admin, is_active)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (username) DO UPDATE SET
                            password_hash = EXCLUDED.password_hash,
                            is_admin = EXCLUDED.is_admin,
                            is_active = EXCLUDED.is_active;
                    """)
                    cur.execute(query, (user_login, password_hash, is_admin, is_active))
                client_conn.commit()
                logging.info(f"Пользователь '{user_login}' успешно синхронизирован с БД клиента '{db_name}'.")
                return True
            except Exception as e:
                logging.error(f"Ошибка синхронизации пользователя с БД клиента: {e}")
                if client_conn: client_conn.rollback()
                messagebox.showerror("Ошибка синхронизации", f"Не удалось обновить данные в базе клиента: {e}", parent=editor_window)
                return False
            finally:
                if client_conn: client_conn.close()
                if temp_cert_file and os.path.exists(temp_cert_file): os.remove(temp_cert_file)

        def add_user():
            """Открывает окно для добавления нового пользователя-администратора."""
            if not client_id: return

            user_window = tk.Toplevel(editor_window)
            user_window.title("Новый администратор")
            user_window.grab_set()

            ttk.Label(user_window, text="Имя:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
            name_entry = ttk.Entry(user_window, width=30)
            name_entry.grid(row=0, column=1, padx=5, pady=5)

            ttk.Label(user_window, text="Логин:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
            login_entry = ttk.Entry(user_window, width=30)
            login_entry.grid(row=1, column=1, padx=5, pady=5)

            ttk.Label(user_window, text="Пароль:").grid(row=2, column=0, padx=5, pady=5, sticky='w')
            pass_entry = ttk.Entry(user_window, width=30, show="*")
            pass_entry.grid(row=2, column=1, padx=5, pady=5)

            def save():
                name, login, password = name_entry.get(), login_entry.get(), pass_entry.get()
                if not all([name, login, password]):
                    messagebox.showwarning("Внимание", "Все поля обязательны.", parent=user_window)
                    return

                try:
                    hashed_pass = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    with get_main_db_connection() as conn:
                        with conn.cursor() as cur:
                            # 1. Сохраняем в главную базу
                            cur.execute("INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, 'администратор', %s)",
                                        (name, login, hashed_pass, client_id))
                        conn.commit()
                    
                    # 2. Синхронизируем с базой клиента
                    if sync_user_with_client_db(login, hashed_pass, True, True):
                        messagebox.showinfo("Успех", "Пользователь успешно создан.", parent=user_window)
                        load_users_for_editor(client_id)
                        user_window.destroy()

                except psycopg2.IntegrityError:
                    messagebox.showerror("Ошибка", f"Пользователь с логином '{login}' уже существует.", parent=user_window)
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось создать пользователя: {e}", parent=user_window)

            ttk.Button(user_window, text="Сохранить", command=save).grid(row=3, column=1, sticky='e', padx=5, pady=10)
            ttk.Button(user_window, text="Отмена", command=user_window.destroy).grid(row=3, column=0, sticky='w', padx=5, pady=10)

        def edit_user():
            """Редактирует имя и пароль выбранного пользователя."""
            selected_item = users_in_editor_tree.focus()
            if not selected_item: return
            user_id, name, login, _, _ = users_in_editor_tree.item(selected_item)['values']

            user_window = tk.Toplevel(editor_window)
            user_window.title(f"Редактор: {name}")
            user_window.grab_set()

            ttk.Label(user_window, text="Имя:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
            name_entry = ttk.Entry(user_window, width=30)
            name_entry.insert(0, name)
            name_entry.grid(row=0, column=1, padx=5, pady=5)

            ttk.Label(user_window, text="Новый пароль:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
            pass_entry = ttk.Entry(user_window, width=30, show="*")
            pass_entry.grid(row=1, column=1, padx=5, pady=5)
            ttk.Label(user_window, text="(оставьте пустым, если не меняете)").grid(row=2, columnspan=2, padx=5)

            def save():
                new_name = name_entry.get()
                new_password = pass_entry.get()
                try:
                    with get_main_db_connection() as conn:
                        with conn.cursor() as cur:
                            if new_password:
                                hashed_pass = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                                cur.execute("UPDATE users SET name = %s, password_hash = %s WHERE id = %s", (new_name, hashed_pass, user_id))
                                # Получаем is_active для синхронизации
                                cur.execute("SELECT is_active FROM users WHERE id = %s", (user_id,))
                                is_active = cur.fetchone()[0]
                                sync_user_with_client_db(login, hashed_pass, True, is_active)
                            else:
                                cur.execute("UPDATE users SET name = %s WHERE id = %s", (new_name, user_id))
                        conn.commit()
                    messagebox.showinfo("Успех", "Данные пользователя обновлены.", parent=user_window)
                    load_users_for_editor(client_id)
                    user_window.destroy()
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось обновить пользователя: {e}", parent=user_window)

            ttk.Button(user_window, text="Сохранить", command=save).grid(row=3, column=1, sticky='e', padx=5, pady=10)
            ttk.Button(user_window, text="Отмена", command=user_window.destroy).grid(row=3, column=0, sticky='w', padx=5, pady=10)

        def delete_user():
            selected_item = users_in_editor_tree.focus()
            if not selected_item: return
            user_id, name, login, _, _ = users_in_editor_tree.item(selected_item)['values']

            if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите удалить пользователя '{name}' ({login})?\nЭто действие необратимо.", parent=editor_window):
                return
            
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                    conn.commit()
                # Синхронизируем удаление (устанавливаем is_active=False и пустой пароль)
                sync_user_with_client_db(login, "deleted", False, False)
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось удалить пользователя: {e}", parent=editor_window)

        def toggle_user_activity():
            selected_item = users_in_editor_tree.focus()
            if not selected_item: return
            user_id, _, login, _, is_active = users_in_editor_tree.item(selected_item)['values']
            new_status = not is_active

            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, user_id))
                        # Получаем хэш пароля для синхронизации
                        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
                        password_hash = cur.fetchone()[0]
                    conn.commit()
                
                sync_user_with_client_db(login, password_hash, True, new_status)
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось изменить статус пользователя: {e}", parent=editor_window)

        # --- Блок управления пользователями ---
        users_management_frame = ttk.LabelFrame(main_editor_frame, text="Пользователи этого клиента")
        users_management_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Кнопки управления
        user_buttons_frame = ttk.Frame(users_management_frame)
        user_buttons_frame.pack(fill=tk.X, padx=5, pady=5)

        btn_add_user = ttk.Button(user_buttons_frame, text="Создать", command=add_user)
        btn_add_user.pack(side=tk.LEFT, padx=2)
        btn_edit_user = ttk.Button(user_buttons_frame, text="Редактировать", command=edit_user)
        btn_edit_user.pack(side=tk.LEFT, padx=2)
        btn_delete_user = ttk.Button(user_buttons_frame, text="Удалить", command=delete_user)
        btn_delete_user.pack(side=tk.LEFT, padx=2)
        btn_toggle_user = ttk.Button(user_buttons_frame, text="Вкл/Выкл", command=toggle_user_activity)
        btn_toggle_user.pack(side=tk.LEFT, padx=2)

        # Таблица пользователей
        user_tree_cols = ('id', 'name', 'login', 'role', 'is_active')
        users_in_editor_tree = ttk.Treeview(users_management_frame, columns=user_tree_cols, show='headings', height=5)
        users_in_editor_tree.heading('id', text='ID')
        users_in_editor_tree.heading('name', text='Имя')
        users_in_editor_tree.heading('login', text='Логин')
        users_in_editor_tree.heading('role', text='Роль')
        users_in_editor_tree.heading('is_active', text='Активен')
        users_in_editor_tree.column('id', width=40)
        users_in_editor_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        def load_users_for_editor(c_id):
            for i in users_in_editor_tree.get_children():
                users_in_editor_tree.delete(i)
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, name, login, role, is_active FROM users WHERE client_id = %s ORDER BY name;", (c_id,))
                        for row in cur.fetchall():
                            users_in_editor_tree.insert('', 'end', values=row)
            except Exception as e:
                logging.error(f"Ошибка загрузки пользователей в редакторе: {e}")

        client_data = None
        if client_id: # Если редактирование, загружаем данные
            # Активируем кнопки
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="normal")
            
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT name, db_host, db_port, db_name, db_user, db_password, db_ssl_cert FROM clients WHERE id = %s", (client_id,))
                        client_data = cur.fetchone()
                if client_data:
                    for field in fields:
                        # Сопоставляем поля с данными из БД по индексу
                        db_field_map = {"Имя": 0, "DB Хост": 1, "DB Порт": 2, "DB Имя": 3, "DB Пользователь": 4, "DB Пароль": 5}
                        if field in db_field_map:
                            idx = db_field_map[field]
                            value = client_data[idx] if client_data[idx] is not None else ""
                            entries[field].insert(0, str(value))
                    # Заполняем поле сертификата
                    ssl_cert_value = client_data[6] if client_data[6] is not None else ""
                    ssl_cert_text.insert('1.0', ssl_cert_value)
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить данные клиента: {e}", parent=editor_window)
                editor_window.destroy()
        else: # Новый клиент
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="disabled")

        def save_client():
            """Сохраняет данные клиента в БД."""
            nonlocal client_id # Позволяем изменять внешнюю переменную client_id
            data_to_save = {
                'name': entries['Имя'].get(),
                'db_host': entries['DB Хост'].get(),
                'db_port': int(entries['DB Порт'].get() or 0),
                'db_name': entries['DB Имя'].get(),
                'db_user': entries['DB Пользователь'].get(),
                'db_password': entries['DB Пароль'].get(),
                'db_ssl_cert': ssl_cert_text.get('1.0', 'end-1c')
            }

            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        if client_id: # Обновление
                            query = sql.SQL("UPDATE clients SET name=%s, db_host=%s, db_port=%s, db_name=%s, db_user=%s, db_password=%s, db_ssl_cert=%s WHERE id=%s")
                            cur.execute(query, (*data_to_save.values(), client_id))
                        else: # Вставка нового клиента
                            query = sql.SQL("INSERT INTO clients (name, db_host, db_port, db_name, db_user, db_password, db_ssl_cert) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id")
                            cur.execute(query, tuple(data_to_save.values()))
                            new_client_id = cur.fetchone()[0]
                            client_id = new_client_id # Обновляем ID для текущего окна
                            
                            # Создаем пользователя по умолчанию
                            default_login = f"admin@{data_to_save['db_name']}"
                            default_pass = "12345"
                            hashed_pass = bcrypt.hashpw(default_pass.encode('utf-8'), bcrypt.gensalt())
                            
                            # Проверяем, не занят ли уже такой логин
                            cur.execute("SELECT 1 FROM users WHERE login = %s", (default_login,))
                            if cur.fetchone():
                                # Если логин занят, откатываем транзакцию и сообщаем об ошибке
                                raise psycopg2.IntegrityError(f"Пользователь с логином '{default_login}' уже существует. Имя базы данных клиента должно быть уникальным.")

                            cur.execute(
                                "INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, %s, %s)",
                                ("Администратор", default_login, hashed_pass.decode('utf-8'), 'администратор', new_client_id)
                            )
                    # Если все прошло без ошибок, коммитим транзакцию
                    conn.commit()
                
                # После успешного сохранения:
                load_clients() # Обновляем главный список
                btn_init_db.config(state="normal") # Активируем кнопку инициализации
                if not editor_window.title().startswith("Редактор"):
                    editor_window.title(f"Редактор клиента: {data_to_save['name']}")

                messagebox.showinfo("Успех", "Данные клиента успешно сохранены.", parent=editor_window)

            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"Ошибка сохранения клиента: {e}\n{error_details}")
                messagebox.showerror("Ошибка", f"Не удалось сохранить клиента: {e}", parent=editor_window)

        # Нижние кнопки
        bottom_buttons_frame = ttk.Frame(main_editor_frame)
        bottom_buttons_frame.pack(fill=tk.X, pady=(10, 0))
        
        btn_init_db = ttk.Button(bottom_buttons_frame, text="Инициализировать/Обновить БД клиента", command=run_client_db_setup, state="disabled" if not client_id else "normal")
        btn_init_db.pack(side=tk.LEFT, padx=5)

        ttk.Button(bottom_buttons_frame, text="Закрыть", command=editor_window.destroy).pack(side=tk.RIGHT)
        ttk.Button(bottom_buttons_frame, text="Сохранить", command=save_client).pack(side=tk.RIGHT, padx=5)

def open_supervisor_creator_window():
    """Открывает окно для создания супервизора."""
    sup_window = tk.Toplevel(root)
    sup_window.title("Создание нового супервизора")
    sup_window.grab_set()

    fields = ["Имя", "Логин", "Пароль"]
    entries = {}
    for i, field in enumerate(fields):
        ttk.Label(sup_window, text=field + ":").grid(row=i, column=0, padx=10, pady=5, sticky='w')
        entry = ttk.Entry(sup_window, width=40, show="*" if field == "Пароль" else "")
        entry.grid(row=i, column=1, padx=10, pady=5)
        entries[field] = entry

    def save_supervisor():
        user_data = {field: entries[field].get() for field in fields}
        if not all(user_data.values()):
            messagebox.showwarning("Внимание", "Все поля должны быть заполнены.", parent=sup_window)
            return
        
        try:
            hashed_pass = bcrypt.hashpw(user_data['Пароль'].encode('utf-8'), bcrypt.gensalt())
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, 'супервизор', NULL)",
                        (user_data['Имя'], user_data['Логин'], hashed_pass.decode('utf-8'))
                    )
                conn.commit()
            messagebox.showinfo("Успех", "Супервизор успешно создан.", parent=sup_window)
            sup_window.destroy()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать супервизора: {e}", parent=sup_window)

    # Добавляем недостающие кнопки
    buttons_frame = ttk.Frame(sup_window)
    buttons_frame.grid(row=len(fields), columnspan=2, pady=10)
    ttk.Button(buttons_frame, text="Сохранить", command=save_supervisor).pack(side=tk.RIGHT, padx=5)
    ttk.Button(buttons_frame, text="Отмена", command=sup_window.destroy).pack(side=tk.RIGHT)

def open_clients_management_window():
    """Открывает окно для управления клиентами и пользователями."""
    
    def load_clients():
        """Загружает список клиентов из БД в Treeview."""
        for i in clients_tree.get_children():
            clients_tree.delete(i)
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, db_host, created_at FROM clients ORDER BY name;")
                    for row in cur.fetchall():
                        clients_tree.insert('', 'end', values=row)
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка загрузки клиентов: {e}\n{error_details}")
            messagebox.showerror("Ошибка", "Не удалось загрузить список клиентов.", parent=clients_window)

    def on_client_select(event):
        """При выборе клиента загружает список его пользователей."""
        selected_item = clients_tree.focus()
        if not selected_item:
            return
        client_id = clients_tree.item(selected_item)['values'][0]
        load_users(client_id)

    def load_users(client_id):
        """Загружает пользователей для указанного клиента."""
        for i in users_tree.get_children():
            users_tree.delete(i)
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, login, role, is_active FROM users WHERE client_id = %s ORDER BY name;", (client_id,))
                    for row in cur.fetchall():
                        users_tree.insert('', 'end', values=row)
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка загрузки пользователей: {e}\n{error_details}")
            messagebox.showerror("Ошибка", "Не удалось загрузить список пользователей.", parent=clients_window)

    def open_client_editor(client_id=None):
        """Открывает окно для добавления или редактирования клиента."""
        editor_window = tk.Toplevel(clients_window)
        editor_window.title("Редактор клиента")
        editor_window.grab_set()

        main_editor_frame = ttk.Frame(editor_window, padding="10")
        main_editor_frame.pack(fill=tk.BOTH, expand=True)

        # Фрейм для полей ввода данных клиента
        client_data_frame = ttk.LabelFrame(main_editor_frame, text="Данные клиента")
        client_data_frame.pack(fill=tk.X, pady=5)

        entries = {}
        # Поля "Имя" и все поля для подключения к БД
        fields = ["Имя", "DB Хост", "DB Порт", "DB Имя", "DB Пользователь", "DB Пароль"]

        for i, field in enumerate(fields):
            ttk.Label(client_data_frame, text=field + ":").grid(row=i, column=0, padx=5, pady=2, sticky='w')
            entry = ttk.Entry(client_data_frame, width=40)
            entry.grid(row=i, column=1, padx=5, pady=2, sticky='ew')
            entries[field] = entry
        client_data_frame.columnconfigure(1, weight=1)

        # Поле для SSL сертификата
        cert_frame = ttk.LabelFrame(main_editor_frame, text="SSL-сертификат для подключения к БД клиента")
        cert_frame.pack(fill=tk.X, pady=5)
        ssl_cert_text = tk.Text(cert_frame, height=8, width=80)
        ssl_cert_text.pack(fill=tk.X, expand=True, padx=5, pady=5)

        def run_client_db_setup():
            """
            Запускает скрипт инициализации/обновления для базы данных клиента.
            """
            if not client_id:
                messagebox.showwarning("Внимание", "Сначала сохраните клиента.", parent=editor_window)
                return

            if not messagebox.askyesno("Подтверждение", "Вы уверены, что хотите инициализировать/обновить схему для базы данных этого клиента?\n\nСуществующие данные не будут удалены, но будут созданы недостающие таблицы.", parent=editor_window):
                return

            client_conn = None
            temp_cert_file = None
            try:
                # 1. Получаем данные для подключения к БД клиента
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT db_host, db_port, db_name, db_user, db_password, db_ssl_cert FROM clients WHERE id = %s", (client_id,))
                        db_data = cur.fetchone()
                
                if not db_data:
                    raise ValueError("Не удалось найти данные для подключения к БД клиента.")

                db_host, db_port, db_name, db_user, db_password, db_ssl_cert = db_data

                # 2. Создаем временный файл для сертификата, если он есть
                ssl_params = {}
                if db_ssl_cert:
                    import tempfile
                    # Создаем временный файл с суффиксом .crt
                    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                        fp.write(db_ssl_cert)
                        temp_cert_file = fp.name
                    ssl_params = {'sslmode': 'verify-full', 'sslrootcert': temp_cert_file}
                    logging.info(f"Используется временный SSL-сертификат: {temp_cert_file}")

                # 3. Подключаемся к БД клиента
                logging.info(f"Подключаюсь к базе клиента '{db_name}' на {db_host}...")
                client_conn = psycopg2.connect(host=db_host, port=db_port, dbname=db_name, user=db_user, password=db_password, **ssl_params)

                # 4. Запускаем функцию обновления схемы
                if update_client_db_schema(client_conn):
                    messagebox.showinfo("Успех", "Схема базы данных клиента успешно обновлена.", parent=editor_window)
                else:
                    messagebox.showerror("Ошибка", "Произошла ошибка при обновлении схемы. Подробности в app.log.", parent=editor_window)

            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось выполнить инициализацию: {e}", parent=editor_window)
            finally:
                if client_conn: client_conn.close()
                if temp_cert_file and os.path.exists(temp_cert_file): os.remove(temp_cert_file)

        # --- Блок управления пользователями ---
        users_management_frame = ttk.LabelFrame(main_editor_frame, text="Пользователи этого клиента")
        users_management_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Кнопки управления
        user_buttons_frame = ttk.Frame(users_management_frame)
        user_buttons_frame.pack(fill=tk.X, padx=5, pady=5)

        btn_add_user = ttk.Button(user_buttons_frame, text="Создать", command=add_user)
        btn_add_user.pack(side=tk.LEFT, padx=2)
        btn_edit_user = ttk.Button(user_buttons_frame, text="Редактировать", command=edit_user)
        btn_edit_user.pack(side=tk.LEFT, padx=2)
        btn_delete_user = ttk.Button(user_buttons_frame, text="Удалить", command=delete_user)
        btn_delete_user.pack(side=tk.LEFT, padx=2)
        btn_toggle_user = ttk.Button(user_buttons_frame, text="Вкл/Выкл", command=toggle_user_activity)
        btn_toggle_user.pack(side=tk.LEFT, padx=2)

        # Таблица пользователей
        user_tree_cols = ('id', 'name', 'login', 'role', 'is_active')
        users_in_editor_tree = ttk.Treeview(users_management_frame, columns=user_tree_cols, show='headings', height=5)
        users_in_editor_tree.heading('id', text='ID')
        users_in_editor_tree.heading('name', text='Имя')
        users_in_editor_tree.heading('login', text='Логин')
        users_in_editor_tree.heading('role', text='Роль')
        users_in_editor_tree.heading('is_active', text='Активен')
        users_in_editor_tree.column('id', width=40)
        users_in_editor_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        def load_users_for_editor(c_id):
            for i in users_in_editor_tree.get_children():
                users_in_editor_tree.delete(i)
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, name, login, role, is_active FROM users WHERE client_id = %s ORDER BY name;", (c_id,))
                        for row in cur.fetchall():
                            users_in_editor_tree.insert('', 'end', values=row)
            except Exception as e:
                logging.error(f"Ошибка загрузки пользователей в редакторе: {e}")

        client_data = None
        if client_id: # Если редактирование, загружаем данные
            # Активируем кнопки
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="normal")
            
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT name, db_host, db_port, db_name, db_user, db_password, db_ssl_cert FROM clients WHERE id = %s", (client_id,))
                        client_data = cur.fetchone()
                if client_data:
                    for field in fields:
                        # Сопоставляем поля с данными из БД по индексу
                        db_field_map = {"Имя": 0, "DB Хост": 1, "DB Порт": 2, "DB Имя": 3, "DB Пользователь": 4, "DB Пароль": 5}
                        if field in db_field_map:
                            idx = db_field_map[field]
                            value = client_data[idx] if client_data[idx] is not None else ""
                            entries[field].insert(0, str(value))
                    # Заполняем поле сертификата
                    ssl_cert_value = client_data[6] if client_data[6] is not None else ""
                    ssl_cert_text.insert('1.0', ssl_cert_value)
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить данные клиента: {e}", parent=editor_window)
                editor_window.destroy()
        else: # Новый клиент
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="disabled")

        def save_client():
            """Сохраняет данные клиента в БД."""
            nonlocal client_id # Позволяем изменять внешнюю переменную client_id
            data_to_save = {
                'name': entries['Имя'].get(),
                'db_host': entries['DB Хост'].get(),
                'db_port': int(entries['DB Порт'].get() or 0),
                'db_name': entries['DB Имя'].get(),
                'db_user': entries['DB Пользователь'].get(),
                'db_password': entries['DB Пароль'].get(),
                'db_ssl_cert': ssl_cert_text.get('1.0', 'end-1c')
            }

            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        if client_id: # Обновление
                            query = sql.SQL("UPDATE clients SET name=%s, db_host=%s, db_port=%s, db_name=%s, db_user=%s, db_password=%s, db_ssl_cert=%s WHERE id=%s")
                            cur.execute(query, (*data_to_save.values(), client_id))
                        else: # Вставка нового клиента
                            query = sql.SQL("INSERT INTO clients (name, db_host, db_port, db_name, db_user, db_password, db_ssl_cert) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id")
                            cur.execute(query, tuple(data_to_save.values()))
                            new_client_id = cur.fetchone()[0]
                            client_id = new_client_id # Обновляем ID для текущего окна
                            
                            # Создаем пользователя по умолчанию
                            default_login = f"admin@{data_to_save['db_name']}"
                            default_pass = "12345"
                            hashed_pass = bcrypt.hashpw(default_pass.encode('utf-8'), bcrypt.gensalt())
                            
                            # Проверяем, не занят ли уже такой логин
                            cur.execute("SELECT 1 FROM users WHERE login = %s", (default_login,))
                            if cur.fetchone():
                                # Если логин занят, откатываем транзакцию и сообщаем об ошибке
                                raise psycopg2.IntegrityError(f"Пользователь с логином '{default_login}' уже существует. Имя базы данных клиента должно быть уникальным.")

                            cur.execute(
                                "INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, %s, %s)",
                                ("Администратор", default_login, hashed_pass.decode('utf-8'), 'администратор', new_client_id)
                            )
                    # Если все прошло без ошибок, коммитим транзакцию
                    conn.commit()
                
                # После успешного сохранения:
                load_clients() # Обновляем главный список
                btn_init_db.config(state="normal") # Активируем кнопку инициализации
                if not editor_window.title().startswith("Редактор"):
                    editor_window.title(f"Редактор клиента: {data_to_save['name']}")

                messagebox.showinfo("Успех", "Данные клиента успешно сохранены.", parent=editor_window)

            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"Ошибка сохранения клиента: {e}\n{error_details}")
                messagebox.showerror("Ошибка", f"Не удалось сохранить клиента: {e}", parent=editor_window)

        # Нижние кнопки
        bottom_buttons_frame = ttk.Frame(main_editor_frame)
        bottom_buttons_frame.pack(fill=tk.X, pady=(10, 0))
        
        btn_init_db = ttk.Button(bottom_buttons_frame, text="Инициализировать/Обновить БД клиента", command=run_client_db_setup, state="disabled" if not client_id else "normal")
        btn_init_db.pack(side=tk.LEFT, padx=5)

        ttk.Button(bottom_buttons_frame, text="Закрыть", command=editor_window.destroy).pack(side=tk.RIGHT)
        ttk.Button(bottom_buttons_frame, text="Сохранить", command=save_client).pack(side=tk.RIGHT, padx=5)

    # --- Основное окно управления клиентами ---
    clients_window = tk.Toplevel(root)
    clients_window.title("Управление клиентами")
    clients_window.geometry("800x600")

    # Разделение окна на две части
    paned_window = ttk.PanedWindow(clients_window, orient=tk.VERTICAL)
    paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # --- Верхняя часть: Клиенты ---
    clients_frame = ttk.LabelFrame(paned_window, text="Клиенты")
    paned_window.add(clients_frame, weight=1)

    clients_tree_frame = ttk.Frame(clients_frame)
    clients_tree_frame.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=5, pady=5)

    clients_cols = ('id', 'name', 'ssh_host', 'created_at')
    clients_tree = ttk.Treeview(clients_tree_frame, columns=clients_cols, show='headings', displaycolumns=('id', 'name', 'ssh_host', 'created_at'))
    clients_tree.heading('id', text='ID')
    clients_tree.heading('name', text='Имя клиента')
    clients_tree.heading('ssh_host', text='Хост БД')
    clients_tree.heading('created_at', text='Дата создания')
    clients_tree.column('id', width=40)
    clients_tree.pack(fill=tk.BOTH, expand=True)
    clients_tree.bind('<<TreeviewSelect>>', on_client_select)

    def edit_selected_client():
        selected_item = clients_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите клиента для редактирования.", parent=clients_window)
            return
        client_id = clients_tree.item(selected_item)['values'][0]
        open_client_editor(client_id)

    def add_new_user():
        """Открывает окно для добавления нового пользователя к выбранному клиенту."""
        selected_item = clients_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Сначала выберите клиента, которому хотите добавить пользователя.", parent=clients_window)
            return
        
        client_id = clients_tree.item(selected_item)['values'][0]
        client_name = clients_tree.item(selected_item)['values'][1]

        user_editor_window = tk.Toplevel(clients_window)
        user_editor_window.title(f"Новый пользователь для '{client_name}'")
        user_editor_window.grab_set()

        fields = ["Имя", "Логин", "Пароль", "Роль"]
        entries = {}

        for i, field in enumerate(fields):
            ttk.Label(user_editor_window, text=field + ":").grid(row=i, column=0, padx=5, pady=5, sticky='w')
            if field == "Роль":
                widget = ttk.Combobox(user_editor_window, values=['пользователь', 'администратор', 'супервизор'], state="readonly")
                widget.set('пользователь') # Значение по умолчанию
            else:
                widget = ttk.Entry(user_editor_window, width=40)
            widget.grid(row=i, column=1, padx=5, pady=5)
            entries[field] = widget

        def save_user():
            user_data = {
                'name': entries['Имя'].get(),
                'login': entries['Логин'].get(),
                'password': entries['Пароль'].get(),
                'role': entries['Роль'].get()
            }

            if not all(user_data.values()):
                messagebox.showwarning("Внимание", "Все поля должны быть заполнены.", parent=user_editor_window)
                return

            try:
                hashed_pass = bcrypt.hashpw(user_data['password'].encode('utf-8'), bcrypt.gensalt())
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, %s, %s)",
                            (user_data['name'], user_data['login'], hashed_pass.decode('utf-8'), user_data['role'], client_id)
                        )
                    conn.commit()
                load_users(client_id) # Обновляем список
                user_editor_window.destroy()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить пользователя: {e}", parent=user_editor_window)

        ttk.Button(user_editor_window, text="Сохранить", command=save_user).grid(row=len(fields), column=1, sticky='e', padx=5, pady=10)
        ttk.Button(user_editor_window, text="Отмена", command=user_editor_window.destroy).grid(row=len(fields), column=0, sticky='w', padx=5, pady=10)

    clients_buttons_frame = ttk.Frame(clients_frame)
    clients_buttons_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
    ttk.Button(clients_buttons_frame, text="Добавить", command=lambda: open_client_editor()).pack(pady=2, fill=tk.X)
    ttk.Button(clients_buttons_frame, text="Редактировать", command=edit_selected_client).pack(pady=2, fill=tk.X)
    ttk.Button(clients_buttons_frame, text="Обновить", command=load_clients).pack(pady=2, fill=tk.X)

    # --- Нижняя часть: Пользователи ---
    users_frame = ttk.LabelFrame(paned_window, text="Пользователи выбранного клиента")
    paned_window.add(users_frame, weight=1)

    users_tree_frame = ttk.Frame(users_frame)
    users_tree_frame.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=5, pady=5)

    users_cols = ('id', 'name', 'login', 'role', 'is_active')
    users_tree = ttk.Treeview(users_tree_frame, columns=users_cols, show='headings')
    users_tree.heading('id', text='ID')
    users_tree.heading('name', text='Имя')
    users_tree.heading('login', text='Логин')
    users_tree.heading('role', text='Роль')
    users_tree.heading('is_active', text='Активен')
    users_tree.column('id', width=40)
    users_tree.pack(fill=tk.BOTH, expand=True)

    users_buttons_frame = ttk.Frame(users_frame)
    users_buttons_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
    ttk.Button(users_buttons_frame, text="Добавить пользователя", command=add_new_user).pack(pady=2, fill=tk.X)
    ttk.Button(users_buttons_frame, text="Сменить пароль").pack(pady=2, fill=tk.X) # TODO
    ttk.Button(users_buttons_frame, text="Изменить роль").pack(pady=2, fill=tk.X) # TODO
    ttk.Button(users_buttons_frame, text="Вкл/Выкл").pack(pady=2, fill=tk.X) # TODO

    # Первоначальная загрузка
    load_clients()

def main():
    """Главная функция для создания и запуска GUI приложения."""
    global root # Делаем root глобальной, чтобы функции могли к ней обращаться
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

    # -- Меню "Администрирование" --
    admin_menu = tk.Menu(menubar, tearoff=0)
    admin_menu.add_command(label="Клиенты", command=open_clients_management_window)
    admin_menu.add_command(label="Создать супервизора", command=open_supervisor_creator_window)
    menubar.add_cascade(label="Администрирование", menu=admin_menu)

    # -- Меню "Справка" --
    help_menu = tk.Menu(menubar, tearoff=0)
    help_menu.add_command(label="О программе") # Пока без функции
    menubar.add_cascade(label="Справка", menu=help_menu)

    # 6. Запускаем главный цикл приложения.
    root.mainloop()

if __name__ == "__main__":
    main()
