"""
Разбор адресной строки на компоненты: область, город/нас.пункт, район,
улица, дом. Формат в базе — метки с двоеточием, разделённые запятыми:

  РЕСПУБЛИКА: Казахстан, ГОРОД РЕСП.ЗНАЧ.: Астана,
  РАЙОН ВНУТРИ ГОРОДА: АЛМАТЫ, ПРОСПЕКТ: АБЫЛАЙ ХАНА, ДОМ: 25

Парсер устойчив к разным меткам: смотрим, какое ключевое слово содержит
метка, и кладём значение в нужную часть.
"""
from __future__ import annotations

# Порядок важен: более специфичные ключи проверяем раньше.
# (ключевое слово в метке -> поле результата)
_LABEL_RULES = [
    ("МИКРОРАЙОН", "street"),
    ("ПРОСПЕКТ", "street"),
    ("ПЕРЕУЛОК", "street"),
    ("БУЛЬВАР", "street"),
    ("ШОССЕ", "street"),
    ("ПРОЕЗД", "street"),
    ("УЛИЦА", "street"),
    ("РАЙОН", "district"),
    ("ГОРОД", "city"),
    ("СЕЛО", "city"),
    ("СЕЛЬСКИЙ ОКРУГ", "city"),
    ("ПОСЕЛОК", "city"),
    ("НАСЕЛЕННЫЙ ПУНКТ", "city"),
    ("АУЛ", "city"),
    ("ОБЛАСТЬ", "region"),
    ("ЗДАНИЕ", "house"),
    ("СТРОЕНИЕ", "house"),
    ("ДОМ", "house"),
]

FIELDS = ("region", "city", "district", "street", "house")


def _classify(label: str) -> str | None:
    up = label.upper()
    for key, field in _LABEL_RULES:
        if key in up:
            return field
    return None


def parse_address(text: str | None) -> dict:
    """Возвращает dict с ключами region/city/district/street/house ('' если нет)."""
    out = {f: "" for f in FIELDS}
    if not text:
        return out
    for part in str(text).split(","):
        if ":" not in part:
            continue
        label, _, value = part.partition(":")
        value = value.strip()
        if not value:
            continue
        field = _classify(label)
        # Не перезаписываем уже заполненное поле (берём первое вхождение),
        # кроме street: первая улично-подобная метка приоритетнее.
        if field and not out[field]:
            out[field] = value
    return out
