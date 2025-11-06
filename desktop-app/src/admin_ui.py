# src/admin_ui.py

import tkinter as tk
import logging

from tkinter import ttk, messagebox, filedialog, simpledialog
import logging
import threading
import json
import pandas as pd
import io
import os
from datetime import datetime

# --- Добавляем глобальный импорт Pillow ---
try:
    from PIL import Image, ImageTk

except ImportError:
    Image = None # Помечаем как недоступный, если Pillow не установлен

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [admin_ui.py] - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log', encoding='utf-8')  # Или другой путь к лог-файлу
    ])

# Импорты для работы с БД и QR-кодами
from .db_connector import get_main_db_connection
from .api_service import ApiService
import bcrypt
import psycopg2
import psycopg2.extras

# Импортируем новый сервис печати
from .printing_service import PrintingService, LabelEditorWindow, ImageSelectionDialog

import requests
from datetime import datetime
import traceback

import zlib, base64 # Для сжатия данных QR-кода

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
        Новая логика: формирует QR-код для настройки сервера и открывает диалог печати.
        В QR-код помещаются базовые данные для подключения, но без SSL-сертификата,
        чтобы код оставался компактным и легко читаемым.

        ИЗМЕНЕНИЕ: Теперь генерируется многочастный QR-код для печати,
        включающий и настройки, и SSL-сертификат для полной офлайн-настройки.
        - Часть 1: Основные настройки.
        - Части 2-11: SSL-сертификат, разделенный ровно на 10 частей.
        """
        # 1. Получаем конфигурацию БД клиента из user_info
        config_data = user_info.get('client_db_config', {}).copy()
        if not config_data:
            messagebox.showerror("Ошибка", "Конфигурация базы данных клиента не найдена в данных пользователя.", parent=setup_window)
            return

        # 2. Определяем адрес сервера
        final_address = server_address_entry.get().strip() or config_data.get('db_host')
        if not final_address:
            messagebox.showerror("Ошибка", "Не удалось определить адрес сервера. Введите его вручную или убедитесь, что он есть в конфигурации клиента.", parent=setup_window)
            return

        # 3. Разделяем данные: основные настройки и сертификат
        ssl_cert_content = config_data.pop('db_ssl_cert', '') # Извлекаем сертификат
        
        main_config = {
            "type": "server_config_main",
            "cert_parts_count": 10, # Фиксированное количество частей для сертификата
            "address": final_address,
            "db_name": config_data.get("db_name"),
            "db_user": config_data.get("db_user"),
            "db_password": config_data.get("db_password"),
            "db_port": config_data.get("db_port")
        }

        # 4. Разбиваем сертификат ровно на 10 частей
        cert_len = len(ssl_cert_content)
        # Вычисляем размер каждой части с округлением вверх
        cert_chunk_size = (cert_len + 9) // 10
        cert_chunks = [ssl_cert_content[i:i + cert_chunk_size] for i in range(0, cert_len, cert_chunk_size)]
        # Дополняем список пустыми строками, если частей меньше 10
        cert_chunks.extend([''] * (10 - len(cert_chunks)))

        # 5. Формируем итоговый список данных для печати (1 + 10 этикеток)
        items_to_print = []
        # Первая этикетка - основные настройки
        items_to_print.append({
            "QR: Конфигурация сервера": json.dumps(main_config, ensure_ascii=False),
            "QR: Конфигурация рабочего места": json.dumps({"error": "not applicable"}),
            "ap_workplaces.warehouse_name": "Настройка сервера (основное)",
            "ap_workplaces.workplace_number": 0
        })
        # Следующие 10 этикеток - части сертификата
        for i, cert_part in enumerate(cert_chunks):
            cert_part_data = {"type": "server_config_cert", "part_index": i + 1, "total_parts": 10, "data": cert_part}
            items_to_print.append({
                "QR: Конфигурация сервера": json.dumps(cert_part_data, ensure_ascii=False),
                "QR: Конфигурация рабочего места": json.dumps({"error": "not applicable"}),
                "ap_workplaces.warehouse_name": f"Сертификат (часть {i+1}/10)",
                "ap_workplaces.workplace_number": 0
            })

        # 6. Открываем диалог печати, передавая ему список всех 11 частей.
        PrintWorkplaceLabelsDialog(setup_window, user_info, f"Настройка сервера: {final_address}", items_to_print)

    ttk.Button(config_frame, text="Сгенерировать QR-код", command=generate_server_config_qr).pack(pady=20)

    # --- Вкладка 2: Рабочие места ---
    workplaces_frame = ttk.Frame(notebook, padding="10")
    notebook.add(workplaces_frame, text="Рабочие места")

    # --- НОВАЯ ЛОГИКА ДЛЯ ВКЛАДКИ "РАБОЧИЕ МЕСТА" ---

    def get_client_db_connection(): # Эта функция локальна для open_workplace_setup_window
        """Вспомогательная функция для подключения к БД клиента."""
        # Используем универсальный метод из PrintingService
        return PrintingService._get_client_db_connection(user_info)

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
        Использует универсальный метод из PrintingService."""
        return PrintingService._get_client_db_connection(self.user_info)

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

class AdminWindow(tk.Tk):
    """Главное окно для роли 'администратор'."""
    def __init__(self, user_info):
        super().__init__()
        self.user_info = user_info
        self.title(f"ТильдаКод [Пользователь: {self.user_info['name']}, Роль: {self.user_info['role']}]")
        self.geometry("600x400")
 
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

    def _set_api_status_color(self, is_valid):
        color = "green" if is_valid else "red"
        self.api_status_indicator.delete("all")
        self.api_status_indicator.create_oval(2, 2, 14, 14, fill=color, outline="")

    def _create_supply_notice_tab(self, parent_frame):
        from .supply_notification_service import SupplyNotificationService
        service = SupplyNotificationService(lambda: PrintingService._get_client_db_connection(self.user_info))
 
        controls = ttk.Frame(parent_frame)
        controls.pack(fill=tk.X, pady=5)

        cols = ('id', 'scenario_name', 'client_name', 'product_groups', 'planned_arrival_date', 
                'vehicle_number', 'status', 'positions_count', 'dm_count', 'actions')
        
        tree = ttk.Treeview(parent_frame, columns=cols, show='headings')
        
        col_map = {
            'id': ('ID', 10, 'center'),
            'scenario_name': ('Сценарий', 150, 'w'),
            'client_name': ('Клиент', 200, 'w'),
            'product_groups': ('Товарные группы', 300, 'w'),
            'planned_arrival_date': ('Дата прибытия', 100, 'center'),
            'vehicle_number': ('Номер Контейнера/ТС', 100, 'center'),
            'status': ('Статус', 100, 'center'),
            'positions_count': ('Позиций', 70, 'center'),
            'dm_count': ('Кодов ДМ', 80, 'center'),
            'actions': ('Действия', 60, 'center')
        }

        for col_key, (text, width, anchor) in col_map.items():
            tree.heading(col_key, text=text)
            tree.column(col_key, width=width, anchor=anchor)

        tree.pack(expand=True, fill='both')

        # Настройка тегов для подсветки строк
        tree.tag_configure('Проект', background='light yellow')
        tree.tag_configure('Ожидание', background='light green')

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

        def open_notification_editor(notification_id=None):
            """Открывает диалог для создания/редактирования уведомления."""
            logging.info(f"Вызвана функция open_notification_editor с notification_id: {notification_id}, тип: {type(notification_id)}")
            if notification_id:
                logging.info(f"Открытие редактора для существующего уведомления ID: {notification_id}")
            else:
                logging.info("Открытие редактора для создания нового уведомления.")
            
            # --- ИСПРАВЛЕНИЕ: Передаем user_info и notification_id в правильный конструктор ---
            dialog = NotificationEditorDialog(self, user_info=self.user_info, notification_id=notification_id)
            # --- ИЗМЕНЕНИЕ: Убираем ожидание, чтобы окно не было модальным ---
            logging.info("Экземпляр NotificationEditorDialog создан.")
            # self.wait_window(dialog) # Убираем блокировку
            # Вместо этого, диалог сам вызовет обновление списка при успешном сохранении
            dialog.on_save_callback = refresh_notifications
            if getattr(dialog, 'result', False):
                logging.info("Диалог уведомлений завершился успешно. Обновление списка...")
                refresh_notifications()

        def archive_notification():
            selected_item = tree.focus()
            if not selected_item: return
            if messagebox.askyesno("Подтверждение", "Переместить уведомление в архив?", parent=self):
                try:
                    service.archive_notification(int(selected_item))
                    refresh_notifications()
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось архивировать уведомление: {e}", parent=self)

        def show_context_menu(event):
            item_id = tree.identify_row(event.y)
            logging.info(f"Правый клик мыши. Событие: y={event.y}. Определен ID строки: {item_id}")
            if not item_id: return
            
            tree.selection_set(item_id) # Выделяем строку, по которой кликнули
            
            menu = tk.Menu(self, tearoff=0)
            # Добавляем логирование прямо в команду меню
            def deferred_open_editor(uid):
                logging.info(f"Выбран пункт меню 'Редактировать' для ID: {uid}")
                # ИСПОЛЬЗУЕМ self.after, чтобы отложить вызов и избежать конфликта модальных окон
                self.after(1, lambda: open_notification_editor(uid))
            menu.add_command(label="Редактировать", command=lambda item_id=item_id: deferred_open_editor(item_id))
            menu.add_command(label="Создать заказ", command=lambda: messagebox.showinfo("В разработке", f"Создание заказа для уведомления {item_id}"))
            menu.add_separator()
            menu.add_command(label="Удалить в архив", command=archive_notification)
            menu.post(event.x_root, event.y_root)

        ttk.Button(controls, text="Создать новое уведомление", command=lambda: open_notification_editor()).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Обновить", command=refresh_notifications).pack(side=tk.LEFT, padx=2)

        tree.bind("<Button-3>", show_context_menu) # Правый клик

        refresh_notifications()

    def _create_catalogs_tab(self, parent_frame):
        logger = logging.getLogger(__name__)
        """Создает содержимое для вкладки 'Справочники'."""
        # --- ИЗМЕНЕНИЕ: Передаем в сервис функцию для подключения к БД клиента ---
        from .catalogs_service import CatalogsService
        service = CatalogsService(self.user_info, lambda: PrintingService._get_client_db_connection(self.user_info))

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
                'id': ('ID', 50, 'center'),
                'name': ('Название сценария', 150, 'w'),
                'scenario_data': ('Параметры (JSON)', 300, 'w')
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
            if not filepath: return
            try:
                # --- ИСПРАВЛЕНИЕ: Явно указываем, что ключевые поля (gtin, id) должны быть текстом ---
                # Это предотвращает потерю ведущих нулей в GTIN.
                # Остальные поля пусть pandas определяет автоматически.
                df = pd.read_excel(filepath, dtype={pk_field: str})
                df = df.where(pd.notna(df), None) # Заменяем NaN на None
                service_methods['import'](df)
                refresh_data()
                messagebox.showinfo("Успех", "Данные успешно импортированы.", parent=self)
            except Exception as e:
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
        """Создает содержимое для вкладки 'Заказы'."""
        controls_frame = ttk.Frame(parent_frame)
        controls_frame.pack(fill=tk.X, pady=5)
 
        def load_orders():
            for i in orders_tree.get_children():
                orders_tree.delete(i)
            try:
                with PrintingService._get_client_db_connection(self.user_info) as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT id, client_name, order_date, status FROM orders ORDER BY id DESC")
                        for order in cur.fetchall():
                            orders_tree.insert('', 'end', values=(order['id'], order['client_name'], order['order_date'], order['status']))
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить заказы: {e}", parent=self)
 
        ttk.Button(controls_frame, text="Обновить", command=load_orders).pack(side=tk.LEFT)
 
        tree_frame = ttk.Frame(parent_frame)
        tree_frame.pack(expand=True, fill="both")
 
        orders_tree = ttk.Treeview(tree_frame, columns=('id', 'client', 'date', 'status'), show='headings')
        orders_tree.heading('id', text='ID')
        orders_tree.heading('client', text='Клиент')
        orders_tree.heading('date', text='Дата')
        orders_tree.heading('status', text='Статус')
        orders_tree.column('id', width=50, anchor=tk.CENTER)
        orders_tree.column('client', width=200)
        orders_tree.column('date', width=100)
        orders_tree.column('status', width=100)
        orders_tree.pack(expand=True, fill="both", side="left")
 
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=orders_tree.yview)
        orders_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
 
        load_orders()
 
    def _open_dm_test_print_dialog(self):
        """
        Открывает диалог печати для тестирования DataMatrix.
        Данные для кода будут получены автоматически сервисом печати
        согласно источнику данных, указанному в макете.
        """
        # Готовим "пустой" набор данных. Сервис печати сам подставит реальные
        # данные из БД, так как в макете указан источник "items.datamatrix".
        item_data_for_printing = {
            "items.datamatrix": None, # Значение будет получено из БД автоматически
            # Добавляем заглушки для других возможных полей в макете, чтобы избежать ошибок.
            "QR: Конфигурация сервера": json.dumps({"error": "not applicable"}),
            "QR: Конфигурация рабочего места": json.dumps({"error": "not applicable"}),
            "ap_workplaces.warehouse_name": "Тест DataMatrix (из БД)",
            "ap_workplaces.workplace_number": "0" # ИСПРАВЛЕНИЕ: Преобразуем в строку, чтобы избежать ошибки 'int' object has no attribute 'isdigit'
        }

        # Вызываем нашу стандартную процедуру печати с предпросмотром.
        PrintWorkplaceLabelsDialog(self, self.user_info, "Тестирование DataMatrix", [item_data_for_printing])


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
        self.item_data = item_data if item_data else {}
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

class NotificationEditorDialog(tk.Toplevel):
    """Диалог для создания/редактирования уведомления."""
    def __init__(self, parent, initial_date=None):
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

    def _create_widgets(self):
        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=5)
        ttk.Button(nav_frame, text="<", command=self._prev_month).pack(side=tk.LEFT)
        self.month_year_label = ttk.Label(nav_frame, font=("Arial", 12, "bold"), width=20, anchor="center")
        self.month_year_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(nav_frame, text=">", command=self._next_month).pack(side=tk.LEFT)

        self.calendar_frame = ttk.Frame(self)
        self.calendar_frame.pack(padx=10, pady=10)

    def _update_calendar(self):
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

class NewNotificationDialog(tk.Toplevel):
    """Диалог для создания/редактирования уведомления."""
    def __init__(self, parent, title="Новое уведомление", initial_name="", initial_date_str=""):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result = None

        frame = ttk.Frame(self, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        # Наименование
        ttk.Label(frame, text="Наименование:").grid(row=0, column=0, sticky="w", pady=2)
        name_frame = ttk.Frame(frame)
        name_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.name_entry = ttk.Entry(name_frame, width=40)
        self.name_entry.insert(0, initial_name)
        self.name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(name_frame, text="Вставить", command=self._paste_name).pack(side=tk.LEFT, padx=(5,0))

        # Дата
        ttk.Label(frame, text="Планируемая дата прибытия:").grid(row=2, column=0, sticky="w", pady=(10, 2))
        date_frame = ttk.Frame(frame)
        date_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.date_var = tk.StringVar(value=initial_date_str)
        self.date_entry = ttk.Entry(date_frame, textvariable=self.date_var, width=40)
        self.date_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(date_frame, text="...", width=3, command=self._open_calendar).pack(side=tk.LEFT, padx=(5,0))

        # Кнопки OK/Отмена
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=2, pady=(20, 0), sticky="e")
        ttk.Button(button_frame, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=self.destroy).pack(side=tk.LEFT)

        self.name_entry.focus_set()

    def _paste_name(self):
        try:
            clipboard_text = self.clipboard_get()
            self.name_entry.delete(0, tk.END)
            self.name_entry.insert(0, clipboard_text)
        except tk.TclError:
            messagebox.showwarning("Буфер обмена", "Буфер обмена пуст или содержит нетекстовые данные.", parent=self)

    def _open_calendar(self):
        try:
            initial_date = datetime.strptime(self.date_var.get(), "%Y-%m-%d")
        except ValueError:
            initial_date = datetime.now()
        
        cal_dialog = CalendarDialog(self, initial_date=initial_date)
        self.wait_window(cal_dialog)
        if cal_dialog.result:
            self.date_var.set(cal_dialog.result.strftime("%Y-%m-%d"))

    def _on_ok(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("Внимание", "Наименование не может быть пустым.", parent=self)
            return

        date_str = self.date_var.get().strip()
        arrival_date = None
        if date_str:
            try:
                arrival_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                messagebox.showerror("Ошибка", "Неверный формат даты. Используйте ГГГГ-ММ-ДД или выберите из календаря.", parent=self)
                return

        self.result = (name, arrival_date)
        self.destroy()
class CalendarDialog(tk.Toplevel): # Этот класс остается, так как он используется в NewNotificationDialog
    """Диалоговое окно с простым календарем."""
    def __init__(self, parent, initial_date=None):
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

    def _create_widgets(self):
        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=5)
        ttk.Button(nav_frame, text="<", command=self._prev_month).pack(side=tk.LEFT)
        self.month_year_label = ttk.Label(nav_frame, font=("Arial", 12, "bold"), width=20, anchor="center")
        self.month_year_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(nav_frame, text=">", command=self._next_month).pack(side=tk.LEFT)

        self.calendar_frame = ttk.Frame(self)
        self.calendar_frame.pack(padx=10, pady=10)

    def _update_calendar(self):
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

class NewNotificationDialog(tk.Toplevel):
    """Диалог для создания/редактирования уведомления."""
    def __init__(self, parent, title="Новое уведомление", initial_name="", initial_date_str=""):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result = None

        frame = ttk.Frame(self, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        # Наименование
        ttk.Label(frame, text="Наименование:").grid(row=0, column=0, sticky="w", pady=2)
        name_frame = ttk.Frame(frame)
        name_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.name_entry = ttk.Entry(name_frame, width=40)
        self.name_entry.insert(0, initial_name)
        self.name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(name_frame, text="Вставить", command=self._paste_name).pack(side=tk.LEFT, padx=(5,0))

        # Дата
        ttk.Label(frame, text="Планируемая дата прибытия:").grid(row=2, column=0, sticky="w", pady=(10, 2))
        date_frame = ttk.Frame(frame)
        date_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.date_var = tk.StringVar(value=initial_date_str)
        self.date_entry = ttk.Entry(date_frame, textvariable=self.date_var, width=40)
        self.date_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(date_frame, text="...", width=3, command=self._open_calendar).pack(side=tk.LEFT, padx=(5,0))

        # Кнопки OK/Отмена
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=2, pady=(20, 0), sticky="e")
        ttk.Button(button_frame, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=self.destroy).pack(side=tk.LEFT)

        self.name_entry.focus_set()

    def _paste_name(self):
        try:
            clipboard_text = self.clipboard_get()
            self.name_entry.delete(0, tk.END)
            self.name_entry.insert(0, clipboard_text)
        except tk.TclError:
            messagebox.showwarning("Буфер обмена", "Буфер обмена пуст или содержит нетекстовые данные.", parent=self)

    def _open_calendar(self):
        try:
            initial_date = datetime.strptime(self.date_var.get(), "%Y-%m-%d")
        except ValueError:
            initial_date = datetime.now()
        
        cal_dialog = CalendarDialog(self, initial_date=initial_date)
        self.wait_window(cal_dialog)
        if cal_dialog.result:
            self.date_var.set(cal_dialog.result.strftime("%Y-%m-%d"))

    def _on_ok(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("Внимание", "Наименование не может быть пустым.", parent=self)
            return

        date_str = self.date_var.get().strip()
        arrival_date = None
        if date_str:
            try:
                arrival_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                messagebox.showerror("Ошибка", "Неверный формат даты. Используйте ГГГГ-ММ-ДД или выберите из календаря.", parent=self)
                return

        self.result = (name, arrival_date)
        self.destroy()

        notebook = ttk.Notebook(parent_frame)
        notebook.pack(expand=True, fill="both")

        # --- Вкладка 1: Участники (существующая логика) ---
        participants_frame = ttk.Frame(notebook, padding="10")
        notebook.add(participants_frame, text="Участники")

        participants_controls = ttk.Frame(participants_frame)
        participants_controls.pack(fill=tk.X, pady=5)

        # --- ИЗМЕНЕНИЕ: Обновляем колонки в таблице ---
        participants_tree = ttk.Treeview(participants_frame, columns=('name', 'inn', 'poa_end'), show='headings')
        participants_tree.heading('name', text='Наименование')
        participants_tree.heading('inn', text='Источник (ИНН)')
        participants_tree.heading('poa_end', text='Окончание доверенности')
        participants_tree.column('name', width=300)
        participants_tree.column('inn', width=150, anchor=tk.CENTER)
        participants_tree.column('poa_end', width=150, anchor=tk.CENTER)
        participants_tree.pack(expand=True, fill='both')

        def refresh_participants_list():
            # Очищаем дерево перед обновлением
            for i in participants_tree.get_children():
                participants_tree.delete(i)
            try:
                participants_list = service.get_participants_catalog()
                # --- ИЗМЕНЕНИЕ: Заполняем новые колонки ---
                for n in participants_list:
                    # Извлекаем дату и обрезаем время
                    poa_end_date = n.get('poa_validity_end', '')
                    if poa_end_date and 'T' in poa_end_date:
                        poa_end_date = poa_end_date.split('T')[0]
                    participants_tree.insert('', 'end', values=(n.get('name', ''), n.get('inn', ''), poa_end_date))
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить справочник: {e}", parent=self)

        ttk.Button(participants_controls, text="Обновить", command=refresh_participants_list).pack(side=tk.LEFT, padx=2)
        refresh_participants_list()

        # --- Вкладка 2: Товарные группы ---
        product_groups_frame = ttk.Frame(notebook, padding="10")
        notebook.add(product_groups_frame, text="Товарные группы")

        pg_controls = ttk.Frame(product_groups_frame)
        pg_controls.pack(fill=tk.X, pady=5)

        pg_tree = ttk.Treeview(product_groups_frame, columns=('id', 'name', 'display_name'), show='headings')
        pg_tree.heading('id', text='ID')
        pg_tree.heading('name', text='Системное имя')
        pg_tree.heading('display_name', text='Отображаемое имя')
        pg_tree.column('id', width=50, anchor=tk.CENTER)
        pg_tree.column('name', width=200)
        pg_tree.column('display_name', width=300)
        pg_tree.pack(expand=True, fill='both')

        def refresh_product_groups():
            for i in pg_tree.get_children(): pg_tree.delete(i)
            try:
                groups = service.get_product_groups()
                for group in groups:
                    pg_tree.insert('', 'end', values=(group['id'], group['group_name'], group['display_name']))
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить товарные группы: {e}", parent=self)

        ttk.Button(pg_controls, text="Обновить", command=refresh_product_groups).pack(side=tk.LEFT, padx=2)
        refresh_product_groups()

        # --- Вкладка 3: Товары ---
        products_frame = ttk.Frame(notebook, padding="10")
        notebook.add(products_frame, text="Товары")

        products_controls = ttk.Frame(products_frame)
        products_controls.pack(fill=tk.X, pady=5)

        products_tree = ttk.Treeview(products_frame, columns=('gtin', 'name'), show='headings')
        products_tree.heading('gtin', text='GTIN')
        products_tree.heading('name', text='Наименование')
        products_tree.column('gtin', width=150, anchor=tk.CENTER)
        products_tree.column('name', width=400)
        products_tree.pack(expand=True, fill='both')

        def refresh_products():
            for i in products_tree.get_children(): products_tree.delete(i)
            try:
                products = service.get_products()
                for product in products:
                    products_tree.insert('', 'end', values=(product['gtin'], product['name']))
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить товары: {e}", parent=self)

        ttk.Button(products_controls, text="Обновить", command=refresh_products).pack(side=tk.LEFT, padx=2)
        refresh_products()

    def _create_orders_tab(self, parent_frame):
        """Создает содержимое для вкладки 'Заказы'."""
        controls_frame = ttk.Frame(parent_frame)
        controls_frame.pack(fill=tk.X, pady=5)
 
        def load_orders():
            for i in orders_tree.get_children():
                orders_tree.delete(i)
            try:
                with PrintingService._get_client_db_connection(self.user_info) as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT id, client_name, order_date, status FROM orders ORDER BY id DESC")
                        for order in cur.fetchall():
                            orders_tree.insert('', 'end', values=(order['id'], order['client_name'], order['order_date'], order['status']))
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить заказы: {e}", parent=self)
 
        ttk.Button(controls_frame, text="Обновить", command=load_orders).pack(side=tk.LEFT)
 
        tree_frame = ttk.Frame(parent_frame)
        tree_frame.pack(expand=True, fill="both")
 
        orders_tree = ttk.Treeview(tree_frame, columns=('id', 'client', 'date', 'status'), show='headings')
        orders_tree.heading('id', text='ID')
        orders_tree.heading('client', text='Клиент')
        orders_tree.heading('date', text='Дата')
        orders_tree.heading('status', text='Статус')
        orders_tree.column('id', width=50, anchor=tk.CENTER)
        orders_tree.column('client', width=200)
        orders_tree.column('date', width=100)
        orders_tree.column('status', width=100)
        orders_tree.pack(expand=True, fill="both", side="left")
 
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=orders_tree.yview)
        orders_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
 
        load_orders()
 
    def _open_dm_test_print_dialog(self):
        """
        Открывает диалог печати для тестирования DataMatrix.
        Данные для кода будут получены автоматически сервисом печати
        согласно источнику данных, указанному в макете.
        """
        # Готовим "пустой" набор данных. Сервис печати сам подставит реальные
        # данные из БД, так как в макете указан источник "items.datamatrix".
        item_data_for_printing = {
            "items.datamatrix": None, # Значение будет получено из БД автоматически
            # Добавляем заглушки для других возможных полей в макете, чтобы избежать ошибок.
            "QR: Конфигурация сервера": json.dumps({"error": "not applicable"}),
            "QR: Конфигурация рабочего места": json.dumps({"error": "not applicable"}),
            "ap_workplaces.warehouse_name": "Тест DataMatrix (из БД)",
            "ap_workplaces.workplace_number": "0" # ИСПРАВЛЕНИЕ: Преобразуем в строку, чтобы избежать ошибки 'int' object has no attribute 'isdigit'
        }

        # Вызываем нашу стандартную процедуру печати с предпросмотром.
        PrintWorkplaceLabelsDialog(self, self.user_info, "Тестирование DataMatrix", [item_data_for_printing])


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

class CalendarDialog(tk.Toplevel):
    """Диалоговое окно с простым календарем."""
    def __init__(self, parent, initial_date=None):
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

    def _create_widgets(self):
        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=5)
        ttk.Button(nav_frame, text="<", command=self._prev_month).pack(side=tk.LEFT)
        self.month_year_label = ttk.Label(nav_frame, font=("Arial", 12, "bold"), width=20, anchor="center")
        self.month_year_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(nav_frame, text=">", command=self._next_month).pack(side=tk.LEFT)

        self.calendar_frame = ttk.Frame(self)
        self.calendar_frame.pack(padx=10, pady=10)

    def _update_calendar(self):
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

class NewNotificationDialog(tk.Toplevel):
    """Диалог для создания/редактирования уведомления."""
    def __init__(self, parent, title="Новое уведомление", initial_name="", initial_date_str=""):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result = None

        frame = ttk.Frame(self, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        # Наименование
        ttk.Label(frame, text="Наименование:").grid(row=0, column=0, sticky="w", pady=2)
        name_frame = ttk.Frame(frame)
        name_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.name_entry = ttk.Entry(name_frame, width=40)
        self.name_entry.insert(0, initial_name)
        self.name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(name_frame, text="Вставить", command=self._paste_name).pack(side=tk.LEFT, padx=(5,0))

        # Дата
        ttk.Label(frame, text="Планируемая дата прибытия:").grid(row=2, column=0, sticky="w", pady=(10, 2))
        date_frame = ttk.Frame(frame)
        date_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.date_var = tk.StringVar(value=initial_date_str)
        self.date_entry = ttk.Entry(date_frame, textvariable=self.date_var, width=40)
        self.date_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(date_frame, text="...", width=3, command=self._open_calendar).pack(side=tk.LEFT, padx=(5,0))

        # Кнопки OK/Отмена
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=2, pady=(20, 0), sticky="e")
        ttk.Button(button_frame, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=self.destroy).pack(side=tk.LEFT)

        self.name_entry.focus_set()

    def _paste_name(self):
        try:
            clipboard_text = self.clipboard_get()
            self.name_entry.delete(0, tk.END)
            self.name_entry.insert(0, clipboard_text)
        except tk.TclError:
            messagebox.showwarning("Буфер обмена", "Буфер обмена пуст или содержит нетекстовые данные.", parent=self)

    def _open_calendar(self):
        try:
            initial_date = datetime.strptime(self.date_var.get(), "%Y-%m-%d")
        except ValueError:
            initial_date = datetime.now()
        
        cal_dialog = CalendarDialog(self, initial_date=initial_date)
        self.wait_window(cal_dialog)
        if cal_dialog.result:
            self.date_var.set(cal_dialog.result.strftime("%Y-%m-%d"))

    def _on_ok(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("Внимание", "Наименование не может быть пустым.", parent=self)
            return

        date_str = self.date_var.get().strip()
        arrival_date = None
        if date_str:
            try:
                arrival_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                messagebox.showerror("Ошибка", "Неверный формат даты. Используйте ГГГГ-ММ-ДД или выберите из календаря.", parent=self)
                return

        self.result = (name, arrival_date)
        self.destroy()

class _LegacyNotificationDialog(tk.Toplevel): # Переименовываем, чтобы избежать конфликта
    def __init__(self, parent, initial_date=None):
        super().__init__(parent)
        self.title("Редактор уведомления о поставке")
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.user_info = user_info
        self.notification_id = notification_id
        from .supply_notification_service import SupplyNotificationService
        self.service = SupplyNotificationService(lambda: PrintingService._get_client_db_connection(self.user_info))
        from .catalogs_service import CatalogsService
        self.catalog_service = CatalogsService(self.user_info, lambda: PrintingService._get_client_db_connection(self.user_info))

        self.initial_data = {}
        if notification_id:
            self.initial_data = self.service.get_notification_by_id(notification_id)

        self._create_widgets()

class NotificationEditorDialog(tk.Toplevel):
    """Диалог для создания/редактирования уведомления."""
    def __init__(self, parent, user_info, notification_id=None):
        super().__init__(parent)
        # --- ИСПРАВЛЕНИЕ: Устанавливаем заголовок в зависимости от режима (создание/редактирование) ---
        title = f"Редактирование уведомления №{notification_id}" if notification_id else "Новое уведомление о поставке"
        self.title(title)
        # --- ИЗМЕНЕНИЕ: Убираем модальность окна ---
        # self.transient(parent)
        # self.grab_set()
        self.result = None
        self.user_info = user_info
        self.notification_id = notification_id

        logging.info(f"Инициализация NotificationEditorDialog. ID: {self.notification_id}")

        # --- Инициализация сервисов ---
        from .supply_notification_service import SupplyNotificationService
        self.on_save_callback = None # Callback для обновления списка после сохранения
        self.service = SupplyNotificationService(lambda: PrintingService._get_client_db_connection(self.user_info))
        from .catalogs_service import CatalogsService
        self.catalog_service = CatalogsService(self.user_info, lambda: PrintingService._get_client_db_connection(self.user_info))

        self.initial_data = {}
        if notification_id:
            logging.info(f"Загрузка данных для уведомления ID: {notification_id}")
            self.initial_data = self.service.get_notification_by_id(notification_id)
            logging.info(f"Данные загружены: {self.initial_data}")

        self._create_widgets()

    def _create_widgets(self):
        logging.info("Начало создания виджетов в NotificationEditorDialog.")
        # --- ИЗМЕНЕНИЕ: Используем PanedWindow для разделения на верх и низ ---
        # The paned_window should be the main container for the dialog's content.
        # The previous main_frame and its contents were redundant and causing the issue.
        paned_window = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        # --- ИЗМЕНЕНИЕ: Создаем PanedWindow и добавляем в него верхнюю часть ---
        top_pane = ttk.Frame(paned_window, padding=10)
        paned_window.add(top_pane, weight=1)

        # --- Верхняя часть: Основная информация ---
        header_frame = ttk.LabelFrame(top_pane, text="Основная информация")
        header_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Сценарий маркировки
        ttk.Label(header_frame, text="Сценарий маркировки:").pack(anchor="w")
        self.scenario_var = tk.StringVar()
        self.scenario_combo = ttk.Combobox(header_frame, textvariable=self.scenario_var, state="readonly")
        self.scenario_combo.pack(fill=tk.X, pady=2)
        self._load_scenarios()
        # --- ИЗМЕНЕНИЕ: Привязываем событие смены сценария к обновлению списка клиентов ---
        self.scenario_combo.bind("<<ComboboxSelected>>", self._on_scenario_change)

        client_frame = ttk.Frame(header_frame)
        client_frame.pack(fill=tk.X, pady=2)
        ttk.Label(client_frame, text="Клиент:").pack(anchor="w")
        self.client_var = tk.StringVar()
        client_inner_frame = ttk.Frame(client_frame)
        client_inner_frame.pack(fill=tk.X)
        self.client_combo = ttk.Combobox(client_inner_frame, textvariable=self.client_var, state="readonly")
        self.client_combo.bind("<Button-1>", self._on_client_combo_click) # Добавляем обработчик для клика
        self.client_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        # --- ИЗМЕНЕНИЕ: Первоначальная загрузка клиентов теперь происходит после выбора сценария ---
        # self._load_clients() # Этот вызов будет сделан в _on_scenario_change

        # 3. Товарные группы
        ttk.Label(header_frame, text="Товарные группы:").pack(anchor="w")
        self.product_groups_listbox = tk.Listbox(header_frame, selectmode=tk.MULTIPLE, height=3)
        self.product_groups_listbox.pack(fill=tk.X, pady=2)
        self._load_product_groups()

        # 4. Предположительная дата прибытия (с календарем)
        ttk.Label(header_frame, text="Планируемая дата прибытия:").pack(anchor="w")
        self.arrival_date_var = tk.StringVar()
        date_frame = ttk.Frame(header_frame)
        date_frame.pack(fill=tk.X, pady=2)
        self.arrival_date_entry = ttk.Entry(date_frame, textvariable=self.arrival_date_var)
        self.arrival_date_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(date_frame, text="...", width=3, command=self._open_calendar_dialog).pack(side=tk.LEFT, padx=(5,0))

        # 5. Номер контейнера/автомобиля
        ttk.Label(header_frame, text="Номер контейнера/автомобиля:").pack(anchor="w")
        self.vehicle_number_entry = ttk.Entry(header_frame)
        self.vehicle_number_entry.pack(fill=tk.X, pady=2)

        # 6. Комментарии
        ttk.Label(header_frame, text="Комментарии:").pack(anchor="w")
        self.comments_text = tk.Text(header_frame, height=3)
        self.comments_text.pack(fill=tk.X, pady=2)

        # --- НОВЫЙ БЛОК: Документы от клиента (только в режиме редактирования) ---
        if self.notification_id:
            docs_frame = ttk.LabelFrame(top_pane, text="Документы от клиента")
            docs_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
            
            docs_controls = ttk.Frame(docs_frame)
            docs_controls.pack(fill=tk.X, pady=2)
            ttk.Button(docs_controls, text="Загрузить", command=self._upload_client_document).pack(side=tk.LEFT)
            ttk.Button(docs_controls, text="Скачать", command=self._download_client_document).pack(side=tk.LEFT, padx=5)
            ttk.Button(docs_controls, text="Удалить", command=self._delete_client_document).pack(side=tk.LEFT)

            self.files_listbox = tk.Listbox(docs_frame, height=3)
            self.files_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self._load_notification_files()

        # --- Нижняя часть: Детализация (только в режиме редактирования) ---
        if self.notification_id:
            bottom_pane = ttk.Frame(paned_window, padding=10)
            paned_window.add(bottom_pane, weight=2)

            details_frame = ttk.LabelFrame(bottom_pane, text="Детализация уведомления")
            details_frame.pack(fill=tk.BOTH, expand=True)

            # Кнопки управления детализацией
            details_controls = ttk.Frame(details_frame)
            details_controls.pack(fill=tk.X, pady=5)
            ttk.Button(details_controls, text="Скачать шаблон", command=self._download_details_template).pack(side=tk.LEFT)
            ttk.Button(details_controls, text="Загрузить из файла", command=self._upload_details_file).pack(side=tk.LEFT, padx=5)
            ttk.Button(details_controls, text="Сохранить детализацию", command=self._save_details_from_table).pack(side=tk.RIGHT)

            # Таблица детализации
            self.details_cols = ["id", "gtin", "quantity", "aggregation", "production_date", "shelf_life_months", "expiry_date"]
            self.details_tree = ttk.Treeview(details_frame, columns=self.details_cols, show='headings')
            
            col_map = {
                "id": ("ID", 40, "center"), "gtin": ("GTIN", 140, "w"), "quantity": ("Кол-во", 80, "e"),
                "aggregation": ("Агрегация", 80, "center"), "production_date": ("Дата произв.", 100, "center"),
                "shelf_life_months": ("Срок годн. (мес)", 100, "center"), "expiry_date": ("Годен до", 100, "center")
            }
            for col, (text, width, anchor) in col_map.items():
                self.details_tree.heading(col, text=text)
                self.details_tree.column(col, width=width, anchor=anchor)
            
            self.details_tree.pack(fill=tk.BOTH, expand=True, pady=5)
            self.details_tree.bind("<Double-1>", self._on_details_double_click)
            self._load_notification_details()

        # Кнопки управления
        buttons_frame = ttk.Frame(top_pane)
        buttons_frame.pack(fill=tk.X, pady=(10,0))
        ttk.Button(buttons_frame, text="Сохранить", command=self._save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(buttons_frame, text="Отмена", command=self.destroy).pack(side=tk.RIGHT)

        # Загрузка начальных данных, если редактирование
        if self.initial_data:
            self._load_initial_values()
        
        # --- ИЗМЕНЕНИЕ: Вызываем смену сценария вручную после загрузки всех данных ---
        # Это гарантирует, что список клиентов будет корректно загружен при открытии окна.
        self._on_scenario_change()
        logging.info("Создание виджетов в NotificationEditorDialog завершено.")

        # --- ИЗМЕНЕНИЕ: Блокируем поля, если это режим редактирования ---
        if self.notification_id:
            self.scenario_combo.config(state='disabled')
            self.client_combo.config(state='disabled')

    # --- Методы для управления документами клиента ---
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
        # Эта функция должна быть реализована в сервисе
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

    # --- Методы для управления детализацией ---
    def _load_notification_details(self):
        for i in self.details_tree.get_children(): self.details_tree.delete(i)
        details = self.service.get_notification_details(self.notification_id)
        for item in details:
            values = [item.get(col, '') for col in self.details_cols]
            self.details_tree.insert('', 'end', iid=item['id'], values=values)

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
        """Собирает данные из Treeview и отправляет на сохранение."""
        details_to_save = []
        for item_id in self.details_tree.get_children():
            # --- ИСПРАВЛЕНИЕ: Преобразуем данные в кортеж (tuple) для execute_values ---
            # execute_values ожидает список кортежей, а не словарей.
            raw_values = self.details_tree.item(item_id, "values")
            
            # --- НОВОЕ ИСПРАВЛЕНИЕ: Приводим типы данных к тем, что ожидает БД ---
            # id, gtin, quantity, aggregation, production_date, shelf_life_months, expiry_date
            processed_values = [
                int(raw_values[0]) if raw_values[0] else None,  # id (int)
                raw_values[1] if raw_values[1] else None,       # gtin (str)
                int(raw_values[2]) if raw_values[2] else None,  # quantity (int)
                int(raw_values[3]) if raw_values[3] else None,  # aggregation (int)
                raw_values[4] if raw_values[4] else None,       # production_date (date as str)
                int(raw_values[5]) if raw_values[5] else None,  # shelf_life_months (int)
                raw_values[6] if raw_values[6] else None        # expiry_date (date as str)
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
        """Обработчик клика по Combobox клиента. Добавляет кнопку "Добавить нового"."""
        # Проверяем, что кнопка еще не создана
        if not hasattr(self, 'add_client_button'):
            self.add_client_button = ttk.Button(self.client_combo.master, text="Добавить нового", command=self._add_new_client)
            self.add_client_button.pack(side=tk.RIGHT, padx=5)

    def _add_new_client(self):
        """Открывает диалог для добавления нового клиента."""
        dialog = AddClientDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            try:
                self.catalog_service.upsert_local_client(dialog.result)
                # Перезагружаем клиентов из текущего источника
                self._load_clients(source=self.client_source)
                # Выбираем только что добавленного клиента
                self.client_var.set(dialog.result['name'])
                messagebox.showinfo("Успех", f"Клиент '{dialog.result['name']}' успешно добавлен.", parent=self)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось добавить клиента: {e}", parent=self)

    def _open_calendar_dialog(self):
        """Открывает диалог календаря для выбора даты прибытия."""
        cal_dialog = CalendarDialog(self, initial_date=datetime.strptime(self.arrival_date_var.get(), "%Y-%m-%d") if self.arrival_date_var.get() else datetime.now())
        self.wait_window(cal_dialog)
        if cal_dialog.result:
            self.arrival_date_var.set(cal_dialog.result.strftime("%Y-%m-%d"))
    def _on_scenario_change(self, event=None):
        """
        Обработчик смены сценария. Определяет, откуда загружать клиентов (локально или из API).
        """
        selected_scenario_name = self.scenario_var.get()
        if not selected_scenario_name:
            return

        selected_scenario = next((s for s in self.scenarios if s['name'] == selected_scenario_name), None)
        if not selected_scenario:
            return

        # Проверяем условие из сценария
        scenario_data = selected_scenario.get('scenario_data', {})
        if scenario_data.get('dm_source') == 'Заказ в ДМ.Код':
            self._load_clients(source='api')
        else:
            self._load_clients(source='local')

    def _load_scenarios(self):
        """Загружает сценарии маркировки в Combobox."""
        scenarios = self.catalog_service.get_marking_scenarios()
        self.scenarios = scenarios # Сохраняем для дальнейшего использования
        self.scenario_combo['values'] = [s['name'] for s in scenarios]
        if scenarios:
            # --- ИСПРАВЛЕНИЕ: Заполняем scenario_var не только при создании, но и при редактировании ---
            if self.initial_data:
                # Если редактируем, пытаемся найти соответствующий сценарий
                # --- ИСПРАВЛЕНИЕ: Используем self.scenarios, который уже содержит полные данные ---
                initial_scenario = next((s for s in self.scenarios if s['id'] == self.initial_data.get('scenario_id')), None)
                if initial_scenario:
                    self.scenario_var.set(initial_scenario['name'])
            else:
                # Если создаем, выбираем первый доступный
                self.scenario_var.set(scenarios[0]['name'])

    def _load_clients(self, source='local'):
        """Загружает клиентов в Combobox из указанного источника ('local' или 'api')."""
        self.client_source = source # Сохраняем источник для использования в _save
        clients = []
        try:
            if source == 'api':
                clients = self.catalog_service.get_participants_catalog()
            else: # 'local'
                clients = self.catalog_service.get_local_clients()
        except Exception as e:
            messagebox.showerror("Ошибка загрузки клиентов", f"Не удалось загрузить список клиентов: {e}", parent=self)

        self.clients = clients # Сохраняем полный список для получения ID при сохранении
        self.client_combo['values'] = [c.get('name', '') for c in clients]

        if clients:
            # Если это редактирование, пытаемся выставить сохраненного клиента
            if self.initial_data:
                initial_client_name = self.initial_data.get('client_name')
                if initial_client_name in self.client_combo['values']:
                    self.client_var.set(initial_client_name)
                else:
                    self.client_var.set(clients[0]['name']) # Если не нашли, ставим первого
            else:
                self.client_var.set(clients[0]['name']) # Для нового уведомления ставим первого
        else:
            self.client_var.set('') # Очищаем, если список пуст

    def _load_product_groups(self):
        """Загружает товарные группы в Listbox."""
        product_groups = self.catalog_service.get_product_groups()
        self.product_groups = product_groups  # Сохраняем для дальнейшего использования
        for pg in product_groups:
            self.product_groups_listbox.insert(tk.END, pg['display_name'])

    def _load_initial_values(self):
        """Загружает начальные значения из existing_data."""
        self.arrival_date_var.set(self.initial_data.get('planned_arrival_date', ''))
        self.vehicle_number_entry.insert(0, self.initial_data.get('vehicle_number', ''))
        self.comments_text.insert(tk.END, self.initial_data.get('comments', ''))
        
        # --- ИСПРАВЛЕНИЕ: Устанавливаем клиента при редактировании ---
        # Выбор товарных групп
        initial_groups = self.initial_data.get('product_groups', [])
        if initial_groups:
            for i, group in enumerate(self.product_groups):
                # Сравниваем по 'group_name', так как это системное имя
                if any(g.get('group_name') == group.get('group_name') for g in initial_groups):
                    self.product_groups_listbox.select_set(i)
        
        # --- ИСПРАВЛЕНИЕ: Устанавливаем клиента при редактировании ---
        initial_client_name = self.initial_data.get('client_name')
        if initial_client_name:
            self.client_var.set(initial_client_name)

    def _save(self):
        """Сохраняет данные."""
        logging.debug("Начало сохранения данных из диалога уведомления.")
        
        selected_product_groups_indices = self.product_groups_listbox.curselection()
        selected_product_groups = [self.product_groups[i] for i in selected_product_groups_indices]

        # --- НОВАЯ ЛОГИКА: Разделяем сохранение для создания и редактирования ---
        if self.notification_id:
            # РЕДАКТИРОВАНИЕ: Собираем только изменяемые поля
            data = {
                'product_groups': [{
                    'id': g['id'], 
                    'group_name': g['group_name'],
                    'name': g['display_name']
                } for g in selected_product_groups],
                'planned_arrival_date': self.arrival_date_var.get(),
                'vehicle_number': self.vehicle_number_entry.get(),
                'comments': self.comments_text.get("1.0", tk.END).strip()
            }
        else:
            # СОЗДАНИЕ: Собираем все поля, как и раньше
            selected_scenario_name = self.scenario_var.get()
            selected_scenario = next((s for s in self.scenarios if s['name'] == selected_scenario_name), None)
            if not selected_scenario:
                messagebox.showerror("Ошибка", "Не выбран сценарий маркировки.", parent=self)
                return

            selected_client_name = self.client_var.get()
            selected_client_obj = next((c for c in self.clients if c.get('name') == selected_client_name), None)
            if not selected_client_obj:
                messagebox.showerror("Ошибка", "Не выбран клиент.", parent=self)
                return

            client_api_id = selected_client_obj.get('id') if self.client_source == 'api' else None
            client_local_id = selected_client_obj.get('id') if self.client_source == 'local' else None

            data = {
                'scenario_id': selected_scenario['id'],
                'scenario_name': selected_scenario['name'],
                'client_api_id': client_api_id,
                'client_local_id': client_local_id,
                'client_name': selected_client_name,
                'product_groups': [{
                    'id': g['id'], 
                    'group_name': g['group_name'],
                    'name': g['display_name']
                } for g in selected_product_groups],
                'planned_arrival_date': self.arrival_date_var.get(),
                'vehicle_number': self.vehicle_number_entry.get(),
                'comments': self.comments_text.get("1.0", tk.END).strip()
            }

        logging.debug(f"Собранные данные для сохранения: {data}")

        # Сохранение данных
        try:
            if self.notification_id:
                logging.info(f"Вызов service.update_notification для ID: {self.notification_id}")
                self.service.update_notification(self.notification_id, data)
            else:
                logging.info("Вызов service.create_notification для создания нового уведомления.")
                self.service.create_notification(data)
            self.result = True
            if self.on_save_callback: # Вызываем callback для обновления списка
                self.on_save_callback()
            self.destroy()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить уведомление: {e}", parent=self)


class CalendarDialog(tk.Toplevel):
    """Диалоговое окно с простым календарем."""
    def __init__(self, parent, initial_date=None):
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

    def _create_widgets(self):
        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=5)
        ttk.Button(nav_frame, text="<", command=self._prev_month).pack(side=tk.LEFT)
        self.month_year_label = ttk.Label(nav_frame, font=("Arial", 12, "bold"), width=20, anchor="center")
        self.month_year_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(nav_frame, text=">", command=self._next_month).pack(side=tk.LEFT)

        self.calendar_frame = ttk.Frame(self)
        self.calendar_frame.pack(padx=10, pady=10)

    def _update_calendar(self):
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

class NewNotificationDialog(tk.Toplevel):
    """Диалог для создания/редактирования уведомления."""
    def __init__(self, parent, title="Новое уведомление", initial_name="", initial_date_str=""):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result = None

        frame = ttk.Frame(self, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        # Наименование
        ttk.Label(frame, text="Наименование:").grid(row=0, column=0, sticky="w", pady=2)
        name_frame = ttk.Frame(frame)
        name_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.name_entry = ttk.Entry(name_frame, width=40)
        self.name_entry.insert(0, initial_name)
        self.name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(name_frame, text="Вставить", command=self._paste_name).pack(side=tk.LEFT, padx=(5,0))

        # Дата
        ttk.Label(frame, text="Планируемая дата прибытия:").grid(row=2, column=0, sticky="w", pady=(10, 2))
        date_frame = ttk.Frame(frame)
        date_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.date_var = tk.StringVar(value=initial_date_str)
        self.date_entry = ttk.Entry(date_frame, textvariable=self.date_var, width=40)
        self.date_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(date_frame, text="...", width=3, command=self._open_calendar).pack(side=tk.LEFT, padx=(5,0))

        # Кнопки OK/Отмена
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=2, pady=(20, 0), sticky="e")
        ttk.Button(button_frame, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Отмена", command=self.destroy).pack(side=tk.LEFT)

        self.name_entry.focus_set()

    def _paste_name(self):
        try:
            clipboard_text = self.clipboard_get()
            self.name_entry.delete(0, tk.END)
            self.name_entry.insert(0, clipboard_text)
        except tk.TclError:
            messagebox.showwarning("Буфер обмена", "Буфер обмена пуст или содержит нетекстовые данные.", parent=self)

    def _open_calendar(self):
        try:
            initial_date = datetime.strptime(self.date_var.get(), "%Y-%m-%d")
        except ValueError:
            initial_date = datetime.now()
        
        cal_dialog = CalendarDialog(self, initial_date=initial_date)
        self.wait_window(cal_dialog)
        if cal_dialog.result:
            self.date_var.set(cal_dialog.result.strftime("%Y-%m-%d"))

    def _on_ok(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("Внимание", "Наименование не может быть пустым.", parent=self)
            return

        date_str = self.date_var.get().strip()
        arrival_date = None
        if date_str:
            try:
                arrival_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                messagebox.showerror("Ошибка", "Неверный формат даты. Используйте ГГГГ-ММ-ДД или выберите из календаря.", parent=self)
                return

        self.result = (name, arrival_date)
        self.destroy()