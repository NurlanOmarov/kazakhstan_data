import { useEffect, useRef, useState } from "react";
import { fetchSuggest } from "./api";

interface Props {
  label: string;
  value: string;
  onChange: (v: string) => void;
  field?: "surname" | "name" | "city" | "district" | "street";
  placeholder?: string;
  inputMode?: "text" | "numeric";
  full?: boolean;
  hint?: string;
}

export function Field({
  label, value, onChange, field, placeholder, inputMode, full, hint,
}: Props) {
  const [items, setItems] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const boxRef = useRef<HTMLDivElement>(null);
  const debounce = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!field) return;
    if (!value.trim() || value.trim().length < 2) {
      setItems([]);
      return;
    }
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(async () => {
      const res = await fetchSuggest(field, value);
      setItems(res);
      setHi(-1);
    }, 220);
    return () => window.clearTimeout(debounce.current);
  }, [value, field]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const pick = (v: string) => {
    onChange(v);
    setItems([]);
    setOpen(false);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!open || !items.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(h + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Enter" && hi >= 0) { e.preventDefault(); pick(items[hi]); }
    else if (e.key === "Escape") setOpen(false);
  };

  const hasSuggest = !!(open && field && items.length > 0);

  return (
    <div className={`field${full ? " full" : ""}`} ref={boxRef}>
      <span>{label}</span>
      <input
        type="text"
        value={value}
        inputMode={inputMode ?? "text"}
        placeholder={placeholder ?? label}
        autoComplete="off"
        role={field ? "combobox" : undefined}
        aria-expanded={field ? hasSuggest : undefined}
        aria-autocomplete={field ? "list" : undefined}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
      />
      {hint && <small className="field-hint">{hint}</small>}
      {hasSuggest && (
        <ul className="suggest" role="listbox">
          {items.map((it, i) => (
            <li
              key={it}
              role="option"
              aria-selected={i === hi}
              className={i === hi ? "active" : ""}
              onMouseDown={(e) => { e.preventDefault(); pick(it); }}
            >
              {it}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
