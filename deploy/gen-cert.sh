#!/usr/bin/env bash
# Генерация self-signed сертификата для доступа по IP (без домена).
# Использование:  ./deploy/gen-cert.sh 203.0.113.10
#
# ВАЖНО (совместимость с мобильными браузерами iOS/Android): серверный серт
# обязан быть «листовым» (CA:FALSE), иметь EKU=serverAuth, SAN с IP и срок
# ≤ 398 дней — иначе мобильные браузеры ЖЁСТКО отклоняют соединение
# («не удаётся получить доступ к сайту»), хотя десктоп позволяет «всё равно
# перейти». Поэтому ниже заданы все эти расширения и срок 397 дней.
set -euo pipefail

IP="${1:-}"
if [[ -z "$IP" ]]; then
  echo "Укажите IP сервера: ./deploy/gen-cert.sh <IP>"; exit 1
fi

CERT_DIR="$(dirname "$0")/nginx/certs"
mkdir -p "$CERT_DIR"

openssl req -x509 -nodes -newkey rsa:2048 -days 397 \
  -keyout "$CERT_DIR/server.key" \
  -out "$CERT_DIR/server.crt" \
  -subj "/CN=$IP" \
  -addext "subjectAltName=IP:$IP" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"

chmod 600 "$CERT_DIR/server.key"
echo "Сертификат создан в $CERT_DIR (CN/SAN = $IP, leaf+serverAuth, срок 397 дней)."
echo "Срок ~13 мес. — поставьте напоминание о перевыпуске (mobile-браузеры требуют ≤398 дн.)."
