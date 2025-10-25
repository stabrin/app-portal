# Этот скрипт для запуска инициализации базы данных в PowerShell.

$Host.UI.RawUI.WindowTitle = "TildaKod DB Setup"

Write-Host "Активация виртуального окружения и запуск скрипта настройки БД..."

& ".\.venv\Scripts\Activate.ps1"; python ".\scripts\setup_database.py"