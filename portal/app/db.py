import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    """Устанавливает соединение с базой данных PostgreSQL."""
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD')
    )
    return conn