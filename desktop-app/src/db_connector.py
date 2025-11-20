# src/db_connector.py

import os
import sys
import tempfile
import logging
import psycopg2
from contextlib import contextmanager
from psycopg2 import pool
from typing import Dict, Any, Optional

from dotenv import load_dotenv

from .utils import project_root_path # --- ИЗМЕНЕНИЕ: Импортируем новую функцию для доступа к корню проекта ---

# --- ИСПРАВЛЕНИЕ: Загружаем переменные окружения в самом начале ---
# Это гарантирует, что DB_HOST и другие будут доступны.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
dotenv_path = os.path.join(project_root, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)

@contextmanager
def get_main_db_connection_DEPRECATED():
    """
    DEPRECATED.
    Контекстный менеджер, который создает новое SSL-соединение при каждом вызове
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

# --- НОВАЯ ЛОГИКА: ПУЛЫ СОЕДИНЕНИЙ ---

main_db_pool = None
client_db_pools: Dict[int, pool.ThreadedConnectionPool] = {}

def initialize_main_db_pool():
    """Инициализирует пул соединений для главной БД."""
    global main_db_pool
    if main_db_pool is None:
        logging.debug("Проверка необходимости инициализации пула для главной БД...")
        # --- ИЗМЕНЕНИЕ: Возвращаемся к жестко заданным параметрам для главной БД, как и требовалось. ---
        db_params = {
            "dbname": "tilda_db",
            "user": "portal_user",
            "password": "!T-W0rkshop",
            "host": "109.172.115.204",
            "port": "5432",
            "connect_timeout": 10,
            "sslmode": 'verify-full',
            "sslrootcert": project_root_path(os.path.join('secrets', 'postgres', 'server.crt'))
        }
        logging.info("Пул для главной БД не найден. Начинаю инициализацию...")
        logging.debug(f"Параметры для пула главной БД: host={db_params['host']}, port={db_params['port']}, dbname={db_params['dbname']}, user={db_params['user']}")
        # minconn=1 (одно соединение всегда открыто), maxconn=5 (до 5 одновременных)
        main_db_pool = pool.ThreadedConnectionPool(1, 5, **db_params)
        logging.info("Пул соединений для главной БД успешно создан.")

def get_client_pool(pool_key: Any, db_config: Dict[str, Any]) -> pool.ThreadedConnectionPool:
    """
    Возвращает или создает и кэширует пул соединений для конкретной клиентской БД.
    Логика выбора адреса (внешний/внутренний) выполняется один раз при создании пула.
    """
    if pool_key not in client_db_pools:
        logging.info(f"Пул для клиента (ключ: {pool_key}) не найден. Начинаю процедуру создания нового пула...")
        
        conn_params = None
        is_local_mode = not isinstance(pool_key, int)

        # 1. Попытка с внешним адресом (SSL) - пропускаем в локальном режиме
        if not is_local_mode:
            try:
                external_params = {
                    'host': db_config.get('db_host'), 'port': db_config.get('db_port'), 'dbname': db_config.get('db_name'),
                    'user': db_config.get('db_user'), 'password': db_config.get('db_password'),
                    'connect_timeout': 3 # Короткий таймаут, чтобы не "висеть"
                }
                if all(external_params.values()):
                    logging.debug(f"Клиент (ключ: {pool_key}): [Попытка 1/2] Проверка внешнего адреса: {external_params['host']}:{external_params['port']} с SSL.")
                    with _attempt_db_connection(external_params, db_config.get('db_ssl_cert'), 'verify-full') as conn:
                        if conn:
                            conn_params = {**external_params, 'sslmode': 'verify-full', 'sslrootcert': _get_cert_path(db_config.get('db_ssl_cert'))}
                            logging.info(f"Клиент (ключ: {pool_key}): Внешний адрес доступен. Пул будет создан с использованием SSL.")
                        else:
                            logging.warning(f"Клиент (ключ: {pool_key}): Тестовое подключение по внешнему адресу не вернуло объект соединения.")
            except psycopg2.OperationalError as e:
                logging.warning(f"Клиент (ключ: {pool_key}): Не удалось подключиться по внешнему адресу. Ошибка: {e}")

        # 2. Попытка с внутренним/локальным адресом, если внешний не удался или мы в локальном режиме
        if not conn_params:
            try:
                # В локальном режиме local_server_address берется из config.ini и кладется в db_host
                host_to_try = db_config.get('local_server_address') if not is_local_mode else db_config.get('db_host')
                port_to_try = db_config.get('local_server_port') if not is_local_mode else db_config.get('db_port')

                local_params = {
                    'host': host_to_try, 'port': port_to_try, 'dbname': db_config.get('db_name'),
                    'user': db_config.get('db_user'), 'password': db_config.get('db_password'),
                    'connect_timeout': 5
                }
                if all(local_params.values()):
                    logging.debug(f"Клиент (ключ: {pool_key}): [Попытка 2/2] Проверка адреса: {local_params['host']}:{local_params['port']} без SSL.")
                    with _attempt_db_connection(local_params, None, 'disable') as conn:
                        if conn:
                            conn_params = {**local_params, 'sslmode': 'disable'}
                            logging.info(f"Клиент (ключ: {pool_key}): Адрес доступен. Пул будет создан без использования SSL.")
                        else:
                            logging.warning(f"Клиент (ключ: {pool_key}): Тестовое подключение по этому адресу не вернуло объект соединения.")
            except psycopg2.OperationalError as e:
                logging.warning(f"Клиент (ключ: {pool_key}): Не удалось подключиться по этому адресу. Ошибка: {e}")

        if not conn_params:
            logging.error(f"Не удалось создать пул для клиента (ключ: {pool_key}). Ни один из адресов не ответил.")
            raise ConnectionError(f"Не удалось создать пул соединений для клиента (ключ: {pool_key}): ни один из адресов не доступен.")

        client_db_pools[pool_key] = pool.ThreadedConnectionPool(1, 5, **conn_params)
        logging.info(f"Пул соединений для клиента (ключ: {pool_key}) успешно создан. Параметры: host={conn_params.get('host')}, port={conn_params.get('port')}, sslmode={conn_params.get('sslmode')}")

    return client_db_pools[pool_key]

@contextmanager
def get_client_db_connection_DEPRECATED(user_info: Dict[str, Any]) -> Optional[psycopg2.extensions.connection]:
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
            
@contextmanager
def get_main_db_connection():
    """Контекстный менеджер, который берет соединение из пула для главной БД."""
    logging.debug("Запрос соединения из пула главной БД...")
    if main_db_pool is None:
        initialize_main_db_pool()
    
    conn = main_db_pool.getconn()
    logging.debug(f"Соединение {id(conn)} получено из пула главной БД.")
    try:
        yield conn
    finally:
        main_db_pool.putconn(conn)

@contextmanager
def get_client_db_connection(user_info: Dict[str, Any]):
    """Контекстный менеджер, который берет соединение из пула для клиентской БД."""
    db_config = user_info.get("client_db_config")
    if not db_config:
        raise ValueError("Конфигурация базы данных клиента не предоставлена.")

    client_id = db_config.get('id') # Может быть 0 в локальном режиме
    if client_id == 0:
        pool_key = db_config.get('db_name')
    else:
        pool_key = client_id
    if not pool_key: raise ValueError("Не удалось определить ключ для пула соединений (ни ID клиента, ни имя БД).")

    client_pool = get_client_pool(pool_key, db_config) # Получаем или создаем пул
    conn = None
    try:
        conn = client_pool.getconn()
        logging.debug(f"Соединение {id(conn)} получено из пула клиента (ключ: {pool_key}).")
        yield conn # Передаем соединение в блок 'with'
    except psycopg2.OperationalError as e:
        logging.error(f"Не удалось получить соединение из пула клиента (ключ: {pool_key}): {e}", exc_info=True)
        raise # Перебрасываем ошибку, чтобы приложение могло ее обработать
    finally:
        if conn:
            # Этот блок теперь будет выполняться ПОСЛЕ завершения работы блока 'with'
            client_pool.putconn(conn)
            logging.debug(f"Соединение {id(conn)} возвращено в пул клиента (ключ: {pool_key}).")

def _get_cert_path(ssl_cert_content: Optional[str]) -> Optional[str]:
    """Создает временный файл для сертификата и возвращает путь к нему."""
    if not ssl_cert_content:
        return None
    # ВАЖНО: Утечка памяти! Файлы не удаляются. В реальном приложении
    # нужно продумать механизм их очистки при закрытии пула.
    # Для простоты примера пока оставляем так.
    fp = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8')
    fp.write(ssl_cert_content.strip())
    fp.close()
    return fp.name

@contextmanager
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
        logging.debug(f"Успешное подключение к БД: host={conn_params['host']}, port={conn_params['port']}, dbname={conn_params['dbname']}")
        return conn
        logging.debug(f"Успешное тестовое подключение к БД: host={conn_params['host']}, dbname={conn_params['dbname']}")
        yield conn # Используем yield для передачи управления
    except psycopg2.OperationalError as e:
        logging.warning(f"Ошибка подключения к БД: {e}")
        raise # Перебрасываем ошибку, чтобы вызывающий код мог ее обработать
    finally:
        # --- ИЗМЕНЕНИЕ: Контекстный менеджер сам закроет соединение ---
        if conn:
            yield conn
            conn.close() # Закрываем соединение после выхода из блока with
        if temp_cert_file and os.path.exists(temp_cert_file):
            try:
                os.remove(temp_cert_file)
                logging.debug(f"Временный файл сертификата {temp_cert_file} удален.")
            except OSError as e:
                logging.warning(f"Не удалось удалить временный файл сертификата {temp_cert_file}: {e}")