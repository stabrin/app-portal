# src/core/db_connector.py

import os
import sys
import socket
import subprocess
import time
import logging
import psycopg2
from contextlib import contextmanager
from dotenv import load_dotenv

class SshTunnelProcess:
    """
    Контекстный менеджер для управления SSH-туннелем через системный процесс ssh.exe.
    """
    def __init__(self, ssh_host, ssh_port, ssh_user, ssh_key, remote_host, remote_port):
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.remote_host = remote_host
        self.remote_port = remote_port
        
        self.local_host = '127.0.0.1'
        self.local_port = self._get_free_port()
        self.process = None

    def _get_free_port(self):
        """Находит свободный TCP-порт для туннеля."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def __enter__(self):
        """Запускает SSH-туннель в фоновом процессе."""
        ssh_executable = 'ssh'
        if sys.platform == "win32":
            ssh_executable = r'C:\Windows\System32\OpenSSH\ssh.exe'

        tunnel_command = [
            ssh_executable, '-N',
            '-L', f'{self.local_host}:{self.local_port}:{self.remote_host}:{self.remote_port}',
            '-p', str(self.ssh_port), '-i', self.ssh_key,
            f'{self.ssh_user}@{self.ssh_host}',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ExitOnForwardFailure=yes'
        ]
        
        logging.info(f"Запуск SSH-туннеля командой: {' '.join(tunnel_command)}")
        
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        self.process = subprocess.Popen(
            tunnel_command, startupinfo=startupinfo,
            stderr=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding='utf-8'
        )
        time.sleep(2)
        
        if self.process.poll() is not None:
            error_output = self.process.stderr.read()
            logging.error(f"Процесс ssh.exe завершился с ошибкой: {error_output.strip()}")
            raise ConnectionError(f"Не удалось запустить SSH-туннель. Ошибка: {error_output.strip()}")
            
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Завершает процесс SSH-туннеля."""
        if self.process:
            logging.info("Закрытие SSH-туннеля...")
            self.process.terminate()
            self.process.wait()
            logging.info("SSH-туннель закрыт.")

@contextmanager
def get_main_db_connection():
    """
    Контекстный менеджер, который создает SSH-туннель и возвращает
    готовое соединение с ГЛАВНОЙ базой данных (tilda_db).
    """
    # Загружаем переменные из .env файла
    # ИСПРАВЛЕНИЕ: Путь к корню приложения - это один уровень вверх от папки 'src'
    desktop_app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    dotenv_path = os.path.join(desktop_app_root, '.env')
    load_dotenv(dotenv_path=dotenv_path)

    ssh_key_path = os.path.join(desktop_app_root, 'keys', os.getenv("SSH_KEY_FILENAME"))
    if not os.path.exists(ssh_key_path):
        raise FileNotFoundError(f"SSH ключ не найден по пути: {ssh_key_path}")

    # Создаем туннель
    tunnel = SshTunnelProcess(
        ssh_host=os.getenv("SSH_HOST"),
        ssh_port=int(os.getenv("SSH_PORT", 22)),
        ssh_user=os.getenv("SSH_USER"),
        ssh_key=ssh_key_path,
        remote_host=os.getenv("DB_HOST"),
        remote_port=int(os.getenv("DB_PORT"))
    )
    
    with tunnel:
        # Подключаемся к БД через локальный порт туннеля
        conn = psycopg2.connect(
            dbname=os.getenv("TILDA_DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=tunnel.local_host,
            port=tunnel.local_port
        )
        yield conn
        conn.close()