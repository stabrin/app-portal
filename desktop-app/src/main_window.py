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

        # Используем централизованную функцию для получения соединения.
        # Она уже содержит всю логику SSL.
        with get_main_db_connection() as conn:
            ssl_info = conn.get_dsn_parameters().get('sslmode')
            logging.info(f"SSL-соединение с PostgreSQL (версия {conn.server_version}) успешно установлено. Режим: {ssl_info}")
        
        messagebox.showinfo("Проверка подключения", "SSL-подключение к базе данных успешно установлено!")

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
                    cur.execute("SELECT id, name, ssh_host, created_at FROM clients ORDER BY name;")
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

        # --- Новая компоновка окна ---
        # Основной фрейм с отступами
        main_editor_frame = ttk.Frame(editor_window, padding="10")
        main_editor_frame.pack(fill=tk.BOTH, expand=True)

        # Верхний фрейм для колонок
        top_frame = ttk.Frame(main_editor_frame)
        top_frame.pack(fill=tk.X, pady=5)

        # Левая колонка (SSH)
        ssh_frame = ttk.LabelFrame(top_frame, text="Параметры подключения SSH")
        ssh_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        # Правая колонка (БД)
        db_frame = ttk.LabelFrame(top_frame, text="Параметры подключения к базе")
        db_frame.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0))

        entries = {}
        ssh_fields = ["Имя", "SSH Хост", "SSH Порт", "SSH Пользователь"]
        db_fields = ["DB Хост", "DB Порт", "DB Имя", "DB Пользователь", "DB Пароль"]

        for i, field in enumerate(ssh_fields):
            ttk.Label(ssh_frame, text=field + ":").grid(row=i, column=0, padx=5, pady=2, sticky='w')
            entry = ttk.Entry(ssh_frame, width=40)
            entry.grid(row=i, column=1, padx=5, pady=2, sticky='ew')
            entries[field] = entry

        for i, field in enumerate(db_fields):
            ttk.Label(db_frame, text=field + ":").grid(row=i, column=0, padx=5, pady=2, sticky='w')
            entry = ttk.Entry(db_frame, width=40)
            entry.grid(row=i, column=1, padx=5, pady=2, sticky='ew')
            entries[field] = entry

        # Поле для SSH ключа
        key_frame = ttk.LabelFrame(main_editor_frame, text="Приватный SSH ключ")
        key_frame.pack(fill=tk.X, pady=5)
        ssh_key_text = tk.Text(key_frame, height=8, width=80)
        ssh_key_text.pack(fill=tk.X, expand=True, padx=5, pady=5)
        entries["SSH Ключ"] = ssh_key_text

        # --- Блок управления пользователями ---
        users_management_frame = ttk.LabelFrame(main_editor_frame, text="Пользователи этого клиента")
        users_management_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Кнопки управления
        user_buttons_frame = ttk.Frame(users_management_frame)
        user_buttons_frame.pack(fill=tk.X, padx=5, pady=5)

        btn_add_user = ttk.Button(user_buttons_frame, text="Создать")
        btn_add_user.pack(side=tk.LEFT, padx=2)
        btn_edit_user = ttk.Button(user_buttons_frame, text="Редактировать")
        btn_edit_user.pack(side=tk.LEFT, padx=2)
        btn_delete_user = ttk.Button(user_buttons_frame, text="Удалить")
        btn_delete_user.pack(side=tk.LEFT, padx=2)
        btn_toggle_user = ttk.Button(user_buttons_frame, text="Вкл/Выкл")
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
                        cur.execute("SELECT name, ssh_host, ssh_port, ssh_user, db_host, db_port, db_name, db_user, db_password, ssh_private_key FROM clients WHERE id = %s", (client_id,))
                        client_data = cur.fetchone()
                if client_data:
                    # Объединяем все поля в один список для итерации
                    all_fields = ssh_fields + db_fields + ["SSH Ключ"]
                    for field in all_fields:
                        # Сопоставляем поля с данными из БД
                        db_field_map = {"Имя": 0, "SSH Хост": 1, "SSH Порт": 2, "SSH Пользователь": 3, "DB Хост": 4, "DB Порт": 5, "DB Имя": 6, "DB Пользователь": 7, "DB Пароль": 8, "SSH Ключ": 9}
                        if field in db_field_map:
                            value = client_data[db_field_map[field]] if client_data[db_field_map[field]] is not None else ""
                            if field == "SSH Ключ":
                                entries[field].insert('1.0', value)
                            else:
                                entries[field].insert(0, str(value))
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить данные клиента: {e}", parent=editor_window)
                editor_window.destroy()
        else: # Новый клиент
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="disabled")

        def save_client():
            """Сохраняет данные клиента в БД."""
            data_to_save = {
                'name': entries['Имя'].get(),
                'ssh_host': entries['SSH Хост'].get(),
                'ssh_port': int(entries['SSH Порт'].get() or 0),
                'ssh_user': entries['SSH Пользователь'].get(),
                'db_host': entries['DB Хост'].get(),
                'db_port': int(entries['DB Порт'].get() or 0),
                'db_name': entries['DB Имя'].get(),
                'db_user': entries['DB Пользователь'].get(),
                'db_password': entries['DB Пароль'].get(),
                'ssh_private_key': entries['SSH Ключ'].get('1.0', 'end-1c')
            }

            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        if client_id: # Обновление
                            query = sql.SQL("UPDATE clients SET name=%s, ssh_host=%s, ssh_port=%s, ssh_user=%s, db_host=%s, db_port=%s, db_name=%s, db_user=%s, db_password=%s, ssh_private_key=%s WHERE id=%s")
                            cur.execute(query, (*data_to_save.values(), client_id))
                        else: # Вставка нового клиента
                            query = sql.SQL("INSERT INTO clients (name, ssh_host, ssh_port, ssh_user, db_host, db_port, db_name, db_user, db_password, ssh_private_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id")
                            cur.execute(query, tuple(data_to_save.values()))
                            new_client_id = cur.fetchone()[0]
                            
                            # Создаем пользователя по умолчанию
                            default_login = f"admin@{data_to_save['name']}"
                            default_pass = "12345"
                            hashed_pass = bcrypt.hashpw(default_pass.encode('utf-8'), bcrypt.gensalt())
                            
                            cur.execute(
                                "INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, %s, %s)",
                                ("Администратор", default_login, hashed_pass.decode('utf-8'), 'администратор', new_client_id)
                            )
                    conn.commit()
                load_clients()
                editor_window.destroy()
            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"Ошибка сохранения клиента: {e}\n{error_details}")
                messagebox.showerror("Ошибка", f"Не удалось сохранить клиента: {e}", parent=editor_window)

        # Нижние кнопки Сохранить/Отмена
        bottom_buttons_frame = ttk.Frame(main_editor_frame)
        bottom_buttons_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(bottom_buttons_frame, text="Сохранить", command=save_client).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_buttons_frame, text="Отмена", command=editor_window.destroy).pack(side=tk.RIGHT)

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
                    cur.execute("SELECT id, name, ssh_host, created_at FROM clients ORDER BY name;")
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

        # --- Новая компоновка окна ---
        # Основной фрейм с отступами
        main_editor_frame = ttk.Frame(editor_window, padding="10")
        main_editor_frame.pack(fill=tk.BOTH, expand=True)

        # Верхний фрейм для колонок
        top_frame = ttk.Frame(main_editor_frame)
        top_frame.pack(fill=tk.X, pady=5)

        # Левая колонка (SSH)
        ssh_frame = ttk.LabelFrame(top_frame, text="Параметры подключения SSH")
        ssh_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        # Правая колонка (БД)
        db_frame = ttk.LabelFrame(top_frame, text="Параметры подключения к базе")
        db_frame.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0))

        entries = {}
        ssh_fields = ["Имя", "SSH Хост", "SSH Порт", "SSH Пользователь"]
        db_fields = ["DB Хост", "DB Порт", "DB Имя", "DB Пользователь", "DB Пароль"]

        for i, field in enumerate(ssh_fields):
            ttk.Label(ssh_frame, text=field + ":").grid(row=i, column=0, padx=5, pady=2, sticky='w')
            entry = ttk.Entry(ssh_frame, width=40)
            entry.grid(row=i, column=1, padx=5, pady=2, sticky='ew')
            entries[field] = entry

        for i, field in enumerate(db_fields):
            ttk.Label(db_frame, text=field + ":").grid(row=i, column=0, padx=5, pady=2, sticky='w')
            entry = ttk.Entry(db_frame, width=40)
            entry.grid(row=i, column=1, padx=5, pady=2, sticky='ew')
            entries[field] = entry

        # Поле для SSH ключа
        key_frame = ttk.LabelFrame(main_editor_frame, text="Приватный SSH ключ")
        key_frame.pack(fill=tk.X, pady=5)
        ssh_key_text = tk.Text(key_frame, height=8, width=80)
        ssh_key_text.pack(fill=tk.X, expand=True, padx=5, pady=5)
        entries["SSH Ключ"] = ssh_key_text

        # --- Блок управления пользователями ---
        users_management_frame = ttk.LabelFrame(main_editor_frame, text="Пользователи этого клиента")
        users_management_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Кнопки управления
        user_buttons_frame = ttk.Frame(users_management_frame)
        user_buttons_frame.pack(fill=tk.X, padx=5, pady=5)

        btn_add_user = ttk.Button(user_buttons_frame, text="Создать")
        btn_add_user.pack(side=tk.LEFT, padx=2)
        btn_edit_user = ttk.Button(user_buttons_frame, text="Редактировать")
        btn_edit_user.pack(side=tk.LEFT, padx=2)
        btn_delete_user = ttk.Button(user_buttons_frame, text="Удалить")
        btn_delete_user.pack(side=tk.LEFT, padx=2)
        btn_toggle_user = ttk.Button(user_buttons_frame, text="Вкл/Выкл")
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
                        cur.execute("SELECT name, ssh_host, ssh_port, ssh_user, db_host, db_port, db_name, db_user, db_password, ssh_private_key FROM clients WHERE id = %s", (client_id,))
                        client_data = cur.fetchone()
                if client_data:
                    fields = list(entries.keys())
                    for i, field in enumerate(fields):
                        # Сопоставляем поля с данными из БД
                        db_field_map = {"Имя": 0, "SSH Хост": 1, "SSH Порт": 2, "SSH Пользователь": 3, "DB Хост": 4, "DB Порт": 5, "DB Имя": 6, "DB Пользователь": 7, "DB Пароль": 8, "SSH Ключ": 9}
                        if field in db_field_map:
                            value = client_data[db_field_map[field]] if client_data[db_field_map[field]] is not None else ""
                            if field == "SSH Ключ":
                                entries[field].insert('1.0', value)
                            else:
                                entries[field].insert(0, str(value))
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить данные клиента: {e}", parent=editor_window)
                editor_window.destroy()
        else: # Новый клиент
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="disabled")

        def save_client():
            """Сохраняет данные клиента в БД."""
            data_to_save = {
                'name': entries['Имя'].get(),
                'ssh_host': entries['SSH Хост'].get(),
                'ssh_port': int(entries['SSH Порт'].get() or 0),
                'ssh_user': entries['SSH Пользователь'].get(),
                'db_host': entries['DB Хост'].get(),
                'db_port': int(entries['DB Порт'].get() or 0),
                'db_name': entries['DB Имя'].get(),
                'db_user': entries['DB Пользователь'].get(),
                'db_password': entries['DB Пароль'].get(),
                'ssh_private_key': entries['SSH Ключ'].get('1.0', 'end-1c')
            }

            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        if client_id: # Обновление
                            query = sql.SQL("UPDATE clients SET name=%s, ssh_host=%s, ssh_port=%s, ssh_user=%s, db_host=%s, db_port=%s, db_name=%s, db_user=%s, db_password=%s, ssh_private_key=%s WHERE id=%s")
                            cur.execute(query, (*data_to_save.values(), client_id))
                        else: # Вставка нового клиента
                            query = sql.SQL("INSERT INTO clients (name, ssh_host, ssh_port, ssh_user, db_host, db_port, db_name, db_user, db_password, ssh_private_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id")
                            cur.execute(query, tuple(data_to_save.values()))
                            new_client_id = cur.fetchone()[0]
                            
                            # Создаем пользователя по умолчанию
                            default_login = f"admin@{data_to_save['name']}"
                            default_pass = "12345"
                            hashed_pass = bcrypt.hashpw(default_pass.encode('utf-8'), bcrypt.gensalt())
                            
                            cur.execute(
                                "INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, %s, %s)",
                                ("Администратор", default_login, hashed_pass.decode('utf-8'), 'администратор', new_client_id)
                            )
                    conn.commit()
                load_clients()
                editor_window.destroy()
            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"Ошибка сохранения клиента: {e}\n{error_details}")
                messagebox.showerror("Ошибка", f"Не удалось сохранить клиента: {e}", parent=editor_window)

        # Нижние кнопки Сохранить/Отмена
        bottom_buttons_frame = ttk.Frame(main_editor_frame)
        bottom_buttons_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(bottom_buttons_frame, text="Сохранить", command=save_client).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_buttons_frame, text="Отмена", command=editor_window.destroy).pack(side=tk.RIGHT)

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
    clients_tree = ttk.Treeview(clients_tree_frame, columns=clients_cols, show='headings')
    clients_tree.heading('id', text='ID')
    clients_tree.heading('name', text='Имя клиента')
    clients_tree.heading('ssh_host', text='SSH Хост')
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
