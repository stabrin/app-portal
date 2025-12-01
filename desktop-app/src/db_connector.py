import os
import sys
import tempfile
import logging
import traceback
import psycopg2
from contextlib import contextmanager
from psycopg2 import pool
from typing import Dict, Any, Optional

from dotenv import load_dotenv

# Импортируем нашу универсальную функцию путей
from .utils import project_root_path 

# --- ЗАГРУЗКА .ENV ---
dotenv_path = project_root_path('.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    logging.warning(f"DB_CONNECTOR: .env не найден по пути: {dotenv_path}")


@contextmanager
def get_main_db_connection_DEPRECATED():
    """DEPRECATED."""
    db_params = {
        "dbname": "tilda_db", "user": "portal_user", "password": "!T-W0rkshop", 
        "host": "109.172.115.204", "port": "5432", "connect_timeout": 10, "sslmode": 'verify-full'
    }
    cert_path = project_root_path(os.path.join('secrets', 'postgres', 'server.crt'))
    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Сертификат сервера не найден по пути: {cert_path}")

    db_params['sslrootcert'] = cert_path
    conn = psycopg2.connect(**db_params)
    try:
        yield conn
    finally:
        conn.close()

# --- ПУЛЫ СОЕДИНЕНИЙ ---

main_db_pool = None
client_db_pools: Dict[int, pool.ThreadedConnectionPool] = {}

def initialize_main_db_pool():
    global main_db_pool
    if main_db_pool is None:
        cert_path = project_root_path(os.path.join('secrets', 'postgres', 'server.crt'))
        if not os.path.exists(cert_path):
             logging.error(f"CRITICAL: Сертификат не найден: {cert_path}")

        db_params = {
            "dbname": "tilda_db", "user": "portal_user", "password": "!T-W0rkshop",
            "host": "109.172.115.204", "port": "5432", "connect_timeout": 10,
            "sslmode": 'verify-full', "sslrootcert": cert_path
        }
        main_db_pool = pool.ThreadedConnectionPool(1, 5, **db_params)
        logging.info("Пул соединений для главной БД успешно создан.")

def get_client_pool(pool_key: Any, db_config: Dict[str, Any]) -> pool.ThreadedConnectionPool:
    if pool_key not in client_db_pools:
        logging.info(f"Пул для клиента (ключ: {pool_key}) не найден. Создаю...")
        conn_params = None
        is_local_mode = not isinstance(pool_key, int)
        logging.debug(f"get_client_pool: db_config = {{{', '.join(f'{k}: {v}' for k,v in (db_config or {}).items() if k != 'db_password')}}}")

        # 1. Попытка с внешним адресом (SSL)
        if not is_local_mode:
            try:
                ext_params = {
                    'host': db_config.get('db_host'), 
                    'port': db_config.get('db_port'), 
                    'dbname': db_config.get('db_name'),
                    'user': db_config.get('db_user'), 
                    'password': db_config.get('db_password'), 
                    'connect_timeout': 3
                }
                # Проверяем обязательные поля (без пустых значений)
                required = ['host', 'port', 'dbname', 'user', 'password']
                if all(ext_params.get(k) for k in required):
                    logging.debug(f"get_client_pool: Попытка подключения по внешнему адресу {ext_params['host']}:{ext_params['port']}")
                    with _attempt_db_connection(ext_params, db_config.get('db_ssl_cert'), 'verify-full') as conn:
                        if conn:
                            logging.info(f"get_client_pool: Успешное подключение по внешнему адресу с SSL")
                            cert_path = _get_cert_path(db_config.get('db_ssl_cert'))
                            if cert_path:
                                conn_params = {**ext_params, 'sslmode': 'verify-full', 'sslrootcert': cert_path}
                            else:
                                conn_params = {**ext_params, 'sslmode': 'disable'}
                else:
                    missing = [k for k in required if not ext_params.get(k)]
                    logging.debug(f"get_client_pool: Пропуск внешнего подключения — отсутствуют поля {missing}")
            except Exception as e:
                log_params = {k: v for k, v in ext_params.items() if k != 'password'}
                logging.warning(f"get_client_pool: Попытка подключения по внешнему адресу не удалась. Параметры: {log_params}. Ошибка: {e}")

        # 2. Попытка с внутренним адресом (без SSL)
        if not conn_params:
            try:
                loc_host = db_config.get('local_server_address') or db_config.get('db_host')
                loc_port = db_config.get('local_server_port') or db_config.get('db_port')
                loc_params = {
                    'host': loc_host, 
                    'port': loc_port, 
                    'dbname': db_config.get('db_name'),
                    'user': db_config.get('db_user'), 
                    'password': db_config.get('db_password'), 
                    'connect_timeout': 5
                }
                required = ['host', 'port', 'dbname', 'user', 'password']
                if all(loc_params.get(k) for k in required):
                    logging.debug(f"get_client_pool: Попытка подключения по внутреннему адресу {loc_host}:{loc_port}")
                    with _attempt_db_connection(loc_params, None, 'disable') as conn:
                        if conn:
                            logging.info(f"get_client_pool: Успешное подключение по внутреннему адресу без SSL")
                            conn_params = {**loc_params, 'sslmode': 'disable'}
                else:
                    missing = [k for k in required if not loc_params.get(k)]
                    logging.debug(f"get_client_pool: Пропуск внутреннего подключения — отсутствуют поля {missing}")
            except Exception as e:
                log_params = {k: v for k, v in loc_params.items() if k != 'password'}
                logging.warning(f"get_client_pool: Попытка подключения по внутреннему адресу не удалась. Параметры: {log_params}. Ошибка: {e}")

        if not conn_params:
            logging.error(f"get_client_pool: Ни одна попытка подключения не удалась. pool_key={pool_key}, is_local_mode={is_local_mode}")
            raise ConnectionError(f"Не удалось создать пул для клиента {pool_key}")

        try:
            logging.info(f"get_client_pool: Создаю пул для клиента {pool_key} с параметрами: host={conn_params.get('host')}, port={conn_params.get('port')}, dbname={conn_params.get('dbname')}")
            client_db_pools[pool_key] = pool.ThreadedConnectionPool(1, 5, **conn_params)
            logging.info(f"get_client_pool: Пул успешно создан для клиента {pool_key}")
        except Exception as e:
            logging.error(f"get_client_pool: Ошибка при создании ThreadedConnectionPool: {e}\n{traceback.format_exc()}")
            raise

    return client_db_pools[pool_key]

@contextmanager
def get_main_db_connection():
    if main_db_pool is None:
        initialize_main_db_pool()
    conn = main_db_pool.getconn()
    try:
        yield conn
    finally:
        main_db_pool.putconn(conn)

@contextmanager
def get_client_db_connection(user_info: Dict[str, Any]):
    db_config = user_info.get("client_db_config")
    if not db_config:
        raise ValueError("Нет конфига БД клиента")
    client_id = db_config.get('id')
    pool_key = db_config.get('db_name') if client_id == 0 else client_id

    client_pool = get_client_pool(pool_key, db_config)
    max_retries = 3
    last_exception = None

    for attempt in range(max_retries):
        conn = None
        try:
            conn = client_pool.getconn()
            yield conn
            # Если код внутри 'with' выполнился без ошибок, выходим из цикла
            return
        except psycopg2.OperationalError as e:
            last_exception = e
            logging.warning(f"Потеряно соединение с БД (попытка {attempt + 1}/{max_retries}): {e}")
            if conn:
                # Закрываем "сломанное" соединение, чтобы пул создал новое
                client_pool.putconn(conn, close=True)
            if attempt < max_retries - 1:
                import time
                time.sleep(0.5) # Пауза перед повторной попыткой
        finally:
            if conn and not conn.closed:
                client_pool.putconn(conn)
    # Если все попытки провалились, пробрасываем последнюю ошибку
    raise ConnectionError(f"Не удалось подключиться к БД после {max_retries} попыток.") from last_exception

# --- ВОССТАНОВЛЕННАЯ ФУНКЦИЯ ---
@contextmanager
def get_client_db_direct_connection(user_info: Dict[str, Any]):
    """
    Создает ПРЯМОЕ соединение с БД клиента, минуя пул.
    """
    db_config = user_info.get("client_db_config")
    if not db_config:
        raise ValueError("Конфигурация базы данных клиента не предоставлена.")

    # 1. Пробуем внешний адрес
    try:
        ext_params = {
            'host': db_config.get('db_host'), 'port': db_config.get('db_port'), 'dbname': db_config.get('db_name'),
            'user': db_config.get('db_user'), 'password': db_config.get('db_password'), 'connect_timeout': 5
        }
        if all(ext_params.values()):
            # Используем наш помощник. Если соединение успешно, он вернет conn.
            # Мы передаем его дальше через yield.
            with _attempt_db_connection(ext_params, db_config.get('db_ssl_cert'), 'verify-full') as conn:
                if conn:
                    yield conn
                    return # Успех, выходим
    except Exception:
        pass

    # 2. Пробуем внутренний адрес (если внешний не сработал)
    try:
        loc_params = {
            'host': db_config.get('local_server_address'), 'port': db_config.get('local_server_port'), 'dbname': db_config.get('db_name'),
            'user': db_config.get('db_user'), 'password': db_config.get('db_password'), 'connect_timeout': 5
        }
        if all(loc_params.values()):
            with _attempt_db_connection(loc_params, None, 'disable') as conn:
                if conn:
                    yield conn
                    return # Успех
    except Exception:
        pass
    
    raise ConnectionError("Не удалось установить прямое соединение с БД клиента.")
# -------------------------------

def _get_cert_path(ssl_cert_content: Optional[str]) -> Optional[str]:
    if not ssl_cert_content:
        return None
    fp = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8')
    fp.write(ssl_cert_content.strip())
    fp.close()
    return fp.name

@contextmanager
def _attempt_db_connection(base_params: Dict[str, Any], ssl_cert_content: Optional[str], ssl_mode: str = 'disable', use_local: bool = False):
    """Попытка подключения. Закрывает соединение при выходе."""
    temp_cert_file = None
    conn = None
    try:
        conn_params = base_params.copy()
        conn_params['sslmode'] = ssl_mode
        conn_params['connect_timeout'] = 5

        # Выбираем адрес/порт для подключения, принимая во внимание возможные ключи
        # base_params может содержать ключи 'host'/'port' или 'db_host'/'db_port' или 'local_server_address'/'local_server_port'.
        if use_local:
            host = conn_params.get('local_server_address') or conn_params.get('host') or conn_params.get('db_host')
            port = conn_params.get('local_server_port') or conn_params.get('port') or conn_params.get('db_port')
        else:
            host = conn_params.get('host') or conn_params.get('db_host') or conn_params.get('local_server_address')
            port = conn_params.get('port') or conn_params.get('db_port') or conn_params.get('local_server_port')
        conn_params['host'] = host
        conn_params['port'] = port

        if ssl_cert_content and ssl_mode == 'verify-full':
            with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                fp.write(ssl_cert_content.strip())
                temp_cert_file = fp.name
            conn_params['sslrootcert'] = temp_cert_file
        # Нормализуем возможные альтернативные имена ключей, которые могут
        # приходить из UI (db_name, db_user, db_password)
        conn_params['dbname'] = conn_params.get('dbname') or conn_params.get('db_name')
        conn_params['user'] = conn_params.get('user') or conn_params.get('db_user')
        conn_params['password'] = conn_params.get('password') or conn_params.get('db_password')

        # Debug: покажем, какие параметры сформированы (пароль маскируем)
        try:
            debug_params = {k: ('***' if k == 'password' and conn_params.get(k) else conn_params.get(k)) for k in ('host','port','dbname','user','password')}
            logging.debug(f"_attempt_db_connection: conn_params prepared: {debug_params}")
        except Exception:
            logging.debug("_attempt_db_connection: unable to prepare debug_params")

        required_keys = ['host', 'port', 'dbname', 'user', 'password']
        if not all(conn_params.get(k) for k in required_keys):
            logging.warning(f"Пропуск попытки подключения: не все обязательные параметры указаны. Host: {conn_params.get('host')}")
            yield None
            return

        # Фильтруем только поддерживаемые psycopg2 параметры, чтобы
        # не передавать лишние ключи (например, 'id').
        allowed_keys = ('host','port','dbname','user','password','connect_timeout','sslmode','sslrootcert')
        filtered = {k: conn_params[k] for k in allowed_keys if k in conn_params and conn_params.get(k) is not None}
        conn = psycopg2.connect(**filtered)
        yield conn 

    finally:
        if conn:
            conn.close()
        if temp_cert_file and os.path.exists(temp_cert_file):
            try:
                os.remove(temp_cert_file)
            except OSError: pass