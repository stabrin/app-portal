# src/admin_ui.py

import tkinter as tk
from tkinter import ttk, messagebox
import logging
import json

# Импорты для работы с БД и QR-кодами
from db_connector import get_main_db_connection
import bcrypt
import psycopg2

import traceback

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
    load_printers()

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
        # auth_data['password'] = "..." 

        json_data = json.dumps(auth_data, ensure_ascii=False)

        qr_window = tk.Toplevel(users_window)
        qr_window.title(f"QR-код для: {name}")
        qr_window.grab_set()

        img = qrcode.make(json_data)
        img = img.resize((300, 300))
        photo = ImageTk.PhotoImage(img)

        label = ttk.Label(qr_window, image=photo)
        label.image = photo # Сохраняем ссылку на изображение
        label.pack(padx=20, pady=20)
        ttk.Label(qr_window, text="Отсканируйте этот код в мобильном приложении").pack(pady=(0, 10))

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

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе")
        menubar.add_cascade(label="Справка", menu=help_menu)