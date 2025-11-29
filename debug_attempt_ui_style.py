# debug_attempt_ui_style.py
# Вызывает _attempt_db_connection так же, как UI: передаёт db_config из БД напрямую
import sys, os, traceback
sys.path.insert(0, os.path.abspath('desktop-app'))
from src.db_connector import get_main_db_connection, _attempt_db_connection

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


if __name__ == '__main__':
    try:
        cfg = fetch_client_cfg(CLIENT_ID)
        print('Calling _attempt_db_connection with raw client cfg keys...')
        print('cfg keys:', list(cfg.keys()))
        with _attempt_db_connection(cfg, cfg.get('db_ssl_cert'), 'verify-full') as conn:
            if conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT 1')
                    print('Result:', cur.fetchone())
            else:
                print('Returned None')
    except Exception:
        traceback.print_exc()
        sys.exit(2)
