# src/admin_ui.py

import tkinter as tk
from tkinter import ttk, messagebox
import logging
import json

# Импорты для работы с БД и QR-кодами
from db_connector import get_main_db_connection
import bcrypt
import psycopg2

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
        address = server_address_entry.get()
        if not address:
            messagebox.showwarning("Внимание", "Введите адрес сервера.", parent=setup_window)
            return

        try:
            import qrcode
            from PIL import Image, ImageTk
            import zlib, base64
        except ImportError:
            messagebox.showerror("Ошибка", "Необходимые библиотеки не установлены.\nУстановите их: pip install qrcode pillow", parent=setup_window)
            return

        config_data = {"type": "server_config", "address": address}
        json_bytes = json.dumps(config_data).encode('utf-8')
        compressed_bytes = zlib.compress(json_bytes, level=9)
        base64_data = base64.b64encode(compressed_bytes).decode('ascii')

        chunk_size = 2500
        chunks = [base64_data[i:i + chunk_size] for i in range(0, len(base64_data), chunk_size)]

        display_qr_sequence(f"Настройка сервера: {address}", chunks, setup_window)

    ttk.Button(config_frame, text="Сгенерировать QR-код", command=generate_server_config_qr).pack(pady=20)

    # --- Вкладка 2: Генерация рабочих мест ---
    workplaces_frame = ttk.Frame(notebook, padding="10")
    notebook.add(workplaces_frame, text="Рабочие места")

    ttk.Label(workplaces_frame, text="Название склада:", font=("Arial", 10, "bold")).pack(anchor="w")
    warehouse_name_entry = ttk.Entry(workplaces_frame, width=40)
    warehouse_name_entry.pack(fill="x", pady=(0, 10))

    ttk.Label(workplaces_frame, text="Количество рабочих мест:", font=("Arial", 10, "bold")).pack(anchor="w")
    workplaces_count_spinbox = ttk.Spinbox(workplaces_frame, from_=1, to=100, width=10)
    workplaces_count_spinbox.pack(anchor="w")

    def generate_workplaces():
        warehouse_name = warehouse_name_entry.get()
        try:
            count = int(workplaces_count_spinbox.get())
        except ValueError:
            messagebox.showwarning("Внимание", "Укажите корректное количество рабочих мест.", parent=setup_window)
            return

        if not warehouse_name or count <= 0:
            messagebox.showwarning("Внимание", "Заполните все поля.", parent=setup_window)
            return

        if not messagebox.askyesno("Подтверждение", f"Будет создано {count} рабочих мест для склада '{warehouse_name}'.\nПродолжить?", parent=setup_window):
            return

        client_db_config = user_info.get("client_db_config")
        if not client_db_config:
            messagebox.showerror("Ошибка", "Не найдены данные для подключения к базе клиента.", parent=setup_window)
            return

        generated_tokens = []
        try:
            # Подключаемся к базе клиента для сохранения токенов
            conn_params = {k: v for k, v in client_db_config.items() if k not in ['db_ssl_cert']}
            conn_params['dbname'] = conn_params.pop('db_name')
            
            with psycopg2.connect(**conn_params) as conn:
                with conn.cursor() as cur:
                    for i in range(1, count + 1):
                        # Вставляем запись и получаем сгенерированный токен
                        cur.execute(
                            "INSERT INTO ap_workplaces (warehouse_name, workplace_number) VALUES (%s, %s) RETURNING access_token",
                            (warehouse_name, i)
                        )
                        token = cur.fetchone()[0]
                        generated_tokens.append({"workplace": f"{warehouse_name} - Место {i}", "token": str(token)})
                conn.commit()
            
            messagebox.showinfo("Успех", f"Успешно создано и сохранено {len(generated_tokens)} рабочих мест.", parent=setup_window)
            display_workplace_qrs(generated_tokens, setup_window)

        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Ошибка генерации рабочих мест: {e}\n{error_details}")
            messagebox.showerror("Ошибка", f"Не удалось создать рабочие места. Подробности в app.log.", parent=setup_window)

    ttk.Button(workplaces_frame, text="Сгенерировать и сохранить", command=generate_workplaces).pack(pady=20)

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

def display_workplace_qrs(tokens_info, parent):
    """Отображает QR-коды для рабочих мест с возможностью печати."""
    try:
        import qrcode
        from PIL import Image, ImageTk
    except ImportError: return

    # Используем ту же функцию, что и для серии QR, но с другой логикой
    qr_window = tk.Toplevel(parent)
    qr_window.title("QR-коды для рабочих мест")
    qr_window.grab_set()

    current_index = 0
    
    info_label = ttk.Label(qr_window, text="", font=("Arial", 12))
    info_label.pack(pady=10)
    qr_label = ttk.Label(qr_window)
    qr_label.pack(padx=20, pady=10)
    token_label = ttk.Label(qr_window, text="", font=("Courier", 10))
    token_label.pack()

    nav_frame = ttk.Frame(qr_window)
    nav_frame.pack(pady=10)
    prev_button = ttk.Button(nav_frame, text="<< Назад")
    prev_button.pack(side=tk.LEFT, padx=5)
    
    def print_label():
        workplace_data = tokens_info[current_index]
        pdf_buffer = PrintingService.generate_workplace_label_pdf(workplace_data['workplace'], workplace_data['token'])
        # TODO: Здесь нужно открыть диалог выбора принтера и размера бумаги
        # и затем вызвать PrintingService.print_pdf(printer, pdf_buffer, paper)
        messagebox.showinfo("Печать", "Функционал печати в разработке.\nPDF сгенерирован в памяти.", parent=qr_window)

    print_button = ttk.Button(nav_frame, text="Печать", command=print_label)
    print_button.pack(side=tk.LEFT, padx=5)

    next_button = ttk.Button(nav_frame, text="Далее >>")
    next_button.pack(side=tk.LEFT, padx=10)

    def show_workplace(index):
        nonlocal current_index
        current_index = index
        workplace_data = tokens_info[index]
        token = workplace_data['token']
        
        qr_payload = json.dumps({"type": "workplace_token", "token": token})
        img = qrcode.make(qr_payload).resize((300, 300))
        photo = ImageTk.PhotoImage(img)
        qr_label.config(image=photo)
        qr_label.image = photo
        info_label.config(text=f"Рабочее место: {workplace_data['workplace']} ({index+1}/{len(tokens_info)})")
        token_label.config(text=f"Токен: {token}")
        prev_button.config(state="normal" if index > 0 else "disabled")
        next_button.config(state="normal" if index < len(tokens_info) - 1 else "disabled")

    def show_next():
        if current_index < len(tokens_info) - 1: show_workplace(current_index + 1)
    def show_prev():
        if current_index > 0: show_workplace(current_index - 1)

    prev_button.config(command=show_prev)
    next_button.config(command=show_next)
    show_workplace(0)

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