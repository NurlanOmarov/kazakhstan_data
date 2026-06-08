#!/usr/bin/env bash
# Генерация self-signed сертификата для доступа по IP (без домена).
# Использование:  ./deploy/gen-cert.sh 203.0.113.10
set -euo pipefail

IP="${1:-}"
if [[ -z "$IP" ]]; then
  echo "Укажите IP сервера: ./deploy/gen-cert.sh <IP>"; exit 1
fi

CERT_DIR="$(dirname "$0")/nginx/certs"
mkdir -p "$CERT_DIR"

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "$CERT_DIR/server.key" \
  -out "$CERT_DIR/server.crt" \
  -subj "/CN=$IP" \
  -addext "subjectAltName=IP:$IP"

chmod 600 "$CERT_DIR/server.key"
echo "Сертификат создан в $CERT_DIR (CN/SAN = $IP, срок 825 дней)."
