from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QMessageBox
)
from PySide6.QtCore import Slot
import sys
import os
import logging
import traceback
import base64
import configparser
import requests
from dotenv import load_dotenv

# Механика поиска project root аналогична старому коду
if getattr(sys, 'frozen', False):
    if hasattr(sys, '_MEIPASS'):
        project_root = sys._MEIPASS
    else:
        project_root = os.path.dirname(sys.executable)
else:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

dotenv_path = os.path.join(project_root, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    logging.warning(f"auth_qt: .env not found at {dotenv_path}")

from .db_connector import get_main_db_connection


class AuthWindow(QMainWindow):
    def __init__(self, on_complete_callback):
        super().__init__()
        self.on_complete = on_complete_callback
        self.setWindowTitle("Вход")
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        v = QVBoxLayout()

        lbl_login = QLabel("Логин:")
        self.edit_login = QLineEdit()

        lbl_pass = QLabel("Пароль:")
        self.edit_pass = QLineEdit()
        self.edit_pass.setEchoMode(QLineEdit.EchoMode.Password)

        btn_login = QPushButton("Войти")
        btn_login.clicked.connect(self.on_login)
        # Сделать кнопку по умолчанию — на Enter сработает, когда форма в фокусе
        try:
            btn_login.setDefault(True)
            btn_login.setAutoDefault(True)
        except Exception:
            pass

        btn_qr = QPushButton("Войти по QR-коду")
        btn_qr.clicked.connect(self.on_login_qr)

        v.addWidget(lbl_login)
        v.addWidget(self.edit_login)
        v.addWidget(lbl_pass)
        v.addWidget(self.edit_pass)

        h = QHBoxLayout()
        h.addWidget(btn_login)
        h.addWidget(btn_qr)
        v.addLayout(h)

        central.setLayout(v)
        self.setCentralWidget(central)
        # Обработчики нажатия Enter в полях ввода
        self.edit_login.returnPressed.connect(self.on_login)
        self.edit_pass.returnPressed.connect(self.on_login)

    @Slot()
    def on_login_qr(self):
        QMessageBox.information(self, "В разработке", "Вход по QR-коду пока не реализован")

    @Slot()
    def on_login(self):
        login = self.edit_login.text().strip()
        password = self.edit_pass.text()

        if not login or not password:
            QMessageBox.critical(self, "Ошибка", "Логин и пароль не могут быть пустыми")
            return

        # Если есть config.ini — локальный режим
        config_path = os.path.join(project_root, 'config.ini')
        cert_path = os.path.join(project_root, 'cert.pem')

        if os.path.exists(config_path):
            try:
                user_info = self._local_auth(login, password, config_path, cert_path)
                if user_info:
                    self.on_complete(user_info)
                    self.close()
                else:
                    QMessageBox.critical(self, "Ошибка", "Авторизация не удалась")
            except Exception as e:
                logging.error(f"Local auth error: {e}\n{traceback.format_exc()}")
                QMessageBox.critical(self, "Критическая ошибка", f"Ошибка локальной авторизации: {e}")
            return

        # Иначе онлайн режим — через main DB
        try:
            user_info = self._online_auth(login, password)
            if user_info:
                self.on_complete(user_info)
                self.close()
            else:
                QMessageBox.critical(self, "Ошибка", "Пользователь не найден или нет прав доступа")
        except Exception as e:
            logging.error(f"Online auth error: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "Критическая ошибка", "Ошибка подключения к базе данных")

    def _local_auth(self, login, password, config_path, cert_path):
        # Читаем config.ini
        cfg = configparser.ConfigParser()
        cfg.read(config_path, encoding='utf-8')
        db_section = cfg['database']

        # Вспомогательная xor-декодировка (копия логики из Tk версии)
        def xor_cipher(data: bytes, key: str) -> str:
            return bytes([c ^ ord(k) for c, k in zip(data, key * (len(data) // len(key) + 1))]).decode('utf-8')

        encryption_key = "TildaKodSecretKey"
        encrypted_b64 = db_section.get('password')
        encrypted_bytes = base64.b64decode(encrypted_b64)
        decrypted_password = xor_cipher(encrypted_bytes, encryption_key)

        client_db_config = {
            "db_name": db_section.get('dbname'),
            "db_host": db_section.get('host'),
            "db_port": db_section.getint('port'),
            "db_user": db_section.get('user'),
            "db_password": decrypted_password,
            "db_ssl_cert": None,
            "id": 0
        }
        if os.path.exists(cert_path):
            with open(cert_path, 'r', encoding='utf-8') as f:
                client_db_config['db_ssl_cert'] = f.read()

        from .db_connector import get_client_db_connection
        user_data = None
        with get_client_db_connection({'client_db_config': client_db_config}) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT username, password_hash, is_admin FROM public.users WHERE username = %s AND is_active = TRUE", (login,))
                user_data = cur.fetchone()
                cur.execute("SELECT setting_key, setting_value FROM public.ap_settings WHERE setting_key IN ('API_BASE_URL','API_EMAIL','API_PASSWORD')")
                settings = {row[0]: row[1] for row in cur.fetchall()}

        if user_data:
            user_name, hashed_password, is_admin = user_data
            import bcrypt
            if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                if not is_admin:
                    QMessageBox.critical(self, "Ошибка", "Для локального входа требуются права администратора")
                    return None
                user_info = {
                    'name': user_name,
                    'role': 'администратор',
                    'client_id': 0,
                    'client_db_config': client_db_config,
                    'client_api_config': {
                        'api_base_url': settings.get('API_BASE_URL'),
                        'api_email': settings.get('API_EMAIL'),
                        'api_password': settings.get('API_PASSWORD')
                    }
                }
                # Токен API больше не получается здесь, только конфигурация
                return user_info
        return None

    def _online_auth(self, login, password):
        with get_main_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT u.name, u.password_hash, u.role, u.client_id, c.db_name, c.db_host, c.db_port, c.db_user, c.db_password, c.db_ssl_cert, c.api_base_url, c.api_email, c.api_password FROM users u LEFT JOIN clients c ON u.client_id = c.id WHERE u.login = %s AND u.is_active = TRUE AND (u.role = 'супервизор' OR u.role = 'администратор')", (login,))
                row = cur.fetchone()

        if not row:
            return None

        (user_name, hashed_password, user_role, client_id, db_name, db_host, db_port, db_user, db_password, db_ssl_cert, api_base_url, api_email, api_password) = row
        import bcrypt
        if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
            user_info = {'name': user_name, 'role': user_role}
            if user_role == 'администратор':
                user_info['client_id'] = client_id
                user_info['client_db_config'] = {
                    'db_name': db_name, 'db_host': db_host, 'db_port': db_port, 'db_user': db_user, 'db_password': db_password, 'db_ssl_cert': db_ssl_cert, 'id': client_id
                }
                user_info['client_api_config'] = {'api_base_url': api_base_url, 'api_email': api_email, 'api_password': api_password}
                # Токен API больше не получается здесь
            return user_info
        return None


def main():
    app = QApplication(sys.argv)
    result_container = {}

    def on_done(user_info):
        result_container['result'] = user_info

    w = AuthWindow(on_done)
    w.show()
    app.exec()

    user_info = result_container.get('result')
    if not user_info:
        logging.info('Login cancelled or failed')
        return

    # После успешной авторизации — создать нужное главное окно в зависимости от роли
    role = user_info.get('role')
    logging.info(f"User role after auth: {role}")
    # Открываем окно супервизора только для роли 'супервизор'
    if role == 'супервизор':
        try:
            from .supervisor_ui_qt import SupervisorWindowQt
            main_win = SupervisorWindowQt(user_info)
            main_win.show()
            sys.exit(app.exec())
        except Exception as e:
            logging.exception(f"Failed to open SupervisorWindowQt: {e}")
            QMessageBox.critical(None, 'Ошибка', f'Не удалось открыть окно супервизора: {e}')
            return
    elif role == 'администратор':
        # Для администратора используем отдельный интерфейс (будет реализован позже).
        try:
            from .admin_ui_qt import AdminWindowQt
            main_win = AdminWindowQt(user_info)
            main_win.show()
            sys.exit(app.exec())
        except Exception as e:
            logging.exception(f"Failed to open AdminWindowQt: {e}")
            QMessageBox.critical(None, 'Ошибка', f'Не удалось открыть окно администратора: {e}')
            return
    else:
        QMessageBox.critical(None, 'Ошибка', f"Неизвестная роль пользователя: {role}")
        return


if __name__ == '__main__':
    main()
