# src/supervisor_ui.py

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os
import logging
import traceback
import psycopg2
import tempfile
from psycopg2 import sql
import bcrypt
 
# --- ИСПРАВЛЕНИЕ: Используем абсолютный импорт, так как 'scripts' - это пакет верхнего уровня ---
from .db_connector import get_main_db_connection
from .utils import resource_path
# --- ИЗМЕНЕНИЕ: Импортируем функции напрямую ---
from scripts.setup_client_database import update_client_db_schema
from scripts.setup_database import initialize_main_database

def run_db_setup():
    """
    Запускает функцию initialize_main_database для создания/обновления главной БД.
    """
    try:
        logging.debug("Запуск функции run_db_setup для инициализации главной БД.")
        # Вызываем импортированную функцию напрямую
        success, message = initialize_main_database()
        if success:
            logging.info(f"Результат инициализации БД: {message}")
            messagebox.showinfo("Успех", message)
        else:
            logging.error(f"Результат инициализации БД: {message}")
            messagebox.showerror("Ошибка", message)
    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Не удалось запустить скрипт 'setup_database.py': {e}\n{error_details}")
        messagebox.showerror("Ошибка запуска", f"Произошла ошибка при запуске скрипта.\nПодробности в файле app.log")

def open_supervisor_creator_window(parent_widget):
    """Открывает окно для создания супервизора."""
    sup_window = tk.Toplevel(parent_widget)
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

    buttons_frame = ttk.Frame(sup_window)
    buttons_frame.grid(row=len(fields), columnspan=2, pady=10)
    ttk.Button(buttons_frame, text="Сохранить", command=save_supervisor).pack(side=tk.RIGHT, padx=5)
    ttk.Button(buttons_frame, text="Отмена", command=sup_window.destroy).pack(side=tk.RIGHT)

def open_clients_management_window(parent_widget):
    """Создает и возвращает фрейм для управления клиентами и пользователями."""
    
    clients_window = ttk.Frame(parent_widget)

    def load_clients():
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
        selected_item = clients_tree.focus()
        if not selected_item: return
        client_id = clients_tree.item(selected_item)['values'][0]
        load_users(client_id)

    def load_users(client_id):
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
        editor_window = tk.Toplevel(clients_window)
        editor_window.title("Редактор клиента")
        editor_window.grab_set()

        main_editor_frame = ttk.Frame(editor_window, padding="10")
        main_editor_frame.pack(fill=tk.BOTH, expand=True)

        client_data_frame = ttk.LabelFrame(main_editor_frame, text="Данные клиента")
        client_data_frame.pack(fill=tk.X, pady=5)

        entries = {}
        fields = ["Имя", "DB Хост", "DB Порт", "DB Имя", "DB Пользователь", "DB Пароль", "API Base URL", "API Email", "API Password"]

        for i, field in enumerate(fields):
            ttk.Label(client_data_frame, text=field + ":").grid(row=i, column=0, padx=5, pady=2, sticky='w')
            entry = ttk.Entry(client_data_frame, width=40)
            entry.grid(row=i, column=1, padx=5, pady=2, sticky='ew')
            entries[field] = entry
        client_data_frame.columnconfigure(1, weight=1)

        cert_frame = ttk.LabelFrame(main_editor_frame, text="SSL-сертификат для подключения к БД клиента")
        cert_frame.pack(fill=tk.X, pady=5)
        ssl_cert_text = tk.Text(cert_frame, height=8, width=80)
        ssl_cert_text.pack(fill=tk.X, expand=True, padx=5, pady=5)

        def run_client_db_setup():
            if not client_id:
                messagebox.showwarning("Внимание", "Сначала сохраните клиента.", parent=editor_window)
                return

            if not messagebox.askyesno("Подтверждение", "Вы уверены, что хотите инициализировать/обновить схему для базы данных этого клиента?\n\nСуществующие данные не будут удалены, но будут созданы недостающие таблицы.", parent=editor_window):
                return

            client_conn = None
            temp_cert_file = None
            try:
                # --- НОВЫЙ БЛОК: Проверка существования БД клиента ---
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT db_host, db_port, db_name, db_user, db_password, db_ssl_cert FROM clients WHERE id = %s", (client_id,))
                        db_data = cur.fetchone()
                
                if not db_data: raise ValueError("Не удалось найти данные для подключения к БД клиента.")
                db_host, db_port, db_name, db_user, db_password, db_ssl_cert = db_data

                ssl_params_check = {}
                temp_cert_file_check = None
                if db_ssl_cert:
                    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                        fp.write(db_ssl_cert)
                        temp_cert_file_check = fp.name
                    ssl_params_check = {'sslmode': 'verify-full', 'sslrootcert': temp_cert_file_check}

                try:
                    # Подключаемся к системной БД 'postgres' для проверки
                    with psycopg2.connect(host=db_host, port=db_port, dbname='postgres', user=db_user, password=db_password, **ssl_params_check) as conn_system:
                        conn_system.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                        with conn_system.cursor() as cur:
                            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                            if not cur.fetchone():
                                error_message = (
                                    f"База данных '{db_name}' не найдена на сервере {db_host}.\n\n"
                                    "Пожалуйста, создайте ее и пользователя с правами на чтение.\n\n"
                                    "Примерные SQL-команды:\n"
                                    f"CREATE DATABASE {db_name};\n"
                                    f"CREATE USER readonly_user WITH PASSWORD 'your_password';\n"
                                    f"GRANT CONNECT ON DATABASE {db_name} TO readonly_user;\n"
                                    f"\\c {db_name}\n"
                                    "GRANT USAGE ON SCHEMA public TO readonly_user;\n"
                                    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user;"
                                )
                                messagebox.showerror("Ошибка", error_message, parent=editor_window)
                                return # Прерываем выполнение, если БД не найдена
                finally:
                    if temp_cert_file_check and os.path.exists(temp_cert_file_check):
                        os.remove(temp_cert_file_check)
                # --- КОНЕЦ НОВОГО БЛОКА ---

                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT db_host, db_port, db_name, db_user, db_password, db_ssl_cert FROM clients WHERE id = %s", (client_id,))
                        db_data = cur.fetchone()
                
                if not db_data: raise ValueError("Не удалось найти данные для подключения к БД клиента.")
                db_host, db_port, db_name, db_user, db_password, db_ssl_cert = db_data

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
                    logging.info(f"Используется временный SSL-сертификат: {temp_cert_file}")

                logging.info(f"Подключаюсь к базе клиента '{db_name}' на {db_host}...")
                client_conn = psycopg2.connect(host=db_host, port=db_port, dbname=db_name, user=db_user, password=db_password, **ssl_params)

                if update_client_db_schema(client_conn):
                    messagebox.showinfo("Успех", "Схема базы данных клиента успешно обновлена.", parent=editor_window)
                else:
                    messagebox.showerror("Ошибка", "Произошла ошибка при обновлении схемы. Подробности в app.log.", parent=editor_window)

            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"Не удалось выполнить инициализацию БД клиента: {e}\n{error_details}")
                messagebox.showerror("Ошибка", f"Не удалось выполнить инициализацию: {e}", parent=editor_window)
            finally:
                if client_conn: client_conn.close()
                if temp_cert_file and os.path.exists(temp_cert_file): os.remove(temp_cert_file)

        def sync_user_with_client_db(user_login, password_hash, is_admin, is_active):
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
                error_details = traceback.format_exc()
                if client_conn: client_conn.rollback()
                logging.error(f"Ошибка синхронизации пользователя с БД клиента: {e}\n{error_details}")
                messagebox.showerror("Ошибка синхронизации", f"Не удалось обновить данные в базе клиента: {e}", parent=editor_window)
                return False
            finally:
                if client_conn: client_conn.close()
                if temp_cert_file and os.path.exists(temp_cert_file): os.remove(temp_cert_file)

        def add_user():
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
                            cur.execute("INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, 'администратор', %s)",
                                        (name, login, hashed_pass, client_id))
                        conn.commit()
                    
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
                        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
                        password_hash = cur.fetchone()[0]
                    conn.commit()
                
                sync_user_with_client_db(login, password_hash, True, new_status)
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось изменить статус пользователя: {e}", parent=editor_window)

        users_management_frame = ttk.LabelFrame(main_editor_frame, text="Пользователи этого клиента")
        users_management_frame.pack(fill=tk.BOTH, expand=True, pady=5)

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
        if client_id:
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="normal")
            
            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT name, db_host, db_port, db_name, db_user, db_password, db_ssl_cert, api_base_url, api_email, api_password FROM clients WHERE id = %s", (client_id,))
                        client_data = cur.fetchone()
                if client_data:
                    # ИСПРАВЛЕНИЕ: Используем числовые индексы для доступа к данным из кортежа client_data
                    db_field_map = {"Имя": 0, "DB Хост": 1, "DB Порт": 2, "DB Имя": 3, "DB Пользователь": 4, "DB Пароль": 5, "API Base URL": 7, "API Email": 8, "API Password": 9}
                    for field in fields:
                        if field in db_field_map:
                            idx = db_field_map[field]
                            value = client_data[idx] if client_data[idx] is not None else ""
                            entries[field].insert(0, str(value))
                    ssl_cert_value = client_data[6] if client_data[6] is not None else "" # Индекс 6 для db_ssl_cert
                    ssl_cert_text.insert('1.0', ssl_cert_value)
                load_users_for_editor(client_id)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить данные клиента: {e}", parent=editor_window)
                return editor_window.destroy()
        else:
            for btn in [btn_add_user, btn_edit_user, btn_delete_user, btn_toggle_user]:
                btn.config(state="disabled")

        def save_client():
            nonlocal client_id
            data_to_save = {
                'name': entries['Имя'].get(),
                'db_host': entries['DB Хост'].get(),
                'db_port': int(entries['DB Порт'].get() or 0),
                'db_name': entries['DB Имя'].get(),
                'db_user': entries['DB Пользователь'].get(),
                'db_password': entries['DB Пароль'].get(),
                'db_ssl_cert': ssl_cert_text.get('1.0', 'end-1c'),
                'api_base_url': entries['API Base URL'].get(),
                'api_email': entries['API Email'].get(),
                'api_password': entries['API Password'].get()
            }

            try:
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        if client_id:
                            query = sql.SQL("UPDATE clients SET name=%s, db_host=%s, db_port=%s, db_name=%s, db_user=%s, db_password=%s, db_ssl_cert=%s, api_base_url=%s, api_email=%s, api_password=%s WHERE id=%s")
                            cur.execute(query, (*data_to_save.values(), client_id))
                        else:
                            query = sql.SQL("INSERT INTO clients (name, db_host, db_port, db_name, db_user, db_password, db_ssl_cert, api_base_url, api_email, api_password) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id")
                            cur.execute(query, tuple(data_to_save.values()))
                            new_client_id = cur.fetchone()[0]
                            client_id = new_client_id
                            
                            default_login = f"admin@{data_to_save['db_name']}"
                            default_pass = "12345"
                            hashed_pass = bcrypt.hashpw(default_pass.encode('utf-8'), bcrypt.gensalt())
                            
                            cur.execute("SELECT 1 FROM users WHERE login = %s", (default_login,))
                            if cur.fetchone():
                                raise psycopg2.IntegrityError(f"Пользователь с логином '{default_login}' уже существует. Имя базы данных клиента должно быть уникальным.")

                            cur.execute(
                                "INSERT INTO users (name, login, password_hash, role, client_id) VALUES (%s, %s, %s, %s, %s)",
                                ("Администратор", default_login, hashed_pass.decode('utf-8'), 'администратор', new_client_id)
                            )
                    conn.commit()
                
                # --- НОВЫЙ БЛОК: Синхронизация настроек с базой клиента ---
                logging.info("Синхронизация настроек API с базой данных клиента...")
                client_conn = None
                temp_cert_file = None
                try:
                    ssl_params = {}
                    if data_to_save['db_ssl_cert']:
                        # --- ИСПРАВЛЕНИЕ: Используем with для автоматического создания и удаления файла ---
                        # Это более безопасный и чистый подход.
                        with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                            fp.write(data_to_save['db_ssl_cert'])
                            temp_cert_file = fp.name # Запоминаем имя файла
                        ssl_params = {'sslmode': 'verify-full', 'sslrootcert': temp_cert_file}

                    client_conn = psycopg2.connect(host=data_to_save['db_host'], port=data_to_save['db_port'], dbname=data_to_save['db_name'], user=data_to_save['db_user'], password=data_to_save['db_password'], **ssl_params)
                    with client_conn.cursor() as cur:
                        # --- ИСПРАВЛЕНИЕ: Сохраняем все необходимые ключи, а не только последние три ---
                        settings_to_sync = [
                            ('DB_HOST', data_to_save['db_host']), ('DB_PORT', str(data_to_save['db_port'])),
                            ('DB_NAME', data_to_save['db_name']), ('DB_USER', data_to_save['db_user']),
                            ('DB_PASSWORD', data_to_save['db_password']), ('API_BASE_URL', data_to_save['api_base_url']),
                            ('API_EMAIL', data_to_save['api_email']), ('API_PASSWORD', data_to_save['api_password'])
                        ]
                        from psycopg2.extras import execute_values
                        upsert_query = "INSERT INTO ap_settings (setting_key, setting_value) VALUES %s ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW();"
                        execute_values(cur, upsert_query, settings_to_sync)
                    client_conn.commit()
                    logging.info("Настройки API успешно синхронизированы с БД клиента.")
                except Exception as sync_err:
                    logging.error(f"Ошибка синхронизации настроек с БД клиента: {sync_err}")
                    messagebox.showerror("Ошибка синхронизации", f"Не удалось обновить настройки в базе клиента: {sync_err}", parent=editor_window)
                finally:
                    if client_conn: client_conn.close()
                    if temp_cert_file and os.path.exists(temp_cert_file): os.remove(temp_cert_file)

                load_clients()
                btn_init_db.config(state="normal")
                if not editor_window.title().startswith("Редактор"):
                    editor_window.title(f"Редактор клиента: {data_to_save['name']}")

                messagebox.showinfo("Успех", "Данные клиента успешно сохранены.", parent=editor_window)

            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"Ошибка сохранения клиента: {e}\n{error_details}")
                messagebox.showerror("Ошибка", f"Не удалось сохранить клиента: {e}", parent=editor_window)

        bottom_buttons_frame = ttk.Frame(main_editor_frame)
        bottom_buttons_frame.pack(fill=tk.X, pady=(10, 0))
        
        btn_init_db = ttk.Button(bottom_buttons_frame, text="Инициализировать/Обновить БД клиента", command=run_client_db_setup, state="disabled" if not client_id else "normal")
        btn_init_db.pack(side=tk.LEFT, padx=5)

        ttk.Button(bottom_buttons_frame, text="Закрыть", command=editor_window.destroy).pack(side=tk.RIGHT)
        ttk.Button(bottom_buttons_frame, text="Сохранить", command=save_client).pack(side=tk.RIGHT, padx=5)

    paned_window = ttk.PanedWindow(clients_window, orient=tk.VERTICAL)
    paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    clients_frame = ttk.LabelFrame(paned_window, text="Клиенты")
    paned_window.add(clients_frame, weight=1)

    client_buttons_frame = ttk.Frame(clients_frame)
    client_buttons_frame.pack(fill=tk.X, padx=5, pady=5)
    ttk.Button(client_buttons_frame, text="Создать", command=lambda: open_client_editor()).pack(side=tk.LEFT, padx=2)

    def edit_selected_client():
        """Безопасно получает ID клиента и открывает редактор."""
        selected_item = clients_tree.focus()
        if not selected_item:
            logging.debug("Кнопка 'Редактировать' нажата, но клиент не выбран.")
            return
        client_id = clients_tree.item(selected_item)['values'][0]
        logging.debug(f"Нажата кнопка 'Редактировать'. ID клиента: {client_id}, тип: {type(client_id)}.")
        open_client_editor(client_id)
    ttk.Button(client_buttons_frame, text="Редактировать", command=edit_selected_client).pack(side=tk.LEFT, padx=2)

    clients_cols = ('id', 'name', 'db_host', 'created_at')
    clients_tree = ttk.Treeview(clients_frame, columns=clients_cols, show='headings')
    clients_tree.heading('id', text='ID')
    clients_tree.heading('name', text='Имя клиента')
    clients_tree.heading('db_host', text='Хост БД')
    clients_tree.heading('created_at', text='Дата создания')
    clients_tree.column('id', width=50, anchor=tk.CENTER)
    clients_tree.column('name', width=250)
    clients_tree.column('db_host', width=200)
    clients_tree.column('created_at', width=150)
    clients_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    clients_tree.bind('<<TreeviewSelect>>', on_client_select)

    users_frame = ttk.LabelFrame(paned_window, text="Пользователи выбранного клиента")
    paned_window.add(users_frame, weight=1)

    users_cols = ('id', 'name', 'login', 'role', 'is_active')
    users_tree = ttk.Treeview(users_frame, columns=users_cols, show='headings')
    users_tree.heading('id', text='ID')
    users_tree.heading('name', text='Имя')
    users_tree.heading('login', text='Логин')
    users_tree.heading('role', text='Роль')
    users_tree.heading('is_active', text='Активен')
    users_tree.column('id', width=50, anchor=tk.CENTER)
    users_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    load_clients()
    
    return clients_window

class SupervisorWindow(tk.Tk):
    """Главное окно для роли 'супервизор'."""
    def __init__(self, user_info):
        super().__init__()
        self.user_info = user_info
        self.title(f"ТильдаКод [Пользователь: {self.user_info['name']}, Роль: {self.user_info['role']}]")
        self.geometry("900x600")
        
        self._create_menu()
        client_management_frame = open_clients_management_window(self)
        client_management_frame.pack(fill=tk.BOTH, expand=True)

    def _create_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Выход", command=self.quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        admin_menu = tk.Menu(menubar, tearoff=0)
        admin_menu.add_command(label="Инициализация/Обновление главной БД", command=run_db_setup)
        admin_menu.add_separator()
        admin_menu.add_command(label="Создать супервизора", command=lambda: open_supervisor_creator_window(self))
        menubar.add_cascade(label="Администрирование", menu=admin_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе")
        menubar.add_cascade(label="Справка", menu=help_menu)