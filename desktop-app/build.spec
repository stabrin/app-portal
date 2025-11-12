# build.spec

# Этот файл является конфигурацией для PyInstaller.
# Он указывает, как правильно собрать ваше приложение в исполняемый файл.

import os
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

# --- Шаг 2: Анализ зависимостей ---
# PyInstaller анализирует ваш код, начиная с auth.py, и находит все импорты.
a = Analysis(
    ['run.py'],
    pathex=[],
    # Явно указываем, что нужно включить DLL. Она будет лежать в корневой папке приложения.
    binaries=[(libdmtx_dll_path, '.')],
    # Указываем, какие файлы данных нужно скопировать.
    # ('путь/откуда', 'путь/куда_в_сборке').
    # Копируем всю папку secrets в корень сборки.
    datas=[
        ('../secrets', 'secrets')
    ],
    # Иногда PyInstaller "пропускает" некоторые импорты.
    # Здесь мы явно указываем их, чтобы избежать ошибок во время выполнения.
    hiddenimports=[
        'pylibdmtx.pylibdmtx',
        'babel.numbers',
        'pytz', # Для работы с часовыми поясами
        'dateutil', # Для работы с датами
        'psycopg2.extras' # Явно включаем extras для psycopg2
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
)

# --- Шаг 3: Создание архива приложения ---
pyz = PYZ(a.pure)

# --- Шаг 4: Создание исполняемого файла ---
exe = EXE(
    pyz,
    a.scripts,
    [],
    name='TildaKod', # Имя вашего .exe файла
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,  # False - для GUI-приложений, чтобы не открывалась черная консоль. Установите в True для отладки.
    icon='src/assets/icon.ico'  # Путь к иконке приложения
)

# --- Шаг 5: Сборка итоговой папки ---
# coll - это итоговая папка со всеми файлами.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TildaKod', # Название итоговой папки
)
