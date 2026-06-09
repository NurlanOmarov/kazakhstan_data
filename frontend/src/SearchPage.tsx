import { useCallback, useEffect, useRef, useState } from "react";
import {
  searchResidents, fetchNeighbors, fetchConnections, addBookmark, myHistory,
  exportUrl, ApiError,
} from "./api";
import type {
  Resident, SearchResponse, SearchParams, Connections, HistoryItem,
} from "./api";
import { ResultsTable, ResidentCard } from "./ResultsTable";
import { Field } from "./Autocomplete";
import { AgeRange } from "./AgeRange";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { useToast } from "./toast";
import {
  formatDate, formatDateTime, apartmentOf, apartmentSortKey, ageFromDob,
} from "./format";

const EMPTY: SearchParams = {
  fio: "", surname: "", name: "", patronymic: "", dob: "", inn: "",
  phone: "", address: "", city: "", district: "", street: "", house: "",
  gender: "", age_min: "", age_max: "",
};

const PAGE_SIZE = 50;
const SORT_LS = "kz_sort";

/** Текстовое описание поискового запроса (для истории и заголовков). */
function describe(p: SearchParams): string {
  const parts: string[] = [];
  const labels: [keyof SearchParams, string][] = [
    ["fio", ""], ["surname", ""], ["name", ""], ["patronymic", ""],
    ["inn", "ИИН"], ["phone", "тел"], ["dob", "д.р."],
    ["city", ""], ["district", ""], ["street", ""], ["house", "д."],
  ];
  for (const [k, pref] of labels) {
    const v = p[k];
    if (v && String(v).trim()) parts.push(pref ? `${pref} ${v}` : String(v));
  }
  if (p.gender) parts.push(p.gender === "Мужской" ? "муж." : "жен.");
  if (p.age_min || p.age_max) parts.push(`${p.age_min || 0}–${p.age_max || "∞"} лет`);
  return parts.join(", ") || "—";
}

/**
 * «Умное» распознавание единой строки ФИО: если введены 12 цифр — это ИИН,
 * длинная цифровая строка — телефон, дата — дата рождения. Иначе оставляем как ФИО.
 */
function routeSmart(p: SearchParams): SearchParams {
  const fio = (p.fio || "").trim();
  if (!fio) return p;
  const compact = fio.replace(/\s/g, "");
  if (/^\d{12}$/.test(compact)) return { ...p, fio: "", inn: compact };
  if (/^\d{1,2}[./-]\d{1,2}[./-]\d{4}$/.test(fio)) return { ...p, fio: "", dob: fio };
  const digits = fio.replace(/\D/g, "");
  if (digits.length >= 10 && /^[\d+()\-\s]+$/.test(fio))
    return { ...p, fio: "", phone: fio };
  return p;
}

/** Сортировка списка соседей: по квартире (числом), по ФИО или по дате рождения. */
function sortNeighbors(rows: Resident[], by: "apt" | "name" | "dob"): Resident[] {
  const fio = (r: Resident) =>
    ["Фамилия", "Имя", "Отчество"].map((c) => r[c] ?? "").join(" ");
  const copy = [...rows];
  if (by === "apt") {
    copy.sort(
      (a, b) =>
        apartmentSortKey(a["Адрес"]) - apartmentSortKey(b["Адрес"]) ||
        fio(a).localeCompare(fio(b), "ru"),
    );
  } else if (by === "name") {
    copy.sort((a, b) => fio(a).localeCompare(fio(b), "ru"));
  } else {
    copy.sort((a, b) =>
      String(a["Дата рождения"] ?? "").localeCompare(String(b["Дата рождения"] ?? "")),
    );
  }
  return copy;
}

/** Группировка жителей дома по фамилии — «семьи в доме». */
function groupByFamily(rows: Resident[]): [string, Resident[]][] {
  const map = new Map<string, Resident[]>();
  for (const r of rows) {
    const key = String(r["Фамилия"] ?? "—").trim().toUpperCase() || "—";
    (map.get(key) ?? map.set(key, []).get(key)!).push(r);
  }
  return [...map.entries()].sort((a, b) =>
    b[1].length - a[1].length || a[0].localeCompare(b[0], "ru"));
}

/** Группировка по квартире — чтобы видеть, кто живёт вместе. */
function groupByApartment(rows: Resident[]): [string, Resident[]][] {
  const map = new Map<string, Resident[]>();
  for (const r of rows) {
    const key = apartmentOf(r["Адрес"]) || "—";
    (map.get(key) ?? map.set(key, []).get(key)!).push(r);
  }
  // Квартиры по возрастанию номера; «без квартиры» — в конец.
  return [...map.entries()].sort((a, b) => {
    const na = a[0] === "—" ? Infinity : Number(a[0].match(/\d+/)?.[0] ?? Infinity);
    const nb = b[0] === "—" ? Infinity : Number(b[0].match(/\d+/)?.[0] ?? Infinity);
    return na - nb || a[0].localeCompare(b[0], "ru");
  });
}

export function SearchPage() {
  const toast = useToast();
  const [mode, setMode] = useState<"people" | "address">("people");
  const [params, setParams] = useState<SearchParams>(EMPTY);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [tokens, setTokens] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState(() => localStorage.getItem(SORT_LS)?.split(":")[0] ?? "");
  const [sortDir, setSortDir] = useState(() => localStorage.getItem(SORT_LS)?.split(":")[1] ?? "asc");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [showHistory, setShowHistory] = useState(
    () => localStorage.getItem("kz_show_history") === "1",
  );
  const [neighbors, setNeighbors] = useState<
    { rows: Resident[]; total: number; fields: string[] } | null
  >(null);
  const [neighSort, setNeighSort] = useState<"apt" | "name" | "dob" | "family">("apt");
  const [detail, setDetail] = useState<{ row: Resident; fields: string[] } | null>(null);
  const [conn, setConn] = useState<Connections | null>(null);
  // Какая кнопка «Связи»/«Соседи» сейчас грузится — для спиннера и защиты от двойных кликов.
  const [pending, setPending] = useState<{ kind: "neigh" | "conn"; rowid: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => { myHistory().then(setHistory).catch(() => {}); }, []);

  const set = (k: keyof SearchParams, v: string) =>
    setParams((p) => ({ ...p, [k]: v }));

  const run = useCallback(
    async (
      base: SearchParams,
      overrides: { _page?: number; _sortBy?: string; _sortDir?: string } = {},
    ) => {
      const effPage = overrides._page ?? 1;
      const effSortBy = overrides._sortBy ?? sortBy;
      const effSortDir = overrides._sortDir ?? sortDir;
      const has = Object.values(base).some((v) => v && String(v).trim());
      if (!has) return;
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setLoading(true);
      setError(null);
      try {
        const res = await searchResidents(
          { ...base, page: effPage, page_size: PAGE_SIZE, sort_by: effSortBy, sort_dir: effSortDir },
          ctrl.signal,
        );
        setData(res);
        setParams(base);
        setPage(effPage);
        setTokens(
          [base.fio, base.surname, base.name, base.patronymic,
           base.address, base.city, base.district, base.street]
            .filter((v): v is string => !!v && v.trim().length > 0),
        );
        myHistory().then(setHistory).catch(() => {});
      } catch (err) {
        if ((err as Error).name !== "AbortError")
          setError(err instanceof ApiError ? err.message : "Ошибка поиска");
      } finally {
        setLoading(false);
      }
    },
    [sortBy, sortDir],
  );

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    setSortBy("");
    localStorage.removeItem(SORT_LS);
    run(mode === "people" ? routeSmart(params) : params, { _page: 1, _sortBy: "" });
  };

  const reset = () => {
    abortRef.current?.abort();
    setParams(EMPTY);
    setData(null);
    setTokens([]);
    setError(null);
    setSortBy("");
    setSortDir("asc");
    setPage(1);
    setNeighbors(null);
    setDetail(null);
    setConn(null);
    setPending(null);
  };

  const switchMode = (m: "people" | "address") => {
    if (m === mode) return;
    setMode(m);
    reset();
  };

  const onSort = (col: string) => {
    const dir = sortBy === col && sortDir === "asc" ? "desc" : "asc";
    setSortBy(col);
    setSortDir(dir);
    localStorage.setItem(SORT_LS, `${col}:${dir}`);
    run(params, { _page: 1, _sortBy: col, _sortDir: dir });
  };

  const gotoPage = (p: number) => run(params, { _page: p });

  const openNeighbors = async (rowid: number) => {
    if (pending) return; // уже идёт запрос — игнорируем повторные клики
    setConn(null);
    setPending({ kind: "neigh", rowid });
    try {
      const res = await fetchNeighbors(rowid);
      const hasApt = res.results.some((r) => apartmentOf(r["Адрес"]));
      setNeighSort(hasApt ? "apt" : "name");
      setNeighbors({ rows: res.results, total: res.total, fields: res.fields });
    } catch {
      setNeighSort("name");
      setNeighbors({ rows: [], total: 0, fields: [] });
    } finally {
      setPending(null);
    }
  };

  const openConnections = async (rowid: number) => {
    if (pending) return; // уже идёт запрос — игнорируем повторные клики
    setPending({ kind: "conn", rowid });
    try {
      setConn(await fetchConnections(rowid));
    } catch {
      toast("Не удалось загрузить связи", "err");
    } finally {
      setPending(null);
    }
  };

  const bookmark = async (row: Resident) => {
    const label =
      ["Фамилия", "Имя", "Отчество"].map((c) => row[c]).filter(Boolean).join(" ") ||
      "Без имени";
    try {
      await addBookmark(Number(row._rowid), label, row);
      toast(`«${label}» добавлен в закладки`);
    } catch (e) {
      toast((e as Error).message || "Не удалось сохранить", "err");
    }
  };

  const runFromHistory = (h: HistoryItem) => {
    const p = { ...EMPTY, ...h.params };
    setMode(p.city || p.street || p.house || p.district ? mode : "people");
    run(p, { _page: 1 });
  };

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  return (
    <>
      <div className="mode-switch" role="tablist">
        <button role="tab" aria-selected={mode === "people"}
          className={mode === "people" ? "active" : ""}
          onClick={() => switchMode("people")}><Icon name="user" size={16} /> По людям</button>
        <button role="tab" aria-selected={mode === "address"}
          className={mode === "address" ? "active" : ""}
          onClick={() => switchMode("address")}><Icon name="home" size={16} /> По адресу</button>
      </div>

      <form className="search-card" onSubmit={submit}>
        {mode === "people" ? (
          <div className="grid">
            <Field label="ФИО / ИИН / телефон одной строкой" value={params.fio!}
              onChange={(v) => set("fio", v)} full
              hint="Введите ФИО, ИИН (12 цифр), телефон или дату — распознаем автоматически. Или заполните поля ниже." />
            <Field label="Фамилия" field="surname" value={params.surname!} onChange={(v) => set("surname", v)} />
            <Field label="Имя" field="name" value={params.name!} onChange={(v) => set("name", v)} />
            <Field label="Отчество" value={params.patronymic!} onChange={(v) => set("patronymic", v)} />
            <Field label="Дата рождения" value={params.dob!} onChange={(v) => set("dob", v)} placeholder="дд.мм.гггг" />
            <Field label="ИИН" value={params.inn!} onChange={(v) => set("inn", v)} inputMode="numeric" />
            <Field label="Телефон" value={params.phone!} onChange={(v) => set("phone", v)} inputMode="numeric" />
            <Field label="Город" field="city" value={params.city!} onChange={(v) => set("city", v)} />
            <Field label="Район" field="district" value={params.district!} onChange={(v) => set("district", v)} />
            <Field label="Улица" field="street" value={params.street!} onChange={(v) => set("street", v)} />
            <Field label="Дом" value={params.house!} onChange={(v) => set("house", v)} />
            <label className="field">
              <span>Пол</span>
              <select className="select" value={params.gender!}
                onChange={(e) => set("gender", e.target.value)}>
                <option value="">любой</option>
                <option value="Мужской">Мужской</option>
                <option value="Женский">Женский</option>
              </select>
            </label>
            <AgeRange
              min={params.age_min!} max={params.age_max!}
              onChange={(lo, hi) => setParams((p) => ({ ...p, age_min: lo, age_max: hi }))}
            />
          </div>
        ) : (
          <div className="grid">
            <Field label="Город" field="city" value={params.city!} onChange={(v) => set("city", v)} />
            <Field label="Район" field="district" value={params.district!} onChange={(v) => set("district", v)} />
            <Field label="Улица" field="street" value={params.street!} onChange={(v) => set("street", v)} full />
            <Field label="Дом" value={params.house!} onChange={(v) => set("house", v)} />
            <p className="field full muted addr-hint">
              Укажите город, улицу и дом — покажем всех зарегистрированных жителей этого дома.
            </p>
          </div>
        )}
        <div className="actions">
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? "Поиск…" : "Найти"}
          </button>
          <button type="button" className="btn-ghost" onClick={reset}>Сбросить</button>
          {data && data.total > 0 && (
            <a className="btn btn-ghost" href={exportUrl(params)} title="Скачать результат в Excel">
              <Icon name="download" size={16} /> Excel
            </a>
          )}
        </div>
      </form>

      {!data && history.length > 0 && (
        <div className="history-card">
          <button
            type="button"
            className="history-head"
            aria-expanded={showHistory}
            onClick={() => {
              const next = !showHistory;
              setShowHistory(next);
              localStorage.setItem("kz_show_history", next ? "1" : "0");
            }}
          >
            <span className="chevron" aria-hidden="true">{showHistory ? "▾" : "▸"}</span>
            Недавние запросы
            <span className="muted"> · {history.length}</span>
          </button>
          {showHistory && (
          <ul className="history-list">
            {history.slice(0, 8).map((h, i) => (
              <li key={i}>
                <button className="history-item" onClick={() => runFromHistory(h)}>
                  <span className="hq">{describe(h.params)}</span>
                  <span className="muted">
                    {h.result_count != null ? `${h.result_count} найд.` : ""}
                    {" · "}{formatDateTime(h.ts)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          )}
        </div>
      )}

      <Results
        data={data} loading={loading} error={error} tokens={tokens}
        sortBy={sortBy} sortDir={sortDir} onSort={onSort}
        onNeighbors={openNeighbors} onDetails={(row) => setDetail({ row, fields: data?.fields ?? [] })}
        onConnections={openConnections} onBookmark={bookmark} pending={pending}
        page={page} totalPages={totalPages} gotoPage={gotoPage}
      />

      {neighbors && (
        <Modal
          title={`Жители по этому адресу: ${neighbors.total}`}
          onClose={() => setNeighbors(null)}
        >
          {(() => {
            const hasApt = neighbors.rows.some((r) => apartmentOf(r["Адрес"]));
            return (
              <div className="neigh-sort">
                <span className="muted">Показать:</span>
                {([["apt", "по квартире"], ["name", "по ФИО"], ["dob", "по дате рожд."],
                   ["family", "по семьям"]] as const).map(([key, label]) => (
                  <button
                    key={key}
                    className={`chip${neighSort === key ? " active" : ""}`}
                    disabled={key === "apt" && !hasApt}
                    title={key === "apt" && !hasApt ? "В этом доме нет данных о квартирах" : undefined}
                    onClick={() => setNeighSort(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            );
          })()}
          <div className="modal-body">
            {neighbors.rows.length === 0 && (
              <p className="muted">Других жителей не найдено.</p>
            )}
            {neighSort === "family" &&
              groupByFamily(neighbors.rows).map(([surname, rows]) => (
                <div className="family-group" key={surname}>
                  <div className="family-head">
                    {surname} <span className="muted">· {rows.length}</span>
                  </div>
                  {sortNeighbors(rows, "name").map((r, i) => (
                    <NeighRow key={i} r={r} onOpen={() => setDetail({ row: r, fields: neighbors.fields })} />
                  ))}
                </div>
              ))}
            {neighSort === "apt" &&
              groupByApartment(neighbors.rows).map(([apt, rows]) => (
                <div className="family-group" key={apt}>
                  <div className="family-head apt-group-head">
                    {apt === "—" ? "Без квартиры" : `Квартира ${apt}`}
                    <span className="muted"> · {rows.length} {rows.length === 1 ? "житель" : "чел."}</span>
                  </div>
                  {sortNeighbors(rows, "name").map((r, i) => (
                    <NeighRow key={i} r={r} onOpen={() => setDetail({ row: r, fields: neighbors.fields })} />
                  ))}
                </div>
              ))}
            {neighSort !== "family" && neighSort !== "apt" &&
              sortNeighbors(neighbors.rows, neighSort).map((r, i) => (
                <NeighRow key={i} r={r} onOpen={() => setDetail({ row: r, fields: neighbors.fields })} />
              ))}
          </div>
        </Modal>
      )}

      {conn && (
        <Modal title="Связи человека" onClose={() => setConn(null)}>
          <div className="modal-body">
            <div className="conn-section">
              <div className="conn-head">
                <Icon name="users" size={16} /> Родственники <span className="muted">(та же фамилия, тот же дом): {conn.relatives_total}</span>
              </div>
              {conn.relatives.length === 0
                ? <p className="muted">Не найдены.</p>
                : conn.relatives.map((r, i) => (
                    <NeighRow key={i} r={r} relation={String(r._relation ?? "")}
                      onOpen={() => setDetail({ row: r, fields: conn.fields })} />
                  ))}
            </div>
            <div className="conn-section">
              <div className="conn-head">
                <Icon name="phone" size={16} /> Один номер телефона <span className="muted">(по всей базе): {conn.phone_total}</span>
              </div>
              {conn.phone.length === 0
                ? <p className="muted">Совпадений нет.</p>
                : conn.phone.map((r, i) => (
                    <NeighRow key={i} r={r} onOpen={() => setDetail({ row: r, fields: conn.fields })} />
                  ))}
            </div>
          </div>
        </Modal>
      )}

      {detail && (
        <Modal
          title={
            [detail.row["Фамилия"], detail.row["Имя"], detail.row["Отчество"]]
              .filter(Boolean)
              .join(" ") || "Карточка жителя"
          }
          onClose={() => setDetail(null)}
        >
          <div className="modal-body detail-body">
            <ResidentCard
              row={detail.row}
              fields={detail.fields}
              tokens={tokens}
              onNeighbors={openNeighbors}
              onConnections={openConnections}
              onBookmark={bookmark}
            />
          </div>
        </Modal>
      )}
    </>
  );
}

/** Строка списка жителей (соседи / связи) — кликабельна для открытия карточки. */
function NeighRow({ r, onOpen, relation }: { r: Resident; onOpen: () => void; relation?: string }) {
  const fio = [r["Фамилия"], r["Имя"], r["Отчество"]].filter(Boolean).join(" ") || "—";
  const apt = apartmentOf(r["Адрес"]);
  const age = ageFromDob(r["Дата рождения"]);
  return (
    <button className="neigh-row" title="Открыть карточку" onClick={onOpen}>
      <span className="k">
        {fio}
        {apt && <span className="apt-badge">кв. {apt}</span>}
        {relation && <span className="rel-badge">{relation}</span>}
      </span>
      <span className="v">
        {formatDate(r["Дата рождения"])}
        {age != null && <span className="muted"> · {age}</span>}
      </span>
    </button>
  );
}

function Results(props: {
  data: SearchResponse | null;
  loading: boolean;
  error: string | null;
  tokens: string[];
  sortBy: string; sortDir: string;
  onSort: (c: string) => void;
  onNeighbors: (rowid: number) => void;
  onDetails: (row: Resident) => void;
  onConnections: (rowid: number) => void;
  onBookmark: (row: Resident) => void;
  pending: { kind: "neigh" | "conn"; rowid: number } | null;
  page: number; totalPages: number; gotoPage: (p: number) => void;
}) {
  const { data, loading, error, tokens, sortBy, sortDir, onSort, onNeighbors,
          onDetails, onConnections, onBookmark, pending, page, totalPages, gotoPage } = props;

  if (error) return <p className="meta err"><Icon name="alert" size={16} /> {error}</p>;
  if (loading && !data) return <div className="empty">Идёт поиск…</div>;
  if (!data)
    return (
      <div className="empty">
        <span className="ico" aria-hidden="true"><Icon name="search" size={40} /></span>
        <p><b>Введите данные для поиска</b></p>
        <p className="muted">Любое поле или их сочетание · работает с опечатками</p>
      </div>
    );
  if (data.total === 0)
    return (
      <div className="empty">
        <span className="ico" aria-hidden="true"><Icon name="search-x" size={40} /></span>
        <p><b>Ничего не найдено</b></p>
        <p className="muted">Проверьте написание или уберите часть полей</p>
      </div>
    );

  const from = (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, data.total);

  return (
    <>
      <div className="meta">
        Найдено <span className="badge">{data.total}</span>
        {data.total > PAGE_SIZE && (
          <span className="muted">показаны {from}–{to}</span>
        )}
        {data.mode === "fuzzy" && (
          <span className="badge fuzzy" title="Точных совпадений нет — показаны близкие по написанию (опечатки, разные раскладки казахских букв)">
            ≈ неточное совпадение
          </span>
        )}
        {loading && <span className="meta-spinner" aria-live="polite">обновление…</span>}
      </div>
      <div className={`results-wrap${loading ? " is-loading" : ""}`}>
        <ResultsTable
          rows={data.results} fields={data.fields} tokens={tokens}
          sortBy={sortBy} sortDir={sortDir} onSort={onSort}
          onNeighbors={onNeighbors} onDetails={onDetails}
          onConnections={onConnections} onBookmark={onBookmark} pending={pending}
        />
      </div>
      {totalPages > 1 && (
        <div className="pager">
          <button disabled={page <= 1} onClick={() => gotoPage(page - 1)}><Icon name="chevron-left" size={15} /> Назад</button>
          <span className="muted">Стр. {page} из {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => gotoPage(page + 1)}>Вперёд <Icon name="chevron-right" size={15} /></button>
        </div>
      )}
    </>
  );
}
