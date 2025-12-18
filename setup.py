import sys
import os
import glob
from cx_Freeze import setup, Executable, hooks
import psycopg2

# --- ЗАПОМНЕННЫЕ ПАРАМЕТРЫ ---
APP_NAME = "TildaKod"
VERSION = "1.0.3"
DESCRIPTION = "Приложение для печати этикеток и сканирования"
AUTHOR = "Tabrin Sergey"
UPGRADE_CODE = "{9503ec40-7cf9-4196-874e-3cafa334bc61}"

# --- ОПРЕДЕЛЕНИЕ ПУТЕЙ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Добавляем папку desktop-app в путь поиска модулей
sys.path.append(os.path.join(BASE_DIR, "desktop-app"))

# Пути к файлам
SCRIPT_FILE = os.path.join(BASE_DIR, "desktop-app", "run.py")
ICON_FILE = os.path.join(BASE_DIR, "ts.ico")

# --- 1. ФОРМИРУЕМ СПИСОК ФАЙЛОВ (include_files) ---
include_files = []

# А) Основные файлы
include_files.append((os.path.join(BASE_DIR, "desktop-app", ".env"), ".env"))
include_files.append((os.path.join(BASE_DIR, "secrets"), "secrets"))
include_files.append((ICON_FILE, "ts.ico"))

# Шрифты
arialbd_path = os.path.join(BASE_DIR, "desktop-app", "src", "arialbd.ttf")
if os.path.exists(arialbd_path):
    include_files.append((arialbd_path, "arialbd.ttf"))

# Б) ИСПРАВЛЕНИЕ ДЛЯ PSYCOPG2 (Поиск скрытых DLL)
# Нам нужно найти libpq.dll, libssl*.dll, libcrypto*.dll
# Они могут быть в папке пакета или в соседней папке psycopg2_binary.libs
psyco_dir = os.path.dirname(psycopg2.__file__)
site_packages_dir = os.path.dirname(psyco_dir) # Папка site-packages
binary_libs_dir = os.path.join(site_packages_dir, "psycopg2_binary.libs")

found_dlls = []

# 1. Ищем внутри самой папки psycopg2
for root, dirs, files in os.walk(psyco_dir):
    for file in files:
        if file.endswith(".dll"):
            found_dlls.append(os.path.join(root, file))

# 2. Ищем в папке binary libs (если есть)
if os.path.exists(binary_libs_dir):
    print(f"INFO: Найдена папка библиотек psycopg2: {binary_libs_dir}")
    for root, dirs, files in os.walk(binary_libs_dir):
        for file in files:
            if file.endswith(".dll"):
                found_dlls.append(os.path.join(root, file))

if not found_dlls:
    print("WARNING: Не удалось найти DLL для psycopg2! Приложение может не запуститься.")
else:
    print(f"INFO: Найдено {len(found_dlls)} DLL для базы данных.")
    for dll in found_dlls:
        # Кладем их прямо рядом с exe, чтобы Windows их точно нашла
        include_files.append((dll, os.path.basename(dll)))


# В) DLL сканера (libdmtx)
try:
    import pylibdmtx
    pylibdmtx_dir = os.path.dirname(pylibdmtx.__file__)
    libdmtx_dll = os.path.join(pylibdmtx_dir, "libdmtx-64.dll")
    if not os.path.exists(libdmtx_dll):
        libdmtx_dll = os.path.join(BASE_DIR, ".venv", "Lib", "site-packages", "pylibdmtx", "libdmtx-64.dll")
except ImportError:
    libdmtx_dll = os.path.join(BASE_DIR, ".venv", "Lib", "site-packages", "pylibdmtx", "libdmtx-64.dll")

include_files.append((libdmtx_dll, "libdmtx-64.dll"))

# Г) Системная msvcr120.dll
msvcr_path = os.path.join(BASE_DIR, "desktop-app", "msvcr120.dll")
if os.path.exists(msvcr_path):
    include_files.append((msvcr_path, "msvcr120.dll"))

# --- ПАКЕТЫ ---
packages = [
    "os", "sys", "PySide6", "pandas", "psycopg2", "PIL", "qrcode",
    "jinja2", "babel", "requests", "bcrypt", "dotenv", 
    "barcode", "pylibdmtx",
    "src"
]

# --- НАСТРОЙКИ СБОРКИ ---
build_exe_options = {
    "packages": packages,
    "include_files": include_files,
    "includes": ["PIL", "qrcode", "pylibdmtx", "barcode"],
    "excludes": ["unittest", "tkinter"],
    "include_msvcr": True,
}

# --- НАСТРОЙКИ MSI ---
bdist_msi_options = {
    "upgrade_code": UPGRADE_CODE,
    "add_to_path": True,
    "initial_target_dir": rf"[ProgramFilesFolder]\{APP_NAME}",
    "install_icon": ICON_FILE,
}

base = "Win32GUI" if sys.platform == "win32" else None

setup(
    name=APP_NAME,
    version=VERSION,
    description=DESCRIPTION,
    author=AUTHOR,
    options={
        "build_exe": build_exe_options,
        "bdist_msi": bdist_msi_options
    },
    executables=[
        Executable(
            script=SCRIPT_FILE,
            base=base,
            target_name=f"{APP_NAME}.exe",
            icon=ICON_FILE,
            shortcut_name=APP_NAME,
            shortcut_dir="ProgramMenuFolder"
        )
    ]
)