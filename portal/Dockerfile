FROM python:3.9-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем только файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем только код приложения (папку 'app') в контейнер
COPY app/ ./app/

# Команда для запуска Gunicorn, которая вызывает фабрику create_app()
# Gunicorn найдет в /app папку 'app', в ней модуль __init__.py (он же 'app')
# и вызовет в нем функцию create_app.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:create_app()"]