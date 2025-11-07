#!/bin/bash

# Директория для хранения сгенерированных секретов (сертификатов).
# Она будет создана в корне проекта, отдельно от исходного кода.
# Это более безопасный и стандартный подход.
CERT_DIR="./secrets/postgres"

# --- СКРИПТ ---
if [ "$#" -eq 0 ]; then
    echo "Ошибка: Не указаны домены или IP-адреса для сертификата."
    echo "Использование: $0 <имя_хоста_1> <ip_адрес_1> [имя_хоста_2] [ip_адрес_2] ..."
    echo "Пример: $0 beeb09fc2128.sn.mynetname.net 88.86.80.143 192.168.108.95"
    exit 1
fi

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
    echo "Удаляем старые файлы сертификата и ключа (если они существуют)..."
    rm -f "$PG_CERT" "$PG_KEY"

    # --- Динамическая генерация SAN ---
    SERVER_CN=$1 # Первый аргумент используется как Common Name
    SAN_LIST=""
    DNS_COUNT=1
    IP_COUNT=1

    # Добавляем CN как первый DNS в SAN, чтобы он всегда присутствовал.
    SAN_LIST+="DNS.$DNS_COUNT = $SERVER_CN\n"
    DNS_COUNT=$((DNS_COUNT + 1))

    for arg in "$@"; do
        # Пропускаем первый аргумент, так как он уже добавлен как CN и первый DNS.
        if [ "$arg" = "$SERVER_CN" ]; then
            continue
        fi
        # Простая проверка, является ли аргумент IP-адресом
        if [[ $arg =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            SAN_LIST+="IP.$IP_COUNT = $arg\n"
            IP_COUNT=$((IP_COUNT + 1))
        else
            SAN_LIST+="DNS.$DNS_COUNT = $arg\n"
            DNS_COUNT=$((DNS_COUNT + 1))
        fi
    done
    echo "Генерируем самоподписанный сертификат и приватный ключ на 10 лет..."
    echo -e "SAN список:\n$SAN_LIST"

    openssl req -new -x509 -days 3650 -nodes \
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
subjectAltName = @alt_names

[alt_names]
$(echo -e "$SAN_LIST")
EOF
)
    echo "Сертификат и ключ успешно сгенерированы."
fi

echo ""
echo "--- Проверка сгенерированного сертификата ---"
openssl x509 -in "$PG_CERT" -text -noout | grep -A 3 "Subject Alternative Name"
echo "-------------------------------------------"
echo ""

echo "Готово! Сертификаты сгенерированы."
echo ""
echo "ВАЖНО: PostgreSQL требует особых прав для файла приватного ключа."
echo "Выполните следующие команды вручную, чтобы установить правильного владельца и права доступа:"
echo "sudo chown 70:70 \"$PG_KEY\""
echo "sudo chmod 600 \"$PG_KEY\""
