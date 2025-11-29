import sys, os
sys.path.insert(0, os.path.abspath('desktop-app'))
from src.db_connector import get_main_db_connection
with get_main_db_connection() as conn:
    with conn.cursor() as cur:
        cur.execute('SELECT id, db_host, db_port, db_name, db_user, local_server_address, local_server_port FROM clients WHERE id = 3')
        row = cur.fetchone()
        if row:
            print(f"Client 3 ID: {row[0]}")
            print(f"  db_host: {row[1]}")
            print(f"  db_port: {row[2]}")
            print(f"  db_name: {row[3]}")
            print(f"  db_user: {row[4]}")
            print(f"  local_server_address: {row[5]}")
            print(f"  local_server_port: {row[6]}")
        else:
            print("Client 3 not found")
