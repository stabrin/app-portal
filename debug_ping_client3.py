# debug_ping_client3.py
# Запускается из корня репозитория в venv: & ./.venv/Scripts/python.exe debug_ping_client3.py
import sys, os, tempfile, traceback
sys.path.insert(0, os.path.abspath('desktop-app'))

from src.db_connector import get_main_db_connection
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


def try_ping(cfg):
    # choose host/port preferring external db_host/db_port, fallback to local_server_address/port
    host = cfg.get('db_host') or cfg.get('local_server_address')
    port = cfg.get('db_port') or cfg.get('local_server_port') or 5432
    dbname = cfg.get('db_name')
    user = cfg.get('db_user')
    password = cfg.get('db_password')
    ssl_pem = cfg.get('db_ssl_cert')

    conn_kwargs = {
        'host': host,
        'port': port,
        'dbname': dbname,
        'user': user,
        'password': password,
        'connect_timeout': 5,
    }

    cert_path = None
    try:
        if ssl_pem:
            fd, cert_path = tempfile.mkstemp(suffix='.pem')
            with os.fdopen(fd, 'wb') as f:
                if isinstance(ssl_pem, str):
                    f.write(ssl_pem.encode('utf-8'))
                else:
                    f.write(ssl_pem)
            conn_kwargs['sslmode'] = 'require'
            conn_kwargs['sslrootcert'] = cert_path
            print(f'Wrote SSL cert to {cert_path}')

        print('Attempting connection with args:')
        for k in ('host','port','dbname','user','sslmode'):
            if k in conn_kwargs:
                print(f'  {k}:', conn_kwargs.get(k))

        with psycopg2.connect(**conn_kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                res = cur.fetchone()
                print('Ping OK, result=', res)
                return True
    except Exception:
        print('Ping failed:')
        traceback.print_exc()
        return False
    finally:
        if cert_path and os.path.exists(cert_path):
            try:
                os.remove(cert_path)
            except Exception:
                pass


if __name__ == '__main__':
    print('Fetching client', CLIENT_ID)
    try:
        cfg = fetch_client_cfg(CLIENT_ID)
        print('Client config:')
        for k,v in cfg.items():
            if k == 'db_ssl_cert' and v:
                print('  db_ssl_cert: <present> (length', len(v), ')')
            else:
                print(f'  {k}:', v)
        ok = try_ping(cfg)
        if ok:
            print('Ping test succeeded')
            sys.exit(0)
        else:
            print('Ping test failed')
            sys.exit(2)
    except Exception:
        traceback.print_exc()
        sys.exit(3)
