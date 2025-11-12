# src/db_connector.py

import os
import tempfile
import logging
import psycopg2
from contextlib import contextmanager
from typing import Dict, Any, Optional

from dotenv import load_dotenv

from .utils import project_root_path # --- ИЗМЕНЕНИЕ: Импортируем новую функцию для доступа к корню проекта ---

@contextmanager
def get_main_db_connection():
    """

    Контекстный менеджер, который возвращает готовое SSL-соединение
    с ГЛАВНОЙ базой данных (portal_db).
    """
    # --- ИЗМЕНЕНИЕ: Убираем зависимость от .env и хардкодим параметры ---
    db_params = {
        "dbname": "tilda_db",
        "user": "portal_user",
        "password": "!T-W0rkshop", 
        "host": "109.172.115.204",
        "port": "5432",
        "connect_timeout": 10,
        "sslmode": 'verify-full'
    }

    # --- ИЗМЕНЕНИЕ: Используем project_root_path для доступа к папке secrets в корне проекта ---
    cert_path = project_root_path(os.path.join('secrets', 'postgres', 'server.crt'))

    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Сертификат сервера не найден по пути: {cert_path}")

    # Подключаемся к БД напрямую с использованием SSL
    db_params['sslrootcert'] = cert_path
    conn = psycopg2.connect(**db_params)
    yield conn
    conn.close()

@contextmanager
def get_client_db_connection(user_info: Dict[str, Any]) -> Optional[psycopg2.extensions.connection]:
    """
    Контекстный менеджер для подключения к БД клиента с логикой отказоустойчивости и кэширования.
    Сначала пытается подключиться по внешнему адресу с SSL, затем по внутреннему без SSL.
    Кэширует успешные параметры подключения в `user_info` для текущей сессии.
    """
    db_config = user_info.get("client_db_config")
    if not db_config:
        logging.error("Отсутствует конфигурация БД клиента в user_info.")
        raise ValueError("Конфигурация базы данных клиента не предоставлена.")

    conn = None
    try:
        # --- ЛОГИКА КЭШИРОВАНИЯ ---
        cached_params = user_info.get('_cached_client_db_params')
        if cached_params:
            logging.debug("Использую кэшированные параметры подключения к БД клиента.")
            try:
                conn = _attempt_db_connection(cached_params, cached_params.get('db_ssl_cert_content'), cached_params.get('sslmode'))
                if conn:
                    logging.info(f"Успешное подключение к БД с использованием кэшированных параметров: {cached_params['dbname']}")
                    yield conn
                    return
            except Exception as e:
                logging.warning(f"Не удалось подключиться к БД клиента с кэшированными параметрами: {e}. Попытка переподключения.")
                user_info.pop('_cached_client_db_params', None)

        # --- ПОПЫТКА 1: ВНЕШНИЙ АДРЕС С SSL ---
        external_params = {
            'host': db_config.get('db_host'), 'port': db_config.get('db_port'), 'dbname': db_config.get('db_name'),
            'user': db_config.get('db_user'), 'password': db_config.get('db_password')
        }
        if all(external_params.values()):
            logging.debug(f"Попытка подключения к БД клиента по внешнему адресу: {external_params['host']}:{external_params['port']} с SSL.")
            try:
                conn = _attempt_db_connection(external_params, db_config.get('db_ssl_cert'), ssl_mode='verify-full')
                if conn:
                    user_info['_cached_client_db_params'] = {**external_params, 'sslmode': 'verify-full', 'db_ssl_cert_content': db_config.get('db_ssl_cert')}
                    logging.info(f"Успешное подключение к БД клиента по внешнему адресу: {external_params['dbname']}")
                    yield conn
                    return
            except Exception as e:
                logging.warning(f"Не удалось подключиться к БД клиента по внешнему адресу с SSL: {e}")

        # --- ПОПЫТКА 2: ВНУТРЕННИЙ АДРЕС БЕЗ SSL ---
        local_params = {
            'host': db_config.get('local_server_address'), 'port': db_config.get('local_server_port'), 'dbname': db_config.get('db_name'),
            'user': db_config.get('db_user'), 'password': db_config.get('db_password')
        }
        if all(local_params.values()):
            logging.debug(f"Попытка подключения к БД клиента по внутреннему адресу: {local_params['host']}:{local_params['port']} без SSL.")
            try:
                conn = _attempt_db_connection(local_params, None, ssl_mode='disable')
                if conn:
                    user_info['_cached_client_db_params'] = {**local_params, 'sslmode': 'disable', 'db_ssl_cert_content': None}
                    logging.info(f"Успешное подключение к БД клиента по внутреннему адресу: {local_params['dbname']}")
                    yield conn
                    return
            except Exception as e:
                logging.warning(f"Не удалось подключиться к БД клиента по внутреннему адресу без SSL: {e}")

        # Если ни один из способов не сработал
        logging.error("Не удалось установить соединение с БД клиента ни по одному из доступных адресов.")
        raise ConnectionError("Не удалось установить соединение с базой данных клиента.")

    finally:
        if conn:
            conn.close()

def _attempt_db_connection(base_params: Dict[str, Any], ssl_cert_content: Optional[str], ssl_mode: str = 'disable') -> Optional[psycopg2.extensions.connection]:
    """
    Вспомогательный метод для попытки подключения к БД с заданными параметрами.
    Управляет созданием и удалением временного файла SSL-сертификата.
    """
    temp_cert_file = None
    conn = None
    try:
        conn_params = base_params.copy()
        # --- ИСПРАВЛЕНИЕ: Удаляем из параметров подключения служебное поле, которое не понимает psycopg2 ---
        # Это поле используется только для передачи содержимого сертификата в этот метод.
        conn_params.pop('db_ssl_cert_content', None)

        conn_params['sslmode'] = ssl_mode
        conn_params['connect_timeout'] = 5 # Добавим таймаут для быстрых проверок

        if ssl_cert_content and ssl_mode == 'verify-full':
            logging.debug("Создание временного файла сертификата SSL для подключения.")
            with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                fp.write(ssl_cert_content.strip())
                temp_cert_file = fp.name
            conn_params['sslrootcert'] = temp_cert_file

        required_keys = ['host', 'port', 'dbname', 'user', 'password']
        if not all(conn_params.get(k) for k in required_keys):
            logging.warning(f"Неполные параметры для попытки подключения: {conn_params}. Пропускаю.")
            return None

        conn = psycopg2.connect(**conn_params)
        return conn
    except psycopg2.OperationalError as e:
        logging.warning(f"Ошибка подключения к БД: {e}")
        if conn: conn.close() # Закрываем соединение, если оно было создано, но произошла ошибка
        raise # Перебрасываем ошибку, чтобы вызывающий код мог ее обработать
    finally:
        if temp_cert_file and os.path.exists(temp_cert_file):
            try:
                os.remove(temp_cert_file)
                logging.debug(f"Временный файл сертификата {temp_cert_file} удален.")
            except OSError as e:
                logging.warning(f"Не удалось удалить временный файл сертификата {temp_cert_file}: {e}")