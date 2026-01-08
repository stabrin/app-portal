import sys
import os
import logging
from dotenv import load_dotenv

def setup_logging():
    if getattr(sys, 'frozen', False):
        project_root = os.path.dirname(sys.executable)
    else:
        project_root = os.path.dirname(os.path.abspath(__file__))

    log_dir = os.path.join(project_root, 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    log_file_path = os.path.join(log_dir, 'app_oper.log')

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(name)s.%(funcName)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, encoding='utf-8', mode='a'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info("="*50)
    logging.info("Logging system configured for Operator Mode.")

# Call logging setup immediately
setup_logging()

# Шаг 3: Принудительные импорты для cx_Freeze
try:
    from PySide6 import QtWidgets, QtCore, QtGui
    import greenlet
    import babel.numbers
    import jinja2
except ImportError as e:
    logging.warning(f"Не удалось выполнить принудительный импорт для cx_Freeze: {e}")

# Шаг 1: Подготовка окружения и конфигурации
def setup_environment():
    """
    Настраивает пути, переменные окружения и создает объект user_info.
    """
    # 1. Настройка путей (логика из run.py)
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(base_dir)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    os.environ['PATH'] = base_dir + os.pathsep + os.environ['PATH']

    # --- ИСПРАВЛЕНИЕ: Добавляем путь к libdmtx-64.dll ---
    try:
        import pylibdmtx
        pylibdmtx_dir = os.path.dirname(pylibdmtx.__file__)
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(pylibdmtx_dir)
        os.environ['PATH'] = pylibdmtx_dir + os.pathsep + os.environ['PATH']
    except ImportError:
        logging.error("Failed to import pylibdmtx", exc_info=True)
    except Exception as e:
        logging.warning(f"Не удалось динамически добавить путь для libdmtx: {e}")
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
    
    project_root = os.path.abspath(os.path.join(base_dir))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 2. Чтение .env
    dotenv_path = os.path.join(project_root, '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
    else:
        raise FileNotFoundError(f"Файл конфигурации .env не найден по пути: {dotenv_path}")

    # 3. Формирование user_info
    user_info = {
        "name": "OperatorMode",
        "role": "оператор",
        "client_db_config": {
            'id': 1,
            'db_host': os.getenv("DB_HOST"),
            'db_port': os.getenv("DB_PORT"),
            'db_name': os.getenv("DB_NAME"),
            'db_user': os.getenv("DB_USER"),
            'db_password': os.getenv("DB_PASSWORD"),
            'local_server_address': os.getenv("LOCAL_SERVER_ADDRESS", os.getenv("DB_HOST")),
            'local_server_port': os.getenv("LOCAL_SERVER_PORT", os.getenv("DB_PORT")),
            'db_ssl_cert': None
        }
    }

    cert_path_str = os.getenv("DB_SSL_CERT_PATH")
    if cert_path_str:
        if getattr(sys, 'frozen', False) and cert_path_str.startswith('../'):
            cert_path_str = cert_path_str.replace('../', '', 1)

        if not os.path.isabs(cert_path_str):
            cert_path_str = os.path.join(project_root, cert_path_str)
        cert_path_str = os.path.abspath(cert_path_str)
        
        if os.path.exists(cert_path_str):
            try:
                with open(cert_path_str, 'r', encoding='utf-8') as f:
                    cert_content = f.read()
                user_info["client_db_config"]["db_ssl_cert"] = cert_content
            except Exception as e:
                logging.error(f"Ошибка при чтении файла SSL сертификата: {e}")
        else:
            logging.warning(f"Файл SSL сертификата не найден по пути: {cert_path_str}")

    # 4. Проверка соединения
    from src.db_connector import get_client_db_connection
    try:
        with get_client_db_connection(user_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception as e:
        logging.critical(f"Не удалось подключиться к базе данных клиента. Проверьте настройки в .env. Ошибка: {e}")
        raise

    return project_root, user_info

def main(user_info):
    from src.task_service import TaskService
    from src.catalogs_service import CatalogsService
    from src.operator_login_ui import OperatorLoginWindow
    from src.operator_work_ui import OperatorWorkWindow
    from src.db_connector import get_client_db_connection
    from PySide6.QtWidgets import QApplication

    logging.info("Initializing Operator application UI...")
    
    app = QApplication(sys.argv)

    task_service = TaskService(user_info)
    db_conn_func = lambda: get_client_db_connection(user_info)
    catalogs_service = CatalogsService(user_info, db_conn_func)
    logging.info("Services initialized.")

    login_dialog = OperatorLoginWindow(task_service)
    
    if login_dialog.exec():
        task_info = login_dialog.get_task_info()
        if task_info:
            logging.info(f"Login successful. Starting main window for task #{task_info['task_id']}.")
            main_window = OperatorWorkWindow(task_service, catalogs_service, user_info, task_info)
            main_window.show()
            sys.exit(app.exec())
        else:
            logging.error("Login dialog was accepted, but no task info was returned.")
            sys.exit(1)
    else:
        logging.info("Login dialog was closed or rejected. Exiting application.")
        sys.exit(0)

if __name__ == "__main__":
    project_root_path = None
    app_user_info = None
    try:
        # Логирование настраивается *после* определения путей, чтобы лог-файл был в правильном месте
        project_root_path, app_user_info = setup_environment()
        # После успешной настройки вызываем main
        main(app_user_info)
    except Exception as e:
        logging.critical("An unhandled exception occurred at the top level.", exc_info=True)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if not QApplication.instance():
                QApplication(sys.argv)
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Critical)
            msg_box.setText("Критическая ошибка запуска")
            msg_box.setInformativeText(f"Произошла непредвиденная ошибка:\n{e}\n\nПодробности в файле app_oper.log")
            msg_box.setStandardButtons(QMessageBox.Ok)
            msg_box.exec()
        except Exception as e_msg:
            logging.critical(f"Failed to show critical error message box: {e_msg}")
        
        sys.exit(1)
