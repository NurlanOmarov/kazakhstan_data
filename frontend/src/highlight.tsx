import type { ReactNode } from "react";

// Нормализация для сопоставления (повторяет логику бэкенда: регистр,
// казахские буквы, ё/й) — чтобы подсветка совпадала с тем, как ищет сервер.
const MAP: Record<string, string> = {
  Ә: "А", Ғ: "Г", Қ: "К", Ң: "Н", Ө: "О", Ұ: "У", Ү: "У",
  Һ: "Х", І: "И", Ё: "Е", Й: "И",
};
function norm(s: string): string {
  return s
    .toUpperCase()
    .split("")
    .map((c) => MAP[c] ?? c)
    .join("");
}

/**
 * Подсвечивает в тексте вхождения любого из токенов (по префиксу слова или
 * подстроке). Возвращает массив React-узлов.
 */
export function highlight(text: string, tokens: string[]): ReactNode {
  const terms = tokens
    .flatMap((t) => t.split(/\s+/))
    .map((t) => norm(t.trim()))
    .filter((t) => t.length >= 2);
  if (!terms.length || !text) return text;

  const ntext = norm(text);
  // Собираем интервалы совпадений
  const ranges: [number, number][] = [];
  for (const term of terms) {
    let from = 0;
    while (true) {
      const idx = ntext.indexOf(term, from);
      if (idx === -1) break;
      ranges.push([idx, idx + term.length]);
      from = idx + term.length;
    }
  }
  if (!ranges.length) return text;
  ranges.sort((a, b) => a[0] - b[0]);

  // Слияние пересечений
  const merged: [number, number][] = [];
  for (const [s, e] of ranges) {
    const last = merged[merged.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }

  const out: ReactNode[] = [];
  let pos = 0;
  merged.forEach(([s, e], i) => {
    if (s > pos) out.push(text.slice(pos, s));
    out.push(<mark key={i}>{text.slice(s, e)}</mark>);
    pos = e;
  });
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}
