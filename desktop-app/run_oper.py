# run_oper.py

import sys
import os
import logging
import traceback
from dotenv import load_dotenv

# --- 1. Настройка путей и принудительные импорты (важно для .exe) ---

# Добавляем корень проекта в sys.path для корректного импорта модулей
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Логика для поиска DLL, как в основном приложении
if getattr(sys, 'frozen', False):
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller
        dll_path = sys._MEIPASS
    else:
        # cx_Freeze / Nuitka
        dll_path = os.path.dirname(sys.executable)
    
    if os.path.isdir(dll_path):
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(dll_path)
        else:
            os.environ['PATH'] = dll_path + os.pathsep + os.environ['PATH']

# Принудительные импорты для сборщика
try:
    from PySide6 import QtMultimedia, QtNetwork, QtOpenGL, QtOpenGLWidgets, QtPdf, QtPdfWidgets, QtPositioning, QtQml, QtQuick, QtQuickWidgets, QtSensors, QtSerialPort, QtSql, QtSvg, QtSvgWidgets, QtWebChannel, QtWebEngineCore, QtWebEngineQuick, QtWebEngineWidgets, QtWebSockets, Qt3DCore, Qt3DAnimation, Qt3DExtras, Qt3DInput, Qt3DLogic, Qt3DRender
    import sqlalchemy
    import greenlet
except ImportError:
    pass # Игнорируем, если в режиме разработки

from PySide6.QtWidgets import QApplication, QMessageBox

from src.task_service import TaskService
from src.catalogs_service import CatalogsService
from src.operator_login_ui import OperatorLoginWindow
from src.operator_work_ui import OperatorWorkWindow
from src.db_connector import get_client_db_connection

def setup_logging():
    """Настраивает систему логирования для приложения оператора."""
    log_dir = os.path.join(project_root, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, 'app_oper.log')

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(module)s.%(funcName)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    logging.info("Логирование для приложения оператора настроено.")

def setup_environment():
    """
    Загружает конфигурацию из .env, формирует user_info и проверяет подключение к БД.
    """
    dotenv_path = os.path.join(project_root, '.env')
    if not os.path.exists(dotenv_path):
        raise FileNotFoundError(f"Файл конфигурации .env не найден по пути: {dotenv_path}")
    
    load_dotenv(dotenv_path=dotenv_path)
    logging.info(f".env файл загружен из {dotenv_path}")

    # --- ИСПРАВЛЕНИЕ: Читаем путь к сертификату и загружаем его содержимое ---
    ssl_cert_content = None
    cert_path_from_env = os.getenv("DB_SSL_CERT_PATH")
    if cert_path_from_env:
        # Используем project_root для построения абсолютного пути от корня проекта
        absolute_cert_path = os.path.join(project_root, cert_path_from_env)
        if os.path.exists(absolute_cert_path):
            with open(absolute_cert_path, 'r', encoding='utf-8') as f:
                ssl_cert_content = f.read()
            logging.info(f"SSL-сертификат успешно загружен из {absolute_cert_path}")
        else:
            logging.warning(f"Файл SSL-сертификата не найден по пути: {absolute_cert_path}")

    # Формируем user_info на основе .env
    user_info = {
        "name": "OperatorMode",
        "role": "администратор", # Используем 'администратор' для доступа ко всем сервисам
        "client_id": None, # ID клиента будет определен после входа
        "client_db_config": {
            'db_host': os.getenv("DB_HOST"), 'db_port': os.getenv("DB_PORT"), 'db_name': os.getenv("DB_NAME"), 'db_user': os.getenv("DB_USER"), 'db_password': os.getenv("DB_PASSWORD"), 'db_ssl_cert': ssl_cert_content, # Передаем содержимое сертификата
            'local_server_address': os.getenv("LOCAL_SERVER_ADDRESS"),
            'local_server_port': os.getenv("LOCAL_SERVER_PORT")
        }
    }

    # Добавляем ID клиента и в client_db_config, так как он используется в db_connector
    user_info["client_db_config"]["id"] = 0 # Используем общий пул для начальной проверки

    # Проверяем, что все необходимые параметры загружены
    required_params = ['db_host', 'db_port', 'db_name', 'db_user', 'db_password']
    if not all(user_info['client_db_config'].get(p) for p in required_params):
        missing = [p for p in required_params if not user_info['client_db_config'].get(p)]
        raise ValueError(f"Не все обязательные параметры БД указаны в .env: {missing}")

    # Проверяем соединение с БД
    logging.info("Проверка соединения с базой данных клиента...")
    try:
        with get_client_db_connection(user_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                if cur.fetchone():
                    logging.info("Соединение с базой данных успешно установлено.")
                else:
                    raise ConnectionError("Запрос к БД не вернул результат.")
    except Exception as e:
        logging.critical(f"Не удалось подключиться к базе данных клиента: {e}", exc_info=True)
        raise ConnectionError(f"Критическая ошибка: не удалось подключиться к БД. Проверьте настройки в .env и доступность базы. Ошибка: {e}")

    return user_info

def main(user_info: dict):
    """Основная функция запуска приложения."""
    app = QApplication(sys.argv)

    # Инициализация сервисов
    logging.info("Инициализация сервисов...")
    task_service = TaskService(user_info)
    db_conn_func = lambda: get_client_db_connection(user_info)
    catalogs_service = CatalogsService(user_info, db_conn_func)
    logging.info("Сервисы инициализированы.")

    # Запуск UI
    login_dialog = OperatorLoginWindow(task_service, user_info)
    if login_dialog.exec():
        task_info = login_dialog.get_task_info()
        if task_info:
            logging.info(f"Вход успешен. Запуск рабочего окна для задачи #{task_info['task_id']}")
            main_window = OperatorWorkWindow(task_service, catalogs_service, user_info, task_info)
            main_window.show()
            sys.exit(app.exec())
        else:
            logging.warning("Диалог входа был принят, но информация о задаче не получена.")
    else:
        logging.info("Вход отменен пользователем. Приложение завершает работу.")
        sys.exit(0)

if __name__ == "__main__":
    try:
        setup_logging()
        app_user_info = setup_environment()
        main(app_user_info)
    except Exception as e:
        logging.critical("Критическая ошибка при запуске приложения оператора.", exc_info=True)
        # Показываем сообщение об ошибке, если QApplication еще не был создан
        if QApplication.instance() is None:
            app = QApplication(sys.argv)
        QMessageBox.critical(None, "Критическая ошибка", f"Не удалось запустить приложение:\n\n{e}\n\nПодробности в файле app_oper.log.")
        sys.exit(1)