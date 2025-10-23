#!/bin/bash

# --- НАСТРОЙКИ ---
# Домен или IP-адрес, по которому будет доступен сервер PostgreSQL.
# Это значение будет вписано в поле "Common Name" сертификата.
# Для проверки по доменному имени.
SERVER_CN="st.it-workshop.ru"
# Публичный IP-адрес вашего сервера. ОБЯЗАТЕЛЬНО ЗАМЕНИТЕ НА СВОЙ!
SERVER_IP="109.172.115.204"

# Директория для хранения сгенерированных секретов (сертификатов).
# Она будет создана в корне проекта, отдельно от исходного кода.
# Это более безопасный и стандартный подход.
CERT_DIR="./secrets/postgres"

# --- СКРИПТ ---
set -e # Прерывать выполнение при ошибке

echo "Проверяем наличие openssl..."
if ! command -v openssl &> /dev/null
then
    echo "Ошибка: openssl не найден. Пожалуйста, установите его."
    echo "Для Debian/Ubuntu: sudo apt-get install openssl"
    echo "Для CentOS/RHEL: sudo yum install openssl"
    exit 1
fi

echo "Создаем директорию для сертификатов: $CERT_DIR"
mkdir -p "$CERT_DIR"

# Пути к файлам сертификата и ключа в проекте
PG_CERT="$CERT_DIR/server.crt"
PG_KEY="$CERT_DIR/server.key"

if [ -f "$PG_CERT" ] && [ -f "$PG_KEY" ]; then
    echo "Файлы сертификата и ключа уже существуют. Генерация пропущена."
    echo "Если вы хотите создать их заново, удалите старые файлы и запустите скрипт еще раз."
else
    echo "Генерируем самоподписанный сертификат и приватный ключ на 10 лет с SANs..."

    openssl req -new -x509 -days 3650 -nodes -text \
        -out "$PG_CERT" \
        -keyout "$PG_KEY" \
        -config <(cat <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
C = RU
ST = Moscow
L = Moscow
O = IT-Workshop
OU = Development
CN = $SERVER_CN

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:$SERVER_CN,IP:$SERVER_IP
EOF
)
    echo "Сертификат и ключ успешно сгенерированы."
fi

echo "Готово! Сертификаты сгенерированы."
echo ""
echo "ВАЖНО: PostgreSQL требует особых прав для файла приватного ключа."
echo "Выполните следующие команды вручную, чтобы установить правильного владельца и права доступа:"
echo "sudo chown 70:70 \"$PG_KEY\""
echo "sudo chmod 600 \"$PG_KEY\""
