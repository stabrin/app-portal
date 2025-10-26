# build.spec

# Этот файл является конфигурацией для PyInstaller.
# Он указывает, как правильно собрать ваше приложение в исполняемый файл.

import os
import sys
import pkg_resources

# --- Шаг 1: Находим DLL для pylibdmtx ---
# Это критически важный шаг, так как без DLL библиотека не будет работать.
# Используем pkg_resources для надежного поиска DLL внутри установленного пакета.
# Этот метод работает независимо от версии пакета или структуры папок.
try:
    libdmtx_dll_path = pkg_resources.resource_filename('pylibdmtx', 'libdmtx-64.dll')
except (pkg_resources.DistributionNotFound, KeyError):
    raise FileNotFoundError(
        "Не удалось найти libdmtx-64.dll. "
        "Убедитесь, что pylibdmtx установлена корректно (`pip install pylibdmtx`)."
    )

# Используем переменную SPECPATH, предоставляемую PyInstaller, для надежного определения пути.
spec_dir = os.path.dirname(SPECPATH)

# --- Шаг 2: Анализ зависимостей ---
# PyInstaller анализирует ваш код, начиная с auth.py, и находит все импорты.
a = Analysis(
    ['src/auth.py'], # PyInstaller автоматически разрешает путь относительно spec-файла
    pathex=[],
    # Явно указываем, что нужно включить DLL. Она будет лежать в корневой папке приложения.
    binaries=[(libdmtx_dll_path, '.')],
    # Указываем, какие файлы данных нужно скопировать.
    # ('путь/откуда', 'путь/куда_в_сборке'). Пути также относительны spec-файла.
    datas=[
        ('../secrets/postgres/server.crt', 'secrets/postgres')
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
