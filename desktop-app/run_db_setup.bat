@echo off
REM Этот скрипт для запуска инициализации базы данных.

title TildaKod DB Setup

echo Активация виртуального окружения и запуск скрипта настройки БД...

call .\.venv\Scripts\activate.bat && python .\scripts\setup_database.py