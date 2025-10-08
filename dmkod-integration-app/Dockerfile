FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Команда для запуска приложения внутри контейнера
CMD ["gunicorn", "--bind", "0.0.0.0:8002", "--timeout", "120", "run:app"]