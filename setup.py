import sys
import os
import glob
from cx_Freeze import setup, Executable
import psycopg2

# --- ПАРАМЕТРЫ ---
APP_NAME = "TildaKod"
VERSION = "1.0.3"
DESCRIPTION = "Приложение для печати этикеток и сканирования"
AUTHOR = "Tabrin Sergey"
UPGRADE_CODE = "{9503ec40-7cf9-4196-874e-3cafa334bc61}"

# --- ПУТИ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, "desktop-app"))

SCRIPT_FILE = os.path.join(BASE_DIR, "desktop-app", "run.py")
ICON_FILE = os.path.join(BASE_DIR, "ts.ico")

# --- 1. ФАЙЛЫ ---
include_files = []
include_files.append((os.path.join(BASE_DIR, "desktop-app", ".env"), ".env"))
include_files.append((os.path.join(BASE_DIR, "secrets"), "secrets"))
include_files.append((ICON_FILE, "ts.ico"))

# Шрифты
arialbd_path = os.path.join(BASE_DIR, "desktop-app", "src", "arialbd.ttf")
if os.path.exists(arialbd_path):
    include_files.append((arialbd_path, "arialbd.ttf"))

# Psycopg2 DLL
psyco_dir = os.path.dirname(psycopg2.__file__)
site_packages_dir = os.path.dirname(psyco_dir)
binary_libs_dir = os.path.join(site_packages_dir, "psycopg2_binary.libs")

found_dlls = []
for root, dirs, files in os.walk(psyco_dir):
    for file in files:
        if file.endswith(".dll"): found_dlls.append(os.path.join(root, file))
if os.path.exists(binary_libs_dir):
    for root, dirs, files in os.walk(binary_libs_dir):
        for file in files:
            if file.endswith(".dll"): found_dlls.append(os.path.join(root, file))

for dll in found_dlls:
    include_files.append((dll, os.path.basename(dll)))

# Pylibdmtx DLL
try:
    import pylibdmtx
    pylibdmtx_dir = os.path.dirname(pylibdmtx.__file__)
    libdmtx_dll = os.path.join(pylibdmtx_dir, "libdmtx-64.dll")
    if not os.path.exists(libdmtx_dll):
         libdmtx_dll = os.path.join(BASE_DIR, ".venv", "Lib", "site-packages", "pylibdmtx", "libdmtx-64.dll")
except:
    libdmtx_dll = os.path.join(BASE_DIR, ".venv", "Lib", "site-packages", "pylibdmtx", "libdmtx-64.dll")
include_files.append((libdmtx_dll, "libdmtx-64.dll"))

# MSVCR120
msvcr_path = os.path.join(BASE_DIR, "desktop-app", "msvcr120.dll")
if os.path.exists(msvcr_path):
    include_files.append((msvcr_path, "msvcr120.dll"))

# --- ПАКЕТЫ ---
packages = [
    "os", "sys", "PySide6", "pandas", "psycopg2", 
    "PIL", "qrcode", # Важно!
    "jinja2", "babel", "requests", "bcrypt", "dotenv", 
    "barcode", "pylibdmtx", "src"
]

# --- INCLUDES (Самое важное для решения проблемы) ---
# Мы принудительно включаем эти модули, даже если cx_Freeze их не видит.
# При этом исключаем части Pillow, завязанные на Tkinter (ImageTk).
includes = [
    "PIL.Image", 
    "PIL.ImageDraw", 
    "PIL.ImageFont", 
    "PIL.ImageWin", # Для печати на Windows
    "qrcode",
    "pylibdmtx.pylibdmtx", 
    "pylibdmtx.wrapper",
    "barcode", 
    "barcode.writer"
]

# --- EXCLUDES ---
# Исключаем tkinter и PIL.ImageTk (он зависит от tkinter)
excludes = ["tkinter", "unittest", "PIL.ImageTk"]

# --- НАСТРОЙКИ СБОРКИ ---
build_exe_options = {
    "packages": packages,
    "includes": includes,
    "include_files": include_files,
    "excludes": excludes,
    "include_msvcr": True,
    "build_exe": os.path.join(BASE_DIR, "build", "TildaKod_v" + VERSION),
}

# --- MSI ---
bdist_msi_options = {
    "upgrade_code": UPGRADE_CODE,
    "add_to_path": True,
    "initial_target_dir": rf"[WindowsVolume]\{APP_NAME}",
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
