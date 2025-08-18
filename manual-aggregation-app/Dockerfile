FROM python:3.9-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    python3-pip \
    libpango-1.0-0 \
    libpango1.0-dev \
    zlib1g-dev \
    libjpeg-dev \
    libffi-dev \
    libcairo2-dev \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запускаем Gunicorn, указывая на фабрику create_app в модуле __init__
CMD ["gunicorn", "--bind", "0.0.0.0:8001", "--log-level", "debug", "--log-file", "-", "app:create_app()"]