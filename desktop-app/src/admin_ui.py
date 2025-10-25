# src/admin_ui.py

import tkinter as tk
from tkinter import ttk, messagebox
import logging
import json

# --- ИСПРАВЛЕНИЕ: Добавляем глобальный импорт Pillow ---
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None # Помечаем как недоступный, если Pillow не установлен

# Импорты для работы с БД и QR-кодами
from db_connector import get_main_db_connection
import bcrypt
import psycopg2
import psycopg2.extras

# Импортируем новый сервис печати
from printing_service import PrintingService, LabelEditorWindow

import traceback

def open_label_editor_window(parent_widget, user_info):
    """
    Открывает окно редактора макетов этикеток.
    """
    # Создаем экземпляр нашего нового класса редактора
    LabelEditorWindow(parent_widget, user_info)

def open_print_management_window(parent_widget):
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
    print_window = tk.Toplevel(parent_widget)
    print_window.title("Управление печатью")
    print_window.geometry("500x400")
    print_window.transient(parent_widget) # Окно будет поверх главного
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
            h_server = win32print.OpenPrinter(None)
            try:
                forms = win32print.EnumForms(h_server)
                for form in forms:
                    if form['Name'].startswith('Tilda_'):
                        name = form['Name']
                        width_mm = form['Size']['cx'] / 1000.0
                        height_mm = form['Size']['cy'] / 1000.0
                        paper_sizes_data[name] = (width_mm, height_mm)
                        paper_listbox.insert(tk.END, f"{name} ({width_mm} x {height_mm} мм)")
            finally:
                win32print.ClosePrinter(h_server)
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
        
        selected_indices = paper_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Внимание", "Пожалуйста, выберите размер бумаги.", parent=print_window)
            return
        
        full_listbox_string = paper_listbox.get(selected_indices[0])
        separator_pos = full_listbox_string.find(' (')
        selected_paper_name = full_listbox_string[:separator_pos].strip() if separator_pos != -1 else full_listbox_string.strip()

        try:
            h_printer = win32print.OpenPrinter(printer_name, None)
            dc = win32ui.CreateDC()
            dc.CreatePrinterDC(printer_name)
            try:
                dc.StartDoc("Тестовая страница из 'ТильдаКод'")
                dc.StartPage()
                font = win32ui.CreateFont({'name': 'Arial', 'height': 20, 'weight': 400, 'charset': 204})
                dc.SelectObject(font)
                dpi_x = dc.GetDeviceCaps(88)
                dpi_y = dc.GetDeviceCaps(90)
                dots_per_mm_x = dpi_x / 25.4
                dots_per_mm_y = dpi_y / 25.4
                paper_width_mm, paper_height_mm = paper_sizes_data[selected_paper_name]
                paper_width_dots = int(paper_width_mm * dots_per_mm_x)
                dc.SetTextAlign(win32con.TA_CENTER | win32con.TA_TOP)
                dc.TextOut(paper_width_dots // 2, 10, "Тестовая печать")
                dc.TextOut(paper_width_dots // 2, 40, "из 'ТильдаКод'")
                dc.EndPage()
                dc.EndDoc()
                messagebox.showinfo("Успех", f"Тестовая страница отправлена на принтер '{printer_name}'.", parent=print_window)
            finally:
                dc.DeleteDC()
                win32print.ClosePrinter(h_printer)
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
    # TODO: Переделать тестовую печать на использование PDF
    load_printers()

def open_workplace_setup_window(parent_widget, user_info):
    """Открывает окно для настройки рабочих мест."""
    setup_window = tk.Toplevel(parent_widget)
    setup_window.title("Настройка рабочих мест")
    setup_window.geometry("600x500")
    setup_window.grab_set()

    notebook = ttk.Notebook(setup_window)
    notebook.pack(expand=True, fill="both", padx=10, pady=10)

    # --- Вкладка 1: Генерация настроечного кода ---
    config_frame = ttk.Frame(notebook, padding="10")
    notebook.add(config_frame, text="Настроечный код")

    ttk.Label(config_frame, text="Локальный адрес сервера:", font=("Arial", 10, "bold")).pack(anchor="w")
    ttk.Label(config_frame, text="(например, http://192.168.1.100:8080)", wraplength=400).pack(anchor="w", pady=(0, 5))
    server_address_entry = ttk.Entry(config_frame, width=60)
    server_address_entry.pack(fill="x")

    def generate_server_config_qr():
        """
        Новая логика: вместо отображения QR на экране, открывает диалог печати.
        """
        address = server_address_entry.get()
        if not address:
            messagebox.showwarning("Внимание", "Введите адрес сервера.", parent=setup_window)
            return

        try:
            # Эти импорты уже есть в display_qr_sequence, но для ясности оставим
            import zlib
            import base64
        except ImportError:
            messagebox.showerror("Ошибка", "Необходимые библиотеки не установлены.\nУстановите их: pip install qrcode pillow", parent=setup_window)
            messagebox.showerror("Ошибка", "Необходимые библиотеки для сжатия не установлены.", parent=setup_window)
            return

        # --- НОВАЯ ЛОГИКА: Получаем SSL-сертификат из БД ---
        # Это необходимо, чтобы включить его в QR-код настройки
        try:
            with get_main_db_connection() as conn:
                # Этот запрос не требует курсора, т.к. мы просто читаем переменную
                ssl_cert_content = conn.info.ssl_root_cert
        except Exception as e:
            ssl_cert_content = f"ERROR: Could not read cert file: {e}"

        config_data = {
            "type": "server_config", # Тип для распознавания сканером
            "address": address
        }

        # Сжатие данных для уменьшения размера QR-кода
        json_bytes = json.dumps(config_data).encode('utf-8')
        compressed_bytes = zlib.compress(json_bytes, level=9) # Максимальное сжатие
        full_base64_data = base64.b64encode(compressed_bytes).decode('ascii')
        # Формируем данные для подстановки в макет.
        # Это имитирует структуру данных, как при печати этикеток рабочих мест.
        item_data_for_printing = {
            "QR: Конфигурация сервера": json.dumps(config_data, ensure_ascii=False)
        }

        chunk_size = 2500
        chunks = [full_base64_data[i:i + chunk_size] for i in range(0, len(full_base64_data), chunk_size)]
        # Открываем новый диалог печати, передавая ему данные для единственной этикетки.
        PrintWorkplaceLabelsDialog(setup_window, user_info, f"Настройка сервера: {address}", [item_data_for_printing])

    ttk.Button(config_frame, text="Сгенерировать QR-код", command=generate_server_config_qr).pack(pady=20)

    # --- Вкладка 2: Рабочие места ---
    workplaces_frame = ttk.Frame(notebook, padding="10")
    notebook.add(workplaces_frame, text="Рабочие места")

    # --- НОВАЯ ЛОГИКА ДЛЯ ВКЛАДКИ "РАБОЧИЕ МЕСТА" ---

    def get_client_db_connection():
        """Вспомогательная функция для подключения к БД клиента."""
        client_db_config = user_info.get("client_db_config")
        if not client_db_config:
            messagebox.showerror("Ошибка", "Не найдены данные для подключения к базе клиента.", parent=setup_window)
            return None
        
        conn_params = {
            'host': client_db_config.get('db_host'), 'port': client_db_config.get('db_port'),
            'dbname': client_db_config.get('db_name'), 'user': client_db_config.get('db_user'),
            'password': client_db_config.get('db_password')
        }
        # SSL пока не используется для этой операции, можно добавить при необходимости
        return psycopg2.connect(**conn_params)

    def load_warehouses():
        """Загружает и отображает склады и количество рабочих мест в них."""
        for i in warehouses_tree.get_children():
            warehouses_tree.delete(i)
        
        try:
            with get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT warehouse_name, COUNT(*) as workplace_count
                        FROM ap_workplaces
                        GROUP BY warehouse_name
                        ORDER BY warehouse_name;
                    """)
                    for row in cur.fetchall():
                        warehouses_tree.insert('', 'end', values=row)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить список складов: {e}", parent=setup_window)

    def create_new_warehouse():
        """Открывает диалог для создания нового склада."""
        name = tk.simpledialog.askstring("Новый склад", "Введите название нового склада:", parent=setup_window)
        if not name: return

        count = tk.simpledialog.askinteger("Количество мест", "Введите количество рабочих мест:", parent=setup_window, minvalue=1)
        if not count: return

        try:
            with get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    # Проверка на существование
                    cur.execute("SELECT 1 FROM ap_workplaces WHERE warehouse_name = %s LIMIT 1", (name,))
                    if cur.fetchone():
                        messagebox.showerror("Ошибка", f"Склад с названием '{name}' уже существует.", parent=setup_window)
                        return

                    for i in range(1, count + 1):
                        cur.execute(
                            "INSERT INTO ap_workplaces (warehouse_name, workplace_number) VALUES (%s, %s)",
                            (name, i)
                        )
                conn.commit()
            messagebox.showinfo("Успех", f"Склад '{name}' с {count} рабочими местами успешно создан.", parent=setup_window)
            load_warehouses()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать склад: {e}", parent=setup_window)

    def change_workplace_count():
        """Изменяет количество рабочих мест для выбранного склада."""
        selected_item = warehouses_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите склад из списка.", parent=setup_window)
            return

        warehouse_name, current_count = warehouses_tree.item(selected_item)['values']
        
        new_count = tk.simpledialog.askinteger(
            "Изменить количество",
            f"Введите новое общее количество мест для склада '{warehouse_name}':",
            parent=setup_window,
            initialvalue=current_count,
            minvalue=0
        )

        if new_count is None or new_count == current_count:
            return

        try:
            with get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    if new_count > current_count:
                        # Добавляем новые места
                        to_add = new_count - current_count
                        cur.execute("SELECT COALESCE(MAX(workplace_number), 0) FROM ap_workplaces WHERE warehouse_name = %s", (warehouse_name,))
                        max_num = cur.fetchone()[0]
                        for i in range(1, to_add + 1):
                            cur.execute(
                                "INSERT INTO ap_workplaces (warehouse_name, workplace_number) VALUES (%s, %s)",
                                (warehouse_name, max_num + i)
                            )
                        msg = f"Добавлено {to_add} новых рабочих мест."
                    else: # new_count < current_count
                        # Удаляем лишние места
                        to_delete = current_count - new_count
                        if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите удалить {to_delete} рабочих мест со склада '{warehouse_name}'?\nБудут удалены места с наибольшими номерами.", parent=setup_window):
                            return
                        
                        # Удаляем записи, начиная с самых больших номеров
                        cur.execute("""
                            DELETE FROM ap_workplaces
                            WHERE id IN (
                                SELECT id FROM ap_workplaces
                                WHERE warehouse_name = %s
                                ORDER BY workplace_number DESC
                                LIMIT %s
                            )
                        """, (warehouse_name, to_delete))
                        msg = f"Удалено {to_delete} рабочих мест."
                conn.commit()
            messagebox.showinfo("Успех", msg, parent=setup_window)
            load_warehouses()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось изменить количество мест: {e}", parent=setup_window)

    def open_workplace_printing_dialog():
        """Открывает диалог для печати этикеток рабочих мест."""
        selected_item = warehouses_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите склад для печати этикеток.", parent=setup_window)
            return
        
        warehouse_name = warehouses_tree.item(selected_item)['values'][0]
        
        # Запускаем новый класс диалога
        PrintWorkplaceLabelsDialog(setup_window, user_info, warehouse_name)

    # --- Виджеты для новой вкладки ---
    
    # Панель с кнопками управления
    controls_frame = ttk.Frame(workplaces_frame)
    controls_frame.pack(fill=tk.X, pady=5)
    ttk.Button(controls_frame, text="Создать склад", command=create_new_warehouse).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="Изменить кол-во", command=change_workplace_count).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="Печать этикеток", command=open_workplace_printing_dialog).pack(side=tk.LEFT, padx=2)

    # Таблица со складами
    tree_frame = ttk.Frame(workplaces_frame)
    tree_frame.pack(expand=True, fill="both", pady=5)

    warehouses_tree = ttk.Treeview(tree_frame, columns=('name', 'count'), show='headings')
    warehouses_tree.heading('name', text='Название склада')
    warehouses_tree.heading('count', text='Кол-во рабочих мест')
    warehouses_tree.column('name', width=300)
    warehouses_tree.column('count', width=150, anchor=tk.CENTER)
    warehouses_tree.pack(side=tk.LEFT, expand=True, fill="both")

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=warehouses_tree.yview)
    warehouses_tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")

    # Загружаем данные при открытии
    load_warehouses()

def display_qr_sequence(title, chunks, parent):
    """Вспомогательная функция для отображения серии QR-кодов."""
    try:
        import qrcode
        from PIL import Image, ImageTk
    except ImportError: return

    qr_window = tk.Toplevel(parent)
    qr_window.title(title)
    qr_window.grab_set()

    current_chunk_index = 0
    
    info_label = ttk.Label(qr_window, text="", font=("Arial", 12))
    info_label.pack(pady=10)
    qr_label = ttk.Label(qr_window)
    qr_label.pack(padx=20, pady=10)
    nav_frame = ttk.Frame(qr_window)
    nav_frame.pack(pady=10)
    prev_button = ttk.Button(nav_frame, text="<< Назад")
    prev_button.pack(side=tk.LEFT, padx=10)
    next_button = ttk.Button(nav_frame, text="Далее >>")
    next_button.pack(side=tk.LEFT, padx=10)

    def show_chunk(index):
        nonlocal current_chunk_index
        current_chunk_index = index
        chunk_data = f"{index+1}/{len(chunks)}:{chunks[index]}"
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(chunk_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").resize((350, 350))
        photo = ImageTk.PhotoImage(img)
        qr_label.config(image=photo)
        qr_label.image = photo
        info_label.config(text=f"Шаг {index + 1} из {len(chunks)}. Отсканируйте код.")
        prev_button.config(state="normal" if index > 0 else "disabled")
        next_button.config(state="normal" if index < len(chunks) - 1 else "disabled")

    def show_next():
        if current_chunk_index < len(chunks) - 1: show_chunk(current_chunk_index + 1)
    def show_prev():
        if current_chunk_index > 0: show_chunk(current_chunk_index - 1)

    prev_button.config(command=show_prev)
    next_button.config(command=show_next)
    show_chunk(0)

def display_qr_sequence(title, chunks, parent):
    """Вспомогательная функция для отображения серии QR-кодов."""
    try:
        import qrcode
        from PIL import Image, ImageTk
    except ImportError: return

    qr_window = tk.Toplevel(parent)
    qr_window.title(title)
    qr_window.grab_set()

    current_chunk_index = 0
    
    info_label = ttk.Label(qr_window, text="", font=("Arial", 12))
    info_label.pack(pady=10)
    qr_label = ttk.Label(qr_window)
    qr_label.pack(padx=20, pady=10)
    nav_frame = ttk.Frame(qr_window)
    nav_frame.pack(pady=10)
    prev_button = ttk.Button(nav_frame, text="<< Назад")
    prev_button.pack(side=tk.LEFT, padx=10)
    next_button = ttk.Button(nav_frame, text="Далее >>")
    next_button.pack(side=tk.LEFT, padx=10)

    def show_chunk(index):
        nonlocal current_chunk_index
        current_chunk_index = index
        chunk_data = f"{index+1}/{len(chunks)}:{chunks[index]}"
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(chunk_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").resize((350, 350))
        photo = ImageTk.PhotoImage(img)
        qr_label.config(image=photo)
        qr_label.image = photo
        info_label.config(text=f"Шаг {index + 1} из {len(chunks)}. Отсканируйте код.")
        prev_button.config(state="normal" if index > 0 else "disabled")
        next_button.config(state="normal" if index < len(chunks) - 1 else "disabled")

    def show_next():
        if current_chunk_index < len(chunks) - 1: show_chunk(current_chunk_index + 1)
    def show_prev():
        if current_chunk_index > 0: show_chunk(current_chunk_index - 1)

    prev_button.config(command=show_prev)
    next_button.config(command=show_next)
    show_chunk(0)

class PreviewLabelsDialog(tk.Toplevel):
    """Новый класс для окна предпросмотра сгенерированных этикеток."""
    def __init__(self, parent, images, on_print_callback):
        super().__init__(parent)
        self.title("Предпросмотр этикеток")
        self.geometry("600x500")
        self.transient(parent)
        self.grab_set()

        self.images = images
        self.on_print_callback = on_print_callback
        self.current_index = 0

        self.info_label = ttk.Label(self, text="", font=("Arial", 12))
        self.info_label.pack(pady=10)

        self.image_label = ttk.Label(self)
        self.image_label.pack(padx=10, pady=10, expand=True, fill="both")

        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=10)

        self.prev_button = ttk.Button(nav_frame, text="<< Назад", command=self._show_prev)
        self.prev_button.pack(side=tk.LEFT, padx=10)

        self.print_button = ttk.Button(nav_frame, text="Напечатать все", command=self._confirm_print)
        self.print_button.pack(side=tk.LEFT, padx=10)

        self.next_button = ttk.Button(nav_frame, text="Далее >>", command=self._show_next)
        self.next_button.pack(side=tk.LEFT, padx=10)

        self._show_image(0)

    def _show_image(self, index):
        self.current_index = index
        image = self.images[index]

        # Масштабируем изображение для предпросмотра, сохраняя пропорции
        max_w, max_h = 500, 350
        img_w, img_h = image.size
        ratio = min(max_w / img_w, max_h / img_h)
        new_size = (int(img_w * ratio), int(img_h * ratio))
        
        resized_image = image.resize(new_size, Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(resized_image)

        self.image_label.config(image=photo)
        self.image_label.image = photo

        self.info_label.config(text=f"Этикетка {index + 1} из {len(self.images)}")
        self.prev_button.config(state="normal" if index > 0 else "disabled")
        self.next_button.config(state="normal" if index < len(self.images) - 1 else "disabled")

    def _show_next(self):
        if self.current_index < len(self.images) - 1: self._show_image(self.current_index + 1)
    def _show_prev(self):
        if self.current_index > 0: self._show_image(self.current_index - 1)

    def _confirm_print(self):
        self.on_print_callback()
        self.destroy()

class PrintWorkplaceLabelsDialog(tk.Toplevel):
    """Диалог для выбора параметров печати этикеток рабочих мест."""
    # --- ИСПРАВЛЕНИЕ: Добавляем импорты для работы с принтерами ---
    try:
        import win32print
        import win32api
    except ImportError:
        win32print = None
        win32api = None
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    def __init__(self, parent, user_info, warehouse_name):
    def __init__(self, parent, user_info, title_name, items_to_print=None):
        super().__init__(parent)
        self.title(f"Печать этикеток для '{warehouse_name}'")
        self.title(f"Печать: '{title_name}'")
        self.geometry("500x400")
        self.transient(parent)
        self.grab_set()

        self.items_to_print = items_to_print # Если данные переданы, используем их
        self.warehouse_name = title_name if items_to_print is None else None # Для обратной совместимости
        self.user_info = user_info
        self.warehouse_name = warehouse_name
        self.layouts = [] # Список загруженных макетов

        if not self.win32print:
            messagebox.showerror("Ошибка", "Библиотека 'pywin32' не установлена.\nФункционал печати недоступен.", parent=self)
            self.destroy()
            return

        self._create_widgets()
        self._load_printers()
        self._load_layouts()

    def _get_client_db_connection(self):
        """Подключение к БД клиента."""
        db_config = self.user_info.get("client_db_config")
        if not db_config: return None
        return psycopg2.connect(
            host=db_config.get('db_host'), port=db_config.get('db_port'),
            dbname=db_config.get('db_name'), user=db_config.get('db_user'),
            password=db_config.get('db_password')
        )

    def _create_widgets(self):
        frame = ttk.Frame(self, padding="10")
        frame.pack(expand=True, fill="both")

        ttk.Label(frame, text="1. Выберите принтер:").pack(fill="x", pady=2)
        self.printer_combo = ttk.Combobox(frame, state="readonly")
        self.printer_combo.pack(fill="x", pady=2)
        self.printer_combo.bind("<<ComboboxSelected>>", self._load_paper_sizes)

        ttk.Label(frame, text="2. Выберите размер бумаги:").pack(fill="x", pady=(10, 2))
        self.paper_combo = ttk.Combobox(frame, state="readonly")
        self.paper_combo.pack(fill="x", pady=2)

        ttk.Label(frame, text="3. Выберите макет этикетки:").pack(fill="x", pady=(10, 2))
        self.layout_combo = ttk.Combobox(frame, state="readonly")
        self.layout_combo.pack(fill="x", pady=2)

        ttk.Button(frame, text="Напечатать", command=self._do_print).pack(fill="x", pady=(20, 2))

    def _load_printers(self):
        try:
            printers = [p[2] for p in self.win32print.EnumPrinters(self.win32print.PRINTER_ENUM_LOCAL, None, 1)]
            self.printer_combo['values'] = printers
            if printers:
                default_printer = self.win32print.GetDefaultPrinter()
                if default_printer in printers:
                    self.printer_combo.set(default_printer)
                else:
                    self.printer_combo.current(0)
                self._load_paper_sizes()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить список принтеров: {e}", parent=self)

    def _load_paper_sizes(self, *args):
        printer_name = self.printer_combo.get()
        if not printer_name: return
        
        paper_names = []
        try:
            h_printer = self.win32print.OpenPrinter(printer_name)
            try:
                # Получаем все формы, доступные для принтера
                forms = self.win32print.EnumForms(h_printer)
                for form in forms:
                    # Фильтруем по префиксу, если нужно
                    if form['Name'].startswith('Tilda_'):
                         paper_names.append(form['Name'])
            finally:
                self.win32print.ClosePrinter(h_printer)
            
            self.paper_combo['values'] = sorted(paper_names)
            if paper_names:
                self.paper_combo.current(0)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить размеры бумаги для принтера: {e}", parent=self)

    def _load_layouts(self):
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, template_json FROM label_templates ORDER BY name")
                    self.layouts = [{'name': row[0], 'json': row[1]} for row in cur.fetchall()]
            
            layout_names = [l['name'] for l in self.layouts]
            self.layout_combo['values'] = layout_names
            if layout_names:
                self.layout_combo.current(0)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить макеты: {e}", parent=self)

    def _do_print(self):
        printer = self.printer_combo.get()
        paper = self.paper_combo.get()
        layout_name = self.layout_combo.get()

        if not all([printer, paper, layout_name]):
            messagebox.showwarning("Внимание", "Все поля должны быть выбраны.", parent=self)
            return

        selected_layout = next((l for l in self.layouts if l['name'] == layout_name), None)
        if not selected_layout:
            messagebox.showerror("Ошибка", "Выбранный макет не найден.", parent=self)
            return

        all_items_data = []
        # Если данные для печати не были переданы напрямую (старый сценарий для рабочих мест)
        if self.items_to_print is None and self.warehouse_name:
            try:
                with self._get_client_db_connection() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT * FROM ap_workplaces WHERE warehouse_name = %s ORDER BY workplace_number", (self.warehouse_name,))
                        workplaces_data = cur.fetchall()
                
                for wp in workplaces_data:
                    item_data = {
                        "ap_workplaces.warehouse_name": wp['warehouse_name'],
                        "ap_workplaces.workplace_number": wp['workplace_number'],
                        "QR: Конфигурация рабочего места": json.dumps({
                            "type": "workplace_config",
                            "warehouse": wp['warehouse_name'],
                            "workplace": wp['workplace_number']
                        }, ensure_ascii=False),
                        "QR: Конфигурация сервера": json.dumps({"error": "This QR type is not for workplace labels"})
                    }
                    all_items_data.append(item_data)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить данные о рабочих местах: {e}", parent=self)
                return
        # Если данные были переданы при создании окна (новый сценарий для QR-кода сервера)
        elif self.items_to_print is not None:
            all_items_data = self.items_to_print
        
        if not all_items_data:
            messagebox.showwarning("Внимание", "Нет данных для генерации этикеток.", parent=self)
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM ap_workplaces WHERE warehouse_name = %s ORDER BY workplace_number", (self.warehouse_name,))
                    workplaces_data = cur.fetchall()
            
            # --- НОВАЯ ЛОГИКА: Сначала генерируем изображения для предпросмотра ---
            images_to_preview = []
            all_items_data = [] # Сохраняем данные для последующей печати
            for wp in workplaces_data:
                # --- НОВАЯ ЛОГИКА: Формируем данные для подстановки в макет ---
                item_data = {
                    # Данные для текстовых полей
                    "ap_workplaces.warehouse_name": wp['warehouse_name'],
                    "ap_workplaces.workplace_number": wp['workplace_number'],
                    
                    # Данные для QR-кодов (ключи соответствуют значениям в Combobox)
                    "QR: Конфигурация рабочего места": json.dumps({
                        "type": "workplace_config",
                        "warehouse": wp['warehouse_name'],
                        "workplace": wp['workplace_number']
                    }, ensure_ascii=False),
                    
                    # Заглушка для второго типа QR, если он вдруг окажется в макете
                    "QR: Конфигурация сервера": json.dumps({"error": "This QR type is not for workplace labels"})
                }

                all_items_data.append(item_data)
                
                # Генерируем изображение для этого элемента
            for item_data in all_items_data:
                img = PrintingService.generate_label_image(selected_layout['json'], item_data)
                images_to_preview.append(img)

            if not images_to_preview:
                messagebox.showwarning("Внимание", "Нет данных для генерации этикеток.", parent=self)
                messagebox.showwarning("Внимание", "Не удалось сгенерировать изображения для предпросмотра.", parent=self)
                return

            # Функция, которая будет вызвана, если пользователь нажмет "Напечатать" в окне предпросмотра
            def perform_actual_printing():
                PrintingService.print_labels_for_items(printer, paper, selected_layout['json'], all_items_data)
                PrintingService.print_labels_for_items(printer, paper, selected_layout['json'], all_items_data) # paper здесь для логов
                messagebox.showinfo("Успех", f"Задание на печать {len(all_items_data)} этикеток отправлено на принтер.", parent=self)
                self.destroy()

            # Открываем окно предпросмотра
            PreviewLabelsDialog(self, images_to_preview, perform_actual_printing)
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка печати этикеток рабочих мест: {e}\n{error_details}")
            messagebox.showerror("Ошибка печати", f"Произошла ошибка: {e}\nПодробности в app.log.", parent=self)

def open_user_management_window(parent_widget, user_info):
    """Открывает окно для управления пользователями клиента."""
    client_id = user_info.get('client_id')
    if not client_id:
        messagebox.showerror("Ошибка", "Информация о клиенте не найдена.", parent=parent_widget)
        return

    users_window = tk.Toplevel(parent_widget)
    users_window.title("Управление пользователями")
    users_window.geometry("700x400")
    users_window.grab_set()

    def load_users():
        for i in users_tree.get_children():
            users_tree.delete(i)
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, login, is_active FROM users WHERE client_id = %s ORDER BY name", (client_id,))
                    for row in cur.fetchall():
                        users_tree.insert('', 'end', values=row)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить пользователей: {e}", parent=users_window)

    def get_selected_user_id():
        selected_item = users_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите пользователя из списка.", parent=users_window)
            return None
        return users_tree.item(selected_item)['values'][0]

    def create_user():
        # Эта функция очень похожа на смену пароля, можно объединить в один класс/функцию
        editor = tk.Toplevel(users_window)
        editor.title("Новый пользователь")
        editor.grab_set()

        ttk.Label(editor, text="Имя:").grid(row=0, column=0, padx=10, pady=5, sticky='w')
        name_entry = ttk.Entry(editor, width=30)
        name_entry.grid(row=0, column=1, padx=10, pady=5)

        ttk.Label(editor, text="Логин:").grid(row=1, column=0, padx=10, pady=5, sticky='w')
        login_entry = ttk.Entry(editor, width=30)
        login_entry.grid(row=1, column=1, padx=10, pady=5)

        ttk.Label(editor, text="Пароль:").grid(row=2, column=0, padx=10, pady=5, sticky='w')
        pass_entry = ttk.Entry(editor, width=30, show="*")
        pass_entry.grid(row=2, column=1, padx=10, pady=5)

        def save():
            name, login, password = name_entry.get(), login_entry.get(), pass_entry.get()
            if not all([name, login, password]):
                messagebox.showwarning("Внимание", "Все поля обязательны.", parent=editor)
                return
            try:
                hashed_pass = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, 'пользователь', %s)",
                                    (name, login, hashed_pass, client_id))
                    conn.commit()
                messagebox.showinfo("Успех", "Пользователь создан.", parent=editor)
                load_users()
                editor.destroy()
            except psycopg2.IntegrityError:
                messagebox.showerror("Ошибка", f"Пользователь с логином '{login}' уже существует.", parent=editor)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось создать пользователя: {e}", parent=editor)

        ttk.Button(editor, text="Сохранить", command=save).grid(row=3, columnspan=2, pady=10)

    def change_password():
        user_id = get_selected_user_id()
        if not user_id: return

        editor = tk.Toplevel(users_window)
        editor.title("Смена пароля")
        editor.grab_set()

        ttk.Label(editor, text="Новый пароль:").grid(row=0, column=0, padx=10, pady=5)
        pass_entry = ttk.Entry(editor, width=30, show="*")
        pass_entry.grid(row=0, column=1, padx=10, pady=5)

        def save():
            password = pass_entry.get()
            if not password:
                messagebox.showwarning("Внимание", "Пароль не может быть пустым.", parent=editor)
                return
            try:
                hashed_pass = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed_pass, user_id))
                    conn.commit()
                messagebox.showinfo("Успех", "Пароль изменен.", parent=editor)
                editor.destroy()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сменить пароль: {e}", parent=editor)

        ttk.Button(editor, text="Сохранить", command=save).grid(row=1, columnspan=2, pady=10)

    def toggle_activity():
        user_id = get_selected_user_id()
        if not user_id: return
        
        is_active = users_tree.item(users_tree.focus())['values'][3]
        new_status = not is_active
        action = "активировать" if new_status else "заблокировать"

        if messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите {action} этого пользователя?", parent=users_window):
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, user_id))
                    conn.commit()
                load_users()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось изменить статус: {e}", parent=users_window)

    def delete_user():
        user_id = get_selected_user_id()
        if not user_id: return

        if messagebox.askyesno("Подтверждение", "Вы уверены, что хотите удалить этого пользователя?\nЭто действие необратимо.", parent=users_window):
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                    conn.commit()
                load_users()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось удалить пользователя: {e}", parent=users_window)

    def generate_qr():
        selected_item = users_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите пользователя из списка.", parent=users_window)
            return
        
        user_id, name, login, is_active = users_tree.item(selected_item)['values']

        try:
            import qrcode
            from PIL import Image, ImageTk
        except ImportError:
            messagebox.showerror("Ошибка", "Библиотеки 'qrcode' и 'Pillow' не установлены.\nУстановите их: pip install qrcode pillow", parent=users_window)
            return

        # Собираем все данные для QR-кода
        auth_data = {
            "type": "user_auth", # Тип для распознавания сканером
            "login": login,
            "client_db_config": user_info.get("client_db_config")
        }
        # Добавляем пароль, если он есть (для будущих реализаций)
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
                    auth_data['password_hash'] = cur.fetchone()[0]
        except Exception: pass # Не критично, если не удалось получить хэш
        
        # Проверка, что конфигурация БД клиента доступна
        if not auth_data["client_db_config"]:
            messagebox.showerror("Ошибка", "Не удалось найти конфигурацию базы данных клиента для генерации QR-кода.", parent=users_window)
            return

        # auth_data['password'] = "..." 

        # --- ИСПРАВЛЕНИЕ: Сжимаем данные перед кодированием ---
        import zlib
        import base64

        # 1. Преобразуем в JSON и кодируем в байты
        json_bytes = json.dumps(auth_data, ensure_ascii=False).encode('utf-8')
        # 2. Сжимаем байты с максимальным уровнем сжатия
        compressed_bytes = zlib.compress(json_bytes, level=9)
        # 3. Кодируем сжатые байты в Base64 для безопасной передачи
        full_base64_data = base64.b64encode(compressed_bytes).decode('ascii')

        # --- НОВАЯ ЛОГИКА: Разбиение на части, если данные слишком большие ---
        # Максимальная емкость QR v40 с коррекцией L ~2953 байт.
        # Возьмем с запасом 2500 символов на чанк.
        chunk_size = 2500
        chunks = [full_base64_data[i:i + chunk_size] for i in range(0, len(full_base64_data), chunk_size)]

        # --- Отображение последовательности QR-кодов ---
        qr_sequence_window = tk.Toplevel(users_window)
        qr_sequence_window.title(f"Настройка для: {name}")
        qr_sequence_window.grab_set()

        current_chunk_index = 0
        
        info_label = ttk.Label(qr_sequence_window, text="", font=("Arial", 12))
        info_label.pack(pady=10)

        qr_label = ttk.Label(qr_sequence_window)
        qr_label.pack(padx=20, pady=10)

        nav_frame = ttk.Frame(qr_sequence_window)
        nav_frame.pack(pady=10)
        prev_button = ttk.Button(nav_frame, text="<< Назад")
        prev_button.pack(side=tk.LEFT, padx=10)
        next_button = ttk.Button(nav_frame, text="Далее >>")
        next_button.pack(side=tk.LEFT, padx=10)

        def show_chunk(index):
            nonlocal current_chunk_index
            current_chunk_index = index
            
            # Формируем данные для этой части: "chunk_index/total_chunks:data"
            chunk_data = f"{index+1}/{len(chunks)}:{chunks[index]}"

            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
            qr.add_data(chunk_data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").resize((350, 350))
            
            photo = ImageTk.PhotoImage(img)
            qr_label.config(image=photo)
            qr_label.image = photo

            info_label.config(text=f"Шаг {index + 1} из {len(chunks)}. Отсканируйте код.")

            prev_button.config(state="normal" if index > 0 else "disabled")
            next_button.config(state="normal" if index < len(chunks) - 1 else "disabled")

        def show_next():
            if current_chunk_index < len(chunks) - 1:
                show_chunk(current_chunk_index + 1)

        def show_prev():
            if current_chunk_index > 0:
                show_chunk(current_chunk_index - 1)

        prev_button.config(command=show_prev)
        next_button.config(command=show_next)

        show_chunk(0)

    # --- Виджеты окна ---
    main_frame = ttk.Frame(users_window, padding="10")
    main_frame.pack(expand=True, fill=tk.BOTH)

    # Левая панель с кнопками
    buttons_frame = ttk.Frame(main_frame)
    buttons_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

    ttk.Button(buttons_frame, text="Создать", command=create_user).pack(fill=tk.X, pady=2)
    ttk.Button(buttons_frame, text="Сменить пароль", command=change_password).pack(fill=tk.X, pady=2)
    ttk.Button(buttons_frame, text="Блок/Разблок", command=toggle_activity).pack(fill=tk.X, pady=2)
    ttk.Button(buttons_frame, text="Удалить", command=delete_user).pack(fill=tk.X, pady=2)
    ttk.Separator(buttons_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
    ttk.Button(buttons_frame, text="QR-код для входа", command=generate_qr).pack(fill=tk.X, pady=2)

    # Правая панель с таблицей
    tree_frame = ttk.Frame(main_frame)
    tree_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

    cols = ('id', 'name', 'login', 'is_active')
    users_tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
    users_tree.heading('id', text='ID')
    users_tree.heading('name', text='Имя')
    users_tree.heading('login', text='Логин')
    users_tree.heading('is_active', text='Активен')
    users_tree.column('id', width=40, anchor=tk.CENTER)
    users_tree.column('name', width=200)
    users_tree.column('login', width=150)
    users_tree.column('is_active', width=80, anchor=tk.CENTER)
    users_tree.pack(expand=True, fill=tk.BOTH)

    load_users()

class AdminWindow(tk.Tk):
    """Главное окно для роли 'администратор'."""
    def __init__(self, user_info):
        super().__init__()
        self.user_info = user_info
        self.title(f"ТильдаКод [Пользователь: {self.user_info['name']}, Роль: {self.user_info['role']}]")
        self.geometry("600x400")

        self._create_menu()

        label = ttk.Label(self, text="Добро пожаловать, Администратор!", font=("Arial", 14))
        label.pack(expand=True)

    def _create_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Выход", command=self.quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        # Меню для управления устройствами
        devices_menu = tk.Menu(menubar, tearoff=0)
        devices_menu.add_command(label="Управление печатью", command=lambda: open_print_management_window(self))
        menubar.add_cascade(label="Устройства", menu=devices_menu)

        # Меню для управления пользователями
        users_menu = tk.Menu(menubar, tearoff=0)
        users_menu.add_command(label="Пользователи клиента", command=lambda: open_user_management_window(self, self.user_info))
        menubar.add_cascade(label="Пользователи", menu=users_menu)

        # Меню для настройки рабочих мест
        setup_menu = tk.Menu(menubar, tearoff=0)
        setup_menu.add_command(label="Настройка рабочих мест", command=lambda: open_workplace_setup_window(self, self.user_info))
        setup_menu.add_separator()
        setup_menu.add_command(label="Редактор макетов", command=lambda: open_label_editor_window(self, self.user_info))
        menubar.add_cascade(label="Настройка", menu=setup_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе")
        menubar.add_cascade(label="Справка", menu=help_menu)