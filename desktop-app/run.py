import sys
import os
import logging

# --- [FIX START] УНИВЕРСАЛЬНЫЙ FIX ПУТЕЙ (EXE + IDE) ---
# Этот блок гарантирует, что Windows найдет DLL (libdmtx и msvcr120)
# как в скомпилированном виде, так и при запуске через VS Code.

if getattr(sys, 'frozen', False):
    # --- РЕЖИМ EXE ---
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller: DLL лежат во временной папке _internal
        base_dir = sys._MEIPASS 
    else:
        # Nuitka: DLL лежат рядом с файлом .exe
        base_dir = os.path.dirname(sys.executable)
else:
    # --- РЕЖИМ IDE (VS Code) ---
    # DLL (msvcr120.dll) лежит в той же папке, что и run.py (desktop-app)
    base_dir = os.path.dirname(os.path.abspath(__file__))

# Добавляем папку в путь поиска DLL (Обязательно для libdmtx)
try:
    # Для Python 3.8+ это основной способ указать папку с DLL
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(base_dir)
    
    # Дополнительно добавляем в PATH для старых библиотек и надежности
    os.environ['PATH'] = base_dir + os.pathsep + os.environ['PATH']
    
    # print(f"DEBUG: DLL search path added: {base_dir}") # Раскомментируйте для отладки
except Exception as e:
    print(f"WARNING: Failed to set DLL path: {e}")
# --- [FIX END] ---


# --- ИСПРАВЛЕНИЕ: Добавляем явные импорты для сборщиков ---
import babel.numbers
import jinja2

# Добавляем папку desktop-app в путь системных модулей
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- НОВЫЙ БЛОК: Централизованная настройка логирования ---
from dotenv import load_dotenv

# Определяем корень проекта для логов и .env
if getattr(sys, 'frozen', False):
    # Если exe - берем папку, где лежит exe файл
    if hasattr(sys, '_MEIPASS'):
         # PyInstaller OneDir
         project_root = os.path.dirname(sys.executable)
    else:
         # Nuitka Standalone
         project_root = os.path.dirname(sys.executable)
else:
    # Если код - берем текущую папку
    project_root = os.path.dirname(os.path.abspath(__file__))

dotenv_path = os.path.join(project_root, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)

# Получаем уровень логирования
log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)

log_file_path = os.path.join(project_root, 'app.log')
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - [%(name)s.%(funcName)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Выбор режима UI: 'tk' (по умолчанию) или 'qt'
ui_mode = os.getenv('DESKTOP_UI', 'tk').lower()

def _get_main_callable():
    """Возвращает функцию main() для выбранного UI без немедленного импорта тяжёлых модулей."""
    if ui_mode == 'qt':
        logging.info('DESKTOP_UI=qt — запуск PySide6 варианта интерфейса')
        from src.auth_qt import main as qt_main
        return qt_main
    else:
        logging.info('DESKTOP_UI=tk (по умолчанию) — запуск Tkinter варианта интерфейса')
        from src.auth import main as tk_main
        return tk_main


if __name__ == "__main__":
    main_callable = None
    try:
        main_callable = _get_main_callable()
        main_callable()
    except Exception as e:
        logging.critical(f"CRITICAL ERROR AT STARTUP: {e}", exc_info=True)
        # Если консоль закрывается мгновенно в EXE, этот input поможет увидеть ошибку
        if getattr(sys, 'frozen', False):
            # Проверяем, есть ли консоль, прежде чем просить ввод
            try:
                if sys.stdout and sys.stdout.isatty():
                    input("Press Enter to exit...")
            except Exception:
                pass