# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import glob
from PyInstaller.utils.hooks import collect_dynamic_libs

# --- ОПРЕДЕЛЯЕМ ПУТИ ---
# SPECPATH - это папка, где лежит этот файл (desktop-app)
try:
    spec_folder = SPECPATH
except NameError:
    spec_folder = os.path.dirname(os.path.abspath(__file__))

project_root = os.path.abspath(os.path.join(spec_folder, '..'))

print(f"INFO: Папка спецификации: {spec_folder}")
print(f"INFO: Корень проекта: {project_root}")

# --- 1. DLL: libdmtx ---
import pylibdmtx
pylibdmtx_dir = os.path.dirname(pylibdmtx.__file__)
libdmtx_dll = os.path.join(pylibdmtx_dir, 'libdmtx-64.dll')

# --- 2. DLL: msvcr120.dll (Важно!) ---
# Ищем его в папке desktop-app (вы его туда положили для Nuitka)
local_msvcr = os.path.join(spec_folder, 'msvcr120.dll')

# --- 3. Системные DLL Python ---
python_dir = os.path.dirname(sys.executable)
msvc_dlls = glob.glob(os.path.join(python_dir, 'vcruntime*.dll')) + \
            glob.glob(os.path.join(python_dir, 'msvcp*.dll'))

# ФОРМИРУЕМ СПИСОК БИНАРНИКОВ
# (путь_откуда, путь_куда) -> '.' это корень exe
my_binaries = [(libdmtx_dll, '.')]

for dll in msvc_dlls:
    my_binaries.append((dll, '.'))

if os.path.exists(local_msvcr):
    print(f"INFO: Добавляем msvcr120.dll: {local_msvcr}")
    my_binaries.append((local_msvcr, '.'))
else:
    print("WARNING: msvcr120.dll не найден в папке desktop-app! Приложение может упасть.")


# --- 4. ФАЙЛЫ ДАННЫХ (.env и secrets) ---
my_datas = []

# А .env (лежит в desktop-app, копируем в корень exe)
env_path = os.path.join(spec_folder, '.env')
if os.path.exists(env_path):
    print("INFO: Добавляем .env")
    my_datas.append((env_path, '.'))
else:
    print("WARNING: .env не найден!")

# Б secrets (лежит в корне проекта, копируем в папку secrets)
secrets_path = os.path.join(project_root, 'secrets')
if os.path.exists(secrets_path):
    print("INFO: Добавляем папку secrets")
    my_datas.append((secrets_path, 'secrets'))
else:
    print("WARNING: Папка secrets не найдена!")


# --- СБОРКА ---
a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=my_binaries,
    datas=my_datas,
    hiddenimports=[
        'pylibdmtx.pylibdmtx',
        'babel.numbers',
        'pytz', 
        'dateutil', 
        'psycopg2.extras',
        'jinja2',
        'PIL'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['mx', 'mx.DateTime'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='TildaKod',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    # Включите True, если снова будет падать, чтобы успеть прочитать ошибку
    # Но лучше запускать через терминал.
    console=False, 
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='../ts.ico', 
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TildaKod',
)