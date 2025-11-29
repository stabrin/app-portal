import os
import sys
import tempfile
import logging
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

        # 1. Попытка с внешним адресом (SSL)
        if not is_local_mode:
            try:
                ext_params = {
                    'host': db_config.get('db_host'), 'port': db_config.get('db_port'), 'dbname': db_config.get('db_name'),
                    'user': db_config.get('db_user'), 'password': db_config.get('db_password'), 'connect_timeout': 3
                }
                if all(ext_params.values()):
                    with _attempt_db_connection(ext_params, db_config.get('db_ssl_cert'), 'verify-full') as conn:
                        if conn:
                            conn_params = {**ext_params, 'sslmode': 'verify-full', 'sslrootcert': _get_cert_path(db_config.get('db_ssl_cert'))}
            except psycopg2.OperationalError: pass

        # 2. Попытка с внутренним адресом
        if not conn_params:
            try:
                host = db_config.get('local_server_address') if not is_local_mode else db_config.get('db_host')
                port = db_config.get('local_server_port') if not is_local_mode else db_config.get('db_port')
                loc_params = {
                    'host': host, 'port': port, 'dbname': db_config.get('db_name'),
                    'user': db_config.get('db_user'), 'password': db_config.get('db_password'), 'connect_timeout': 5
                }
                if all(loc_params.values()):
                    with _attempt_db_connection(loc_params, None, 'disable') as conn:
                        if conn:
                            conn_params = {**loc_params, 'sslmode': 'disable'}
            except psycopg2.OperationalError: pass

        if not conn_params:
            raise ConnectionError(f"Не удалось создать пул для клиента {pool_key}")

        client_db_pools[pool_key] = pool.ThreadedConnectionPool(1, 5, **conn_params)

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
    conn = client_pool.getconn()
    try:
        yield conn 
    except psycopg2.OperationalError:
        raise
    finally:
        client_pool.putconn(conn)

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
def _attempt_db_connection(base_params: Dict[str, Any], ssl_cert_content: Optional[str], ssl_mode: str = 'disable'):
    """Попытка подключения. Закрывает соединение при выходе."""
    temp_cert_file = None
    conn = None
    try:
        conn_params = base_params.copy()
        conn_params.pop('db_ssl_cert_content', None)
        conn_params['sslmode'] = ssl_mode
        conn_params['connect_timeout'] = 5

        if ssl_cert_content and ssl_mode == 'verify-full':
            with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                fp.write(ssl_cert_content.strip())
                temp_cert_file = fp.name
            conn_params['sslrootcert'] = temp_cert_file

        required_keys = ['host', 'port', 'dbname', 'user', 'password']
        if not all(conn_params.get(k) for k in required_keys):
            yield None
            return

        conn = psycopg2.connect(**conn_params)
        yield conn 

    except psycopg2.OperationalError as e:
        logging.warning(f"Connection attempt failed: {e}")
        yield None
    finally:
        if conn:
            conn.close()
        if temp_cert_file and os.path.exists(temp_cert_file):
            try:
                os.remove(temp_cert_file)
            except OSError: pass