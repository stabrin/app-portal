# src/admin_ui.py

import tkinter as tk
import logging
from tkinter import ttk, messagebox, filedialog, simpledialog
import logging
import threading # Keep this, it's used for update_api_status
import re
import json
import time
import pandas as pd
import io
import os
from datetime import datetime

# --- Добавляем глобальный импорт Pillow ---
try:
    from PIL import Image, ImageTk

except ImportError:
    Image = None # Помечаем как недоступный, если Pillow не установлен

# Импорты для работы с БД и QR-кодами
from .db_connector import get_main_db_connection, get_client_db_connection
from .api_service import ApiService
from .supply_notification_service import SupplyNotificationService
import bcrypt
import psycopg2
import psycopg2.extras

# Импортируем новый сервис печати
from .printing_service import PrintingService, LabelEditorWindow, ImageSelectionDialog

from .aggregation_service import run_aggregation_process_desktop
import requests
from datetime import datetime
import traceback
import base64
import zlib, base64 # Для сжатия данных QR-кода (оставляем один раз)

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
    logging.debug("Выбран пункт меню 'Настройка/Настройка рабочих мест'. Открытие окна...")
    setup_window = tk.Toplevel(parent_widget)
    setup_window.title("Настройка рабочих мест")
    setup_window.geometry("600x500")
    setup_window.grab_set()

    notebook = ttk.Notebook(setup_window)
    notebook.pack(expand=True, fill="both", padx=10, pady=10)

    # --- Вкладка 1: Генерация настроечного кода ---
    config_frame = ttk.Frame(notebook, padding="10")
    notebook.add(config_frame, text="Настроечный код")

    ttk.Label(config_frame, text="Создание файлов конфигурации для мобильного приложения.", font=("Arial", 10, "bold")).pack(anchor="w")
    ttk.Label(config_frame, text="Будут созданы файлы 'config.ini' и 'cert.pem' с настройками подключения к базе данных этого клиента.", wraplength=400).pack(anchor="w", pady=(0, 5))
    
    def save_config_files():
        """
        Получает данные из БД клиента, формирует ini-файл и файл сертификата,
        и сохраняет их в выбранную пользователем директорию.
        """
        try:
            # 1. Получаем настройки из БД клиента
            with get_client_db_connection(user_info) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT setting_key, setting_value FROM ap_settings WHERE setting_key IN ('LOCAL_SERVER_ADDRESS', 'LOCAL_SERVER_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')")
                    settings_from_db = {row['setting_key']: row['setting_value'] for row in cur.fetchall()}

            # 2. Получаем сертификат из конфигурации, переданной при логине
            ssl_cert_content = user_info.get('client_db_config', {}).get('db_ssl_cert', '')

            # 3. Проверяем наличие всех данных
            required_keys = ['LOCAL_SERVER_ADDRESS', 'LOCAL_SERVER_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
            if not all(key in settings_from_db for key in required_keys):
                missing_keys = [key for key in required_keys if key not in settings_from_db]
                raise ValueError(f"В таблице 'ap_settings' отсутствуют необходимые настройки: {', '.join(missing_keys)}")

            # 4. Шифруем пароль
            def xor_cipher(data, key):
                return bytes([ord(c) ^ ord(k) for c, k in zip(data, key * (len(data) // len(key) + 1))])

            encryption_key = "TildaKodSecretKey" # Ключ должен быть таким же в мобильном приложении
            # Шифруем, а затем кодируем в Base64 для безопасного хранения в текстовом файле
            encrypted_bytes = xor_cipher(settings_from_db['DB_PASSWORD'], encryption_key)
            encrypted_password_b64 = base64.b64encode(encrypted_bytes).decode('ascii')

            # 5. Формируем содержимое ini-файла
            ini_content = (
                "[database]\n"
                f"host = {settings_from_db['LOCAL_SERVER_ADDRESS']}\n"
                f"port = {settings_from_db['LOCAL_SERVER_PORT']}\n"
                f"dbname = {settings_from_db['DB_NAME']}\n"
                f"user = {settings_from_db['DB_USER']}\n"
                f"password = {encrypted_password_b64}"
            )

            # 6. Запрашиваем у пользователя место для сохранения
            save_path = filedialog.askdirectory(title="Выберите папку для сохранения файлов конфигурации", parent=setup_window)
            if not save_path:
                return # Пользователь отменил выбор

            # 7. Сохраняем файлы
            with open(os.path.join(save_path, 'config.ini'), 'w', encoding='utf-8') as f:
                f.write(ini_content)
            if ssl_cert_content:
                with open(os.path.join(save_path, 'cert.pem'), 'w', encoding='utf-8') as f:
                    f.write(ssl_cert_content)

            messagebox.showinfo("Успех", f"Файлы 'config.ini' и 'cert.pem' успешно сохранены в папку:\n{save_path}", parent=setup_window)

        except Exception as e:
            logging.error(f"Ошибка при создании файлов конфигурации: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось создать файлы конфигурации: {e}", parent=setup_window)
            return

    ttk.Button(config_frame, text="Сохранить файлы конфигурации", command=save_config_files).pack(pady=20)

    # --- Вкладка 2: Рабочие места ---
    workplaces_frame = ttk.Frame(notebook, padding="10")
    notebook.add(workplaces_frame, text="Рабочие места")

    def load_warehouses():
        """Загружает и отображает склады и количество рабочих мест в них."""
        for i in warehouses_tree.get_children():
            warehouses_tree.delete(i)
        
        try:
            with get_client_db_connection(user_info) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT warehouse_name, COUNT(*) as workplace_count
                        FROM ap_workplaces
                        GROUP BY warehouse_name
                        ORDER BY warehouse_name;
                    """)
                    for row in cur.fetchall():
                        warehouses_tree.insert('', 'end', values=(row['warehouse_name'], row['workplace_count']))
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить список складов: {e}", parent=setup_window)

    def create_new_warehouse():
        """Открывает диалог для создания нового склада."""
        name = tk.simpledialog.askstring("Новый склад", "Введите название нового склада:", parent=setup_window)
        if not name: return

        count = tk.simpledialog.askinteger("Количество мест", "Введите количество рабочих мест:", parent=setup_window, minvalue=1)
        if not count: return

        try:
            with get_client_db_connection(user_info) as conn:
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
            with get_client_db_connection(user_info) as conn:
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

class PreviewLabelsDialog(tk.Toplevel):
    """Новый класс для окна предпросмотра сгенерированных этикеток."""
    def __init__(self, parent, images, on_print_all_callback, on_print_current_callback):
        super().__init__(parent)
        self.title("Предпросмотр этикеток")
        self.geometry("600x500")
        self.transient(parent)
        self.grab_set()

        self.parent_dialog = parent
        self.images = images
        self.on_print_all_callback = on_print_all_callback
        self.on_print_current_callback = on_print_current_callback
        self.current_index = 0


        self.info_label = ttk.Label(self, text="", font=("Arial", 12))
        self.info_label.pack(pady=10)

        self.image_label = ttk.Label(self)
        self.image_label.pack(padx=10, pady=10, expand=True, fill="both")

        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=10)

        self.prev_button = ttk.Button(nav_frame, text="<< Назад", command=self._show_prev)
        self.prev_button.pack(side=tk.LEFT, padx=10)

        self.print_all_button = ttk.Button(nav_frame, text="Напечатать все", command=self._print_all)
        self.print_all_button.pack(side=tk.LEFT, padx=10)

        self.print_current_button = ttk.Button(nav_frame, text="Напечатать текущую", command=self._print_current)
        self.print_current_button.pack(side=tk.LEFT, padx=10)

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

    def _print_all(self):
        self.on_print_all_callback()
        self.destroy()

    def _print_current(self):
        # Вызываем callback, передавая индекс текущего изображения
        self.on_print_current_callback(self.current_index)
        # Окно не закрываем

class PrintWorkplaceLabelsDialog(tk.Toplevel):
    """Диалог для выбора параметров печати этикеток рабочих мест."""
    # --- ИСПРАВЛЕНИЕ: Добавляем импорты для работы с принтерами ---
    try:
        import win32print
        import win32api
    except ImportError:
        win32print = None # type: ignore
        win32api = None
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
    def __init__(self, parent, user_info, title_name, items_to_print=None, preselected_layout=None, pregenerated_images=None):
        super().__init__(parent)
        self.title(f"Печать: '{title_name}'")
        self.geometry("500x400")
        self.transient(parent)
        self.grab_set()

        self.items_to_print = items_to_print # Если данные переданы, используем их
        self.user_info = user_info
        self.warehouse_name = title_name if items_to_print is None else None # Для обратной совместимости
        self.layouts = [] # Список загруженных макетов
        self.preselected_layout = preselected_layout # Для предустановки макета
        self.pregenerated_images = pregenerated_images # Для предпросмотра из редактора

        if not self.win32print:
            messagebox.showerror("Ошибка", "Библиотека 'pywin32' не установлена.\nФункционал печати недоступен.", parent=self)
            self.destroy()
            return

        self._create_widgets()
        self._load_printers()
        self._load_layouts()

    def _get_client_db_connection(self):
        """Вспомогательный метод для подключения к БД клиента.
        Использует централизованный метод из db_connector."""
        return get_client_db_connection(self.user_info)

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
                if self.preselected_layout and self.preselected_layout in layout_names:
                    self.layout_combo.set(self.preselected_layout)
                else:
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
                        }, ensure_ascii=False)
                    }
                    # --- ИСПРАВЛЕНИЕ: Добавляем заглушку для источника данных QR-кода сервера ---
                    # Это предотвращает ошибку, если макет содержит объект с таким источником.
                    # Раньше это поле отсутствовало, что приводило к сбою.
                    item_data["QR: Конфигурация сервера"] = json.dumps({"error": "This QR type is not for workplace labels"})
                    
                    # Добавляем заглушку для DataMatrix, чтобы избежать ошибок при печати рабочих мест
                    # с макетом, содержащим DataMatrix.
                    item_data["items.datamatrix"] = "DM_placeholder"

                    all_items_data.append(item_data)
            except Exception as e:
                # --- ИЗМЕНЕНИЕ: Ошибка теперь пишется в лог, а не показывается в messagebox ---
                logging.error(f"Не удалось загрузить данные о рабочих местах: {e}\n{traceback.format_exc()}")
                messagebox.showerror("Ошибка", "Не удалось загрузить данные о рабочих местах. Подробности в app.log.", parent=self)
                return
        # Если данные были переданы при создании окна (новый сценарий для QR-кода сервера)
        elif self.items_to_print is not None:
            all_items_data = self.items_to_print
        
        if not all_items_data:
            messagebox.showwarning("Внимание", "Нет данных для генерации этикеток.", parent=self)
            return

        images_to_preview = []
        # Если изображения уже были сгенерированы (например, из редактора), используем их
        if self.pregenerated_images:
            images_to_preview = self.pregenerated_images
        else:
            # Иначе генерируем их сейчас
            try:
                text_cache = {}
                for item_data in all_items_data:
                    try:
                        img = PrintingService.generate_label_image(selected_layout['json'], item_data, self.user_info, text_cache)
                        images_to_preview.append(img)
                    except Exception as e:
                        logging.error(f"Ошибка генерации изображения для предпросмотра: {e}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сгенерировать изображения для предпросмотра: {e}", parent=self)
                return
            if not images_to_preview:
                messagebox.showwarning("Внимание", "Не удалось сгенерировать изображения для предпросмотра.", parent=self)
                return

            # Callback для кнопки "Напечатать все"
            def perform_actual_printing():
                # --- ИСПРАВЛЕНИЕ: Используем уже сгенерированные изображения ---
                PrintingService.print_generated_images(printer, paper, images_to_preview, self.user_info)
                messagebox.showinfo("Успех", f"Задание на печать {len(images_to_preview)} этикеток отправлено на принтер.", parent=self)
                self.destroy()

            # Callback для кнопки "Напечатать текущую"
            def perform_single_print(index):
                # --- ИСПРАВЛЕНИЕ: Используем уже сгенерированные изображения ---
                image_to_print = images_to_preview[index]
                PrintingService.print_generated_images(printer, paper, [image_to_print], self.user_info)
                messagebox.showinfo("Успех", f"Задание на печать 1 этикетки отправлено на принтер.", parent=self)

            # Открываем окно предпросмотра
            PreviewLabelsDialog(self, images_to_preview, perform_actual_printing, perform_single_print)

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

        # --- ИСПРАВЛЕНИЕ: Используем глобальную функцию для отображения последовательности QR-кодов ---
        display_qr_sequence(f"Настройка для: {name}", chunks, users_window)

class CodeUploadFrame(ttk.Frame):
    """Фрейм для загрузки файлов с кодами маркировки."""
    def __init__(self, parent, user_info, order_id):
        super().__init__(parent)
        self.user_info = user_info
        self.order_id = order_id

        self._create_widgets()

    def _create_widgets(self):
        main_frame = ttk.Frame(self, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Загрузите файлы с кодами маркировки (csv, txt):").pack(anchor="w")

        # Поле для выбора файлов
        file_entry_frame = ttk.Frame(main_frame)
        file_entry_frame.pack(fill=tk.X, pady=5)
        self.file_path_var = tk.StringVar()
        ttk.Entry(file_entry_frame, textvariable=self.file_path_var, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(file_entry_frame, text="Обзор...", command=self._select_files).pack(side=tk.LEFT, padx=(5,0))

        # --- НОВЫЙ БЛОК: Выбор типа кодов ---
        dm_type_frame = ttk.Frame(main_frame)
        dm_type_frame.pack(fill=tk.X, pady=5)
        ttk.Label(dm_type_frame, text="Тип кодов DataMatrix:").pack(side=tk.LEFT)
        self.dm_type_var = tk.StringVar(value="standard")
        ttk.Combobox(dm_type_frame, textvariable=self.dm_type_var, values=["standard", "tobacco"], state="readonly", width=15).pack(side=tk.LEFT, padx=5)

        # Настройки агрегации
        agg_frame = ttk.LabelFrame(main_frame, text="Настройки агрегации", padding="10")
        agg_frame.pack(fill=tk.X, pady=10)
        
        self.aggregation_mode_var = tk.StringVar(value="none")
        ttk.Radiobutton(agg_frame, text="Без агрегации", variable=self.aggregation_mode_var, value="none").pack(anchor="w")
        
        level1_frame = ttk.Frame(agg_frame)
        level1_frame.pack(fill=tk.X)
        ttk.Radiobutton(level1_frame, text="Агрегация в короба:", variable=self.aggregation_mode_var, value="level1").pack(side=tk.LEFT)
        self.level1_qty_var = tk.StringVar(value="10")
        ttk.Entry(level1_frame, textvariable=self.level1_qty_var, width=5).pack(side=tk.LEFT)

        # Кнопка запуска
        ttk.Button(main_frame, text="Запустить обработку", command=self._run_processing).pack(pady=10)

    def _select_files(self):
        filepaths = filedialog.askopenfilenames(
            title="Выберите файлы с кодами",
            filetypes=[("Текстовые файлы", "*.txt *.csv"), ("Все файлы", "*.*")],
            parent=self
        )
        if filepaths:
            self.file_path_var.set(";".join(filepaths))

    def _run_processing(self):
        filepaths = self.file_path_var.get().split(";")
        if not all(filepaths):
            messagebox.showwarning("Внимание", "Не выбраны файлы для загрузки.", parent=self)
            return

        try:
            level1_qty = int(self.level1_qty_var.get()) if self.aggregation_mode_var.get() == 'level1' else 0
        except (ValueError, TypeError):
            messagebox.showerror("Ошибка", "Количество в коробе должно быть целым числом.", parent=self)
            return

        # --- НОВАЯ ЛОГИКА: Запуск обработки в отдельном потоке ---
        log_window = tk.Toplevel(self)
        log_window.title(f"Лог обработки заказа №{self.order_id}")
        log_window.geometry("700x500")
        log_text = tk.Text(log_window, wrap="word", padx=10, pady=10, state="disabled")
        log_text.pack(expand=True, fill=tk.BOTH)

        def append_log(message):
            if not log_window.winfo_exists(): return
            log_text.config(state="normal")
            log_text.insert(tk.END, message + "\n")
            log_text.see(tk.END)
            log_text.config(state="disabled")

        def task():
            try:
                logs = run_aggregation_process_desktop(
                    user_info=self.user_info,
                    order_id=self.order_id,
                    filepaths=filepaths,
                    dm_type=self.dm_type_var.get(),
                    aggregation_mode=self.aggregation_mode_var.get(),
                    level1_qty=level1_qty
                )
                for log_line in logs:
                    self.after(0, lambda line=log_line: append_log(line))
            except Exception as e:
                self.after(0, lambda err=e: append_log(f"КРИТИЧЕСКАЯ ОШИБКА: {err}\n{traceback.format_exc()}"))

        thread = threading.Thread(target=task, daemon=True)
        thread.start()

class AddClientDialog(tk.Toplevel):
    """Диалог для добавления нового клиента в локальный справочник."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Добавить нового клиента")
        self.transient(parent)
        self.grab_set()
        self.result = None # Будет хранить {'name': ..., 'inn': ...}

        frame = ttk.Frame(self, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Наименование:").grid(row=0, column=0, sticky="w", pady=2)
        self.name_entry = ttk.Entry(frame, width=40)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(frame, text="ИНН (опционально):").grid(row=1, column=0, sticky="w", pady=2)
        self.inn_entry = ttk.Entry(frame, width=40)
        self.inn_entry.grid(row=1, column=1, sticky="ew", pady=2)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=2, pady=(20, 0), sticky="e")
        ttk.Button(button_frame, text="Сохранить", command=self._on_save).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=self.destroy).pack(side=tk.LEFT)

        self.name_entry.focus_set()

    def _on_save(self):
        name = self.name_entry.get().strip()
        inn = self.inn_entry.get().strip()
        if not name:
            messagebox.showwarning("Внимание", "Наименование клиента не может быть пустым.", parent=self)
            return
        self.result = {'name': name, 'inn': inn if inn else None}
        self.destroy()

class NotificationEditorDialog(tk.Toplevel):
    """Диалог для создания/редактирования уведомления."""
    def __init__(self, parent, user_info, notification_id=None):
        super().__init__(parent)
        title = f"Редактирование уведомления №{notification_id}" if notification_id else "Новое уведомление о поставке"
        self.title(title)
        self.result = None
        self.user_info = user_info
        self.notification_id = notification_id

        logging.info(f"Инициализация NotificationEditorDialog. ID: {self.notification_id}")

        # from .supply_notification_service import SupplyNotificationService # Уже импортирован
        self.on_save_callback = None
        self.service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
        from .catalogs_service import CatalogsService
        self.catalog_service = CatalogsService(self.user_info, lambda: get_client_db_connection(self.user_info))

        self.initial_data = {}
        if notification_id:
            logging.info(f"Загрузка данных для уведомления ID: {notification_id}")
            self.initial_data = self.service.get_notification_by_id(notification_id)
            logging.info(f"Данные загружены: {self.initial_data}")

        self._create_widgets()

    def _create_widgets(self):
        logging.info("Начало создания виджетов в NotificationEditorDialog.")
        
        # --- ИЗМЕНЕНИЕ: Создаем Notebook для вкладок ---
        main_notebook = ttk.Notebook(self)
        main_notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- ВКЛАДКА 1: Общая информация ---
        general_tab = ttk.Frame(main_notebook, padding=10)
        main_notebook.add(general_tab, text="Общая информация")

        ttk.Label(general_tab, text="Сценарий маркировки:").pack(anchor="w")
        self.scenario_var = tk.StringVar()
        self.scenario_combo = ttk.Combobox(general_tab, textvariable=self.scenario_var, state="readonly")
        self.scenario_combo.pack(fill=tk.X, pady=2)
        self._load_scenarios()
        self.scenario_combo.bind("<<ComboboxSelected>>", self._on_scenario_change)

        client_frame = ttk.Frame(general_tab)
        client_frame.pack(fill=tk.X, pady=2)
        ttk.Label(client_frame, text="Клиент:").pack(anchor="w")
        self.client_var = tk.StringVar()
        client_inner_frame = ttk.Frame(client_frame)
        client_inner_frame.pack(fill=tk.X)
        self.client_combo = ttk.Combobox(client_inner_frame, textvariable=self.client_var, state="readonly")
        self.client_combo.bind("<Button-1>", self._on_client_combo_click)
        self.client_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- ИЗМЕНЕНИЕ: Замена Listbox на Combobox для товарной группы ---
        ttk.Label(general_tab, text="Товарная группа:").pack(anchor="w")
        self.product_group_var = tk.StringVar()
        self.product_group_combo = ttk.Combobox(general_tab, textvariable=self.product_group_var, state="readonly")
        self.product_group_combo.pack(fill=tk.X, pady=2)
        self._load_product_groups()

        ttk.Label(general_tab, text="Планируемая дата прибытия:").pack(anchor="w")
        self.arrival_date_var = tk.StringVar()
        date_frame = ttk.Frame(general_tab)
        date_frame.pack(fill=tk.X, pady=2)
        self.arrival_date_entry = ttk.Entry(date_frame, textvariable=self.arrival_date_var)
        self.arrival_date_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(date_frame, text="...", width=3, command=self._open_calendar_dialog).pack(side=tk.LEFT, padx=(5,0))

        ttk.Label(general_tab, text="Номер контейнера/автомобиля:").pack(anchor="w")
        self.vehicle_number_entry = ttk.Entry(general_tab)
        self.vehicle_number_entry.pack(fill=tk.X, pady=2)

        ttk.Label(general_tab, text="Комментарии:").pack(anchor="w")
        self.comments_text = tk.Text(general_tab, height=3)
        self.comments_text.pack(fill=tk.X, pady=2)

        if self.notification_id:
            # --- ВКЛАДКА 2: Документы ---
            docs_tab = ttk.Frame(main_notebook, padding=10)
            main_notebook.add(docs_tab, text="Документы")
            
            docs_controls = ttk.Frame(docs_tab)
            docs_controls.pack(fill=tk.X, pady=2)
            ttk.Button(docs_controls, text="Загрузить", command=self._upload_client_document).pack(side=tk.LEFT)
            ttk.Button(docs_controls, text="Скачать", command=self._download_client_document).pack(side=tk.LEFT, padx=5)
            ttk.Button(docs_controls, text="Удалить", command=self._delete_client_document).pack(side=tk.LEFT)

            self.files_listbox = tk.Listbox(docs_tab, height=4)
            self.files_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self._load_notification_files()

            # --- ВКЛАДКА 3: Детализация заказа ---
            details_tab = ttk.Frame(main_notebook, padding=10)
            main_notebook.add(details_tab, text="Детализация заказа")

            details_controls = ttk.Frame(details_tab)
            details_controls.pack(fill=tk.X, pady=5)
            ttk.Button(details_controls, text="Скачать шаблон", command=self._download_details_template).pack(side=tk.LEFT)
            ttk.Button(details_controls, text="Загрузить из файла", command=self._upload_details_file).pack(side=tk.LEFT, padx=5)
            ttk.Button(details_controls, text="Сохранить детализацию", command=self._save_details_from_table).pack(side=tk.RIGHT)

            self.details_cols = ["id", "gtin", "quantity", "aggregation", "production_date", "shelf_life_months", "expiry_date"]
            self.details_tree = ttk.Treeview(details_tab, columns=self.details_cols, show='headings')
            
            col_map = {
                "id": ("ID", 40, "center"), "gtin": ("GTIN", 140, "w"), "quantity": ("Кол-во", 80, "e"),
                "aggregation": ("Агрегация", 80, "center"), "production_date": ("Дата произв.", 100, "center"),
                "shelf_life_months": ("Срок годн. (мес)", 100, "center"), "expiry_date": ("Годен до", 100, "center")
            }
            for col, (text, width, anchor) in col_map.items():
                self.details_tree.heading(col, text=text)
                self.details_tree.column(col, width=width, anchor=anchor)
            
            self.details_tree.pack(fill=tk.BOTH, expand=True, pady=(5,0))
            self.details_tree.bind("<Double-1>", self._on_details_double_click)
            self._load_notification_details()

        # --- ИЗМЕНЕНИЕ: Кнопки сохранения/отмены теперь внизу, вне вкладок ---
        buttons_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons_frame.pack(fill=tk.X)
        # Переименовываем кнопку в "Создать/Обновить"
        ttk.Button(buttons_frame, text="Создать", command=self._save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(buttons_frame, text="Отмена", command=self.destroy).pack(side=tk.RIGHT)

        if self.initial_data:
            self._load_initial_values()
        
        self._on_scenario_change()
        logging.info("Создание виджетов в NotificationEditorDialog завершено.")

        if self.notification_id:
            self.scenario_combo.config(state='disabled')
            self.client_combo.config(state='disabled')

    def _load_notification_files(self):
        self.files_listbox.delete(0, tk.END)
        self.client_files = self.service.get_notification_files(self.notification_id)
        for f in self.client_files:
            self.files_listbox.insert(tk.END, f['filename'])

    def _upload_client_document(self):
        filepath = filedialog.askopenfilename(parent=self)
        if not filepath: return
        try:
            filename = os.path.basename(filepath)
            with open(filepath, 'rb') as f:
                file_data = f.read()
            self.service.add_notification_file(self.notification_id, filename, file_data, 'client_document')
            self._load_notification_files()
            messagebox.showinfo("Успех", "Файл успешно загружен.", parent=self)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить файл: {e}", parent=self)

    def _download_client_document(self):
        selected_indices = self.files_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Внимание", "Выберите файл для скачивания.", parent=self)
            return
        file_id = self.client_files[selected_indices[0]]['id']
        file_content, filename = self.service.get_file_content(file_id)
        save_path = filedialog.asksaveasfilename(initialfile=filename, parent=self)
        if save_path:
            with open(save_path, 'wb') as f:
                f.write(file_content)
            messagebox.showinfo("Успех", "Файл сохранен.", parent=self)

    def _delete_client_document(self):
        selected_indices = self.files_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Внимание", "Выберите файл для удаления.", parent=self)
            return
        if not messagebox.askyesno("Подтверждение", "Удалить выбранный файл?", parent=self):
            return
        file_id = self.client_files[selected_indices[0]]['id']
        self.service.delete_notification_file(file_id)
        self._load_notification_files()

    def _load_notification_details(self):
        for i in self.details_tree.get_children(): self.details_tree.delete(i)
        details = self.service.get_notification_details(self.notification_id)
        for item in details:
            values = [item.get(col, '') for col in self.details_cols]
            self.details_tree.insert('', 'end', iid=item['id'], values=values)

    def _on_details_double_click(self, event):
        region = self.details_tree.identify("region", event.x, event.y)
        if region != "cell": return

        column_id = self.details_tree.identify_column(event.x)
        column_index = int(column_id.replace('#', '')) - 1
        item_id = self.details_tree.focus()
        
        x, y, width, height = self.details_tree.bbox(item_id, column_id)

        entry_var = tk.StringVar()
        entry = ttk.Entry(self.details_tree, textvariable=entry_var)
        entry.place(x=x, y=y, width=width, height=height)
        
        current_value = self.details_tree.item(item_id, "values")[column_index]
        entry_var.set(current_value)
        entry.focus_set()

        def on_focus_out(event):
            new_value = entry_var.get()
            current_values = list(self.details_tree.item(item_id, "values"))
            current_values[column_index] = new_value
            self.details_tree.item(item_id, values=tuple(current_values))
            entry.destroy()

        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", on_focus_out)

    def _download_details_template(self):
        df = self.service.get_formalization_template()
        save_path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], parent=self)
        if save_path:
            df.to_excel(save_path, index=False)
            messagebox.showinfo("Успех", "Шаблон успешно сохранен.", parent=self)

    def _upload_details_file(self):
        if not messagebox.askyesno("Подтверждение", "Загрузка из файла полностью заменит текущую детализацию. Продолжить?", parent=self):
            return
        filepath = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")], parent=self)
        if not filepath: return
        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
            rows_processed = self.service.process_formalized_file(self.notification_id, file_data)
            self._load_notification_details()
            messagebox.showinfo("Успех", f"Файл успешно обработан. Загружено {rows_processed} строк.", parent=self)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось обработать файл: {e}", parent=self)

    def _save_details_from_table(self):
        details_to_save = []
        for item_id in self.details_tree.get_children():
            raw_values = self.details_tree.item(item_id, "values")
            processed_values = [
                int(raw_values[0]) if raw_values[0] else None,
                raw_values[1] if raw_values[1] else None,
                int(raw_values[2]) if raw_values[2] else None,
                int(raw_values[3]) if raw_values[3] else None,
                raw_values[4] if raw_values[4] else None,
                int(raw_values[5]) if raw_values[5] else None,
                raw_values[6] if raw_values[6] else None
            ]
            details_to_save.append(tuple(processed_values))
        try:
            logging.debug(f"Данные для сохранения детализации: {details_to_save}")
            self.service.save_notification_details(details_to_save)
            messagebox.showinfo("Успех", "Изменения в детализации успешно сохранены.", parent=self)
        except Exception as e:
            logging.error(f"Ошибка при сохранении детализации: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось сохранить детализацию: {e}", parent=self)

    def _on_client_combo_click(self, event):
        # --- ИСПРАВЛЕНИЕ: Показываем кнопку "Добавить нового" только для локального справочника ---
        # Это предотвращает появление кнопки при работе с клиентами из API ДМ.Код.
        if self.client_source == 'local' and not hasattr(self, 'add_client_button'):
            self.add_client_button = ttk.Button(self.client_combo.master, text="Добавить нового", command=self._add_new_client)
            self.add_client_button.pack(side=tk.RIGHT, padx=5)

    def _add_new_client(self):
        dialog = AddClientDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            try:
                self.catalog_service.upsert_local_client(dialog.result)
                self._load_clients(source=self.client_source)
                self.client_var.set(dialog.result['name'])
                messagebox.showinfo("Успех", f"Клиент '{dialog.result['name']}' успешно добавлен.", parent=self)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось добавить клиента: {e}", parent=self)

    def _open_calendar_dialog(self):
        cal_dialog = CalendarDialog(self, initial_date=datetime.strptime(self.arrival_date_var.get(), "%Y-%m-%d") if self.arrival_date_var.get() else datetime.now())
        self.wait_window(cal_dialog)
        if cal_dialog.result:
            self.arrival_date_var.set(cal_dialog.result.strftime("%Y-%m-%d"))

    def _on_scenario_change(self, event=None):
        selected_scenario_name = self.scenario_var.get()
        if not selected_scenario_name: return
        selected_scenario = next((s for s in self.scenarios if s['name'] == selected_scenario_name), None)
        if not selected_scenario: return
        scenario_data = selected_scenario.get('scenario_data', {})
        if scenario_data.get('dm_source') == 'Заказ в ДМ.Код':
            self._load_clients(source='api')
        else:
            self._load_clients(source='local')

    def _load_scenarios(self):
        scenarios = self.catalog_service.get_marking_scenarios()
        self.scenarios = scenarios
        self.scenario_combo['values'] = [s['name'] for s in scenarios]
        if scenarios:
            if self.initial_data:
                initial_scenario = next((s for s in self.scenarios if s['id'] == self.initial_data.get('scenario_id')), None)
                if initial_scenario:
                    self.scenario_var.set(initial_scenario['name'])
            else:
                self.scenario_var.set(scenarios[0]['name'])

    def _load_clients(self, source='local'):
        self.client_source = source
        clients = []
        try:
            if source == 'api':
                clients = self.catalog_service.get_participants_catalog()
            else:
                clients = self.catalog_service.get_local_clients()
        except Exception as e:
            messagebox.showerror("Ошибка загрузки клиентов", f"Не удалось загрузить список клиентов: {e}", parent=self)

        self.clients = clients
        self.client_combo['values'] = [c.get('name', '') for c in clients]

        if clients:
            if self.initial_data:
                initial_client_name = self.initial_data.get('client_name')
                if initial_client_name in self.client_combo['values']:
                    self.client_var.set(initial_client_name)
                else:
                    self.client_var.set(clients[0]['name'])
            else:
                self.client_var.set(clients[0]['name'])
        else:
            self.client_var.set('')

    def _load_product_groups(self):
        product_groups = self.catalog_service.get_product_groups()
        self.product_groups = product_groups
        # --- ИЗМЕНЕНИЕ: Заполняем Combobox ---
        self.product_group_combo['values'] = [pg['display_name'] for pg in product_groups]


    def _load_initial_values(self):
        data = self.initial_data
        if not data: return
        
        # Сценарий и клиент уже загружаются в _load_scenarios и _load_clients
        
        # Товарные группы
        # --- ИЗМЕНЕНИЕ: Устанавливаем значение для Combobox ---
        initial_pgs = data.get('product_groups', [])
        if initial_pgs:
            initial_pg_name = initial_pgs[0].get('name')
            if initial_pg_name in self.product_group_combo['values']:
                self.product_group_var.set(initial_pg_name)

        # Дата прибытия
        if data.get('planned_arrival_date'):
            self.arrival_date_var.set(str(data['planned_arrival_date']))

        # Номер ТС
        self.vehicle_number_entry.insert(0, data.get('vehicle_number', ''))

        # Комментарии
        self.comments_text.insert('1.0', data.get('comments', ''))

    def _save(self):
        try:
            # Сбор данных
            selected_scenario_name = self.scenario_var.get()
            scenario = next((s for s in self.scenarios if s['name'] == selected_scenario_name), None)
            
            selected_client_name = self.client_var.get()
            client = next((c for c in self.clients if c['name'] == selected_client_name), None)

            # --- ИЗМЕНЕНИЕ: Получаем данные из Combobox ---
            selected_pg_name = self.product_group_var.get()
            selected_pg = next((pg for pg in self.product_groups if pg['display_name'] == selected_pg_name), None)
            # Сохраняем как список из одного элемента для совместимости
            selected_pgs = [selected_pg] if selected_pg else []

            data = {
                'scenario_id': scenario['id'],
                'scenario_name': scenario['name'],
                'client_name': client['name'],
                'product_groups': [{'id': pg['id'], 'name': pg['display_name']} for pg in selected_pgs],
                'planned_arrival_date': self.arrival_date_var.get() or None,
                'vehicle_number': self.vehicle_number_entry.get(),
                'comments': self.comments_text.get('1.0', 'end-1c')
            }
            
            if self.client_source == 'api':
                data['client_api_id'] = client['id']
                data['client_local_id'] = None
            else: # local
                data['client_api_id'] = None
                data['client_local_id'] = client['id']

            if self.notification_id:
                self.service.update_notification(self.notification_id, data)
            else:
                self.notification_id = self.service.create_notification(data)
                self.title(f"Редактирование уведомления №{self.notification_id}")

            messagebox.showinfo("Успех", "Уведомление успешно сохранено.", parent=self)
            if self.on_save_callback:
                self.on_save_callback()
            self.destroy() # type: ignore

        except Exception as e:
            logging.error(f"Ошибка сохранения уведомления: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось сохранить уведомление: {e}", parent=self)

class ApiIntegrationFrame(ttk.Frame):
    """Фрейм для интеграции с API ДМкод, встраиваемый во вкладку."""
    def __init__(self, parent, user_info, order_id, post_processing_mode=None):
        super().__init__(parent)
        self.post_processing_mode = post_processing_mode

        self.user_info = user_info
        self.order_id = order_id
        self.api_service = ApiService(user_info)
        self.order_data = None

        self._load_order_data()
        self._create_widgets()

    def _get_client_db_connection(self):
        return get_client_db_connection(self.user_info)

    def _load_order_data(self):
        """Загружает данные заказа для определения состояния кнопок."""
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM orders WHERE id = %s", (self.order_id,)) # type: ignore
                    self.order_data = cur.fetchone() # type: ignore
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить данные заказа: {e}", parent=self)
            self.destroy()

    def _create_widgets(self):
        frame = ttk.Frame(self, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        # --- ИЗМЕНЕНИЕ: Реорганизация кнопок в несколько логических рядов ---
        # Ряд 1: Основной флоу получения кодов
        flow_panel = ttk.Frame(frame)
        flow_panel.pack(fill=tk.X, pady=2)
        self.request_codes_btn = ttk.Button(flow_panel, text="Запросить коды", command=self._request_codes_flow)
        self.request_codes_btn.pack(side=tk.LEFT, padx=2)
        self.get_codes_btn = ttk.Button(flow_panel, text="Получить коды", command=self._get_codes_flow)
        self.get_codes_btn.pack(side=tk.LEFT, padx=2)
        self.split_runs_btn = ttk.Button(flow_panel, text="Разбить на тиражи", command=self._split_runs)
        self.split_runs_btn.pack(side=tk.LEFT, padx=2)
        self.prepare_json_btn = ttk.Button(flow_panel, text="Подготовить JSON", command=self._prepare_json)
        self.prepare_json_btn.pack(side=tk.LEFT, padx=2)
        self.download_codes_btn = ttk.Button(flow_panel, text="Скачать коды", command=self._download_codes)
        self.download_codes_btn.pack(side=tk.LEFT, padx=2)

        # Ряд 2: Флоу отчетности
        reporting_panel = ttk.Frame(frame)
        reporting_panel.pack(fill=tk.X, pady=2)
        self.prepare_report_data_btn = ttk.Button(reporting_panel, text="Подготовить сведения", command=self._prepare_report_data)
        self.prepare_report_data_btn.pack(side=tk.LEFT, padx=2)
        self.prepare_report_btn = ttk.Button(reporting_panel, text="Подготовить отчет", command=self._prepare_report)
        self.prepare_report_btn.pack(side=tk.LEFT, padx=2)

        # --- НОВЫЙ БЛОК: Кнопки в зависимости от post_processing_mode ---
        # Ряд 3: Интеграция с внешним ПО (появляется только при необходимости)
        if self.post_processing_mode == "Внешнее ПО":
            integration_panel = ttk.Frame(frame)
            integration_panel.pack(fill=tk.X, pady=2)
       
        # --- НОВЫЙ БЛОК: Поле для вывода ответа от API ---
        response_frame = ttk.LabelFrame(frame, text="Ответ API")
        response_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.response_text = tk.Text(response_frame, wrap="word", height=10, state="disabled")
        scrollbar = ttk.Scrollbar(response_frame, orient="vertical", command=self.response_text.yview)
        self.response_text.configure(yscrollcommand=scrollbar.set)
        self.response_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._update_buttons_state()

    def _update_buttons_state(self):
        """Обновляет состояние кнопок в зависимости от статуса заказа."""
        if self.order_data:
            api_order_id = self.order_data.get('api_order_id')
            api_status = self.order_data.get('api_status')
 
            # --- ИЗМЕНЕНИЕ: Сначала прячем ВСЕ кнопки ---
            all_buttons = [
                self.request_codes_btn, self.get_codes_btn, self.split_runs_btn, self.prepare_json_btn,
                self.download_codes_btn, self.prepare_report_data_btn, self.prepare_report_btn
            ]
            for btn in all_buttons:
                btn.pack_forget()
 
            # --- НОВАЯ ЛОГИКА: Если заказ еще не в API, показываем только одну кнопку ---
            if not api_order_id or not api_status:
                self.request_codes_btn.pack(side=tk.LEFT, padx=2)
            # --- НОВАЯ ЛОГИКА: Если запрос создан, показываем ТОЛЬКО кнопку "Получить коды" ---
            elif api_status == 'Запрос создан':
                self.get_codes_btn.pack(side=tk.LEFT, padx=2)
            else:
                # --- Логика для всех остальных статусов ---
                if api_status == 'Тиражи созданы':
                    self.prepare_json_btn.pack(side=tk.LEFT, padx=2)
                
                self.download_codes_btn.pack(side=tk.LEFT, padx=2)
                self.prepare_report_data_btn.pack(side=tk.LEFT, padx=2)
                self.prepare_report_btn.pack(side=tk.LEFT, padx=2)
                # Управляем состоянием (активна/неактивна)
                self.download_codes_btn.config(state="normal" if api_status in ['JSON заказан', 'Коды скачаны', 'Сведения подготовлены', 'Отчет подготовлен'] else "disabled")
                self.prepare_report_data_btn.config(state="normal" if api_status in ['JSON заказан', 'Коды скачаны'] else "disabled")
                self.prepare_report_btn.config(state="normal" if api_status == 'Сведения подготовлены' else "disabled")

    def _display_api_response(self, status_code, body):
        """Отображает ответ API в текстовом поле, безопасно преобразуя тело ответа в строку."""
        self.response_text.config(state="normal")
        self.response_text.delete("1.0", tk.END)
        
        # --- ИСПРАВЛЕНИЕ: Гарантируем, что body является строкой перед отображением ---
        # Это предотвращает ошибку 'can only concatenate str (not "int") to str',
        # если body является словарем или другим не-строковым типом.
        if not isinstance(body, str):
            body = json.dumps(body, indent=2, ensure_ascii=False)

        response_content = f"Статус: {status_code}\n\nТело ответа:\n{body}"
        self.response_text.insert(tk.END, response_content)
        self.response_text.config(state="disabled")

    def _append_log(self, message):
        """Добавляет сообщение в лог в текстовом поле."""
        self.response_text.config(state="normal")
        self.response_text.insert(tk.END, f"\n{message}")
        self.response_text.see(tk.END) # Прокрутка вниз
        self.response_text.config(state="disabled")
        self.update_idletasks() # Обновляем UI

    def _request_codes_flow(self):
        """
        Выполняет полную цепочку: создание заказа (если нужно), пауза, создание запроса на коды.
        """
        def task():
            try:
                # Шаг 1: Проверка токена (любой запрос, например get_participants, проверит и обновит токен при необходимости)
                self.after(0, lambda: self._display_api_response(200, "Шаг 1/7: Проверка токена API..."))
                self.api_service.get_participants() # Этот вызов проверит и при необходимости обновит токен
                self.after(0, lambda: self._append_log("Токен API в порядке."))

                # Шаг 2 и 3: Проверка и создание заказа в API
                api_order_id = self.order_data.get('api_order_id')
                if not api_order_id:
                    self.after(0, lambda: self._append_log("\nШаг 2-3/7: Создание заказа в API..."))
                    with self._get_client_db_connection() as conn:
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                            cur.execute("SELECT o.notes, pg.dm_template, o.client_api_id FROM orders o JOIN dmkod_product_groups pg ON o.product_group_id = pg.id WHERE o.id = %s", (self.order_id,))
                            order_info = cur.fetchone()
                            cur.execute("SELECT gtin, dm_quantity FROM dmkod_aggregation_details WHERE order_id = %s", (self.order_id,))
                            products_data = cur.fetchall()
                    products_df = pd.DataFrame(products_data).groupby('gtin').agg(dm_quantity=('dm_quantity', 'sum')).reset_index()
                    products_payload = [{"gtin": p['gtin'], "code_template": order_info['dm_template'], "qty": int(p['dm_quantity']), "unit_type": "UNIT", "release_method": "IMPORT", "payment_type": 2} for _, p in products_df.iterrows()]
                    api_payload = {"participant_id": order_info['client_api_id'], "production_order_id": order_info['notes'] or "", "contact_person": self.user_info['name'], "products": products_payload}
                    
                    self.after(0, lambda p=api_payload: self._append_log(f"Тело запроса на создание заказа:\n{json.dumps(p, indent=2, ensure_ascii=False)}"))
                    response_data = self.api_service.create_order(api_payload)
                    api_order_id = response_data.get('order_id')
                    if not api_order_id: raise Exception(f"API не вернуло ID заказа: {response_data}")

                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE orders SET api_order_id = %s WHERE id = %s", (api_order_id, self.order_id))
                        conn.commit()
                    self.order_data['api_order_id'] = api_order_id
                    self.after(0, lambda: self._append_log(f"Заказ в API создан с ID: {api_order_id}"))
                else:
                    self.after(0, lambda: self._append_log(f"\nШаг 2-3/7: Заказ ID {api_order_id} уже существует."))

                # Шаг 4: Ожидание активации заказа
                self.after(0, lambda: self._append_log(f"\nШаг 4/7: Ожидание активации заказа ID {api_order_id}..."))
                max_wait_time, check_interval = 300, 5
                start_time = time.time()
                while time.time() - start_time < max_wait_time:
                    details = self.api_service.get_order_details(api_order_id)
                    # --- ИЗМЕНЕНИЕ: Более безопасная проверка ответа от API ---
                    # Если 'orders' пуст, просто продолжаем цикл ожидания.
                    orders_list = details.get('orders', [])
                    if not orders_list:
                        self.after(0, lambda: self._append_log("Ожидание данных заказа от API..."))
                        time.sleep(check_interval)
                        continue
                    order_obj = orders_list[0]
                    products = order_obj.get('products', [])
                    order_active = order_obj.get('state') == 'ACTIVE'
                    products_active = all(p.get('state') == 'ACTIVE' for p in products)

                    if order_active and products_active:
                        self.after(0, lambda: self._append_log("Заказ и все продукты активны."))
                        break
                    
                    self.after(0, lambda: self._append_log(f"Ожидание... (проверка через {check_interval} сек)"))
                    time.sleep(check_interval)
                else:
                    # --- ИЗМЕНЕНИЕ: Более информативное сообщение об ошибке ---
                    final_details_str = json.dumps(details, indent=2, ensure_ascii=False)
                    error_message = f"Время ожидания активации заказа истекло. Последний ответ от API:\n{final_details_str}"
                    raise Exception(error_message)

                # Шаг 5: Создание запроса на коды
                self.after(0, lambda: self._append_log(f"\nШаг 5/7: Создание запроса на коды..."))
                suborder_req_payload = {"order_id": int(api_order_id)}
                suborder_req_response = self.api_service.create_suborder_request(suborder_req_payload)
                self.after(0, lambda r=suborder_req_response: self._append_log(f"Ответ API: {json.dumps(r, ensure_ascii=False)}"))

                # Шаг 6: Получение сводки для пользователя
                suborders_to_sign = []
                total_codes = 0
                gtin_summary = {}

                # --- НОВАЯ ЛОГИКА: Цикл ожидания активного подзаказа ---
                self.after(0, lambda: self._append_log(f"\nШаг 6/7: Ожидание активного запроса на коды..."))
                max_wait_time, check_interval = 120, 3 # Ждем до 2 минут
                start_time = time.time()
                while time.time() - start_time < max_wait_time:
                    suborders_details = self.api_service.get_suborders(api_order_id)
                    for order in suborders_details.get('orders', []):
                        for suborder in order.get('suborders', []):
                            if suborder.get('state') == 'ACTIVE':
                                suborders_to_sign.append(suborder)
                                for product in suborder.get('suborder_products', []):
                                    qty = product.get('qty', 0)
                                    gtin = product.get('gtin')
                                    total_codes += qty
                                    if gtin:
                                        gtin_summary[gtin] = gtin_summary.get(gtin, 0) + qty
                    if suborders_to_sign:
                        break # Выходим из цикла, если нашли активный запрос
                    self.after(0, lambda: self._append_log(f"Ожидание... (проверка через {check_interval} сек)"))
                    time.sleep(check_interval)
                # --- КОНЕЦ НОВОЙ ЛОГИКИ ---
                
                if not suborders_to_sign:
                    raise Exception(f"Не найдено активных запросов к подписи после их создания. Последний ответ от API: {json.dumps(suborders_details, indent=2, ensure_ascii=False)}")

                summary_text = f"\n--- Сводка для подписи ---\n"
                summary_text += f"Найдено запросов к подписи: {len(suborders_to_sign)}\n"
                summary_text += f"Общее количество кодов: {total_codes}\n\n"
                summary_text += "Детализация по GTIN:\n"
                for gtin, qty in gtin_summary.items():
                    summary_text += f"  - GTIN: {gtin}, Кол-во: {qty}\n"
                self.after(0, lambda: self._append_log(summary_text))

                # --- НОВЫЙ БЛОК: Показываем пользователю явное уведомление ---
                final_message = (
                    f"Запрос на {total_codes} кодов успешно создан в ДМ.Код.\n\n"
                    "Пожалуйста, перейдите на сайт ДМ.Код и подпишите созданный запрос с помощью ЭЦП."
                )
                self.after(0, lambda: messagebox.showinfo("Требуется действие", final_message, parent=self))

                # Шаг 7: Финальное обновление статуса в локальной БД
                with self._get_client_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE orders SET api_status = 'Запрос создан' WHERE id = %s", (self.order_id,))
                    conn.commit()
                self.order_data['api_status'] = 'Запрос создан'
                self.after(0, self._update_buttons_state)

            except Exception as e:
                error_body = f"ОШИБКА: {e}\n\n{traceback.format_exc()}"
                # --- НОВАЯ ЛОГИКА: Сброс статуса при ошибке ---
                try:
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE orders SET api_status = NULL WHERE id = %s", (self.order_id,))
                        conn.commit()
                    self.order_data['api_status'] = None
                    self.after(0, lambda: self._append_log("\nОШИБКА: Статус заказа сброшен. Попробуйте выполнить запрос снова."))
                except Exception as db_err:
                    self.after(0, lambda: self._append_log(f"\nКРИТИЧЕСКАЯ ОШИБКА: Не удалось сбросить статус заказа в БД: {db_err}"))
                self.after(0, lambda: self._display_api_response(500, error_body))
            finally:
                self.after(0, lambda: self.request_codes_btn.config(state="normal"))
        
        self._run_in_thread(task)

    def _get_codes_flow(self):
        """
        Выполняет полную цепочку: разбивка на тиражи, подготовка JSON, скачивание кодов.
        """
        def task():
            # Запускаем задачи последовательно, каждая из них теперь содержит свою логику ожидания.
            # self.after(0, lambda: self._display_api_response(200, "Запуск полного цикла получения кодов..."))
            if self._split_runs_task(show_final_message=False):
                if self._prepare_json_task(show_final_message=False):
                    self._download_codes_task()

        self._run_in_thread(task)

    def _run_in_thread(self, target_func):
        """Запускает функцию в отдельном потоке, чтобы не блокировать UI."""
        thread = threading.Thread(target=target_func, daemon=True)
        thread.start()

    def _split_runs(self):
        self._run_in_thread(self._split_runs_task)

    def _split_runs_task(self, show_final_message=True):
        """Задача для разбиения заказа на тиражи."""
        self.after(0, lambda: self._display_api_response(200, "Начинаю создание тиражей..."))
        try:            
            api_order_id = self.order_data.get('api_order_id')

            # --- НОВЫЙ ШАГ: Синхронизация существующих активных тиражей из API ---
            self.after(0, lambda: self._append_log("Шаг 1: Синхронизация активных тиражей из API..."))
            try:
                # 1. Запрашиваем актуальные данные из API
                self.after(0, lambda: self._append_log(f"  Запрос деталей для заказа API ID: {api_order_id}"))
                # --- ИСПРАВЛЕНИЕ: Используем метод get_printruns, который обращается к эндпоинту psp/printruns ---
                api_printruns = self.api_service.get_printruns(api_order_id)

                # --- ИСПРАВЛЕНИЕ: Извлекаем список тиражей из словаря по ключу 'printruns' ---
                if not isinstance(api_printruns_response, dict) or 'printruns' not in api_printruns_response:
                    raise Exception(f"Ответ API не содержит ожидаемый ключ 'printruns'. Получен ответ: {api_printruns_response}")
                
                api_printruns = api_printruns_response.get('printruns', [])

                gtin_to_active_run_id = {}
                for printrun in api_printruns: # Теперь api_printruns - это точно список
                    if printrun.get('state') == 'ACTIVE':
                        gtin = printrun.get('gtin')
                        if gtin:
                            gtin_to_active_run_id[gtin] = printrun.get('id')
                # 3. Полностью очищаем старые api_id для этого заказа.
                self.after(0, lambda: self._append_log("  Очистка старых ID тиражей в локальной БД..."))
                with self._get_client_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE dmkod_aggregation_details SET api_id = NULL WHERE order_id = %s", (self.order_id,))
                    conn.commit()
                self.after(0, lambda: self._append_log("  Очистка завершена."))

                # 4. Теперь, когда все очищено, обновляем базу ID активных тиражей, если они были найдены.
                if gtin_to_active_run_id:
                    self.after(0, lambda: self._append_log(f"  Найдено {len(gtin_to_active_run_id)} активных тиражей в API. Обновление локальной БД..."))
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            for gtin, run_id in gtin_to_active_run_id.items():
                                cur.execute("UPDATE dmkod_aggregation_details SET api_id = %s WHERE order_id = %s AND gtin = %s", (run_id, self.order_id, gtin))
                                self.after(0, lambda g=gtin, r=run_id: self._append_log(f"    -> GTIN {g} установлен api_id = {r}"))
                        conn.commit()
                    self.after(0, lambda: self._append_log("  Обновление локальной БД завершено."))
                else:
                    self.after(0, lambda: self._append_log("  Активных тиражей в API не найдено. Поле api_id оставлено пустым."))

                # --- ВРЕМЕННАЯ МЕРА: Останавливаем выполнение для проверки ---
                self.after(0, lambda: self._append_log("\nСинхронизация завершена. Дальнейшая обработка остановлена для проверки. Можете запускать процесс снова."))
                return False # Прерываем выполнение функции

            except Exception as sync_err:
                raise Exception(f"Ошибка на шаге синхронизации активных тиражей: {sync_err}")

            post_processing_mode = self.post_processing_mode

            self.after(0, lambda: self._append_log(f"Режим постобработки: '{post_processing_mode}'"))

            # --- НОВАЯ ЛОГИКА: Получение данных в зависимости от post_processing_mode ---
            if post_processing_mode == "Печать через Bartender":
                self.after(0, lambda: self._append_log("Получение всех строк из dmkod_aggregation_details."))
                with self._get_client_db_connection() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT id, gtin, dm_quantity, api_id FROM dmkod_aggregation_details WHERE order_id = %s", (self.order_id,))
                        details_data = cur.fetchall()
            else: # Для всех остальных режимов
                self.after(0, lambda: self._append_log("Группировка данных из dmkod_aggregation_details по GTIN."))
                with self._get_client_db_connection() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        # --- ИСПРАВЛЕНИЕ: Группируем по gtin и api_id, чтобы не создавать дубликаты тиражей ---
                        cur.execute("""
                            SELECT gtin, api_id, SUM(dm_quantity) as dm_quantity
                            FROM dmkod_aggregation_details WHERE order_id = %s
                            GROUP BY gtin, api_id
                        """, (self.order_id,))
                        details_data = cur.fetchall()

            if not details_data:
                raise Exception("В заказе нет детализации для создания тиражей.")
            details_df = pd.DataFrame(details_data)
            self.after(0, lambda: self._append_log(f"Найдено {len(details_df)} позиций в локальной БД."))


            # Шаг 2: Получаем детали заказа из API
            self.after(0, lambda: self._append_log("Получение деталей заказа из API..."))
            order_details_from_api = self.api_service.get_order_details(api_order_id)
            api_products = order_details_from_api.get('orders', [{}])[0].get('products', [])
            if not api_products:
                raise Exception("API не вернуло список продуктов в заказе.")

            # Шаг 3: Сопоставляем GTIN и api_product_id, используя более строгое условие
            gtin_to_api_product_id = {
                p['gtin']: p['id'] for p in api_products
                if p.get('state') == 'ACTIVE' and p.get('qty') == p.get('qty_received')
            }
            details_df['api_product_id'] = details_df['gtin'].map(gtin_to_api_product_id)
            self.after(0, lambda: self._append_log("Сопоставление продуктов с API завершено."))

            # --- ДОБАВЛЕНО: Шаг 3.5 - Обновление справочника товаров, как в веб-версии ---
            products_to_upsert = [{'gtin': p['gtin'], 'name': p['name']} for p in api_products if p.get('name')]
            if products_to_upsert:
                self.after(0, lambda: self._append_log("Обновление локального справочника товаров..."))
                from .utils import upsert_data_to_db
                upsert_df = pd.DataFrame(products_to_upsert)
                with self._get_client_db_connection() as conn:
                    with conn.cursor() as cur:
                        upsert_data_to_db(cur, 'products', upsert_df, 'gtin')
                    conn.commit()

            # Шаг 4: Цикл создания тиражей
            for i, row in details_df.iterrows():
                if pd.notna(row.get('api_id')):
                    self.after(0, lambda r=row: self._append_log(f"Пропуск GTIN {r['gtin']}, тираж уже существует (ID: {r['api_id']})."))
                    continue
                
                api_product_id = row.get('api_product_id')
                if pd.isna(api_product_id):
                    self.after(0, lambda r=row: self._append_log(f"Пропуск GTIN {r['gtin']}, не найден активный продукт в API."))
                    continue

                self.after(0, lambda r=row: self._append_log(f"--- Создаю тираж для GTIN {r['gtin']}..."))
                
                # --- ИЗМЕНЕНИЕ: Добавляем обработку специфичной ошибки 400 ---
                try:
                    tirage_payload = {"order_product_id": int(api_product_id), "qty": int(row['dm_quantity'])}
                    response_data = self.api_service.create_printrun(tirage_payload) # type: ignore
                    new_printrun_id = response_data.get('printrun_id')

                    if not new_printrun_id:
                        raise Exception(f"API не вернуло 'printrun_id' для GTIN {row['gtin']}.")
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 400:
                        # Если это ошибка 400, прерываем цикл и выводим дружелюбное сообщение
                        self.after(0, lambda: self._append_log("\nAPI вернуло ошибку. Вероятно, система еще обрабатывает предыдущий запрос."))
                        self.after(0, lambda: self._append_log("Пожалуйста, подождите несколько минут и запустите операцию 'Разбить на тиражи' еще раз."))
                        self.after(0, self._update_buttons_state)
                        return False # Выходим из функции _split_runs_task
                    else:
                        raise # Если другая ошибка, пробрасываем ее дальше
                
                # Обновляем нашу БД
                if 'id' in row and pd.notna(row['id']): # Режим "Печать через Bartender" - обновляем по ID строки
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE dmkod_aggregation_details SET api_id = %s WHERE id = %s", (new_printrun_id, row['id']))
                        conn.commit()
                else: # Остальные режимы - обновляем все строки с этим GTIN для данного заказа
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE dmkod_aggregation_details SET api_id = %s WHERE order_id = %s AND gtin = %s",
                                (new_printrun_id, self.order_id, row['gtin'])
                            )
                        conn.commit()
                self.after(0, lambda r=row, p_id=new_printrun_id: self._append_log(f"  Успешно создан тираж ID {p_id} для GTIN {r['gtin']}."))
                
                # --- НОВАЯ ЛОГИКА: Ожидание готовности тиража ---
                self.after(0, lambda: self._append_log("  Ожидание обработки тиража сервером..."))
                max_wait_time, check_interval = 120, 5
                start_time = time.time()
                while time.time() - start_time < max_wait_time:
                    printruns_details = self.api_service.get_order_details(api_order_id) # type: ignore
                    # --- ИСПРАВЛЕНИЕ: Ждем, пока конкретный созданный тираж станет ACTIVE ---
                    # Предыдущая логика проверяла все тиражи на статус 'AWAITING', что было некорректно,
                    # так как тираж мог быть в другом промежуточном состоянии (например, 'PROCESSING').
                    current_printrun_state = None
                    for order in printruns_details.get('orders', []):
                        for printrun in order.get('printruns', []):
                            if printrun.get('id') == new_printrun_id:
                                current_printrun_state = printrun.get('state')
                                break
                        if current_printrun_state is not None: break
                    
                    if current_printrun_state == 'ACTIVE':
                        self.after(0, lambda p_id=new_printrun_id: self._append_log(f"  Тираж {p_id} успешно активирован."))
                        break
                    
                    self.after(0, lambda s=current_printrun_state: self._append_log(f"  Ожидание активации тиража {new_printrun_id} (статус: {s}). Проверка через {check_interval} сек..."))
                    time.sleep(check_interval)
                else: # если вышли по таймауту
                    raise Exception(f"Время ожидания активации тиража {new_printrun_id} истекло.")

            # Шаг 5: Обновление статуса заказа
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'Тиражи созданы' WHERE id = %s", (self.order_id,))
                conn.commit()
            
            self.order_data['api_status'] = 'Тиражи созданы'
            if show_final_message:
                self.after(0, lambda: self._display_api_response(200, "Все тиражи успешно созданы!"))
            self.after(0, self._update_buttons_state)
            return True

        except Exception as e:
            self.after(0, lambda: self._append_log(f"\nОШИБКА: {e}\n{traceback.format_exc()}"))
            self.after(0, lambda err=e: self._display_api_response(500, f"ОШИБКА: {err}"))
            self.after(0, self._update_buttons_state)
            return False

    def _prepare_json(self):
        self._run_in_thread(self._prepare_json_task)

    def _prepare_json_task(self, show_final_message=True):
        self.after(0, lambda: self._display_api_response(200, "Начинаю подготовку JSON..."))
        try:
            api_order_id = self.order_data.get('api_order_id')
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT api_id FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL", (self.order_id,))
                    # --- ИСПРАВЛЕНИЕ: Получаем только уникальные ID тиражей ---
                    # Это предотвращает отправку дублирующихся запросов, если несколько строк
                    # детализации относятся к одному сгруппированному тиражу.
                    unique_printrun_ids = {item['api_id'] for item in cur.fetchall()}
            
            if not unique_printrun_ids:
                raise Exception("Не найдено уникальных ID тиражей для обработки.")

            for i, printrun_id in enumerate(unique_printrun_ids):
                self.after(0, lambda p_id=printrun_id, num=i+1: self._append_log(f"--- {num}/{len(unique_printrun_ids)}: Запрос JSON для тиража ID {p_id}..."))
                self.api_service.create_printrun_json({"printrun_id": printrun_id}) # type: ignore
                self.after(0, lambda p_id=printrun_id: self._append_log(f"  Запрос для тиража {p_id} успешно отправлен."))
                time.sleep(0.5)

            # --- НОВАЯ ЛОГИКА: Ожидание готовности JSON ---
            self.after(0, lambda: self._append_log("\nОжидание генерации JSON сервером..."))
            max_wait_time, check_interval = 300, 5
            start_time = time.time()
            while time.time() - start_time < max_wait_time:
                printruns_details = self.api_service.get_order_details(api_order_id) # type: ignore
                
                # --- ИСПРАВЛЕНИЕ: Проверяем готовность JSON только для тех тиражей, которые мы запросили ---
                api_printruns_json_status = {p['id']: p.get('json', False) for order in printruns_details.get('orders', []) for p in order.get('printruns', [])}

                all_requested_json_ready = True
                for run_id in unique_printrun_ids:
                    if not api_printruns_json_status.get(run_id, False):
                        all_requested_json_ready = False
                        self.after(0, lambda r_id=run_id: self._append_log(f"  JSON для тиража {r_id} еще не готов."))
                        break # Нет смысла проверять дальше
                
                if all_requested_json_ready:
                    self.after(0, lambda: self._append_log("  Все JSON-файлы готовы."))
                    break
                
                self.after(0, lambda: self._append_log(f"  Проверка через {check_interval} сек..."))
                time.sleep(check_interval)
            else: # если вышли по таймауту
                raise Exception("Время ожидания готовности JSON истекло.")

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'JSON заказан' WHERE id = %s", (self.order_id,))
                conn.commit()
            
            self.order_data['api_status'] = 'JSON заказан'
            if show_final_message:
                self.after(0, lambda: self._display_api_response(200, "Все запросы на подготовку JSON успешно отправлены!"))
            self.after(0, self._update_buttons_state)
            return True

        except Exception as e:
            self.after(0, lambda: self._append_log(f"\nОШИБКА: {e}\n{traceback.format_exc()}"))
            self.after(0, lambda err=e: self._display_api_response(500, f"ОШИБКА: {err}"))
            self.after(0, self._update_buttons_state)
            return False

    def _download_codes(self):
        self._run_in_thread(self._download_codes_task)

    def _download_codes_task(self):
        self.after(0, lambda: self._display_api_response(200, "Начинаю скачивание кодов..."))
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT id, api_id, gtin FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL", (self.order_id,))
                    details_to_process = cur.fetchall()
            
            if not details_to_process:
                raise Exception("Не найдено тиражей для скачивания кодов.")

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    for i, detail in enumerate(details_to_process):
                        self.after(0, lambda d=detail, num=i+1: self._append_log(f"--- {num}/{len(details_to_process)}: Запрос кодов для тиража ID {d['api_id']}..."))
                        response_data = self.api_service.download_printrun_json({"printrun_id": detail['api_id']}) # type: ignore
                        codes = response_data.get('codes', [])
                        if not codes:
                            self.after(0, lambda d=detail: self._append_log(f"  Коды для тиража {d['api_id']} еще не готовы или отсутствуют."))
                            continue
                        
                        cur.execute(
                            "UPDATE dmkod_aggregation_details SET api_codes_json = %s WHERE id = %s",
                            (json.dumps({'codes': codes}), detail['id'])
                        )
                        self.after(0, lambda c=len(codes), d_id=detail['id']: self._append_log(f"  Сохранено {c} кодов в БД для строки ID {d_id}."))

                conn.commit() # Фиксируем сохранение всех JSON

            # Обновляем статус после успешного сохранения
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'Коды скачаны' WHERE id = %s", (self.order_id,))
                conn.commit()
            self.order_data['api_status'] = 'Коды скачаны'
            self.after(0, lambda: self._display_api_response(200, "Все коды успешно сохранены в базу данных."))
            self.after(0, self._update_buttons_state)

        except Exception as e:
            self.after(0, lambda: self._append_log(f"\nОШИБКА: {e}\n{traceback.format_exc()}"))
            self.after(0, lambda err=e: self._display_api_response(500, f"ОШИБКА: {err}"))
            self.after(0, self._update_buttons_state)

    def _prepare_report_data(self):
        self._run_in_thread(self._prepare_report_data_task)

    def _prepare_report_data_task(self):
        """Задача для подготовки сведений для отчета."""
        self.after(0, lambda: self._display_api_response(200, "Начинаю подготовку сведений для отчета..."))
        try:
            self.after(0, lambda: self._append_log(f"Статус заказа: {self.order_data.get('status')}"))
            order_status = self.order_data.get('status')

            if order_status == 'delta':
                # Логика для статуса 'delta'
                with self._get_client_db_connection() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT id, printrun_id, codes_json FROM delta_result WHERE order_id = %s AND utilisation_upload_id IS NULL", (self.order_id,))
                        results_to_process = cur.fetchall()

                if not results_to_process:
                    self.after(0, lambda: self._display_api_response(200, "Нет новых данных от 'Дельта' для отправки."))
                    # Обновляем статус, даже если нет данных
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE orders SET api_status = 'Сведения подготовлены' WHERE id = %s", (self.order_id,))
                        conn.commit()
                    self.order_data['api_status'] = 'Сведения подготовлены'
                    self.after(0, lambda: self._display_api_response(200, "Статус заказа обновлен: 'Сведения подготовлены' (нет новых данных от 'Дельта')."))
                    return
                else: # Если данные есть, обрабатываем их и обновляем статус
                    self.after(0, lambda: self._append_log(f"Найдено {len(results_to_process)} записей от 'Дельта' для обработки."))

                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            for i, result in enumerate(results_to_process):
                                payload = result['codes_json']
                                self.after(0, lambda i=i, r=result: self._append_log(f"--- {i+1}/{len(results_to_process)}: Отправка данных для тиража ID {r['printrun_id']} ---"))
                                
                                # Вызов API
                                response_data = self.api_service.upload_utilisation_data(payload)
                                
                                # --- ИСПРАВЛЕНИЕ: Получаем ID из ответа API ---
                                upload_id_from_api = response_data.get('utilisation_upload_id')
                                if not upload_id_from_api:
                                    raise ValueError(f"API не вернуло 'utilisation_upload_id' в ответе: {response_data}")

                                # Обновляем запись в delta_result, используя ID из API
                                cur.execute("UPDATE delta_result SET utilisation_upload_id = %s WHERE id = %s", (upload_id_from_api, result['id']))
                                
                                self.after(0, lambda r=response_data: self._append_log(f"  Ответ API: {json.dumps(r, ensure_ascii=False)}"))
                                self.after(0, lambda r=upload_id_from_api: self._append_log(f"  Записи присвоен ID из API: {r}"))
                                time.sleep(5)
                        conn.commit()
                    
                    # Обновляем статус заказа после успешной обработки
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE orders SET api_status = 'Сведения подготовлены' WHERE id = %s", (self.order_id,))
                        conn.commit()
                    self.order_data['api_status'] = 'Сведения подготовлены'
                    self.after(0, lambda: self._display_api_response(200, "Все сведения успешно отправлены!"))
                return
            elif order_status == 'dmkod':
                # Логика для статуса 'dmkod'
                with self._get_client_db_connection() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        # --- ИЗМЕНЕНИЕ: Добавляем o.fias_code в SELECT ---
                        cur.execute("""
                            SELECT d.api_id, d.production_date, d.expiry_date, d.id as detail_id, o.fias_code
                            FROM dmkod_aggregation_details d
                            JOIN orders o ON d.order_id = o.id
                            WHERE d.order_id = %s AND d.api_id IS NOT NULL AND d.utilisation_upload_id IS NULL
                        """, (self.order_id,))
                        details_to_process = cur.fetchall()

                if not details_to_process:
                    self.after(0, lambda: self._display_api_response(200, "Нет новых тиражей для отправки сведений."))
                    # Обновляем статус, даже если нет данных
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE orders SET api_status = 'Сведения подготовлены' WHERE id = %s", (self.order_id,))
                        conn.commit()
                    self.order_data['api_status'] = 'Сведения подготовлены'
                    self.after(0, lambda: self._display_api_response(200, "Статус заказа обновлен: 'Сведения подготовлены' (нет новых тиражей)."))
                    return
                else: # Если данные есть, обрабатываем их и обновляем статус
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            for i, detail in enumerate(details_to_process):
                                attributes = {}
                                if detail.get('production_date'):
                                    attributes['production_date'] = detail['production_date'].strftime('%Y-%m-%d')
                                if detail.get('expiry_date'):
                                    attributes['expiration_date'] = detail['expiry_date'].strftime('%Y-%m-%d')
                                if detail.get('fias_code'):
                                    attributes['fias_id'] = detail['fias_code']

                                payload = {"all_from_printrun": detail['api_id']}
                                if attributes:
                                    payload['attributes'] = attributes
                                
                                response_data = self.api_service.upload_utilisation_data(payload)
                                
                                upload_id_from_api = response_data.get('utilisation_upload_id')
                                if not upload_id_from_api:
                                    raise ValueError(f"API не вернуло 'utilisation_upload_id' в ответе: {response_data}")

                                cur.execute("UPDATE dmkod_aggregation_details SET utilisation_upload_id = %s WHERE id = %s", (upload_id_from_api, detail['detail_id']))
                                self.after(0, lambda r=upload_id_from_api: self._append_log(f"  Записи присвоен ID из API: {r}"))
                                time.sleep(5)
                        conn.commit()

                    # Обновляем статус заказа после успешной обработки
                    with self._get_client_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE orders SET api_status = 'Сведения подготовлены' WHERE id = %s", (self.order_id,))
                        conn.commit()
                    self.order_data['api_status'] = 'Сведения подготовлены'
                    self.after(0, lambda: self._display_api_response(200, "Все сведения успешно отправлены!"))
                    return

        except Exception as e:
            self.after(0, lambda err=e: self._display_api_response(500, f"КРИТИЧЕСКАЯ ОШИБКА: {err}\n\n{traceback.format_exc()}"))
        finally:
            self.after(0, self._update_buttons_state)

    def _prepare_report(self):
        self._run_in_thread(self._prepare_report_task)

    def _prepare_report_task(self):
        # --- ИСПРАВЛЕНИЕ: Заменяем заглушку на реальную логику из веб-приложения ---
        self.after(0, lambda: self._display_api_response(200, "Начинаю подготовку отчета..."))
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, api_id, gtin FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL ORDER BY id",
                        (self.order_id,)
                    )
                    details_to_process = cur.fetchall()

            if not details_to_process:
                raise Exception("Не найдено позиций с ID тиража (api_id) для подготовки отчета.")

            self.after(0, lambda: self._append_log(f"Найдено {len(details_to_process)} позиций для подготовки отчета."))

            for i, detail in enumerate(details_to_process):
                payload = {"printrun_id": detail['api_id']}
                self.after(0, lambda i=i, d=detail: self._append_log(f"--- {i+1}/{len(details_to_process)}: Отправка запроса для GTIN {d['gtin']} (ID тиража: {d['api_id']}) ---"))
                
                # Вызываем новый метод в ApiService
                self.api_service.create_utilisation_report(payload)
                
                self.after(0, lambda d=detail: self._append_log(f"  Запрос для тиража {d['api_id']} успешно отправлен."))

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'Отчет подготовлен' WHERE id = %s", (self.order_id,))
                conn.commit()
            
            self.order_data['api_status'] = 'Отчет подготовлен'
            self.after(0, lambda: self._display_api_response(200, "Отчет успешно подготовлен!"))

        except Exception as e:
            self.after(0, lambda err=e: self._display_api_response(500, f"ОШИБКА: {err}"))
            self.after(0, self._update_buttons_state)

class OrderEditorFrame(ttk.Frame):
    """Фрейм для редактирования деталей заказа, встраиваемый во вкладку."""
    def __init__(self, parent, user_info, order_id, scenario_data=None):
        super().__init__(parent)

        self.user_info = user_info
        self.order_id = order_id
        self.scenario_data = scenario_data if scenario_data else {}

        self._create_widgets()
        self._load_details()

        # --- НОВОВВЕДЕНИЕ: Добавляем прогресс-бар для длительных операций ---
        self.progress_bar = ttk.Progressbar(self, orient='horizontal', mode='determinate')
        # Он будет показан только во время выполнения импорта.
        # self.progress_bar.pack(fill=tk.X, padx=10, pady=(0, 5), side=tk.BOTTOM)

    def _sign_with_eds(self):
        """Заглушка для функции подписания ЭЦП."""
        messagebox.showinfo("В разработке", "Функционал подписания запроса с помощью ЭЦП находится в разработке.", parent=self)

    def _sanitize_filename_part(self, text: str) -> str:
        """Очищает строку для безопасного использования в имени файла."""
        if not isinstance(text, str) or not text.strip():
            # Возвращаем имя по умолчанию, если текст пустой
            return "report"
        # Удаляем недопустимые символы
        sanitized = re.sub(r'[\\/*?:"<>|]', "", text)
        # Заменяем пробелы и прочие разделители на подчеркивание
        sanitized = re.sub(r'[\s\.]+', '_', sanitized)
        return sanitized.strip('_')

    def _get_client_db_connection(self):
        return get_client_db_connection(self.user_info)

    def _create_widgets(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- ИЗМЕНЕНИЕ: Создаем несколько фреймов для кнопок в несколько рядов ---
        # Ряд 1: Основные операции с детализацией
        controls_frame_1 = ttk.Frame(main_frame)
        controls_frame_1.pack(fill=tk.X, pady=2)
        ttk.Button(controls_frame_1, text="Сохранить изменения", command=self._save_changes).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame_1, text="Выгрузить в Excel", command=self._export_details_to_excel).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame_1, text="Загрузить из Excel", command=self._import_details_from_excel).pack(side=tk.LEFT, padx=2)

        # --- ИЗМЕНЕНИЕ: Разделяем логику кнопок в зависимости от сценария ---
        post_processing_mode = self.scenario_data.get('post_processing')

        # Ряд 2: Кнопки, зависящие от сценария
        controls_frame_2 = ttk.Frame(main_frame)
        controls_frame_2.pack(fill=tk.X, pady=2)

        if post_processing_mode == "Печать через Bartender":
            # Кнопки для работы со справочником товаров и Bartender View
            ttk.Button(controls_frame_2, text="Экспорт товаров", command=self._export_products_to_excel).pack(side=tk.LEFT, padx=2)
            ttk.Button(controls_frame_2, text="Импорт товаров", command=self._import_products_from_excel).pack(side=tk.LEFT, padx=2)
            ttk.Button(controls_frame_2, text="Создать/Обновить View", command=self._create_bartender_view).pack(side=tk.LEFT, padx=2)

        elif post_processing_mode == "Внешнее ПО":
            # Кнопки для выгрузки/загрузки данных для внешнего ПО
            ttk.Button(controls_frame_2, text="Экспорт данных", command=self._export_data_for_external_sw).pack(side=tk.LEFT, padx=2)
            ttk.Button(controls_frame_2, text="Импорт данных", command=self._import_data_for_external_sw).pack(side=tk.LEFT, padx=2)

        # --- НОВЫЙ БЛОК: Кнопка для отчета декларанта (не зависит от сценария) ---
        # Ряд 3: Отчеты
        controls_frame_3 = ttk.Frame(main_frame)
        controls_frame_3.pack(fill=tk.X, pady=2)
        ttk.Button(controls_frame_3, text="Скачать отчет декларанта", command=self._download_declarator_report).pack(side=tk.LEFT, padx=2)

        # --- НОВЫЙ БЛОК: Кнопка архивации в самом низу ---
        archive_frame = ttk.Frame(main_frame)
        # Размещаем этот фрейм в самом низу, с отступом сверху
        archive_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))
        ttk.Separator(archive_frame).pack(fill=tk.X, pady=5)
        ttk.Button(archive_frame, text="Перенести в архив", command=self._move_to_archive, style="Danger.TButton").pack(side=tk.RIGHT)


        # ttk.Button(controls_frame, text="Закрыть", command=self.destroy).pack(side=tk.RIGHT, padx=2)

        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.details_cols = ["id", "gtin", "dm_quantity", "aggregation_level", "production_date", "expiry_date"]
        self.details_tree = ttk.Treeview(tree_frame, columns=self.details_cols, show='headings')

        col_map = {
            "id": ("ID", 40, "center"), "gtin": ("GTIN", 140, "w"), "dm_quantity": ("Кол-во", 80, "e"),
            "aggregation_level": ("Агрегация", 80, "center"), "production_date": ("Дата произв.", 100, "center"),
            "expiry_date": ("Годен до", 100, "center")
        }
        for col, (text, width, anchor) in col_map.items():
            self.details_tree.heading(col, text=text)
            self.details_tree.column(col, width=width, anchor=anchor)

        self.details_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.details_tree.yview)
        self.details_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.details_tree.bind("<Double-1>", self._on_details_double_click)

    def _load_details(self):
        for i in self.details_tree.get_children(): self.details_tree.delete(i)
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM dmkod_aggregation_details WHERE order_id = %s ORDER BY id", (self.order_id,))
                    details = cur.fetchall()
            for item in details:
                values = [item.get(col, '') for col in self.details_cols]
                self.details_tree.insert('', 'end', iid=item['id'], values=values)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить детали заказа: {e}", parent=self)

    def _on_details_double_click(self, event):
        """Обработчик двойного клика для редактирования ячейки."""
        region = self.details_tree.identify("region", event.x, event.y)
        if region != "cell": return

        column_id = self.details_tree.identify_column(event.x)
        column_index = int(column_id.replace('#', '')) - 1
        item_id = self.details_tree.focus()
        
        x, y, width, height = self.details_tree.bbox(item_id, column_id)

        entry_var = tk.StringVar()
        entry = ttk.Entry(self.details_tree, textvariable=entry_var)
        entry.place(x=x, y=y, width=width, height=height)
        
        current_value = self.details_tree.item(item_id, "values")[column_index]
        entry_var.set(current_value)
        entry.focus_set()

        def on_focus_out(event):
            new_value = entry_var.get()
            current_values = list(self.details_tree.item(item_id, "values"))
            current_values[column_index] = new_value
            self.details_tree.item(item_id, values=tuple(current_values))
            entry.destroy()

        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", on_focus_out)

    def _move_to_archive(self):
        """
        Перемещает текущий заказ в архив.
        Функционал адаптирован из `_create_orders_view`.
        """
        if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите переместить заказ №{self.order_id} в архив?", parent=self):
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Получаем текущий статус, чтобы правильно сформировать архивный
                    cur.execute("SELECT status FROM orders WHERE id = %s", (self.order_id,))
                    current_status = cur.fetchone()['status']
                    new_status = f"Архив_{current_status}"

                    cur.execute("UPDATE orders SET status = %s WHERE id = %s RETURNING notification_id", (new_status, self.order_id))
                    result = cur.fetchone()
                    notification_id = result['notification_id'] if result else None
                    if notification_id:
                        cur.execute("UPDATE ap_supply_notifications SET status = 'В архиве' WHERE id = %s", (notification_id,))
                conn.commit()
            messagebox.showinfo("Успех", "Заказ успешно перемещен в архив. Обновите список заказов.", parent=self)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось архивировать заказ: {e}", parent=self)

    def _save_changes(self):
        """Собирает данные из Treeview и сохраняет их в БД."""
        updates = []
        for item_id in self.details_tree.get_children():
            values = self.details_tree.item(item_id, "values")
            updates.append(dict(zip(self.details_cols, values)))
        
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    for item in updates:
                        cur.execute("""
                            UPDATE dmkod_aggregation_details SET
                                gtin = %s, dm_quantity = %s, aggregation_level = %s,
                                production_date = %s, expiry_date = %s
                            WHERE id = %s
                        """, (
                            item['gtin'], item['dm_quantity'], item['aggregation_level'],
                            item['production_date'] or None, item['expiry_date'] or None,
                            item['id']
                        ))
                conn.commit()
            messagebox.showinfo("Успех", "Изменения успешно сохранены.", parent=self)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить изменения: {e}", parent=self)

    def _export_details_to_excel(self):
        """Собирает данные из таблицы и выгружает их в Excel-файл."""
        logging.debug(f"Запуск экспорта детализации для заказа ID: {self.order_id}")
        try:
            items_to_export = []
            for item_id in self.details_tree.get_children():
                values = self.details_tree.item(item_id, "values")
                items_to_export.append(dict(zip(self.details_cols, values)))
            
            if not items_to_export:
                messagebox.showwarning("Внимание", "Нет данных для экспорта.", parent=self)
                return

            df = pd.DataFrame(items_to_export)
            
            filepath = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel", "*.xlsx")],
                initialfile=f"order_{self.order_id}_details.xlsx",
                parent=self
            )

            if filepath:
                df.to_excel(filepath, index=False)
                messagebox.showinfo("Успех", f"Детализация заказа успешно выгружена в файл:\n{filepath}", parent=self)

        except Exception as e:
            logging.error(f"Ошибка при экспорте детализации заказа {self.order_id}: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось экспортировать данные: {e}", parent=self)

    def _import_details_from_excel(self):
        """Заменяет детализацию из Excel-файла."""
        logging.debug(f"Запуск импорта детализации для заказа ID: {self.order_id}")
        if not messagebox.askyesno("Подтверждение", "Импорт из файла полностью заменит текущую детализацию заказа. Продолжить?", parent=self):
            return

        filepath = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")], parent=self)
        if not filepath:
            return

        try:
            df = pd.read_excel(filepath, dtype={'gtin': str})
            df = df.where(pd.notna(df), None) # Заменяем NaN на None для корректной вставки в БД

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Удаляем старую детализацию
                    cur.execute("DELETE FROM dmkod_aggregation_details WHERE order_id = %s", (self.order_id,))
                    logging.info(f"Старая детализация для заказа {self.order_id} удалена.")

                    # 2. Вставляем новые данные
                    from .utils import upsert_data_to_db # Локальный импорт
                    upsert_data_to_db(cur, 'dmkod_aggregation_details', df, ['order_id', 'gtin'])
                conn.commit()
            messagebox.showinfo("Успех", f"Детализация заказа успешно импортирована. Загружено {len(df)} строк.", parent=self)
            self._load_details() # Обновляем таблицу
        except Exception as e:
            logging.error(f"Ошибка при импорте детализации для заказа {self.order_id}: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось импортировать данные: {e}", parent=self)

    def _export_products_to_excel(self):
        """Выгружает в Excel данные о товарах, связанных с текущим заказом."""
        logging.info(f"Запуск экспорта товаров для заказа ID: {self.order_id}")
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # 1. Получаем уникальные GTIN из детализации заказа
                    cur.execute("SELECT DISTINCT gtin FROM dmkod_aggregation_details WHERE order_id = %s AND gtin IS NOT NULL", (self.order_id,))
                    gtins = [row['gtin'] for row in cur.fetchall()]
                    
                    if not gtins:
                        messagebox.showwarning("Внимание", "В заказе нет товаров для экспорта.", parent=self)
                        return

                    # 2. Получаем данные этих товаров из справочника products
                    cur.execute("SELECT gtin, name, description_1, description_2, description_3 FROM products WHERE gtin = ANY(%s)", (gtins,))
                    products_data = cur.fetchall()

            if not products_data:
                messagebox.showwarning("Внимание", "Не найдено записей в справочнике товаров для GTIN из этого заказа.", parent=self)
                return

            df = pd.DataFrame(products_data)
            filepath = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel", "*.xlsx")],
                initialfile=f"order_{self.order_id}_products.xlsx",
                parent=self
            )

            if filepath:
                df.to_excel(filepath, index=False)
                messagebox.showinfo("Успех", f"Товары заказа успешно выгружены в файл:\n{filepath}", parent=self)

        except Exception as e:
            logging.error(f"Ошибка при экспорте товаров заказа {self.order_id}: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось экспортировать товары: {e}", parent=self)

    def _import_products_from_excel(self):
        """Импортирует (обновляет) данные о товарах из Excel-файла в общий справочник."""
        logging.info(f"Запуск импорта товаров из файла.")
        if not messagebox.askyesno("Подтверждение", "Данные из файла обновят записи в общем справочнике товаров. Продолжить?", parent=self):
            return

        filepath = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")], parent=self)
        if not filepath:
            return

        try:
            df = pd.read_excel(filepath, dtype={'gtin': str})
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    from .utils import upsert_data_to_db
                    upsert_data_to_db(cur, 'products', df, 'gtin')
                conn.commit()
            messagebox.showinfo("Успех", f"Справочник товаров успешно обновлен. Обработано {len(df)} строк.", parent=self)
        except Exception as e:
            logging.error(f"Ошибка при импорте товаров: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось импортировать товары: {e}", parent=self)

    def _download_declarator_report(self):
        """
        Формирует и выгружает отчет для декларанта напрямую из БД, не создавая представлений.
        Адаптировано из datamatrix-app/app/services/view_service.py.
        """
        logging.info(f"Запуск формирования отчета декларанта для заказа ID: {self.order_id}")

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT notes FROM orders WHERE id = %s", (self.order_id,))
                    order_info = cur.fetchone()
                
                # Этот запрос объединяет логику создания base_view и sscc_view в один
                query = """
                WITH RECURSIVE base_data AS (
                    SELECT
                        i.datamatrix, i.gtin, i.package_id,
                        p.name AS product_name, p.description_1, p.description_2, p.description_3
                    FROM items i
                    LEFT JOIN products p ON i.gtin = p.gtin
                    WHERE i.order_id = %(order_id)s
                ),
                package_hierarchy AS (
                    SELECT
                        p.id as base_box_id, p.id as package_id, p.level, p.sscc, p.parent_id
                    FROM packages p
                    WHERE p.level = 1 AND p.id IN (SELECT DISTINCT package_id FROM base_data WHERE package_id IS NOT NULL)
                    UNION ALL
                    SELECT ph.base_box_id, p_parent.id as package_id, p_parent.level, p_parent.sscc, p_parent.parent_id
                    FROM package_hierarchy ph JOIN packages p_parent ON ph.parent_id = p_parent.id
                ),
                sscc_data AS (
                    SELECT
                        base_box_id AS id_level_1,
                        MAX(CASE WHEN level = 1 THEN sscc END) AS sscc_level_1,
                        MAX(CASE WHEN level = 2 THEN sscc END) AS sscc_level_2,
                        MAX(CASE WHEN level = 3 THEN sscc END) AS sscc_level_3
                    FROM package_hierarchy
                    GROUP BY base_box_id
                )
                SELECT
                    b.datamatrix,
                    b.gtin,
                    SUBSTRING(b.datamatrix for 24) AS dm_part_24,
                    SUBSTRING(b.datamatrix for 31) AS dm_part_31,
                    s.sscc_level_1,
                    s.sscc_level_2,
                    s.sscc_level_3,
                    b.product_name,
                    b.description_1,
                    b.description_2,
                    b.description_3
                FROM base_data b
                LEFT JOIN sscc_data s ON b.package_id = s.id_level_1
                ORDER BY b.datamatrix;
                """
                df = pd.read_sql(query, conn, params={'order_id': self.order_id})

            if df.empty:
                messagebox.showwarning("Нет данных", "Не найдено данных для формирования отчета.", parent=self)
                return

            # Очистка данных от недопустимых для Excel символов
            def clean_illegal_chars(val):
                if isinstance(val, str):
                    return val.replace('\x1d', ' ') # Заменяем символ GS на пробел
                return val
            df = df.applymap(clean_illegal_chars)

            report_name = self._sanitize_filename_part(order_info.get('notes') if order_info else '')

            filepath = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel", "*.xlsx")],
                initialfile=f"{report_name}_order_{self.order_id}.xlsx",
                parent=self
            )
            if filepath:
                df.to_excel(filepath, index=False)
                messagebox.showinfo("Успех", f"Отчет декларанта успешно сохранен в файл:\n{filepath}", parent=self)

        except Exception as e:
            logging.error(f"Ошибка при формировании отчета декларанта для заказа {self.order_id}: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось сформировать отчет: {e}", parent=self)

    def _export_data_for_external_sw(self):
        """Выгружает данные в формате 'Дельта' для внешнего ПО."""
        logging.info(f"Запуск экспорта данных в формате 'Дельта' для заказа ID: {self.order_id}")
        try:
            with self._get_client_db_connection() as conn:
                # --- НОВЫЙ БЛОК: Получаем 'notes' для имени файла ---
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT notes FROM orders WHERE id = %s", (self.order_id,))
                    order_info = cur.fetchone()
                # --- КОНЕЦ НОВОГО БЛОКА ---
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT api_codes_json, production_date, expiry_date
                        FROM dmkod_aggregation_details
                        WHERE order_id = %s AND api_codes_json IS NOT NULL
                        """,
                        (self.order_id,)
                    )
                    details_to_process = cur.fetchall()

                if not details_to_process:
                    messagebox.showwarning("Нет данных", "В заказе нет скачанных кодов для выгрузки.", parent=self)
                    return

                all_rows = []
                from dateutil.relativedelta import relativedelta

                for detail in details_to_process:
                    codes = detail.get('api_codes_json', {}).get('codes', [])
                    prod_date = detail.get('production_date')
                    exp_date = detail.get('expiry_date')

                    life_time_months = ''
                    if prod_date and exp_date:
                        delta = relativedelta(exp_date, prod_date)
                        life_time_months = delta.years * 12 + delta.months

                    for code in codes:
                        if not code or len(code) < 16: continue
                        all_rows.append({
                            'DataMatrix': code,
                            'DataMatrixCode': '',
                            'Barcode': code[2:16], # Извлекаем GTIN
                            'LifeTime': life_time_months
                        })

                if not all_rows:
                    messagebox.showwarning("Нет данных", "Не найдено корректных кодов для выгрузки.", parent=self)
                    return

                df = pd.DataFrame(all_rows)
                # --- ИЗМЕНЕНИЕ: Используем новую логику для имени файла ---
                report_name = self._sanitize_filename_part(order_info.get('notes') if order_info else '')
                initial_filename = f"{report_name}_order_{self.order_id}.csv"
                
                filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV (Tab-separated)", "*.csv")], initialfile=initial_filename, parent=self)
                if not filepath: return

                # --- ИСПРАВЛЕНИЕ: Добавляем quoting=csv.QUOTE_NONE, чтобы pandas не экранировал спецсимволы ---
                import csv
                df.to_csv(filepath, sep='\t', index=False, encoding='utf-8', lineterminator='\r\n', quoting=csv.QUOTE_NONE)

                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET status = 'delta' WHERE id = %s", (self.order_id,))
                conn.commit()

                messagebox.showinfo("Успех", f"Данные успешно выгружены в файл:\n{filepath}\n\nСтатус заказа обновлен на 'delta'.", parent=self)
        except Exception as e:
            logging.error(f"Ошибка при экспорте данных для внешнего ПО (заказ {self.order_id}): {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось экспортировать данные: {e}", parent=self)

    def _import_data_for_external_sw(self):
        """
        Обрабатывает CSV-файл от 'Дельта', создает упаковки, товары и готовит данные для API.
        Адаптировано из dmkod-integration-app/app/routes.py, action 'upload_delta_csv'.
        """
        logging.info(f"[Delta Import] Запуск импорта данных из CSV для заказа ID: {self.order_id}")

        filepath = filedialog.askopenfilename(
            title="Выберите CSV-файл от 'Дельта'",
            filetypes=[("CSV files", "*.csv")],
            parent=self
        )
        if not filepath:
            logging.info("[Delta Import] Импорт отменен пользователем.")
            return

        # --- НОВОВВЕДЕНИЕ: Показываем и настраиваем прогресс-бар ---
        self.progress_bar.pack(fill=tk.X, padx=10, pady=(0, 5), side=tk.BOTTOM)
        self.progress_bar['value'] = 0
        self.progress_bar['maximum'] = 100
        self.update_idletasks()

        # 1. Валидация имени файла
        expected_filename_part = f"order_{self.order_id}.csv"
        if expected_filename_part not in os.path.basename(filepath):
            messagebox.showerror("Ошибка", f'Имя файла должно содержать "{expected_filename_part}".', parent=self)
            return

        conn = None
        try:
            # 2. Чтение и валидация CSV
            df = pd.read_csv(filepath, sep='\t', dtype={'Barcode': str, 'BoxSSCC': str, 'PaletSSCC': str})
            df.columns = df.columns.str.strip()
            required_columns = ['DataMatrix', 'Barcode', 'StartDate', 'EndDate', 'BoxSSCC', 'PaletSSCC']
            if not all(col in df.columns for col in required_columns):
                raise ValueError(f'В файле отсутствуют необходимые колонки. Ожидаются: {", ".join(required_columns)}.')

            # --- ИСПРАВЛЕНИЕ: Добавляем ведущий ноль к 13-значным GTIN ---
            # Это решает проблему, когда в файле от "Дельты" GTIN представлен
            # в формате EAN-13 (13 символов) вместо GTIN-14.
            df['Barcode'] = df['Barcode'].apply(lambda x: '0' + str(x) if isinstance(x, str) and len(x) == 13 else x)

            df['BoxSSCC'] = df['BoxSSCC'].str[-18:]
            df['PaletSSCC'] = df['PaletSSCC'].str[-18:]
            df['StartDate'] = pd.to_datetime(df['StartDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')
            df['EndDate'] = pd.to_datetime(df['EndDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')

            self.progress_bar['value'] = 10
            self.update_idletasks()

            # --- ИСПРАВЛЕНИЕ: Используем новый метод подключения к БД через пул ---
            # Это решает проблему с созданием лишних подключений.
            with get_client_db_connection(self.user_info) as conn:
              with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                from .utils import upsert_data_to_db
                
                # 3. Создание упаковок (короба и паллеты)
                unique_boxes = df[['BoxSSCC']].dropna().drop_duplicates().rename(columns={'BoxSSCC': 'sscc'})
                unique_pallets = df[['PaletSSCC']].dropna().drop_duplicates().rename(columns={'PaletSSCC': 'sscc'})
                
                packages_to_insert = []
                if not unique_boxes.empty:
                    unique_boxes['level'] = 1
                    packages_to_insert.append(unique_boxes)
                if not unique_pallets.empty:
                    unique_pallets['level'] = 2
                    packages_to_insert.append(unique_pallets)

                if packages_to_insert:
                    all_packages_df = pd.concat(packages_to_insert, ignore_index=True)
                    all_packages_df['owner'] = 'delta'
                    
                    # Устанавливаем связи "короб-паллета"
                    box_pallet_map = df[['BoxSSCC', 'PaletSSCC']].dropna().drop_duplicates()
                    box_to_pallet_sscc_map = pd.Series(box_pallet_map.PaletSSCC.values, index=box_pallet_map.BoxSSCC).to_dict()
                    
                    def find_parent_sscc(row):
                        if row['level'] == 1: return box_to_pallet_sscc_map.get(row['sscc'])
                        return None
                    all_packages_df['parent_sscc'] = all_packages_df.apply(find_parent_sscc, axis=1)

                    # Используем UPSERT для безопасной вставки
                    upsert_data_to_db(cur, 'packages', all_packages_df, 'sscc')
                    logging.info(f"[Delta Import] Загружено/обновлено {len(all_packages_df)} упаковок.")

                    # Обновляем parent_id после вставки
                    cur.execute("""
                        UPDATE packages p_child SET parent_id = p_parent.id
                        FROM packages AS p_parent
                        WHERE p_child.parent_sscc = p_parent.sscc AND p_child.parent_sscc IS NOT NULL;
                    """)
                    cur.execute("UPDATE packages SET parent_sscc = NULL WHERE parent_sscc IS NOT NULL;")
                    logging.info("[Delta Import] Связи 'короб-паллета' обновлены.")

                self.progress_bar['value'] = 30
                self.update_idletasks()

                # 4. Создание товаров (items)
                from .aggregation_service import parse_datamatrix
                parsed_dm_data = [parse_datamatrix(dm) for dm in df['DataMatrix']]
                items_df = pd.DataFrame(parsed_dm_data)
                items_df['order_id'] = self.order_id
                items_df['BoxSSCC'] = df['BoxSSCC']

                # Получаем ID коробов для привязки
                box_ssccs_tuple = tuple(df['BoxSSCC'].dropna().unique())
                sscc_to_id_map = {}
                if box_ssccs_tuple:
                    cur.execute("SELECT sscc, id FROM packages WHERE sscc IN %s", (box_ssccs_tuple,))
                    sscc_to_id_map = {row['sscc']: row['id'] for row in cur.fetchall()}
                
                items_df['package_id'] = items_df['BoxSSCC'].map(sscc_to_id_map)
                items_df['package_id'] = items_df['package_id'].astype('object').where(pd.notna(items_df['package_id']), None)
                
                columns_to_save = ['datamatrix', 'gtin', 'serial', 'crypto_part_91', 'crypto_part_92', 'crypto_part_93', 'order_id', 'package_id']
                items_to_upload = items_df[columns_to_save]
                upsert_data_to_db(cur, 'items', items_to_upload, 'datamatrix')
                logging.info(f"[Delta Import] Загружено/обновлено {len(items_to_upload)} кодов маркировки.")

                self.progress_bar['value'] = 80
                self.update_idletasks()

                # 5. Подготовка данных для delta_result
                df_for_json = df.copy()
                df_for_json.rename(columns={'Barcode': 'gtin', 'StartDate': 'production_date', 'EndDate': 'expiration_date'}, inplace=True)
                
                cur.execute("SELECT gtin, api_id FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL", (self.order_id,))
                # --- ИСПРАВЛЕНИЕ: Гарантируем, что ключ (GTIN) является строкой ---
                # Это решает проблему, когда GTIN с ведущим нулем обрабатывался как число.
                gtin_to_printrun_map = {str(row['gtin']): row['api_id'] for row in cur.fetchall()}

                # --- ИСПРАВЛЕНИЕ №2: Принудительно приводим GTIN к строковому типу СРАЗУ ПОСЛЕ ПЕРЕИМЕНОВАНИЯ ---
                # Это гарантирует, что pandas будет работать с GTIN как с текстом на всех последующих этапах,
                # предотвращая потерю ведущих нулей и ошибки сопоставления.
                df_for_json['gtin'] = df_for_json['gtin'].astype(str)

                if not gtin_to_printrun_map:
                    raise Exception("Не удалось найти ID тиражей (api_id) в деталях заказа. Убедитесь, что тиражи созданы в API.")

                df_for_json['printrun_id'] = df_for_json['gtin'].map(gtin_to_printrun_map)
                # --- ИСПРАВЛЕНИЕ: Проверяем, что все GTIN были сопоставлены ---
                # Это предотвращает молчаливую потерю данных, если для GTIN из файла нет тиража.
                if df_for_json['printrun_id'].isnull().any():
                    unmapped_gtins = df_for_json[df_for_json['printrun_id'].isnull()]['gtin'].unique()
                    raise ValueError(f"Ошибка: Для GTIN(ов) {list(unmapped_gtins)} из файла не найден соответствующий ID тиража в заказе.")

                grouped_for_api = df_for_json.groupby(['printrun_id', 'production_date', 'expiration_date']).agg({'DataMatrix': list}).reset_index()

                # --- ИСПРАВЛЕНИЕ: Полностью переписанная логика для устранения SyntaxError ---
                # Используем list comprehension для надежного и быстрого создания JSON.
                # Это решает ошибку с несоответствием скобок.
                grouped_for_api['codes_json'] = [
                    json.dumps({
                        "include": [{"code": code.replace('\x1d', '')} for code in row.DataMatrix],
                        "attributes": {
                            "production_date": str(row.production_date),
                            "expiration_date": str(row.expiration_date)
                        }
                    })
                    for row in grouped_for_api.itertuples()
                ]
                grouped_for_api['order_id'] = self.order_id
                grouped_for_api['printrun_id'] = grouped_for_api['printrun_id'].astype(int)
                grouped_for_api['production_date'] = pd.to_datetime(grouped_for_api['production_date']).dt.date

                delta_result_df = grouped_for_api[['order_id', 'printrun_id', 'production_date', 'codes_json']]
                upsert_data_to_db(cur, 'delta_result', delta_result_df, ['order_id', 'printrun_id', 'production_date'])
                logging.info(f"[Delta Import] Сохранено {len(delta_result_df)} сгруппированных записей в 'delta_result'.")

                # # 6. Обновление статуса заказа
                # cur.execute("UPDATE orders SET status = 'delta_loaded' WHERE id = %s", (self.order_id,))
            
              # 6. Фиксируем все изменения в одной транзакции
              conn.commit() # теперь управляется контекстным менеджером 'with conn'
              messagebox.showinfo("Успех", "Данные из CSV-файла 'Дельта' успешно импортированы и обработаны.", parent=self)

        except Exception as e:
            logging.error(f"Ошибка при импорте данных 'Дельта' для заказа {self.order_id}: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось импортировать данные: {e}", parent=self)
        finally:
            # --- НОВОВВЕДЕНИЕ: Прячем прогресс-бар после завершения ---
            self.progress_bar.pack_forget()
            self.update_idletasks()


    def _create_bartender_view(self):
        """Создает/обновляет представления для Bartender."""
        logging.info(f"Запущена процедура создания Bartender view для заказа ID: {self.order_id}")
        # --- НОВАЯ ЛОГИКА: Сначала импорт, потом создание представлений ---
        try:
            from .aggregation_service import run_import_from_dmkod
            
            # Шаг 1: Выполняем импорт и агрегацию
            logging.info(f"Шаг 1: Запуск run_import_from_dmkod для заказа ID: {self.order_id}")
            logs = run_import_from_dmkod(self.user_info, self.order_id)
            logging.info(f"run_import_from_dmkod для заказа ID: {self.order_id} завершен.")
            
            # Показываем лог выполнения в новом окне
            log_window = tk.Toplevel(self)
            log_window.title(f"Лог обработки заказа №{self.order_id}")
            log_window.geometry("700x500")
            log_text = tk.Text(log_window, wrap="word", padx=10, pady=10)
            # --- ИЗМЕНЕНИЕ: Логируем также то, что показываем пользователю ---
            user_log_content = "\n".join(logs)
            logging.debug(f"Лог для пользователя (заказ ID {self.order_id}):\n--- НАЧАЛО ЛОГА ---\n{user_log_content}\n--- КОНЕЦ ЛОГА ---")
            log_text.insert(tk.END, "\n".join(logs))
            log_text.config(state="disabled")
            log_text.pack(expand=True, fill=tk.BOTH)

            # Шаг 2: Создаем представления
            logging.info(f"Шаг 2: Запуск create_bartender_views для заказа ID: {self.order_id}")
            result = PrintingService.create_bartender_views(self.user_info, self.order_id)
            logging.info(f"create_bartender_views для заказа ID: {self.order_id} завершен. Результат: {result}")
            if result.get('success'):
                messagebox.showinfo("Успех", result.get('message', 'Представления успешно созданы/обновлены.'), parent=self)
            else:
                messagebox.showerror("Ошибка", result.get('message', 'Произошла неизвестная ошибка.'), parent=self)
        except Exception as e:
            messagebox.showerror("Критическая ошибка", f"Не удалось создать представления: {e}", parent=self)

class AdminWindow(tk.Tk):
    """Главное окно для роли 'администратор'."""
    def __init__(self, user_info):
        super().__init__()
        self.user_info = user_info
        self.title(f"ТильдаКод [Пользователь: {self.user_info['name']}, Роль: {self.user_info['role']}]")
        self.state('zoomed') # Запускаем окно в развернутом виде
 
        self._create_menu()
 
        # --- НОВАЯ ЛОГИКА: Создаем контейнер с вкладками ---
        notebook = ttk.Notebook(self)
        notebook.pack(expand=True, fill="both", padx=10, pady=10)
 
        # Создаем фреймы для каждой вкладки
        supply_notice_frame = ttk.Frame(notebook, padding="10")
        orders_frame = ttk.Frame(notebook, padding="10")
        catalogs_frame = ttk.Frame(notebook, padding="10")
        reports_frame = ttk.Frame(notebook, padding="10")
        admin_frame = ttk.Frame(notebook, padding="10")
 
        # Добавляем вкладки в контейнер
        notebook.add(supply_notice_frame, text="Уведомление о поставке")
        notebook.add(orders_frame, text="Заказы")
        # --- НОВЫЙ БЛОК: Добавляем вкладку "Загрузка кодов" ---
        # Она будет скрыта по умолчанию и будет появляться только для определенных сценариев.
        self.code_upload_frame = ttk.Frame(notebook, padding="10")
        notebook.add(self.code_upload_frame, text="Загрузка кодов", state="hidden")
        notebook.add(catalogs_frame, text="Справочники")
        notebook.add(reports_frame, text="Отчеты")
        notebook.add(admin_frame, text="Администрирование")
 
        # --- Заполняем вкладки ---
        self._create_supply_notice_tab(supply_notice_frame)
        self._create_orders_tab(orders_frame)
        self._create_catalogs_tab(catalogs_frame) # Заполняем новую вкладку "Справочники"
 
        # Заглушки для остальных вкладок
        ttk.Label(reports_frame, text="Раздел 'Отчеты' в разработке.", font=("Arial", 14)).pack(expand=True)
        ttk.Label(admin_frame, text="Раздел 'Администрирование' в разработке.", font=("Arial", 14)).pack(expand=True)

        # --- Индикатор статуса API ---
        self.api_status_indicator = tk.Canvas(self, width=16, height=16, highlightthickness=0, relief='flat')
        self.api_status_indicator.pack(side=tk.RIGHT, padx=10)
        self.update_api_status()

    def check_api_token(self):
        """Проверяет валидность API-токена."""
        try:
            api_service = ApiService(self.user_info)
            api_service.get_participants()
            return True
        except Exception:
            return False

    def update_api_status(self):
        """Обновляет индикатор статуса API."""
        # Запускаем проверку в отдельном потоке, чтобы не блокировать UI
        threading.Thread(target=self._update_api_status_bg, daemon=True).start()

    def _update_api_status_bg(self):
        """Фоновая задача для обновления статуса API."""
        is_valid = False
        try:
            is_valid = self.check_api_token()
        except Exception as e:
            logging.error(f"Ошибка при проверке API-токена: {e}")
        
        # --- ИСПРАВЛЕНИЕ: Обновляем UI из главного потока ---
        self.after(0, self._set_api_status_color, is_valid)
        
        # --- УЛУЧШЕНИЕ: Запускаем следующую проверку через 600 секунд ---
        self.after(600000, self.update_api_status)

    def _set_api_status_color(self, is_valid):
        color = "green" if is_valid else "red"
        self.api_status_indicator.delete("all")
        self.api_status_indicator.create_oval(2, 2, 14, 14, fill=color, outline="")

    def _create_supply_notice_tab(self, parent_frame):
        from .supply_notification_service import SupplyNotificationService
        service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
 
        # --- ИЗМЕНЕНИЕ: Создаем PanedWindow для разделения на верхнюю и нижнюю части ---
        main_paned_window = ttk.PanedWindow(parent_frame, orient=tk.VERTICAL)
        main_paned_window.pack(fill=tk.BOTH, expand=True)

        # --- Верхняя часть (список и редактор) ---
        top_pane = ttk.Frame(main_paned_window)
        main_paned_window.add(top_pane, weight=5)

        # --- ИЗМЕНЕНИЕ: Разделяем верхнюю часть на левую (таблица) и правую (редактор) ---
        top_paned_window = ttk.PanedWindow(top_pane, orient=tk.HORIZONTAL)
        top_paned_window.pack(fill=tk.BOTH, expand=True)

        cols = ('id', 'scenario_name', 'client_name', 'product_groups', 'planned_arrival_date',
                'vehicle_number', 'status', 'positions_count', 'dm_count')
        
        # --- Левая панель (2/3) для таблицы ---
        left_pane = ttk.LabelFrame(top_paned_window, text="Уведомления", padding=10) # Сделаем левую панель LabelFrame с отступом
        top_paned_window.add(left_pane, weight=1) # Левая панель будет уже

        controls = ttk.Frame(left_pane)
        controls.pack(fill=tk.X, pady=5)

        tree = ttk.Treeview(left_pane, columns=cols, show='headings')
        tree["displaycolumns"] = [c for c in cols if c != 'id'] # Скрываем колонку ID
        col_map = {
            'id': ('ID', 10, 'center'),
            'scenario_name': ('Сценарий', 150, 'w'),
            'client_name': ('Клиент', 200, 'w'),
            'product_groups': ('Товарные группы', 300, 'w'),
            'planned_arrival_date': ('Дата прибытия', 100, 'center'),
            'vehicle_number': ('Номер Контейнера/ТС', 100, 'center'),
            'status': ('Статус', 100, 'center'),
            'positions_count': ('Позиций', 70, 'center'),
            'dm_count': ('Кодов ДМ', 80, 'center')
        }

        for col_key, (text, width, anchor) in col_map.items():
            tree.heading(col_key, text=text)
            tree.column(col_key, width=width, anchor=anchor)

        tree.pack(expand=True, fill='both')

        # Настройка тегов для подсветки строк
        tree.tag_configure('Проект', background='lightpink')
        tree.tag_configure('Ожидание', background='light yellow')
        tree.tag_configure('Заказ создан', background='light green')

        # --- Правая панель (1/3) для редактора ---
        right_pane = ttk.LabelFrame(top_paned_window, text="Детали уведомления", padding=10)
        top_paned_window.add(right_pane, weight=2) # Правая панель будет шире
        ttk.Label(right_pane, text="Выберите уведомление из списка слева", anchor="center").pack(expand=True)

        # --- Нижняя часть (1/4) для сводки ---
        bottom_pane = ttk.LabelFrame(main_paned_window, text="Сводка по дням", padding=5)
        main_paned_window.add(bottom_pane, weight=1)

        # --- ИЗМЕНЕНИЕ: Создаем многоуровневые заголовки для сводки ---
        # 1. Фрейм для верхних заголовков (даты)
        summary_header_frame = ttk.Frame(bottom_pane)
        summary_header_frame.pack(fill=tk.X)

        # 2. Таблица с нижними заголовками (Ув, Поз, ДМ)
        summary_cols = ['client_name']
        summary_col_map = {'client_name': ('Клиент', 200, 'w')}
        
        # --- ИЗМЕНЕНИЕ: Генерируем реальные даты и убираем точки из заголовков ---
        from datetime import date, timedelta, datetime
        today = datetime.now().date()
        day_labels = [(today + timedelta(days=i)).strftime('%d-%m-%Y') for i in range(4)]
        sub_headers = ['Ув', 'Поз', 'ДМ']
        
        # Заполняем фрейм с верхними заголовками
        # --- НОВАЯ ЛОГИКА: Возвращаемся к grid, но с minsize вместо weight для более точного контроля ---
        summary_header_frame.columnconfigure(0, weight=1) # Левая распорка
        summary_header_frame.columnconfigure(1, minsize=200) # Отступ для колонки "Клиент"
        for i in range(4):
            summary_header_frame.columnconfigure(i + 2, minsize=240) # Блок для даты (3*80px)
        summary_header_frame.columnconfigure(6, weight=1) # Правая распорка

        # Блоки для дат
        for i, day_label in enumerate(day_labels):
            header_width = 240 # 3 колонки * 80px
            header_height = 25 # Явно задаем высоту
            logging.debug(f"Создание блока заголовка для '{day_label}' с размерами {header_width}x{header_height}px")
            date_header_block = ttk.Frame(summary_header_frame, width=header_width, height=header_height)
            date_header_block.grid(row=0, column=i + 2, sticky='nsew')
            date_header_block.pack_propagate(False) # Запрещаем дочерним элементам менять размер родителя
            ttk.Label(date_header_block, text=day_label, anchor='center', borderwidth=1, relief="solid").pack(fill=tk.BOTH, expand=True)

        # Формируем ключи и заголовки для таблицы
        for i in range(4):
            day_key = f"d{i}"
            for j, sub_header in enumerate(sub_headers):
                col_key = f"{day_key}_{sub_headers[j].lower()}"
                summary_cols.append(col_key)
                summary_col_map[col_key] = (sub_header, 80, 'center')
        
        # --- ИЗМЕНЕНИЕ: Добавляем рамку вокруг таблицы ---
        summary_tree_frame = ttk.Frame(bottom_pane, borderwidth=1, relief="solid")
        summary_tree_frame.pack(expand=True, fill='both', pady=(2,0))
        summary_tree = ttk.Treeview(summary_tree_frame, columns=summary_cols, show='headings')
        summary_tree.pack(expand=True, fill='both')

        for col_key, (text, width, anchor) in summary_col_map.items():
            summary_tree.heading(col_key, text=text)
            summary_tree.column(col_key, width=width, anchor=anchor)

        # --- НОВЫЙ БЛОК: Настройка тега для итоговой строки ---
        summary_tree.tag_configure('total_row', background='lightgrey', font=('Arial', 9, 'bold'))

        def refresh_summary_data():
            for i in summary_tree.get_children(): summary_tree.delete(i)
            try:
                summary_data = service.get_arrival_summary()
                
                # --- НОВЫЙ БЛОК: Подсчет итогов ---
                totals = {key: 0 for key in summary_cols if key != 'client_name'}
                
                for row in summary_data:
                    # --- ИЗМЕНЕНИЕ: Заменяем None на 0 при сборке значений ---
                    values = [row.get(key, 0) for key in summary_cols]
                    summary_tree.insert('', 'end', values=values)
                    # Суммируем значения для итоговой строки
                    for key in totals:
                        totals[key] += row.get(key, 0)
                
                # Добавляем итоговую строку, если есть данные
                if summary_data:
                    total_values = ['ИТОГО'] + [totals.get(key, 0) for key in summary_cols if key != 'client_name']
                    summary_tree.insert('', 'end', values=total_values, tags=('total_row',))

            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить сводку: {e}", parent=self)

        def refresh_notifications():
            for i in tree.get_children(): tree.delete(i)
            try:
                notifications = service.get_notifications_with_counts()
                for n in notifications:
                    # Преобразуем JSON с товарными группами в строку
                    pg_list = n.get('product_groups', [])
                    pg_names = ", ".join([pg.get('name', '') for pg in pg_list]) if pg_list else ''
                    
                    values = (
                        n['id'], n['scenario_name'], n['client_name'], pg_names,
                        n['planned_arrival_date'], n['vehicle_number'], n['status'],
                        n['positions_count'], n['dm_count'], "..."
                    )
                    tree.insert('', 'end', iid=n['id'], values=values, tags=(n['status'],))
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить уведомления: {e}", parent=self)

        def refresh_all():
            refresh_notifications()
            refresh_summary_data()

        def populate_editor_pane(notification_id):
            """Заполняет правую панель данными выбранного уведомления."""
            # Очищаем правую панель
            for widget in right_pane.winfo_children():
                widget.destroy()

            if not notification_id:
                ttk.Label(right_pane, text="Выберите уведомление из списка слева", anchor="center").pack(expand=True)
                return

            try:
                # Загружаем данные
                notification_data = service.get_notification_by_id(notification_id)
                if not notification_data:
                    ttk.Label(right_pane, text="Не удалось загрузить данные.", anchor="center").pack(expand=True)
                    return

                # Создаем виджеты, как в NotificationEditorDialog
                editor_notebook = ttk.Notebook(right_pane)
                editor_notebook.pack(fill=tk.BOTH, expand=True)

                # --- НОВЫЙ БЛОК: Вкладка "Общее" ---
                general_tab = ttk.Frame(editor_notebook, padding=10)
                editor_notebook.add(general_tab, text="Общее")

                # --- Поля для редактирования (адаптировано из NotificationEditorDialog) ---
                from .catalogs_service import CatalogsService
                catalog_service = CatalogsService(self.user_info, lambda: get_client_db_connection(self.user_info))

                # --- ИЗМЕНЕНИЕ: Замена Listbox на Combobox для товарной группы ---
                ttk.Label(general_tab, text="Товарная группа:").pack(anchor="w")
                product_group_var = tk.StringVar()
                product_group_combo = ttk.Combobox(general_tab, textvariable=product_group_var, state="readonly")
                product_group_combo.pack(fill=tk.X, pady=2)
                
                all_product_groups = catalog_service.get_product_groups()
                product_group_combo['values'] = [pg['display_name'] for pg in all_product_groups]
                
                initial_pgs = notification_data.get('product_groups', [])
                if initial_pgs:
                    initial_pg_name = initial_pgs[0].get('name')
                    if initial_pg_name in product_group_combo['values']:
                        product_group_var.set(initial_pg_name)

                # Дата прибытия
                ttk.Label(general_tab, text="Планируемая дата прибытия:").pack(anchor="w")
                arrival_date_var = tk.StringVar(value=str(notification_data.get('planned_arrival_date', '')))
                date_frame = ttk.Frame(general_tab)
                date_frame.pack(fill=tk.X, pady=2)
                arrival_date_entry = ttk.Entry(date_frame, textvariable=arrival_date_var)
                arrival_date_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
                
                def _open_calendar():
                    try: initial_date = datetime.strptime(arrival_date_var.get(), "%Y-%m-%d")
                    except ValueError: initial_date = datetime.now()
                    cal_dialog = CalendarDialog(self, initial_date=initial_date)
                    self.wait_window(cal_dialog)
                    if cal_dialog.result: arrival_date_var.set(cal_dialog.result.strftime("%Y-%m-%d"))
                
                ttk.Button(date_frame, text="...", width=3, command=_open_calendar).pack(side=tk.LEFT, padx=(5,0))

                # Номер ТС
                ttk.Label(general_tab, text="Номер контейнера/автомобиля:").pack(anchor="w")
                vehicle_number_entry = ttk.Entry(general_tab)
                vehicle_number_entry.insert(0, notification_data.get('vehicle_number', ''))
                vehicle_number_entry.pack(fill=tk.X, pady=2)

                # Комментарии
                ttk.Label(general_tab, text="Комментарии:").pack(anchor="w")
                comments_text = tk.Text(general_tab, height=3)
                comments_text.insert('1.0', notification_data.get('comments', ''))
                comments_text.pack(fill=tk.X, pady=2)

                # --- ИЗМЕНЕНИЕ: Универсальная функция сохранения ---
                def _save_general_info_from_panel():
                    try:
                        selected_pg_name = product_group_var.get()
                        selected_pg = next((pg for pg in all_product_groups if pg['display_name'] == selected_pg_name), None)
                        selected_pgs = [selected_pg] if selected_pg else []
                        data_to_save = { # Собираем данные для сохранения
                            'product_groups': [{'id': pg['id'], 'name': pg['display_name']} for pg in selected_pgs],
                            'planned_arrival_date': arrival_date_var.get() or None,
                            'vehicle_number': vehicle_number_entry.get(),
                            'comments': comments_text.get('1.0', 'end-1c')
                        }
                        service.update_notification(notification_id, data_to_save)
                        messagebox.showinfo("Успех", "Данные уведомления успешно обновлены.", parent=self)
                        refresh_all()
                        return True # Возвращаем успех
                    except Exception as e:
                        logging.error(f"Ошибка сохранения данных из боковой панели: {e}", exc_info=True)
                        messagebox.showerror("Ошибка", f"Не удалось сохранить изменения: {e}", parent=self)
                        return False # Возвращаем неудачу

                # --- Кнопка "Создать заказ" ---
                def _create_order_from_panel():
                    logging.info(f"Запрос на создание заказа из уведомления ID: {notification_id}")
                    try:
                        success, message = service.create_order_from_notification(notification_id)
                        if success:
                            messagebox.showinfo("Успех", message, parent=self)
                            refresh_all() # Обновляем, чтобы увидеть изменение статуса
                        else:
                            messagebox.showwarning("Внимание", message, parent=self)
                    except Exception as e:
                        logging.error(f"Ошибка при создании заказа из уведомления {notification_id}: {e}", exc_info=True)
                        messagebox.showerror("Ошибка", f"Не удалось создать заказ: {e}", parent=self)

                # --- НОВАЯ КНОПКА: Создать/Обновить заказ ---
                def _save_and_create_order():
                    if _save_general_info_from_panel(): # Сначала сохраняем
                        _create_order_from_panel()      # Затем создаем заказ

                # --- Размещаем кнопки ---
                buttons_frame = ttk.Frame(general_tab)
                buttons_frame.pack(fill=tk.X, pady=(10,0))
                ttk.Button(buttons_frame, text="Сохранить изменения", command=_save_general_info_from_panel).pack(side=tk.LEFT, padx=(0, 5))

                # --- ИЗМЕНЕНИЕ: Кнопка доступна, только если детализация загружена (статус 'Ожидание') ---
                if notification_data.get('status') != 'Проект':
                    ttk.Button(buttons_frame, text="Создать/Обновить заказ", command=_save_and_create_order).pack(side=tk.LEFT)

                # Вкладка "Документы"
                docs_frame = ttk.Frame(editor_notebook, padding=5)
                editor_notebook.add(docs_frame, text="Документы")

                docs_controls = ttk.Frame(docs_frame)
                docs_controls.pack(fill=tk.X, pady=2)
                
                # --- Функции для работы с файлами (адаптированы из NotificationEditorDialog) ---
                files_listbox = tk.Listbox(docs_frame, height=4)
                client_files = []

                def _load_files():
                    nonlocal client_files
                    files_listbox.delete(0, tk.END)
                    client_files = service.get_notification_files(notification_id)
                    for f in client_files:
                        files_listbox.insert(tk.END, f['filename'])
                
                def _upload_doc():
                    filepath = filedialog.askopenfilename(parent=self)
                    if not filepath: return
                    try:
                        filename = os.path.basename(filepath)
                        with open(filepath, 'rb') as f: file_data = f.read()
                        service.add_notification_file(notification_id, filename, file_data, 'client_document')
                        _load_files()
                        messagebox.showinfo("Успех", "Файл успешно загружен.", parent=self)
                    except Exception as e: messagebox.showerror("Ошибка", f"Не удалось загрузить файл: {e}", parent=self)

                def _download_doc():
                    selected = files_listbox.curselection()
                    if not selected: return
                    # --- ИСПРАВЛЕНИЕ: Получаем file_id из кэша client_files ---
                    selected_file_info = client_files[selected[0]]
                    file_id = selected_file_info['id']
                    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
                    content, filename = service.get_file_content(file_id) # get_file_content должен быть в сервисе
                    save_path = filedialog.asksaveasfilename(initialfile=filename, parent=self)
                    if save_path:
                        with open(save_path, 'wb') as f: f.write(content)
                        messagebox.showinfo("Успех", "Файл сохранен.", parent=self)

                def _delete_doc():
                    selected = files_listbox.curselection()
                    if not selected: return
                    if not messagebox.askyesno("Подтверждение", "Удалить выбранный файл?", parent=self): return
                    file_id = client_files[selected[0]]['id']
                    service.delete_notification_file(file_id)
                    _load_files()

                ttk.Button(docs_controls, text="Загрузить", command=_upload_doc).pack(side=tk.LEFT)
                ttk.Button(docs_controls, text="Скачать", command=_download_doc).pack(side=tk.LEFT, padx=5)
                ttk.Button(docs_controls, text="Удалить", command=_delete_doc).pack(side=tk.LEFT)
                files_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
                _load_files()

                # --- ИСПРАВЛЕНИЕ: Сначала создаем вкладку "Детализация", а потом ее содержимое ---
                details_frame = ttk.Frame(editor_notebook, padding=5)
                editor_notebook.add(details_frame, text="Детализация")

                # --- ИСПРАВЛЕНИЕ: Создаем Treeview внутри правильного контейнера (tree_container) ---
                details_cols = ["id", "gtin", "quantity", "aggregation", "production_date", "shelf_life_months", "expiry_date"]

                col_map = {
                    "id": ("ID", 40, "center"), "gtin": ("GTIN", 90, "w"), "quantity": ("Кол-во", 40, "e"),
                    "aggregation": ("Агрегация", 40, "center"), "production_date": ("Дата произв.", 80, "center"),
                    "shelf_life_months": ("Срок годн. (мес)", 40, "center"), "expiry_date": ("Годен до", 80, "center") # type: ignore
                }

                def _download_details_template_panel():
                    df = service.get_formalization_template()
                    save_path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], parent=self)
                    if save_path:
                        df.to_excel(save_path, index=False)
                        messagebox.showinfo("Успех", "Шаблон успешно сохранен.", parent=self)

                def _upload_details_file_panel():
                    if not messagebox.askyesno("Подтверждение", "Загрузка из файла полностью заменит текущую детализацию. Продолжить?", parent=self):
                        return
                    filepath = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")], parent=self)
                    if not filepath: return
                    try:
                        with open(filepath, 'rb') as f:
                            file_data = f.read()
                        rows_processed = service.process_formalized_file(notification_id, file_data)
                        _load_notification_details_panel()
                        messagebox.showinfo("Успех", f"Файл успешно обработан. Загружено {rows_processed} строк.", parent=self)
                    except Exception as e:
                        messagebox.showerror("Ошибка", f"Не удалось обработать файл: {e}", parent=self)

                def _save_details_from_table_panel():
                    details_to_save = []
                    for item_id in details_tree.get_children():
                        raw_values = details_tree.item(item_id, "values")
                        processed_values = [
                            int(raw_values[0]) if raw_values[0] else None, # id
                            raw_values[1] if raw_values[1] else None, # gtin
                            int(raw_values[2]) if raw_values[2] else None, # quantity
                            int(raw_values[3]) if raw_values[3] else None, # aggregation
                            raw_values[4] if raw_values[4] else None, # production_date
                            int(raw_values[5]) if raw_values[5] else None, # shelf_life_months
                            raw_values[6] if raw_values[6] else None # expiry_date
                        ]
                        details_to_save.append(tuple(processed_values))
                    try:
                        service.save_notification_details(details_to_save)
                        messagebox.showinfo("Успех", "Изменения в детализации успешно сохранены.", parent=self)
                    except Exception as e:
                        messagebox.showerror("Ошибка", f"Не удалось сохранить детализацию: {e}", parent=self)

                def _on_details_double_click_panel(event):
                    """Обработчик двойного клика для редактирования ячейки в Treeview."""
                    region = details_tree.identify("region", event.x, event.y)
                    if region != "cell": return

                    column_id = details_tree.identify_column(event.x)
                    column_index = int(column_id.replace('#', '')) - 1
                    item_id = details_tree.focus()
                    
                    x, y, width, height = details_tree.bbox(item_id, column_id)

                    entry_var = tk.StringVar()
                    entry = ttk.Entry(details_tree, textvariable=entry_var)
                    entry.place(x=x, y=y, width=width, height=height)
                    
                    current_value = details_tree.item(item_id, "values")[column_index]
                    entry_var.set(current_value)
                    entry.focus_set()

                    def on_focus_out(event):
                        new_value = entry_var.get()
                        current_values = list(details_tree.item(item_id, "values"))
                        current_values[column_index] = new_value
                        details_tree.item(item_id, values=tuple(current_values))
                        entry.destroy()

                    entry.bind("<FocusOut>", on_focus_out)
                    entry.bind("<Return>", on_focus_out)

                # Панель с кнопками
                details_controls = ttk.Frame(details_frame)
                details_controls.pack(fill=tk.X, pady=5)
                ttk.Button(details_controls, text="Скачать шаблон", command=_download_details_template_panel).pack(side=tk.LEFT, padx=2)
                ttk.Button(details_controls, text="Загрузить из файла", command=_upload_details_file_panel).pack(side=tk.LEFT, padx=2)
                ttk.Button(details_controls, text="Сохранить изменения", command=_save_details_from_table_panel).pack(side=tk.RIGHT, padx=2)

                # Контейнер для таблицы и скроллбара
                tree_container = ttk.Frame(details_frame)
                tree_container.pack(fill=tk.BOTH, expand=True, pady=(5,0))

                # Создаем Treeview внутри tree_container
                details_tree = ttk.Treeview(tree_container, columns=details_cols, show='headings')
                details_tree["displaycolumns"] = [c for c in details_cols if c != 'id'] # Скрываем колонку ID
                for col, (text, width, anchor) in col_map.items():
                    details_tree.heading(col, text=text)
                    details_tree.column(col, width=width, anchor=anchor)

                details_scrollbar = ttk.Scrollbar(tree_container, orient="vertical", command=details_tree.yview)
                details_tree.configure(yscrollcommand=details_scrollbar.set)

                # Загрузчик данных, который решает, показывать таблицу или заглушку
                def _load_notification_details_panel():
                    """Загружает и отображает детализацию в Treeview."""
                    for i in details_tree.get_children(): details_tree.delete(i)
                    details = service.get_notification_details(notification_id)
                    
                    # Очищаем контейнер перед добавлением виджетов
                    for widget in tree_container.winfo_children():
                        if widget not in (details_tree, details_scrollbar):
                            widget.destroy()

                    if details:
                        for item in details:
                            values = [item.get(col, '') for col in details_cols]
                            details_tree.insert('', 'end', iid=item['id'], values=values)
                        details_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                        details_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
                    else:
                        details_tree.pack_forget()
                        details_scrollbar.pack_forget()
                        ttk.Label(tree_container, text="Детализация не загружена.", anchor="center").pack(expand=True)

                details_tree.bind("<Double-1>", _on_details_double_click_panel)
                _load_notification_details_panel()

            except Exception as e:
                logging.error(f"Ошибка при заполнении панели редактора: {e}", exc_info=True)
                for widget in right_pane.winfo_children(): widget.destroy()
                ttk.Label(right_pane, text=f"Ошибка: {e}", wraplength=right_pane.winfo_width()-20).pack(expand=True)
        
        def open_notification_editor(notification_id=None):
            """Открывает диалог для создания/редактирования уведомления."""
            logging.info(f"Вызвана функция open_notification_editor с notification_id: {notification_id}, тип: {type(notification_id)}")
            dialog = NotificationEditorDialog(self, self.user_info, notification_id=int(notification_id) if notification_id else None)
            dialog.on_save_callback = refresh_all
            # self.wait_window(dialog) # Эта строка делала окно модальным, но мы используем callback
        ttk.Button(controls, text="Создать новое уведомление", command=lambda: open_notification_editor()).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Обновить", command=refresh_all).pack(side=tk.LEFT, padx=2)

        def on_tree_select(event):
            """Обработчик выбора элемента в таблице."""
            selected_item = tree.focus()
            if selected_item:
                populate_editor_pane(int(selected_item))
        
        # Удаляем привязку контекстного меню
        # tree.bind("<Button-3>", show_context_menu) # Правый клик
        tree.bind("<Double-1>", lambda event: open_notification_editor(tree.focus())) # Двойной клик
        tree.bind("<<TreeviewSelect>>", on_tree_select) # Выбор элемента

        refresh_all()

    def _create_catalogs_tab(self, parent_frame):
        logger = logging.getLogger(__name__)
        """Создает содержимое для вкладки 'Справочники'."""
        # --- ИЗМЕНЕНИЕ: Передаем в сервис функцию для подключения к БД клиента ---
        from .catalogs_service import CatalogsService
        service = CatalogsService(self.user_info, lambda: get_client_db_connection(self.user_info))

        # --- ИЗМЕНЕНИЕ: Создаем вложенный Notebook для разных справочников ---
        notebook = ttk.Notebook(parent_frame)
        notebook.pack(expand=True, fill="both")

        # --- НОВАЯ ВКЛАДКА: Клиенты (локальный справочник) ---
        self._create_generic_catalog_tab(
            parent=notebook,
            title="Клиенты (локальные)",
            service_methods={
                'get': service.get_local_clients,
                'upsert': service.upsert_local_client,
                'delete': service.delete_local_client,
                'template': service.get_local_clients_template,
                'import': service.process_local_clients_import
            },
            columns={
                'id': ('ID', 50, 'center'),
                'name': ('Наименование', 400, 'w'),
                'inn': ('ИНН', 150, 'center')
            },
            pk_field='id'
        )

        # --- Вкладка 1: Участники (существующая логика) ---
        participants_frame = ttk.Frame(notebook, padding="10")
        notebook.add(participants_frame, text="Участники")

        participants_controls = ttk.Frame(participants_frame)
        participants_controls.pack(fill=tk.X, pady=5)

        participants_tree = ttk.Treeview(participants_frame, columns=('name', 'inn', 'poa_end'), show='headings')
        participants_tree.heading('name', text='Наименование')
        participants_tree.heading('inn', text='Источник (ИНН)')
        participants_tree.heading('poa_end', text='Окончание доверенности')
        participants_tree.column('name', width=300)
        participants_tree.column('inn', width=150, anchor=tk.CENTER)
        participants_tree.column('poa_end', width=150, anchor=tk.CENTER)
        participants_tree.pack(expand=True, fill='both')

        def refresh_participants_list():
            for i in participants_tree.get_children(): participants_tree.delete(i)
            try:
                participants_list = service.get_participants_catalog()
                for n in participants_list:
                    poa_end_date = n.get('poa_validity_end', '')
                    if poa_end_date and 'T' in poa_end_date:
                        poa_end_date = poa_end_date.split('T')[0]
                    participants_tree.insert('', 'end', values=(n.get('name', ''), n.get('inn', ''), poa_end_date))
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить справочник: {e}", parent=self)

        ttk.Button(participants_controls, text="Обновить", command=refresh_participants_list).pack(side=tk.LEFT, padx=2)
        refresh_participants_list()

        # --- Вкладка 2: Товарные группы ---
        self._create_generic_catalog_tab(
            parent=notebook,
            title="Товарные группы",
            service_methods={
                'get': service.get_product_groups,
                'upsert': service.upsert_product_group,
                'delete': service.delete_product_group,
                'template': service.get_product_groups_template,
                'import': service.process_product_groups_import
            },
            columns={
                'id': ('ID', 50, 'center'),
                'group_name': ('Системное имя', 200, 'w'),
                'display_name': ('Отображаемое имя', 300, 'w'),
                'fias_required': ('Нужен ФИАС', 100, 'center'),
                'code_template': ('Шаблон кода', 200, 'w'),
                'dm_template': ('Шаблон ДМ', 200, 'w')
            },
            pk_field='id'
        )

        # --- Вкладка 3: Товары ---
        self._create_generic_catalog_tab(
            parent=notebook,
            title="Товары",
            service_methods={
                'get': service.get_products,
                'upsert': service.upsert_product,
                'delete': service.delete_product,
                'template': service.get_products_template,
                'import': service.process_products_import
            },
            columns={
                'gtin': ('GTIN', 150, 'center'),
                'name': ('Наименование', 300, 'w'),
                'description_1': ('Описание 1', 200, 'w'),
                'description_2': ('Описание 2', 200, 'w'),
                'description_3': ('Описание 3', 200, 'w')
            },
            pk_field='gtin'
        )

        # --- Вкладка 4: Сценарии маркировки ---
        self._create_generic_catalog_tab(
            parent=notebook,
            title="Сценарии маркировки",
            service_methods={
                'get': service.get_marking_scenarios,
                'upsert': service.upsert_marking_scenario,
                'delete': service.delete_marking_scenario,
                'template': service.get_marking_scenarios_template,
                'import': service.process_marking_scenarios_import
            },
            columns={
                'id': ('ID', 10, 'center'),
                'name': ('Название сценария', 150, 'w'),
                'scenario_data': ('Параметры (JSON)', 1000, 'w')
            },
            pk_field='id',
            editor_class=ScenarioEditorDialog # Используем кастомный редактор
        )

    def _create_generic_catalog_tab(self, parent, title, service_methods, columns, pk_field, editor_class=None):
        """Создает универсальную вкладку для справочника с полным CRUD."""
        logger = logging.getLogger(__name__)
        frame = ttk.Frame(parent, padding="10")
        parent.add(frame, text=title)

        controls = ttk.Frame(frame)
        controls.pack(fill=tk.X, pady=5)

        tree = ttk.Treeview(frame, columns=list(columns.keys()), show='headings')
        # --- НОВЫЙ БЛОК: Кэш для хранения оригинальных данных ---
        data_cache = {}

        for col_key, (col_title, col_width, col_anchor) in columns.items():
            tree.heading(col_key, text=col_title)
            tree.column(col_key, width=col_width, anchor=col_anchor)
        tree.pack(expand=True, fill='both')

        def refresh_data():
            for i in tree.get_children(): tree.delete(i)
            data_cache.clear() # Очищаем кэш перед обновлением
            try:
                items = service_methods['get']()
                for item in items:
                    # --- ИСПРАВЛЕНИЕ: Принудительно конвертируем все значения в строки ---
                    # Это предотвращает автоматическое преобразование GTIN в число и потерю ведущих нулей.
                    values = [str(item.get(key, '')) for key in columns.keys()]
                    pk_value = str(item.get(pk_field))
                    # Сохраняем оригинальный объект в кэш и используем PK как ID строки в Treeview
                    data_cache[pk_value] = item
                    tree.insert('', 'end', iid=pk_value, values=values)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить '{title}': {e}", parent=self)

        def open_editor(item_data=None, editor_class=None):
            """Открывает диалог для добавления/редактирования."""
            logger.debug(f"Открытие редактора для '{title}'. Данные для редактирования: {item_data}")
            # --- ИСПРАВЛЕНИЕ: Убираем лишнее преобразование и передаем словарь напрямую ---
            
            # --- НОВАЯ ЛОГИКА: Используем кастомный редактор, если он указан ---
            if editor_class:
                # Для кастомного редактора передаем user_info
                dialog = editor_class(self, self.user_info, item_data)
            else:
                # Для стандартного редактора передаем колонки и pk_field
                dialog = GenericEditorDialog(self, f"Редактор: {title}", columns, item_data, pk_field)


            self.wait_window(dialog)
            if dialog.result:
                try:
                    logger.debug(f"Сохранение данных из редактора: {dialog.result}")
                    service_methods['upsert'](dialog.result)
                    refresh_data()
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось сохранить запись: {e}", parent=self)

        def delete_item():
            selected_item = tree.focus()
            if not selected_item: return
            # --- ИСПРАВЛЕНИЕ: Получаем PK напрямую из ID строки (iid), а не из values ---
            # Это гарантирует, что tkinter не преобразует GTIN в число и не потеряет ведущий ноль.
            pk_value = selected_item
            logger.debug(f"Запрос на удаление записи с ключом '{pk_value}' из справочника '{title}'.")
            if messagebox.askyesno("Подтверждение", f"Удалить запись с ключом '{pk_value}'?", parent=self):
                try:
                    logger.info(f"Подтверждено удаление записи с ключом '{pk_value}'.")
                    service_methods['delete'](pk_value)
                    refresh_data()
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось удалить запись: {e}", parent=self)

        def export_to_excel():
            try:
                items = service_methods['get']()
                df = pd.DataFrame(items, columns=list(columns.keys())) if items else service_methods['template']()
                filepath = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], parent=self)
                if filepath:
                    df.to_excel(filepath, index=False)
                    messagebox.showinfo("Успех", f"Справочник '{title}' выгружен.", parent=self)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось выгрузить файл: {e}", parent=self)

        def import_from_excel():
            # --- ИЗМЕНЕНИЕ: Уточняем типы файлов для диалога ---
            filepath = filedialog.askopenfilename(
                title=f"Импорт: {title}",
                filetypes=[
                    ("Excel files", "*.xlsx"),
                    ("All files", "*.*")
                ],
                parent=self
            )
            if not filepath:
                logger.debug(f"Импорт для '{title}' отменен: файл не выбран.")
                return
            
            logger.info(f"Начало импорта для '{title}' из файла: {filepath}")
            try:
                # --- ИСПРАВЛЕНИЕ: Явно указываем, что ключевые поля (gtin, id) должны быть текстом ---
                # Это предотвращает потерю ведущих нулей в GTIN.
                # Остальные поля пусть pandas определяет автоматически.
                df = pd.read_excel(filepath, dtype={pk_field: str})
                
                logger.debug(f"Прочитано {len(df)} строк из Excel файла.")
                
                service_methods['import'](df) # Передаем DataFrame напрямую

                logger.info(f"Импорт для '{title}' успешно завершен. Обновление таблицы...")
                refresh_data()
                messagebox.showinfo("Успех", "Данные успешно импортированы.", parent=self)
            except Exception as e:
                logger.error(f"Ошибка при импорте из Excel для '{title}': {e}", exc_info=True)
                messagebox.showerror("Ошибка", f"Ошибка импорта: {e}", parent=self)

        ttk.Button(controls, text="Добавить", command=lambda: open_editor(editor_class=editor_class)).pack(side=tk.LEFT, padx=2)
        # --- ИЗМЕНЕНИЕ: Логика кнопки "Редактировать" ---
        def edit_selected():
            selected_item_id = tree.focus()
            if not selected_item_id: return
            # --- ИСПРАВЛЕНИЕ: Получаем PK напрямую из ID строки (iid), а не из values ---
            # Это гарантирует, что tkinter не преобразует GTIN в число.
            pk_value = selected_item_id
            # Находим оригинальные данные в кэше по этому PK
            original_data = data_cache.get(pk_value)
            # --- ИСПРАВЛЕНИЕ: Передаем в редактор словарь, а не список значений ---
            # Это гарантирует, что данные будут правильно сопоставлены с полями.
            open_editor(original_data, editor_class=editor_class)
        ttk.Button(controls, text="Редактировать", command=edit_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Удалить", command=delete_item).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Выгрузить в Excel", command=export_to_excel).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Загрузить из Excel", command=import_from_excel).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Обновить", command=refresh_data).pack(side=tk.LEFT, padx=2)

        refresh_data()

    def _create_orders_tab(self, parent_frame):
        """Создает содержимое для вкладки 'Заказы' с разделением на 'В работе' и 'Архив'."""
        notebook = ttk.Notebook(parent_frame)
        notebook.pack(expand=True, fill="both")

        in_progress_frame = ttk.Frame(notebook)
        archive_frame = ttk.Frame(notebook)

        notebook.add(in_progress_frame, text="В работе")
        notebook.add(archive_frame, text="Архив")

        def _create_orders_view(parent, is_archive):
            """Создает представление заказов с новой компоновкой."""
            # 1. Основной PanedWindow (вертикальный)
            main_paned_window = ttk.PanedWindow(parent, orient=tk.VERTICAL)
            main_paned_window.pack(fill=tk.BOTH, expand=True)

            # 2. Верхняя панель (для управления и таблицы)
            top_pane = ttk.Frame(main_paned_window)
            main_paned_window.add(top_pane, weight=2) # 2/3 высоты

            # 3. Нижняя панель (для статистики)
            bottom_pane = ttk.LabelFrame(main_paned_window, text="Статистика", padding=5)
            main_paned_window.add(bottom_pane, weight=1) # 1/3 высоты

            # 4. Разделение верхней панели (горизонтальное)
            top_paned_window = ttk.PanedWindow(top_pane, orient=tk.HORIZONTAL)
            top_paned_window.pack(fill=tk.BOTH, expand=True)

            # 5. Левая панель вверху (управление)
            left_pane = ttk.Frame(top_paned_window)
            top_paned_window.add(left_pane, weight=2) # 2/3 ширины

            # 6. Правая панель вверху (таблица)
            right_pane = ttk.LabelFrame(top_paned_window, text="Управление", padding=10)
            top_paned_window.add(right_pane, weight=1) # 1/3 ширины
 
            # --- Заполнение левой панели (таблица заказов и ее элементы управления) ---
            order_controls_frame = ttk.Frame(left_pane)
            order_controls_frame.pack(fill=tk.X, padx=5, pady=5)
 
            cols = ('date', 'client', 'status', 'notes')
            tree = ttk.Treeview(left_pane, columns=cols, show='headings')
            tree.heading('date', text='Дата')
            tree.heading('client', text='Клиент')
            tree.heading('status', text='Статус')
            tree.heading('notes', text='Комментарий')
 
            tree.column('date', width=100, anchor=tk.CENTER)
            tree.column('client', width=300, anchor=tk.W)
            tree.column('status', width=100, anchor=tk.CENTER)
            tree.column('notes', width=300, anchor=tk.W)
            tree.pack(expand=True, fill="both", side="left")
            scrollbar = ttk.Scrollbar(left_pane, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")

            # --- НОВЫЙ БЛОК: Создаем виджеты в панели управления ---
            # Заглушка, которая видна, когда ничего не выбрано
            placeholder_label = ttk.Label(right_pane, text="Выберите заказ для управления", anchor="center")
            placeholder_label.pack(expand=True, fill="both")

            # Notebook, который будет содержать вкладки
            management_notebook = ttk.Notebook(right_pane)
            # management_notebook.pack(fill="both", expand=True) # pack будет вызываться в on_order_select

            # Вкладка "Редактирование"
            # --- ИЗМЕНЕНИЕ: Создаем вкладку для загрузки кодов ---
            # Она будет показана или скрыта в зависимости от сценария.
            upload_tab = ttk.Frame(management_notebook, padding=10)
            management_notebook.add(upload_tab, text="Загрузка кодов")

            edit_tab = ttk.Frame(management_notebook, padding=10)
            management_notebook.add(edit_tab, text="Редактирование")
            # Вкладка "АПИ"
            api_tab = ttk.Frame(management_notebook, padding=10)
            management_notebook.add(api_tab, text="АПИ")

            tree.tag_configure('pink_row', background='lightpink')
            tree.tag_configure('green_row', background='lightgreen')
            tree.tag_configure('yellow_row', background='lightyellow')
            tree.tag_configure('blue_row', background='lightblue')

            # --- Функции для работы с данными и UI ---
            def load_data():
                on_order_select() # Сбрасываем состояние кнопок при обновлении
                # (логика загрузки данных остается прежней)
                for i in tree.get_children(): tree.delete(i)
                try:
                    with get_client_db_connection(self.user_info) as conn:
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                            status_filter = "status LIKE 'Архив%%'" if is_archive else "status NOT LIKE 'Архив%%'"
                            query = f"SELECT id, client_name, order_date, status, notes, api_status FROM orders WHERE {status_filter} ORDER BY id DESC"
                            cur.execute(query)
                            for order in cur.fetchall():
                                client_display = f"{order['client_name']} заказ № {order['id']}"
                                values = (order['order_date'], client_display, order['status'], order['notes'])
                                tag = ''
                                if order['api_status'] == 'Отчет подготовлен': tag = 'pink_row'
                                elif order['api_status'] == 'Коды скачаны': tag = 'green_row'
                                elif order['api_status'] == 'Запрос создан': tag = 'yellow_row'
                                elif order['status'] == 'completed': tag = 'blue_row'
                                tree.insert('', 'end', iid=order['id'], values=values, tags=(tag,))
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось загрузить заказы: {e}", parent=parent)

            def move_to_archive(order_id, current_status):
                # (логика архивации остается прежней)
                if messagebox.askyesno("Подтверждение", "Переместить заказ в архив?", parent=parent):
                    try:
                        new_status = f"Архив_{current_status}"
                        with get_client_db_connection(self.user_info) as conn:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE orders SET status = %s WHERE id = %s RETURNING notification_id", (new_status, order_id))
                                result = cur.fetchone()
                                notification_id = result[0] if result else None
                                if notification_id:
                                    cur.execute("UPDATE ap_supply_notifications SET status = 'В архиве' WHERE id = %s", (notification_id,))
                            conn.commit()
                        load_data()
                        # TODO: Нужно обновить и другую вкладку тоже
                    except Exception as e:
                        messagebox.showerror("Ошибка", f"Не удалось архивировать заказ: {e}", parent=parent)

            def open_correct_editor(order_id):
                """Проверяет сценарий и открывает соответствующий редактор."""
                try:
                    with get_client_db_connection(self.user_info) as conn:
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                            cur.execute("SELECT s.scenario_data FROM orders o JOIN ap_marking_scenarios s ON o.scenario_id = s.id WHERE o.id = %s", (order_id,))
                            result = cur.fetchone()
                    scenario_data = result['scenario_data'] if result else {}
                    OrderEditorDialog(self, self.user_info, order_id, scenario_data)
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось определить сценарий заказа: {e}", parent=self)

            def on_management_tab_change(event):
                """Обработчик переключения вкладок в панели управления."""
                selected_tab_index = management_notebook.index(management_notebook.select())
                selected_order_id = tree.focus()

                if not selected_order_id:
                    return

                if selected_tab_index == 0: # Вкладка "Редактирование"
                    open_correct_editor(selected_order_id)
                elif selected_tab_index == 1: # Вкладка "АПИ"
                    ApiIntegrationDialog(self, self.user_info, selected_order_id)

            def on_order_select(event=None):
                """Обработчик выбора строки в таблице. Активирует/деактивирует кнопки."""
                logging.debug("on_order_select: Сработал обработчик выбора заказа.")
                # Очищаем панель управления
                placeholder_label.pack_forget()
                management_notebook.pack_forget()
                for tab in (edit_tab, api_tab, upload_tab):
                    for widget in tab.winfo_children():
                        widget.destroy()

                selected_item = tree.focus()
                if not selected_item:
                    logging.debug("on_order_select: Заказ не выбран (tree.focus() пуст). Выход из функции.")
                    placeholder_label.pack(expand=True, fill="both")
                    return

                order_id = selected_item
                order_status = tree.item(order_id, "values")[2]
                logging.debug(f"on_order_select: Выбран заказ ID: {order_id}, Статус: {order_status}")

                try:
                    logging.debug(f"on_order_select: Запрос данных сценария для заказа ID {order_id}...")
                    with get_client_db_connection(self.user_info) as conn:
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                            cur.execute("SELECT s.scenario_data FROM orders o JOIN ap_marking_scenarios s ON o.scenario_id = s.id WHERE o.id = %s", (order_id,))
                            result = cur.fetchone()
                    scenario_data = result['scenario_data'] if result else {}
                    # --- НОВАЯ ЛОГИКА: Получаем источник кодов ---
                    dm_source = scenario_data.get('dm_source')
                    post_processing_mode = scenario_data.get('post_processing')
                    logging.debug(f"on_order_select: Данные сценария получены. post_processing_mode: '{post_processing_mode}'.")
 
                    logging.debug(f"on_order_select: Создание OrderEditorFrame для заказа ID {order_id}...")
                    OrderEditorFrame(edit_tab, self.user_info, order_id, scenario_data).pack(fill="both", expand=True)
                    logging.debug("on_order_select: OrderEditorFrame успешно создан и упакован.")
 
                    # --- НОВАЯ ЛОГИКА: Создаем нужный фрейм в зависимости от источника ---
                    if dm_source == "Файлы клиента (csv, txt)":
                        logging.debug(f"on_order_select: Создание CodeUploadFrame для заказа ID {order_id}...")
                        CodeUploadFrame(upload_tab, self.user_info, order_id).pack(fill="both", expand=True)
                        logging.debug("on_order_select: CodeUploadFrame успешно создан.")
                    else:
                        logging.debug(f"on_order_select: Создание ApiIntegrationFrame для заказа ID {order_id}...")
                        ApiIntegrationFrame(api_tab, self.user_info, order_id, post_processing_mode).pack(fill="both", expand=True)
                        logging.debug("on_order_select: ApiIntegrationFrame успешно создан.")

                except Exception as e:
                    logging.error(f"on_order_select: КРИТИЧЕСКАЯ ОШИБКА при создании интерфейсов управления: {e}", exc_info=True)
                    messagebox.showerror("Ошибка", f"Не удалось создать интерфейсы управления: {e}\n\nПодробности в файле app.log", parent=self)
                    placeholder_label.pack(expand=True, fill="both")
                    return

                management_notebook.pack(fill="both", expand=True)
                # --- НОВАЯ ЛОГИКА: Показываем/скрываем вкладки ---
                if dm_source == "Файлы клиента (csv, txt)":
                    management_notebook.hide(api_tab)
                    management_notebook.tab(upload_tab, state="normal")
                else:
                    management_notebook.hide(upload_tab)
                    management_notebook.tab(api_tab, state="normal" if order_status in ('delta', 'dmkod') else "disabled")


 
            tree.bind("<<TreeviewSelect>>", on_order_select) # Привязываем обработчик выбора

            # --- Заполнение элементов управления над таблицей ---
            ttk.Button(order_controls_frame, text="Обновить", command=load_data).pack(side=tk.LEFT)

            # --- Заполнение нижней панели (статистика) ---
            ttk.Label(bottom_pane, text="Раздел статистики в разработке.").pack(expand=True)

            # Первоначальная загрузка
            load_data()
            return load_data

        # Создаем обе вкладки
        refresh_in_progress = _create_orders_view(in_progress_frame, is_archive=False)
        refresh_archive = _create_orders_view(archive_frame, is_archive=True)

        # При переключении вкладок можно добавить автообновление
        def on_tab_change(event):
            if notebook.index(notebook.select()) == 0:
                # --- НОВЫЙ БЛОК: Логика для вкладки "Загрузка кодов" ---
                # Этот блок будет пустым, так как вкладка скрыта по умолчанию
                pass
            elif notebook.index(notebook.select()) == 1:
                refresh_in_progress()
            else:
                refresh_archive()
        notebook.bind("<<NotebookTabChanged>>", on_tab_change)

    def _get_test_datamatrix_data(self):
        """
        Получает один тестовый код DataMatrix из базы данных клиента.
        Возвращает словарь с данными для печати или None в случае ошибки.
        """
        try:
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Загружаем один любой код из таблицы items
                    cur.execute("SELECT datamatrix FROM items WHERE datamatrix IS NOT NULL LIMIT 1")
                    result = cur.fetchone()
            
            if not result or not result.get('datamatrix'):
                messagebox.showwarning("Нет данных", "Не удалось найти ни одного кода DataMatrix в базе данных для тестовой печати.", parent=self)
                return None

            return {
                "items.datamatrix": result.get('datamatrix'),
                # Добавляем заглушки для других возможных полей в макете, чтобы избежать ошибок.
                "QR: Конфигурация сервера": json.dumps({"error": "not applicable"}),
                "QR: Конфигурация рабочего места": json.dumps({"error": "not applicable"}),
                "ap_workplaces.warehouse_name": "Тест DataMatrix (из БД)",
                "ap_workplaces.workplace_number": "0"
            }
        except Exception as e:
            logging.error(f"Ошибка при получении тестового DataMatrix: {e}", exc_info=True)
            messagebox.showerror("Ошибка БД", f"Не удалось получить тестовый код из базы данных: {e}", parent=self)
            return None

    def _open_dm_test_print_dialog(self):
        item_data = self._get_test_datamatrix_data()
        if item_data:
            PrintWorkplaceLabelsDialog(self, self.user_info, "Тестирование DataMatrix", [item_data])


    def _create_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Выход", command=self.quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        # Меню для управления устройствами
        devices_menu = tk.Menu(menubar, tearoff=0)
        devices_menu.add_command(label="Управление печатью", command=lambda: open_print_management_window(self))
        devices_menu.add_separator()
        devices_menu.add_command(label="Тестирование ДМ", command=self._open_dm_test_print_dialog)
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

class GenericEditorDialog(tk.Toplevel):
    """Универсальный диалог для редактирования записи справочника."""
    def __init__(self, parent, title, columns, item_data=None, pk_field=None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.entries = {}

        frame = ttk.Frame(self, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        # --- ИСПРАВЛЕНИЕ: Гарантируем, что item_data - это словарь, даже если он пустой ---
        if item_data is None:
            item_data = {}

        for i, (key, (label, _, _)) in enumerate(columns.items()):
            ttk.Label(frame, text=f"{label}:").grid(row=i, column=0, sticky="w", padx=5, pady=3)
            if key == 'fias_required':
                # Преобразуем строковое 'True'/'False' в булево
                initial_value = str(item_data.get(key, 'False')).lower() == 'true'
                var = tk.BooleanVar(value=initial_value)
                entry = ttk.Checkbutton(frame, variable=var)
                self.entries[key] = var
            else:
                entry = ttk.Entry(frame, width=50)
                entry.insert(0, str(item_data.get(key, '')))
                # --- ИСПРАВЛЕНИЕ: Блокируем редактирование первичного ключа ---
                # Это предотвращает случайное изменение GTIN и создание дубликата.
                if item_data and key == pk_field:
                    entry.config(state='readonly')
                self.entries[key] = entry
            entry.grid(row=i, column=1, sticky="ew", padx=5, pady=2)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=len(columns), column=0, columnspan=2, pady=(10,0), sticky="e")
        ttk.Button(button_frame, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=self.destroy).pack(side=tk.LEFT)

    def _on_ok(self):
        self.result = {}
        for key, widget in self.entries.items():
            self.result[key] = widget.get()
        self.destroy()

class ScenarioEditorDialog(tk.Toplevel):
    """Кастомный редактор для 'Сценариев маркировки'."""
    def __init__(self, parent, user_info, item_data=None):
        super().__init__(parent)
        self.title("Редактор сценария маркировки")
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.widgets = {}

        # Инициализация данных
        # --- ИСПРАВЛЕНИЕ: Глубокое копирование, чтобы изменения в редакторе не затрагивали кэш ---
        self.item_data = json.loads(json.dumps(item_data)) if item_data else {}
        self.scenario_data = self.item_data.get('scenario_data', {})

        # --- Создание виджетов ---
        main_frame = ttk.Frame(self, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Название сценария
        ttk.Label(main_frame, text="Название сценария:").pack(anchor="w")
        self.name_entry = ttk.Entry(main_frame, width=60)
        self.name_entry.insert(0, self.item_data.get('name', ''))
        self.name_entry.pack(fill="x", pady=(0, 10))

        # 2. Тип сценария
        ttk.Label(main_frame, text="Тип сценария:").pack(anchor="w")
        self.scenario_type_var = tk.StringVar(value=self.scenario_data.get('type', 'Маркировка'))
        scenario_type_combo = ttk.Combobox(main_frame, textvariable=self.scenario_type_var, values=['Маркировка', 'Ручная агрегация'], state='readonly')
        scenario_type_combo.pack(fill="x")
        scenario_type_combo.bind("<<ComboboxSelected>>", self._on_type_change)

        # 3. Контейнеры для опций
        self.marking_frame = ttk.LabelFrame(main_frame, text="Опции маркировки", padding="10")
        self.aggregation_frame = ttk.LabelFrame(main_frame, text="Опции ручной агрегации", padding="10")

        self._create_marking_widgets(self.marking_frame)
        self._create_manual_aggregation_widgets(self.aggregation_frame)

        # Кнопки
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x", pady=(15, 0))
        ttk.Button(button_frame, text="Сохранить", command=self._on_ok).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=self.destroy).pack(side=tk.RIGHT)

        self._on_type_change() # Первоначальная настройка видимости

    def _create_marking_widgets(self, parent):
        # Источник кодов ДМ
        ttk.Label(parent, text="Источник кодов ДМ:").pack(anchor="w")
        self.widgets['dm_source'] = tk.StringVar(value=self.scenario_data.get('dm_source', 'Заказ в ДМ.Код'))
        ttk.Combobox(parent, textvariable=self.widgets['dm_source'], values=['Заказ в ДМ.Код', 'Файлы клиента (csv, txt)', 'Внешняя система (1С)', 'Без кодов ДМ'], state='readonly').pack(fill="x", pady=(0, 5))

        # Агрегация
        self.widgets['aggregation_needed'] = tk.BooleanVar(value=self.scenario_data.get('aggregation_needed', False))
        ttk.Checkbutton(parent, text="Нужна агрегация", variable=self.widgets['aggregation_needed']).pack(anchor="w")

        # Источник SSCC
        ttk.Label(parent, text="Источник кодов SSCC:").pack(anchor="w", pady=(5,0))
        self.widgets['sscc_source'] = tk.StringVar(value=self.scenario_data.get('sscc_source', 'Генерировать самостоятельно'))
        ttk.Combobox(parent, textvariable=self.widgets['sscc_source'], values=['Генерировать самостоятельно', 'Предоставит клиент'], state='readonly').pack(fill="x", pady=(0, 5))

        # Постобработка
        ttk.Label(parent, text="Постобработка:").pack(anchor="w")
        self.widgets['post_processing'] = tk.StringVar(value=self.scenario_data.get('post_processing', 'Печать через Bartender'))
        ttk.Combobox(parent, textvariable=self.widgets['post_processing'], values=['Печать через Bartender', 'Внешнее ПО', 'Собственный алгоритм'], state='readonly').pack(fill="x", pady=(0, 5))

        # --- ИСПРАВЛЕНИЕ: Используем общие переменные для доп. опций ---
        self.widgets['clarify_prod_date'] = tk.BooleanVar(value=self.scenario_data.get('clarify_prod_date', False))
        ttk.Checkbutton(parent, text="Уточнить дату производства", variable=self.widgets['clarify_prod_date']).pack(anchor="w")
        self.widgets['clarify_prod_country'] = tk.BooleanVar(value=self.scenario_data.get('clarify_prod_country', False))
        ttk.Checkbutton(parent, text="Уточнить страну производства", variable=self.widgets['clarify_prod_country']).pack(anchor="w")

    def _create_manual_aggregation_widgets(self, parent):
        # Варианты агрегации
        ttk.Label(parent, text="Варианты агрегации:").pack(anchor="w")
        self.widgets['manual_agg_variant'] = tk.StringVar(value=self.scenario_data.get('manual_agg_variant', 'Агрегация в набор'))
        ttk.Combobox(parent, textvariable=self.widgets['manual_agg_variant'], values=['Агрегация в набор', 'Агрегация в короб', 'Агрегация в набор а затем в короб'], state='readonly').pack(fill="x", pady=(0, 5))

        # --- ИСПРАВЛЕНИЕ: Используем те же общие переменные, что и для маркировки ---
        # Виджеты будут созданы в _create_marking_widgets, здесь мы их просто используем.
        # Чтобы они не дублировались, мы можем просто перенести их создание в одно место
        # или, для простоты, просто создать их еще раз, но привязать к тем же переменным.
        ttk.Checkbutton(parent, text="Уточнить дату производства", variable=self.widgets['clarify_prod_date']).pack(anchor="w")
        ttk.Checkbutton(parent, text="Уточнить страну производства", variable=self.widgets['clarify_prod_country']).pack(anchor="w")

    def _on_type_change(self, event=None):
        """Показывает/скрывает фреймы в зависимости от типа сценария."""
        selected_type = self.scenario_type_var.get()
        if selected_type == 'Маркировка':
            self.marking_frame.pack(fill="x", expand=True, pady=5)
            self.aggregation_frame.pack_forget()
        elif selected_type == 'Ручная агрегация':
            self.marking_frame.pack_forget()
            self.aggregation_frame.pack(fill="x", expand=True, pady=5)
        else:
            self.marking_frame.pack_forget()
            self.aggregation_frame.pack_forget()

    def _on_ok(self):
        """Собирает данные из виджетов и формирует результат."""
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("Внимание", "Название сценария не может быть пустым.", parent=self)
            return

        scenario_data = {'type': self.scenario_type_var.get()}

        if scenario_data['type'] == 'Маркировка':
            scenario_data['dm_source'] = self.widgets['dm_source'].get()
            scenario_data['aggregation_needed'] = self.widgets['aggregation_needed'].get()
            if scenario_data['aggregation_needed']:
                scenario_data['sscc_source'] = self.widgets['sscc_source'].get()
            scenario_data['post_processing'] = self.widgets['post_processing'].get()
            if scenario_data['post_processing'] == 'Собственный алгоритм':
                scenario_data['clarify_prod_date'] = self.widgets['clarify_prod_date'].get()
                scenario_data['clarify_prod_country'] = self.widgets['clarify_prod_country'].get()

        elif scenario_data['type'] == 'Ручная агрегация':
            scenario_data['manual_agg_variant'] = self.widgets['manual_agg_variant'].get()
            scenario_data['clarify_prod_date'] = self.widgets['clarify_prod_date'].get()
            scenario_data['clarify_prod_country'] = self.widgets['clarify_prod_country'].get()

        self.result = {
            'id': self.item_data.get('id'),
            'name': name,
            'scenario_data': scenario_data
        }
        self.destroy()

class CalendarDialog(tk.Toplevel): # Этот класс остается Toplevel, так как он всегда должен быть модальным
    def __init__(self, parent, initial_date=None): # Переименованный класс
        super().__init__(parent)
        self.title("Выберите дату")
        self.transient(parent)
        self.grab_set()
        self.result = None
        if initial_date:
            self._current_date = initial_date
        else:
            self._current_date = datetime.now()

        self._create_widgets()
        self._update_calendar()

    def _create_widgets(self): # Этот метод остается без изменений
        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=5)
        ttk.Button(nav_frame, text="<", command=self._prev_month).pack(side=tk.LEFT)
        self.month_year_label = ttk.Label(nav_frame, font=("Arial", 12, "bold"), width=20, anchor="center")
        self.month_year_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(nav_frame, text=">", command=self._next_month).pack(side=tk.LEFT)
        self.calendar_frame = ttk.Frame(self)
        self.calendar_frame.pack(padx=10, pady=10)

    def _update_calendar(self): # Этот метод остается без изменений
        for widget in self.calendar_frame.winfo_children():
            widget.destroy()

        self.month_year_label.config(text=self._current_date.strftime("%B %Y"))

        days_of_week = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        for i, day in enumerate(days_of_week):
            ttk.Label(self.calendar_frame, text=day).grid(row=0, column=i, padx=2, pady=2)

        first_day_of_month = self._current_date.replace(day=1)
        start_weekday = first_day_of_month.weekday() # 0=Пн, 6=Вс

        import calendar
        month_days = calendar.monthrange(self._current_date.year, self._current_date.month)[1]

        current_day = 1
        for row in range(1, 7):
            for col in range(7):
                if (row == 1 and col < start_weekday) or current_day > month_days:
                    continue
                
                btn = ttk.Button(self.calendar_frame, text=str(current_day), width=4,
                                 command=lambda d=current_day: self._select_date(d))
                btn.grid(row=row, column=col, padx=1, pady=1)
                current_day += 1

    def _select_date(self, day):
        self.result = self._current_date.replace(day=day).date()
        self.destroy()

    def _prev_month(self):
        self._current_date = self._current_date - pd.DateOffset(months=1)
        self._update_calendar()

    def _next_month(self):
        self._current_date = self._current_date + pd.DateOffset(months=1)
        self._update_calendar()