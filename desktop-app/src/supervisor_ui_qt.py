from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QTabWidget, QApplication, QLabel, QLineEdit,
    QDialog, QFormLayout, QTextEdit, QSpinBox, QDialogButtonBox
)
from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import Slot
import bcrypt
import sys
import logging
import traceback
import subprocess
import os

from .db_connector import get_main_db_connection
from .utils import resource_path
import psycopg2
from psycopg2 import sql
import psycopg2.extras
from scripts.setup_client_database import update_client_db_schema


class SupervisorWindowQt(QMainWindow):
    def __init__(self, user_info: dict):
        super().__init__()
        self.user_info = user_info
        self.setWindowTitle(f"Supervisor - {user_info.get('name','')}")
        self.resize(1000, 700)
        self._build_ui()
        # Автозагрузим список клиентов при создании окна
        try:
            self.load_clients()
        except Exception:
            logging.exception('Auto load clients failed')

    def _build_ui(self):
        tabs = QTabWidget()

        # Clients tab
        self.clients_tab = QWidget()
        self._build_clients_tab()
        tabs.addTab(self.clients_tab, "Клиенты")

        # Tools tab
        self.tools_tab = QWidget()
        self._build_tools_tab()
        tabs.addTab(self.tools_tab, "Инструменты")

        self.setCentralWidget(tabs)

    def _build_clients_tab(self):
        layout = QVBoxLayout()
        # статус загрузки клиентов
        self.clients_status_label = QLabel("")
        layout.addWidget(self.clients_status_label)
        btn_layout = QHBoxLayout()
        load_btn = QPushButton("Загрузить клиентов")
        load_btn.clicked.connect(self.load_clients)
        add_btn = QPushButton("Новый клиент")
        add_btn.clicked.connect(self.open_client_editor)
        edit_btn = QPushButton("Редактировать выбранного")
        edit_btn.clicked.connect(self.edit_selected_client)
        btn_layout.addWidget(load_btn)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(edit_btn)

        self.clients_table = QTableWidget(0, 4)
        self.clients_table.setHorizontalHeaderLabels(["ID", "Имя", "DB Host", "Создано"])
        self.clients_table.cellClicked.connect(self.on_client_selected)

        layout.addLayout(btn_layout)
        layout.addWidget(self.clients_table)
        self.clients_tab.setLayout(layout)

    def _build_tools_tab(self):
        layout = QVBoxLayout()
        
        # --- НОВЫЙ БЛОК: Кнопки и таблица супервизоров ---
        top_btn_layout = QHBoxLayout()
        run_db_setup_btn = QPushButton("Инициализировать / Обновить главную БД")
        run_db_setup_btn.clicked.connect(self.run_db_setup)
        add_supervisor_btn = QPushButton("Создать супервизора")
        add_supervisor_btn.clicked.connect(self.create_supervisor)
        top_btn_layout.addWidget(run_db_setup_btn)
        top_btn_layout.addWidget(add_supervisor_btn)
        
        self.supervisors_table = QTableWidget(0, 3)
        self.supervisors_table.setHorizontalHeaderLabels(["ID", "Имя", "Логин"])
        
        layout.addLayout(top_btn_layout)
        layout.addWidget(self.supervisors_table)
        self.tools_tab.setLayout(layout)
        self.load_supervisors() # Загружаем при создании

    def load_supervisors(self):
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, login FROM users WHERE role = 'супервизор' ORDER BY name;")
                    rows = cur.fetchall()
            self.supervisors_table.setRowCount(0)
            for r in rows:
                row_pos = self.supervisors_table.rowCount()
                self.supervisors_table.insertRow(row_pos)
                self.supervisors_table.setItem(row_pos, 0, QTableWidgetItem(str(r[0])))
                self.supervisors_table.setItem(row_pos, 1, QTableWidgetItem(str(r[1])))
                self.supervisors_table.setItem(row_pos, 2, QTableWidgetItem(str(r[2])))
        except Exception as e:
            logging.error(f"Ошибка загрузки супервизоров: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", "Не удалось загрузить список супервизоров.")

    def create_supervisor(self):
        """Открывает диалог создания супервизора."""
        # Можно создать отдельный класс диалога, но для простоты используем UserEditorDialog
        # с флагом is_supervisor
        dlg = UserEditorDialog(parent=self, client_id=None, is_supervisor=True)
        if dlg.exec():
            self.load_supervisors()

    @Slot()
    def on_client_selected(self, row, col):
        pass # Теперь пользователи загружаются в редакторе

    @Slot()
    def run_db_setup(self):
        try:
            script_path = resource_path(os.path.join('scripts', 'setup_database.py'))
            # Явно указываем кодировку и поведение при ошибках декодирования,
            # чтобы избежать UnicodeDecodeError в потоках чтения на Windows
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                check=False,
                encoding='utf-8',
                errors='replace'
            )
            success = result.returncode == 0
            # Защита на случай, если stdout/stderr == None
            out_text = (result.stdout or '')
            err_text = (result.stderr or '')
            msg = out_text.strip() or err_text.strip() or 'Готово.'
            if success:
                QMessageBox.information(self, "Успех", msg)
            else:
                QMessageBox.critical(self, "Ошибка", msg)
        except Exception as e:
            logging.error(f"Ошибка при запуске setup_database: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", "Не удалось запустить инициализацию БД. См. логи.")

    @Slot()
    def load_clients(self):
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, db_host, created_at FROM clients ORDER BY name;")
                    rows = cur.fetchall()
            count = len(rows)
            logging.info(f"Loaded {count} clients from main DB")
            self.clients_status_label.setText(f"Загружено клиентов: {count}")

            self.clients_table.setRowCount(0)
            for r in rows:
                row = self.clients_table.rowCount()
                self.clients_table.insertRow(row)
                self.clients_table.setItem(row, 0, QTableWidgetItem(str(r[0])))
                self.clients_table.setItem(row, 1, QTableWidgetItem(str(r[1])))
                self.clients_table.setItem(row, 2, QTableWidgetItem(str(r[2] or '')))
                self.clients_table.setItem(row, 3, QTableWidgetItem(str(r[3] or '')))

            if count == 0:
                # Подсказка для тестирования — если нет клиентов, покажем сообщение
                self.clients_status_label.setText("Нет записей в таблице clients. Для теста можно добавить запись в БД или запустить init_db.")
        except Exception as e:
            logging.error(f"Ошибка загрузки клиентов: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", "Не удалось загрузить список клиентов. Подробности в логах.")

    def _get_selected_client_id(self):
        cur = self.clients_table.currentRow()
        if cur < 0:
            return None
        item = self.clients_table.item(cur, 0)
        if not item: return None
        return int(item.text())

    def open_client_editor(self, client_id: int = None):
        dlg = ClientEditorDialog(parent=self, client_id=client_id)
        if dlg.exec():
            # saved, reload clients
            self.load_clients()

    def edit_selected_client(self):
        client_id = self._get_selected_client_id()
        if not client_id:
            QMessageBox.warning(self, "Внимание", "Выберите клиента для редактирования.")
            return
        self.open_client_editor(client_id)


def sync_user_with_client_db(client_id: int, user_login: str, password_hash: str, is_admin: bool, is_active: bool):
    """
    Синхронизирует пользователя в клиентской базе данных (insert or update).
    Возвращает True при успехе, False при ошибке.
    """
    client_conn = None
    try:
        with get_main_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM clients WHERE id = %s", (client_id,))
                db_data = cur.fetchone()
        if not db_data:
            raise ValueError("Данные клиента не найдены.")
        # Добавляем id для пула соединений
        db_data['id'] = client_id
        from .db_connector import get_client_db_connection
        user_info_for_client_db = {'client_db_config': db_data}
        with get_client_db_connection(user_info_for_client_db) as client_conn:
            with client_conn.cursor() as cur:
                upsert = sql.SQL("""
                    INSERT INTO users (username, password_hash, is_admin, is_active)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (username) DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        is_admin = EXCLUDED.is_admin,
                        is_active = EXCLUDED.is_active;
                """)
                cur.execute(upsert, (user_login, password_hash, is_admin, is_active))
            client_conn.commit()
        logging.info(f"Пользователь '{user_login}' синхронизирован с БД клиента (id={client_id}).")
        return True
    except Exception as e:
        logging.error(f"Ошибка синхронизации пользователя с БД клиента: {e}\n{traceback.format_exc()}")
        try:
            if client_conn:
                client_conn.rollback()
        except Exception:
            pass
        return False


class ClientEditorDialog(QDialog):
    def __init__(self, parent=None, client_id: int = None):
        super().__init__(parent)
        self.client_id = client_id
        self.setWindowTitle(f"Редактор клиента: {client_id}" if client_id else "Новый клиент")
        self.resize(800, 600)
        self._build_ui()
        if client_id:
            self._load_client()

    def _build_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_edit = QSpinBox(); self.port_edit.setMaximum(65535); self.port_edit.setValue(5432)
        self.dbname_edit = QLineEdit()
        self.dbuser_edit = QLineEdit()
        self.dbpass_edit = QLineEdit()
        self.api_base_edit = QLineEdit()
        self.api_email_edit = QLineEdit()
        self.api_pass_edit = QLineEdit()
        self.local_server_addr_edit = QLineEdit()
        self.local_server_port_edit = QSpinBox(); self.local_server_port_edit.setMaximum(65535); self.local_server_port_edit.setValue(5432)

        form.addRow("Имя", self.name_edit)
        form.addRow("DB Host", self.host_edit)
        form.addRow("DB Port", self.port_edit)
        form.addRow("DB Name", self.dbname_edit)
        form.addRow("DB User", self.dbuser_edit)
        form.addRow("DB Password", self.dbpass_edit)
        form.addRow("API Base URL", self.api_base_edit)
        form.addRow("API Email", self.api_email_edit)
        form.addRow("API Password", self.api_pass_edit)
        form.addRow("Локальный адрес сервера", self.local_server_addr_edit)
        form.addRow("Локальный порт сервера", self.local_server_port_edit)

        cert_label = QLabel("SSL сертификат (PEM)")
        self.cert_text = QTextEdit()
        self.cert_text.setFixedHeight(120)

        btn_layout = QHBoxLayout()
        self.init_db_btn = QPushButton("Инициализировать/Обновить БД клиента")
        self.init_db_btn.setEnabled(bool(self.client_id))
        self.init_db_btn.clicked.connect(self.run_client_db_setup)
        ping_btn = QPushButton("Пинг-тест")
        # --- ИСПРАВЛЕНИЕ: Пинг-тест теперь использует правильные имена виджетов ---
        # и показывает детальный лог в отдельном окне.
        ping_btn.clicked.connect(self._run_ping_test)
        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self.save)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.init_db_btn)
        btn_layout.addWidget(ping_btn)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(form)
        layout.addWidget(cert_label)
        layout.addWidget(self.cert_text)
        layout.addLayout(btn_layout)

        # Секция управления пользователями клиента
        users_label = QLabel("Пользователи клиента")
        layout.addWidget(users_label)
        self.users_table = QTableWidget(0, 5)
        self.users_table.setHorizontalHeaderLabels(["ID", "Имя", "Логин", "Роль", "Активен"])
        layout.addWidget(self.users_table)

        user_btns = QHBoxLayout()
        add_user_btn = QPushButton("Создать пользователя")
        add_user_btn.clicked.connect(self.add_user)
        edit_user_btn = QPushButton("Редактировать пользователя")
        edit_user_btn.clicked.connect(self.edit_user)
        delete_user_btn = QPushButton("Удалить пользователя")
        delete_user_btn.clicked.connect(self.delete_user)
        toggle_user_btn = QPushButton("Вкл/Выкл")
        toggle_user_btn.clicked.connect(self.toggle_user_activity)
        user_btns.addWidget(add_user_btn)
        user_btns.addWidget(edit_user_btn)
        user_btns.addWidget(delete_user_btn)
        user_btns.addWidget(toggle_user_btn)
        layout.addLayout(user_btns)

        self.setLayout(layout)

    def _load_client(self):
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, name, db_host, db_port, db_name, db_user, db_password, 
                               db_ssl_cert, api_base_url, api_email, api_password,
                               local_server_address, local_server_port 
                        FROM clients WHERE id = %s
                    """, (self.client_id,))
                    row = cur.fetchone()
            if not row:
                QMessageBox.critical(self, "Ошибка", "Клиент не найден")
                return
            (
                _, name, host, port, dbname, dbuser, dbpass, sslcert, 
                api_base, api_email, api_pass,
                local_addr, local_port
            ) = row

            self.name_edit.setText(name or '')
            self.host_edit.setText(host or '')
            try: self.port_edit.setValue(int(port or 5432))
            except Exception: pass
            self.dbname_edit.setText(dbname or '')
            self.dbuser_edit.setText(dbuser or '')
            self.dbpass_edit.setText(dbpass or '')
            self.api_base_edit.setText(api_base or '')
            self.api_email_edit.setText(api_email or '')
            self.api_pass_edit.setText(api_pass or '')
            self.local_server_addr_edit.setText(local_addr or '')
            try: self.local_server_port_edit.setValue(int(local_port or 5432))
            except Exception: pass

            self.cert_text.setPlainText(sslcert or '')

            # Загружаем пользователей для этого клиента, передавая ID
            self.load_users_for_editor(self.client_id)
        except Exception as e:
            logging.error(f"Ошибка загрузки клиента: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", "Не удалось загрузить данные клиента")

    def save(self):
        try:
            name = self.name_edit.text().strip()
            host = self.host_edit.text().strip()
            port = int(self.port_edit.value())
            dbname = self.dbname_edit.text().strip()
            dbuser = self.dbuser_edit.text().strip()
            dbpass = self.dbpass_edit.text().strip()
            sslcert = self.cert_text.toPlainText().strip()
            api_base = self.api_base_edit.text().strip()
            api_email = self.api_email_edit.text().strip()
            api_pass = self.api_pass_edit.text().strip()
            local_addr = self.local_server_addr_edit.text().strip()
            local_port = int(self.local_server_port_edit.value())

            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    if self.client_id:
                        cur.execute("""
                            UPDATE clients SET 
                                name=%s, db_host=%s, db_port=%s, db_name=%s, db_user=%s, 
                                db_password=%s, db_ssl_cert=%s, api_base_url=%s, api_email=%s, 
                                api_password=%s, local_server_address=%s, local_server_port=%s
                            WHERE id=%s
                        """, (
                            name, host, port, dbname, dbuser, dbpass, sslcert, 
                            api_base, api_email, api_pass, local_addr, local_port,
                            self.client_id
                        ))
                    else:
                        cur.execute("""
                            INSERT INTO clients (
                                name, db_host, db_port, db_name, db_user, db_password, 
                                db_ssl_cert, api_base_url, api_email, api_password,
                                local_server_address, local_server_port
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
                        """, (
                            name, host, port, dbname, dbuser, dbpass, sslcert, 
                            api_base, api_email, api_pass, local_addr, local_port
                        ))
                        new_id = cur.fetchone()[0]
                        self.client_id = new_id
                        self.init_db_btn.setEnabled(True)
                        self.setWindowTitle(f"Редактор клиента: {new_id}")
                conn.commit()
            QMessageBox.information(self, "Успех", "Данные сохранены")
            self.accept()
        except Exception as e:
            logging.error(f"Ошибка сохранения клиента: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить данные клиента: {e}")

    def run_client_db_setup(self):
        """Инициализация / обновление схемы для базы данных выбранного клиента."""
        if not self.client_id:
            QMessageBox.warning(self, "Внимание", "Сначала сохраните клиента перед инициализацией базы данных")
            return
        
        # --- НОВЫЙ БЛОК: Окно с логом, как для главной БД ---
        log_dialog = QDialog(self)
        log_dialog.setWindowTitle("Инициализация БД клиента")
        log_dialog.setMinimumSize(600, 400)
        log_layout = QVBoxLayout()
        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_layout.addWidget(log_text)
        log_dialog.setLayout(log_layout)

        def add_log(message, level="INFO"):
            log_text.append(f"[{level}] {message}")
            QApplication.processEvents() # Обновляем UI

        # --- ИСПРАВЛЕНИЕ: Переносим импорт в начало, чтобы избежать UnboundLocalError ---
        import psycopg2
        try:
            # --- ИСПРАВЛЕНИЕ: Используем psycopg2.extras.RealDictCursor ---
            with get_main_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur: # Теперь psycopg2 определен
                    cur.execute("SELECT * FROM clients WHERE id = %s", (self.client_id,))
                    db_data = cur.fetchone()
            if not db_data:
                raise ValueError("Не удалось найти данные для подключения к БД клиента.")
            
            add_log("Данные клиента из главной БД получены.")

            # Проверка существования БД клиента на сервере
            db_host = db_data.get('db_host')
            db_port = db_data.get('db_port')
            db_name = db_data.get('db_name')
            db_user = db_data.get('db_user')
            db_password = db_data.get('db_password')
            db_ssl_cert = db_data.get('db_ssl_cert')

            ssl_params_check = {}
            temp_cert_file_check = None
            if db_ssl_cert:
                from tempfile import NamedTemporaryFile
                with NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                    fp.write(db_ssl_cert)
                    temp_cert_file_check = fp.name
                ssl_params_check = {'sslmode': 'verify-full', 'sslrootcert': temp_cert_file_check}
            add_log(f"Проверка существования БД '{db_name}' на сервере {db_host}...")

            try:
                with psycopg2.connect(host=db_host, port=db_port, dbname='postgres', user=db_user, password=db_password, **ssl_params_check) as conn_system:
                    conn_system.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                    with conn_system.cursor() as cur:
                        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                        exists = cur.fetchone() is not None
                        if not exists:
                            add_log(f"ОШИБКА: База данных '{db_name}' не найдена.", "ERROR")
                            # Предложим сохранить команды создания БД в файл
                            msg = f"База данных '{db_name}' не найдена на сервере {db_host}.\n\nХотите сохранить команды для ее создания в SQL-файл?"
                            if QMessageBox.question(self, "База данных не найдена", msg, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                                # --- ИЗМЕНЕНИЕ: Добавляем GRANT CONNECT для основного пользователя ---
                                sql_commands = (
                                    f"CREATE DATABASE {db_name};\n"
                                    f"GRANT CONNECT ON DATABASE {db_name} TO \"{db_user}\";\n"
                                )
                                fn, _ = QFileDialog.getSaveFileName(self, "Сохранить SQL", f"create_{db_name}.sql", "SQL Files (*.sql);;Text files (*.txt)")
                                if fn:
                                    with open(fn, 'w', encoding='utf-8') as f:
                                        f.write(sql_commands)
                                    QMessageBox.information(self, "Успех", f"Команды сохранены в файл:\n{fn}")
                            return
                        add_log("БД найдена. Проверка пройдена.")
            finally:
                if temp_cert_file_check and os.path.exists(temp_cert_file_check):
                    try:
                        os.remove(temp_cert_file_check)
                    except Exception:
                        pass

            add_log("Подключение к БД клиента для обновления схемы...")
            # Подключаемся к клиентской БД через пул и выполняем update_client_db_schema
            from .db_connector import get_client_db_connection
            db_data['id'] = self.client_id
            user_info_for_client_db = {'client_db_config': db_data}
            try:
                with get_client_db_connection(user_info_for_client_db) as client_conn:
                    if not client_conn:
                        raise ConnectionError("Не удалось установить соединение с БД клиента.")
                    add_log("Соединение установлено. Запуск обновления схемы...")
                    success = update_client_db_schema(client_conn)
                    if success:
                        add_log("УСПЕХ: Схема базы данных клиента успешно обновлена.", "SUCCESS")
                    else:
                        add_log("ОШИБКА: Произошла ошибка при обновлении схемы. См. логи приложения.", "ERROR")
            except Exception as e:
                logging.error(f"Не удалось выполнить инициализацию БД клиента: {e}\n{traceback.format_exc()}")
                add_log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", "ERROR")
        except Exception as e:
            logging.error(f"Ошибка в run_client_db_setup: {e}\n{traceback.format_exc()}")
            add_log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", "ERROR")
        
        log_dialog.exec() # Показываем окно с логом

    def _run_ping_test(self):
        # --- ИСПРАВЛЕНИЕ: Пинг-тест теперь показывает детальный лог в отдельном окне ---
        log_dialog = QDialog(self)
        log_dialog.setWindowTitle("Лог Пинг-теста")
        log_dialog.setMinimumSize(600, 400)
        log_layout = QVBoxLayout()
        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_layout.addWidget(log_text)
        log_dialog.setLayout(log_layout)

        def add_log(message, level="INFO"):
            log_text.append(f"[{level}] {message}")
            QApplication.processEvents() # Обновляем UI

        try:
            add_log("Начало пинг-теста...")
            # Формируем конфиг из полей
            # --- ИСПРАВЛЕНИЕ: Используем правильные имена виджетов (self.port_edit, self.dbname_edit и т.д.) ---
            db_config_from_ui = {
                'db_host': self.host_edit.text().strip(),
                'db_port': int(self.port_edit.value()),
                'db_name': self.dbname_edit.text().strip(),
                'db_user': self.dbuser_edit.text().strip(),
                'db_password': self.dbpass_edit.text().strip(),
                'db_ssl_cert': self.cert_text.toPlainText().strip(),
                'local_server_address': self.local_server_addr_edit.text().strip(),
                'local_server_port': int(self.local_server_port_edit.value())
            }
            from .db_connector import _attempt_db_connection
            
            # Попытка 1: внешний адрес с SSL
            ext_host = db_config_from_ui['db_host']
            if ext_host:
                add_log(f"Шаг 1: Попытка подключения по внешнему адресу {ext_host}:{db_config_from_ui['db_port']} с SSL...")
                try:
                    with _attempt_db_connection(db_config_from_ui, db_config_from_ui['db_ssl_cert'], 'verify-full') as conn:
                        if conn:
                            add_log("УСПЕХ: Подключение по внешнему адресу с SSL прошло успешно!", "SUCCESS")
                            log_dialog.exec()
                            return # Выходим, если успешно
                except Exception as e:
                    add_log(f"ОШИБКА: Не удалось подключиться. {e}", "ERROR")
            else:
                add_log("Шаг 1: Пропущен. Внешний адрес не указан.", "INFO")

            # Попытка 2: внутренний адрес без SSL
            local_host = db_config_from_ui['local_server_address']
            if local_host:
                add_log(f"Шаг 2: Попытка подключения по внутреннему адресу {local_host}:{db_config_from_ui['local_server_port']} без SSL...")
                try:
                    with _attempt_db_connection(db_config_from_ui, None, 'disable', use_local=True) as conn:
                        if conn:
                            add_log("УСПЕХ: Подключение по внутреннему адресу без SSL прошло успешно!", "SUCCESS")
                            log_dialog.exec()
                            return # Выходим, если успешно
                except Exception as e:
                    add_log(f"ОШИБКА: Не удалось подключиться. {e}", "ERROR")
            else:
                add_log("Шаг 2: Пропущен. Внутренний адрес не указан.", "INFO")
            
            add_log("ПРОВАЛ: Не удалось подключиться ни по одному из адресов.", "ERROR")
            log_dialog.exec()
        except Exception as e:
            logging.error(f"Ping test error: {e}\n{traceback.format_exc()}")
            add_log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", "ERROR")
            log_dialog.exec()

    def load_users_for_editor(self, c_id: int):
        try:
            self.users_table.setRowCount(0)
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, login, role, is_active FROM users WHERE client_id = %s ORDER BY name;", (c_id,))
                    rows = cur.fetchall()
            for r in rows:
                row = self.users_table.rowCount()
                self.users_table.insertRow(row)
                self.users_table.setItem(row, 0, QTableWidgetItem(str(r[0])))
                self.users_table.setItem(row, 1, QTableWidgetItem(str(r[1] or '')))
                self.users_table.setItem(row, 2, QTableWidgetItem(str(r[2] or '')))
                self.users_table.setItem(row, 3, QTableWidgetItem(str(r[3] or '')))
                self.users_table.setItem(row, 4, QTableWidgetItem(str(bool(r[4]))))
        except Exception as e:
            logging.error(f"Ошибка загрузки пользователей в редакторе: {e}\n{traceback.format_exc()}")

    def add_user(self):
        if not self.client_id:
            QMessageBox.warning(self, "Внимание", "Сначала сохраните клиента")
            return
        dlg = UserEditorDialog(parent=self, client_id=self.client_id)
        if dlg.exec():
            try:
                self.load_users_for_editor(self.client_id)
            except Exception:
                pass

    def edit_user(self):
        sel = self.users_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите пользователя")
            return
        user_id_item = self.users_table.item(sel, 0)
        if not user_id_item:
            return
        user_id = int(user_id_item.text())
        # Откроем диалог редактирования: позволим поменять имя и пароль
        dlg = QDialog(self)
        dlg.setWindowTitle("Редактирование пользователя")
        form = QFormLayout()
        name_edit = QLineEdit()
        pass_edit = QLineEdit()
        pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Имя", name_edit)
        form.addRow("Новый пароль (оставьте пустым, если не меняете)", pass_edit)
        btns = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        cancel_btn = QPushButton("Отмена")
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        v = QVBoxLayout()
        v.addLayout(form)
        v.addLayout(btns)
        dlg.setLayout(v)

        def do_save():
            try:
                new_name = name_edit.text().strip()
                new_password = pass_edit.text()
                with get_main_db_connection() as conn:
                    with conn.cursor() as cur:
                        if new_password:
                            import bcrypt
                            hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                            cur.execute("UPDATE users SET name = %s, password_hash = %s WHERE id = %s", (new_name, hashed, user_id))
                            cur.execute("SELECT login, is_active FROM users WHERE id = %s", (user_id,))
                            row = cur.fetchone()
                            login = row[0]
                            is_active = row[1]
                            try:
                                sync_user_with_client_db(self.client_id, login, hashed, True, is_active)
                            except Exception:
                                logging.exception('sync failed')
                        else:
                            cur.execute("UPDATE users SET name = %s WHERE id = %s", (new_name, user_id))
                    conn.commit()
                QMessageBox.information(self, "Успех", "Данные пользователя обновлены")
                dlg.accept()
                self.load_users_for_editor(self.client_id)
            except Exception as e:
                logging.error(f"Ошибка обновления пользователя: {e}\n{traceback.format_exc()}")
                QMessageBox.critical(self, "Ошибка", f"Не удалось обновить пользователя: {e}")

        save_btn.clicked.connect(do_save)
        cancel_btn.clicked.connect(dlg.reject)
        # Предзаполним текущие значения
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
                    row = cur.fetchone()
                    if row:
                        name_edit.setText(row[0] or '')
        except Exception:
            pass
        dlg.exec()

    def delete_user(self):
        sel = self.users_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите пользователя")
            return
        user_id = int(self.users_table.item(sel, 0).text())
        login = self.users_table.item(sel, 2).text()
        if QMessageBox.question(self, "Подтверждение", f"Вы уверены, что хотите удалить пользователя '{login}'?") != QMessageBox.Yes:
            return
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                conn.commit()
            try:
                sync_user_with_client_db(self.client_id, login, 'deleted', False, False)
            except Exception:
                logging.exception('sync delete failed')
            self.load_users_for_editor(self.client_id)
            QMessageBox.information(self, "Успех", "Пользователь удалён")
        except Exception as e:
            logging.error(f"Ошибка удаления пользователя: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить пользователя: {e}")

    def toggle_user_activity(self):
        sel = self.users_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите пользователя")
            return
        user_id = int(self.users_table.item(sel, 0).text())
        login = self.users_table.item(sel, 2).text()
        is_active = self.users_table.item(sel, 4).text().lower() in ('true', '1')
        new_status = not is_active
        try:
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, user_id))
                    cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
                    password_hash = cur.fetchone()[0]
                conn.commit()
            try:
                sync_user_with_client_db(self.client_id, login, password_hash, True, new_status)
            except Exception:
                logging.exception('sync toggle failed')
            self.load_users_for_editor(self.client_id)
        except Exception as e:
            logging.error(f"Ошибка изменения статуса пользователя: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось изменить статус пользователя: {e}")


class UserEditorDialog(QDialog):
    def __init__(self, parent=None, client_id: int = None, is_supervisor: bool = False):
        super().__init__(parent)
        self.client_id = client_id
        self.is_supervisor = is_supervisor
        self.setWindowTitle("Новый супервизор" if is_supervisor else "Новый пользователь")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.login_edit = QLineEdit()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Имя", self.name_edit)
        form.addRow("Логин", self.login_edit)
        form.addRow("Пароль", self.pass_edit)

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self.save)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(form)
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def save(self):
        try:            
            name = self.name_edit.text().strip()
            login = self.login_edit.text().strip()
            password = self.pass_edit.text()
            if not all([name, login, password]):
                QMessageBox.warning(self, "Внимание", "Все поля обязательны")
                return
            import bcrypt
            role = 'супервизор' if self.is_supervisor else 'администратор'
            client_id_to_save = None if self.is_supervisor else self.client_id

            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            with get_main_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (name, login, password_hash, role, client_id, is_active) VALUES (%s,%s,%s,%s,%s, TRUE)", 
                        (name, login, hashed, role, client_id_to_save)
                    )
                conn.commit()
            # Синхронизируем только администраторов, не супервизоров.
            if not self.is_supervisor and self.client_id:
                try:
                    synced = sync_user_with_client_db(self.client_id, login, hashed, True, True)
                    if not synced:
                        logging.warning(f"Не удалось синхронизировать пользователя {login} с клиентской БД (id={self.client_id})")
                except Exception:
                    logging.exception('sync_user_with_client_db failed')

            QMessageBox.information(self, "Успех", f"{role.capitalize()} создан")
            self.accept()
        except Exception as e:
            logging.error(f"Ошибка создания пользователя: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать {role.lower()}: {e}")
