# build.spec

# Этот файл является конфигурацией для PyInstaller.
# Он указывает, как правильно собрать ваше приложение в исполняемый файл.

import os
import sys
from PyInstaller.utils.hooks import get_hook_dirs

# --- Шаг 1: Находим DLL для pylibdmtx ---
# Это критически важный шаг, так как без DLL библиотека не будет работать.
# PyInstaller не всегда находит её автоматически.

# --- ИСПРАВЛЕНИЕ: Используем абсолютный путь от .spec файла, а не от текущей директории ---
spec_dir = os.path.dirname(os.path.abspath(__file__))
venv_path = os.path.join(spec_dir, '.venv')
libdmtx_dll_path = None

# Ищем DLL в папке site-packages вашего виртуального окружения
site_packages_path = os.path.join(venv_path, 'Lib', 'site-packages')
if os.path.isdir(site_packages_path):
    for root, dirs, files in os.walk(site_packages_path):
        # Имя DLL может немного отличаться, но обычно содержит 'libdmtx'
        if 'libdmtx-64.dll' in files:
            libdmtx_dll_path = os.path.join(root, 'libdmtx-64.dll')
            break

if not libdmtx_dll_path:
    raise FileNotFoundError(
        "Не удалось найти libdmtx-64.dll в .venv/Lib/site-packages. "
        "Убедитесь, что pylibdmtx установлена корректно (`pip install pylibdmtx`)."
    )

# --- Шаг 2: Анализ зависимостей ---
# PyInstaller анализирует ваш код, начиная с auth.py, и находит все импорты.
a = Analysis(
    [os.path.join(spec_dir, 'src', 'auth.py')],
    pathex=[],
    # Явно указываем, что нужно включить DLL. Она будет лежать в корневой папке приложения.
    binaries=[(libdmtx_dll_path, '.')],
    # Указываем, какие файлы данных нужно скопировать.
    # ('путь/откуда', 'путь/куда_в_сборке'). Теперь путь абсолютный.
    datas=[
        (os.path.join(spec_dir, '..', 'secrets', 'postgres', 'server.crt'), 'secrets/postgres')
    ],
    # Иногда PyInstaller "пропускает" некоторые импорты.
    # Здесь мы явно указываем их, чтобы избежать ошибок во время выполнения.
    hiddenimports=[
        'pylibdmtx.pylibdmtx',
        'babel.numbers' # Часто требуется для других библиотек
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# --- Шаг 3: Создание архива приложения ---
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# --- Шаг 4: Создание исполняемого файла ---
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TildaKodApp', # Имя вашего .exe файла
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # False - для GUI-приложений, чтобы не открывалась черная консоль
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --- Шаг 5: Сборка итоговой папки ---
# coll - это итоговая папка со всеми файлами.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TildaKodApp', # Название итоговой папки
)
