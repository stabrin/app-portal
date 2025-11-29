# debug_ping_ui_client3.py
# Повторяет логику пинга, как в UI: использует _attempt_db_connection для внешней и локальной попыток.
import sys, os, traceback
sys.path.insert(0, os.path.abspath('desktop-app'))

from src.db_connector import get_main_db_connection, _attempt_db_connection
import psycopg2

CLIENT_ID = 3


def fetch_client_cfg(client_id):
    with get_main_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, db_host, db_port, db_name, db_user, db_password, db_ssl_cert, local_server_address, local_server_port
                FROM clients WHERE id = %s
            ''', (client_id,))
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f'Client {client_id} not found')
            keys = ['id','db_host','db_port','db_name','db_user','db_password','db_ssl_cert','local_server_address','local_server_port']
            return dict(zip(keys,row))


def run_ui_like_ping(cfg):
    print('Starting UI-like ping test...')
    # Step 1: external with SSL
    ext_host = cfg.get('db_host')
    if ext_host:
        print(f"Step 1: Trying external {ext_host}:{cfg.get('db_port')} with SSL (verify-full)")
        try:
            with _attempt_db_connection({'host': cfg.get('db_host'), 'port': cfg.get('db_port'), 'dbname': cfg.get('db_name'), 'user': cfg.get('db_user'), 'password': cfg.get('db_password')}, cfg.get('db_ssl_cert'), 'verify-full') as conn:
                if conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT 1')
                        print('External attempt OK:', cur.fetchone())
                        return True
                else:
                    print('External attempt returned no conn (None)')
        except Exception as e:
            print('External attempt raised exception:')
            traceback.print_exc()
    else:
        print('Step 1 skipped: no external host')

    # Step 2: local without SSL
    local_host = cfg.get('local_server_address')
    if local_host:
        print(f"Step 2: Trying local {local_host}:{cfg.get('local_server_port')} without SSL")
        try:
            with _attempt_db_connection({'host': cfg.get('local_server_address'), 'port': cfg.get('local_server_port'), 'dbname': cfg.get('db_name'), 'user': cfg.get('db_user'), 'password': cfg.get('db_password')}, None, 'disable', use_local=True) as conn:
                if conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT 1')
                        print('Local attempt OK:', cur.fetchone())
                        return True
                else:
                    print('Local attempt returned no conn (None)')
        except Exception as e:
            print('Local attempt raised exception:')
            traceback.print_exc()
    else:
        print('Step 2 skipped: no local host')

    print('All attempts failed')
    return False


if __name__ == '__main__':
    try:
        cfg = fetch_client_cfg(CLIENT_ID)
        print('Client cfg loaded: host=', cfg.get('db_host'), 'local=', cfg.get('local_server_address'))
        ok = run_ui_like_ping(cfg)
        print('Result:', ok)
        sys.exit(0 if ok else 2)
    except Exception:
        traceback.print_exc()
        sys.exit(3)
