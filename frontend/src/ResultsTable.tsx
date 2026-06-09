import type { ReactNode } from "react";
import type { Resident } from "./api";
import { highlight } from "./highlight";
import {
  DATE_COLUMNS, PHONE_COLUMNS, SENSITIVE_COLUMNS, colLabel, formatDate,
  formatAddress, splitPhones, telHref, ageFromDob, yandexMapsHref,
  residentToText, maskValue,
} from "./format";
import { useToast } from "./toast";
import { useMask } from "./mask";
import { Icon } from "./Icon";

/** Копирует все поля жителя в буфер обмена (для вставки в мессенджер). */
async function copyResident(
  row: Resident,
  fields: string[],
  push: (msg: string, kind?: "ok" | "err") => void,
) {
  try {
    await navigator.clipboard.writeText(residentToText(row, fields));
    push("Данные скопированы");
  } catch {
    push("Не удалось скопировать", "err");
  }
}

const SORTABLE = new Set([
  "Фамилия", "Имя", "Отчество", "Дата рождения", "ИНН",
  "Гражданство", "Национальность",
]);

interface MaskApi {
  masking: boolean;
  isRevealed: (rowid: number | string | null | undefined, col: string) => boolean;
  reveal: (rowid: number | string | null | undefined, col: string) => void;
}

interface Props {
  rows: Resident[];
  fields: string[];
  tokens: string[];
  sortBy: string;
  sortDir: string;
  onSort: (col: string) => void;
  onNeighbors: (rowid: number) => void;
  onDetails: (row: Resident) => void;
  onConnections?: (rowid: number) => void;
  onBookmark?: (row: Resident) => void;
  pending?: Pending;
}

/** Какая кнопка «Связи»/«Соседи» сейчас грузит данные (для спиннера и блокировки). */
type Pending = { kind: "neigh" | "conn"; rowid: number } | null;

/** Маленький встроенный спиннер для кнопок (анимация spin из App.css). */
function Spin() {
  return <span className="btn-spin" aria-hidden="true" />;
}

const NAME_FIELDS = ["Фамилия", "Имя", "Отчество"];

/** Русское склонение слова «год» по числу: 1 год, 2 года, 5 лет. */
function pluralYears(n: number): string {
  const m100 = n % 100;
  if (m100 >= 11 && m100 <= 14) return "лет";
  switch (n % 10) {
    case 1: return "год";
    case 2:
    case 3:
    case 4: return "года";
    default: return "лет";
  }
}

/** Замаскированное значение с кнопкой «показать» (раскрытие пишется в аудит). */
function MaskedCell({
  value, onReveal,
}: { value: Resident[string]; onReveal: () => void }) {
  return (
    <span className="masked">
      <span className="masked-val">{maskValue(value)}</span>
      <button type="button" className="reveal-btn" title="Показать полностью"
        onClick={onReveal}>показать</button>
    </span>
  );
}

/**
 * Карточка одного жителя: ФИО заголовком + все непустые поля «ключ-значение».
 * Используется в мобильной выдаче и в модалке «Подробнее».
 */
export function ResidentCard({
  row, fields, tokens, onNeighbors, onConnections, onBookmark, pending,
}: {
  row: Resident;
  fields: string[];
  tokens: string[];
  onNeighbors: (rowid: number) => void;
  onConnections?: (rowid: number) => void;
  onBookmark?: (row: Resident) => void;
  pending?: Pending;
}) {
  const push = useToast();
  const mask = useMask();
  const rowid = Number(row._rowid);
  const busy = !!pending;
  const connLoading = pending?.kind === "conn" && pending.rowid === rowid;
  const neighLoading = pending?.kind === "neigh" && pending.rowid === rowid;
  return (
    <div className="card">
      <div className="card-name">
        {highlight(
          NAME_FIELDS.map((c) => row[c]).filter(Boolean).join(" "),
          tokens,
        )}
      </div>
      {fields
        .filter(
          (c) =>
            !NAME_FIELDS.includes(c) &&
            row[c] != null &&
            String(row[c]).trim() !== "",
        )
        .map((c) => (
          <div className="card-row" key={c}>
            <span className="k">{colLabel(c)}</span>
            <span className="v">{renderCell(c, row[c], tokens, row._rowid, mask)}</span>
          </div>
        ))}
      <div className="card-actions">
        <button className="link-btn" onClick={() => copyResident(row, fields, push)}>
          <Icon name="clipboard" size={15} /> Копировать
        </button>
        {onBookmark && (
          <button className="link-btn" onClick={() => onBookmark(row)}>
            <Icon name="star" size={15} /> В закладки
          </button>
        )}
        {onConnections && (
          <button
            className="link-btn"
            disabled={busy}
            onClick={() => onConnections(rowid)}
          >
            {connLoading ? <Spin /> : <Icon name="link" size={15} />} Связи
          </button>
        )}
        <button
          className="link-btn card-neighbors"
          disabled={busy}
          onClick={() => onNeighbors(rowid)}
        >
          {neighLoading && <Spin />} Жители по этому адресу
        </button>
      </div>
    </div>
  );
}

function renderPhones(value: Resident[string], tokens: string[]): ReactNode {
  const phones = splitPhones(value);
  if (!phones.length) return "";
  return phones.map((p, i) => (
    <span key={i}>
      {i > 0 && ", "}
      <a href={telHref(p)}>{highlight(p, tokens)}</a>
    </span>
  ));
}

/** Значение ячейки с учётом типа колонки (дата / телефон / адрес / текст). */
function renderCell(
  col: string,
  value: Resident[string],
  tokens: string[],
  rowid: Resident[string] | undefined,
  mask: MaskApi,
): ReactNode {
  if (value == null || String(value).trim() === "") return "";
  // Маскирование чувствительных полей (ИИН, телефоны) до раскрытия.
  if (SENSITIVE_COLUMNS.has(col) && mask.masking && !mask.isRevealed(rowid, col)) {
    return <MaskedCell value={value} onReveal={() => mask.reveal(rowid, col)} />;
  }
  if (col === "Дата рождения") {
    const age = ageFromDob(value);
    return (
      <>
        {highlight(formatDate(value), tokens)}
        {age != null && <span className="age"> · {age} {pluralYears(age)}</span>}
      </>
    );
  }
  if (DATE_COLUMNS.has(col)) return highlight(formatDate(value), tokens);
  if (PHONE_COLUMNS.has(col)) return renderPhones(value, tokens);
  if (col === "Адрес") {
    const text = highlight(formatAddress(value), tokens);
    const href = yandexMapsHref(value);
    return href ? (
      <a
        className="addr-link"
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        title="Открыть дом в Яндекс.Картах"
      >
        {text}
      </a>
    ) : text;
  }
  return highlight(String(value), tokens);
}

export function ResultsTable({
  rows, fields, tokens, sortBy, sortDir, onSort, onNeighbors, onDetails,
  onConnections, onBookmark, pending,
}: Props) {
  const push = useToast();
  const mask = useMask();
  const busy = !!pending;
  const sortGlyph = (c: string) => {
    if (!SORTABLE.has(c)) return null;
    const active = sortBy === c;
    return (
      <span className={`sort-ind${active ? " active" : ""}`} aria-hidden="true">
        <Icon
          name={active ? (sortDir === "desc" ? "chevron-down" : "chevron-up") : "chevrons"}
          size={14}
        />
      </span>
    );
  };
  const ariaSort = (c: string): "ascending" | "descending" | "none" | undefined => {
    if (!SORTABLE.has(c)) return undefined;
    if (sortBy !== c) return "none";
    return sortDir === "desc" ? "descending" : "ascending";
  };

  return (
    <>
      {/* Десктоп — таблица */}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {fields.map((c) => {
                const sortable = SORTABLE.has(c);
                return (
                  <th
                    key={c}
                    className={sortable ? "sortable" : ""}
                    aria-sort={ariaSort(c)}
                    tabIndex={sortable ? 0 : undefined}
                    role={sortable ? "button" : undefined}
                    onClick={() => sortable && onSort(c)}
                    onKeyDown={(e) => {
                      if (sortable && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault();
                        onSort(c);
                      }
                    }}
                  >
                    {colLabel(c)}
                    {sortGlyph(c)}
                  </th>
                );
              })}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {fields.map((c) => (
                  <td key={c} className={c === "Адрес" ? "addr" : ""}>
                    {renderCell(c, row[c], tokens, row._rowid, mask)}
                  </td>
                ))}
                <td className="row-actions">
                  <button
                    className="link-btn"
                    title="Все данные о человеке"
                    onClick={() => onDetails(row)}
                  >
                    Подробнее
                  </button>
                  {onConnections && (
                    <button
                      className="link-btn"
                      title="Родственники и связи по телефону"
                      disabled={busy}
                      onClick={() => onConnections(Number(row._rowid))}
                    >
                      {pending?.kind === "conn" && pending.rowid === Number(row._rowid) && <Spin />} Связи
                    </button>
                  )}
                  {onBookmark && (
                    <button
                      className="link-btn"
                      title="Сохранить в закладки"
                      onClick={() => onBookmark(row)}
                    >
                      <Icon name="star" size={15} />
                    </button>
                  )}
                  <button
                    className="link-btn"
                    title="Скопировать все данные"
                    onClick={() => copyResident(row, fields, push)}
                  >
                    Копировать
                  </button>
                  <button
                    className="link-btn"
                    title="Жители по этому адресу"
                    disabled={busy}
                    onClick={() => onNeighbors(Number(row._rowid))}
                  >
                    {pending?.kind === "neigh" && pending.rowid === Number(row._rowid) && <Spin />} Соседи
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Мобайл — карточки */}
      <div className="cards">
        {rows.map((row, i) => (
          <ResidentCard
            key={i}
            row={row}
            fields={fields}
            tokens={tokens}
            onNeighbors={onNeighbors}
            onConnections={onConnections}
            onBookmark={onBookmark}
            pending={pending}
          />
        ))}
      </div>
    </>
  );
}
