// Форматирование значений для отображения.
// Даты — везде в виде дд.мм.гггг (бэкенд хранит ISO гггг-мм-дд).

/** ISO-дата (гггг-мм-дд[...]) → дд.мм.гггг. Иначе возвращает как есть. */
export function formatDate(v: unknown): string {
  if (v == null) return "";
  const s = String(v).trim();
  if (!s) return "";
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : s;
}

/** ISO-таймштамп → дд.мм.гггг ЧЧ:ММ. */
export function formatDateTime(v: unknown): string {
  if (v == null) return "";
  const s = String(v).trim();
  if (!s) return "";
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  if (m) return `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}`;
  return formatDate(s);
}

// Колонки с датами в таблице результатов.
export const DATE_COLUMNS = new Set(["Дата рождения"]);

// Колонки с телефонами.
export const PHONE_COLUMNS = new Set(["Мобильный", "Рабочий", "Домашний"]);

// Переименование заголовков колонок для отображения (бэкенд отдаёт «ИНН»,
// но в Казахстане корректный термин — ИИН).
export const COLUMN_LABELS: Record<string, string> = { "ИНН": "ИИН" };
export const colLabel = (c: string) => COLUMN_LABELS[c] ?? c;

function titleCase(s: string): string {
  return s
    .toLowerCase()
    .replace(/(^|[\s-])(\p{L})/gu, (_, p: string, c: string) => p + c.toUpperCase());
}

// Метки адреса, которые не показываем (страна) или показываем с префиксом.
const ADDR_DROP = ["РЕСПУБЛИКА"];
const HOUSE_KEYS = ["ДОМ", "ЗДАНИЕ", "СТРОЕНИЕ"];
const APT_KEYS = ["КВАРТИРА", "ОФИС", "ПОМЕЩЕНИЕ"];

/**
 * Сырой адрес с метками вида «ГОРОД РЕСП.ЗНАЧ.: Астана, ПРОСПЕКТ: АБЫЛАЙ ХАНА,
 * ДОМ: 25, КВАРТИРА: 19» → читаемое «Астана, Абылай Хана, д. 25, кв. 19».
 */
export function formatAddress(v: unknown): string {
  if (v == null) return "";
  const s = String(v).trim();
  if (!s || !s.includes(":")) return s;
  const parts: string[] = [];
  for (const chunk of s.split(",")) {
    const i = chunk.indexOf(":");
    if (i === -1) {
      const t = chunk.trim();
      if (t) parts.push(titleCase(t));
      continue;
    }
    const label = chunk.slice(0, i).toUpperCase();
    const value = chunk.slice(i + 1).trim();
    if (!value || ADDR_DROP.some((k) => label.includes(k))) continue;
    if (HOUSE_KEYS.some((k) => label.includes(k))) parts.push(`д. ${value}`);
    else if (APT_KEYS.some((k) => label.includes(k))) parts.push(`кв. ${value}`);
    else parts.push(titleCase(value));
  }
  return parts.length ? parts.join(", ") : s;
}

/** Номер квартиры из сырого адреса («…, КВАРТИРА: 19») или "" если не задан. */
export function apartmentOf(v: unknown): string {
  if (v == null) return "";
  const s = String(v);
  if (!s.includes(":")) return "";
  for (const chunk of s.split(",")) {
    const i = chunk.indexOf(":");
    if (i === -1) continue;
    const label = chunk.slice(0, i).toUpperCase();
    if (APT_KEYS.some((k) => label.includes(k))) return chunk.slice(i + 1).trim();
  }
  return "";
}

/** Ключ сортировки по квартире: число при числовой части, иначе — в конец. */
export function apartmentSortKey(v: unknown): number {
  const m = apartmentOf(v).match(/\d+/);
  return m ? Number(m[0]) : Number.POSITIVE_INFINITY;
}

/**
 * Все непустые поля жителя одним текстом «Ключ: значение» с переносами строк —
 * для копирования и вставки в мессенджер. Даты, адрес и возраст форматируются
 * как в выдаче.
 */
export function residentToText(
  row: Record<string, unknown>,
  fields: string[],
): string {
  const lines: string[] = [];
  for (const c of fields) {
    const raw = row[c];
    if (raw == null || String(raw).trim() === "") continue;
    let val: string;
    if (c === "Дата рождения") {
      const age = ageFromDob(raw);
      val = formatDate(raw) + (age != null ? ` (${age})` : "");
    } else if (c === "Адрес") {
      val = formatAddress(raw);
    } else {
      val = String(raw).trim();
    }
    lines.push(`${colLabel(c)}: ${val}`);
  }
  return lines.join("\n");
}

/** Полных лет на сегодня по ISO-дате рождения (гггг-мм-дд), или null. */
export function ageFromDob(v: unknown): number | null {
  if (v == null) return null;
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  const y = +m[1], mo = +m[2], d = +m[3];
  const now = new Date();
  let age = now.getFullYear() - y;
  const beforeBirthday =
    now.getMonth() + 1 < mo || (now.getMonth() + 1 === mo && now.getDate() < d);
  if (beforeBirthday) age -= 1;
  return age >= 0 && age < 130 ? age : null;
}

/** Телефонная строка может содержать несколько номеров через запятую. */
export function splitPhones(v: unknown): string[] {
  if (v == null) return [];
  return String(v)
    .split(/[,;]+/)
    .map((p) => p.trim())
    .filter(Boolean)
    // дубликаты бэкенд иногда дублирует один номер дважды
    .filter((p, i, a) => a.indexOf(p) === i);
}

export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

// Колонки с чувствительными ПДн, которые маскируются по умолчанию.
export const SENSITIVE_COLUMNS = new Set(["ИНН", "Мобильный", "Рабочий", "Домашний"]);

/**
 * Маскирует чувствительное значение, оставляя последние `tail` символов.
 * «771234567890» → «••••••7890». Несколько телефонов маскируются каждый.
 */
export function maskValue(value: unknown, tail = 4): string {
  if (value == null) return "";
  const s = String(value).trim();
  if (!s) return "";
  return s
    .split(/([,;]+)/)
    .map((part) => {
      if (/^[,;]+$/.test(part)) return part;
      const t = part.trim();
      if (t.length <= tail) return t ? "•".repeat(t.length) : t;
      const visible = t.slice(-tail);
      const hidden = "•".repeat(Math.min(8, t.length - tail));
      return part.replace(t, hidden + visible);
    })
    .join("");
}

/**
 * Ссылка на Яндекс.Карты с поиском дома по адресу.
 * Из читаемого адреса выкидываем квартиру (Яндексу нужен только дом)
 * и префикс «д. », оставляя «город, улица, номер».
 */
export function yandexMapsHref(v: unknown): string {
  const formatted = formatAddress(v);
  if (!formatted) return "";
  const query = formatted
    .split(",")
    .map((p) => p.trim())
    .filter((p) => p && !/^кв\.\s/i.test(p))
    .map((p) => p.replace(/^д\.\s*/i, ""))
    .join(", ");
  if (!query) return "";
  return `https://yandex.ru/maps/?text=${encodeURIComponent(query)}`;
}
