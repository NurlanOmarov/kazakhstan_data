#!/usr/bin/env bash
# Создаёт сжатый (Btrfs + zstd) том для базы данных и монтирует его в ./data.
# Замер на этой базе: zstd сжимает ~4.7x -> 12 ГБ занимают ~2.5-3 ГБ на диске.
# Прозрачно для SQLite, без потери производительности (zstd распаковывает
# быстрее, чем читается диск; горячие страницы кэшируются распакованными).
#
# Требуется Linux с btrfs-progs.  Запуск от root:
#   sudo ./deploy/setup-compressed-volume.sh 16G
set -euo pipefail

SIZE="${1:-16G}"                       # логический размер тома (файл-образ — sparse)
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMG="$PROJECT_DIR/data.btrfs.img"
MNT="$PROJECT_DIR/data"

if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo"; exit 1; fi
command -v mkfs.btrfs >/dev/null || { echo "Установите btrfs-progs"; exit 1; }

if [[ -e "$IMG" ]]; then echo "$IMG уже существует — прерываю."; exit 1; fi

echo "1) Создаю sparse-образ $SIZE: $IMG"
truncate -s "$SIZE" "$IMG"

echo "2) Форматирую в Btrfs"
mkfs.btrfs -q "$IMG"

echo "3) Монтирую с compress-force=zstd:6 в $MNT"
mkdir -p "$MNT"
mount -o loop,compress-force=zstd:6,noatime "$IMG" "$MNT"

echo "4) Готово. Скопируйте базы в $MNT:"
echo "     cp kazakhstan_data.db $MNT/"
echo
echo "Автомонтирование при загрузке — добавьте в /etc/fstab:"
echo "  $IMG  $MNT  btrfs  loop,compress-force=zstd:6,noatime  0 0"
echo
echo "Проверить экономию после копирования базы:"
echo "  compsize $MNT        # (пакет btrfs-compsize)"
