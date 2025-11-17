# Desktop-приложение "ТильдаКод"

Это десктопное приложение для взаимодействия с базой данных портала.

## Создание установочного файла (.msi)

Для распространения приложения можно создать установочный файл `.msi` с помощью `cx_Freeze`.

1.  **Установите `cx_Freeze`**:
    В вашем активированном виртуальном окружении выполните:
    ```bash
    pip install cx_freeze
    ```

2.  **Создайте файл `setup.py`**:
    В корневой папке `desktop-app` (рядом с `.venv`, `src` и `keys`) создайте файл с именем `setup.py` и следующим содержимым:

    ```python
    # setup.py
    import sys
    from cx_Freeze import setup, Executable

    # Зависимости, которые нужно явно включить в сборку
    build_exe_options = {
        "packages": ["os", "tkinter", "psycopg2", "dotenv", "idna"],
        "includes": ["tkinter.ttk"],
        "include_files": [
            "keys/",  # Включаем папку с ключами
            "certs/"  # Включаем папку с сертификатом сервера
        ],
    }

    # Базовые настройки для исполняемого файла
    base = None
    if sys.platform == "win32":
        base = "Win32GUI"  # Скрывает окно консоли при запуске

    setup(
        name="TildaKod",
        version="1.0",
        description="Desktop client for portal",
        options={"build_exe": build_exe_options},
        executables=[Executable("src/main_window.py", base=base, target_name="TildaKodApp.exe")]
    )
    ```

3.  **Запустите сборку**:
    В терминале, находясь в папке `desktop-app`, выполните команду:
    ```bash
    python setup.py bdist_msi
    ```
    После завершения процесса в папке `desktop-app/build/` появится подпапка (например, `exe.win-amd64-3.11`), внутри которой вы найдете готовый `.msi` файл.

---

## Инструкция по развертыванию на новом ПК

Для корректной работы приложения на новом компьютере под управлением Windows необходимо выполнить следующие шаги после установки.

### Требования

*   Операционная система Windows.
*   Установочный файл приложения (`.msi`).

### Шаги установки и настройки

1.  **Установка приложения**
    *   Запустите установочный файл `.msi` и следуйте инструкциям мастера установки.
2.  **Копирование конфигурационного файла**
    *   Найдите папку, куда было установлено приложение (например, `C:\Program Files (x86)\TildaKod\`).
    *   Скопируйте в эту папку файл `.env` из вашего проекта.
    *   **Важно**: Убедитесь, что в файле `.env` переменная `DB_HOST` содержит **публичный IP-адрес** вашего сервера.
    *   В итоге структура в папке установки должна выглядеть так:
        ```
        C:\Program Files (x86)\TildaKod\
        ├── TildaKodApp.exe
        ├── .env
        ```

После выполнения этих двух шагов приложение готово к работе. Оно будет подключаться к базе данных напрямую, используя защищенное SSL-соединение.

---
### Для разработки

Активация виртуального окружения:
```powershell
.\.venv\Scripts\Activate.ps1
```
git log --oneline --graph
для выхода q в английской раскладке
git reset --hard <хэш_коммита>
git push origin <название_вашей_ветки> --force

git remote set-url origin https://stabrin:<ВАШ_PERSONAL_ACCESS_TOKEN>@github.com/stabrin/app-portal.git
git push origin main --force
git remote set-url origin https://github.com/stabrin/app-portal.git

