// Двойной ползунок «возраст от–до».
// Значения хранятся в params как строки: "" = граница не задана (фильтр выключен).
// В слайдере пусто отображается как 0 (низ) и MAX (верх); при отправке такие
// крайние значения превращаются обратно в "" — поэтому qs их не шлёт.

export const AGE_MIN = 0;
export const AGE_MAX = 100;

interface Props {
  /** Сырые строковые значения из params (age_min / age_max). */
  min: string;
  max: string;
  /** Возвращает новые строковые значения ("" если граница на краю). */
  onChange: (min: string, max: string) => void;
}

const clamp = (n: number) => Math.min(AGE_MAX, Math.max(AGE_MIN, n));

export function AgeRange({ min, max, onChange }: Props) {
  const lo = min.trim() ? clamp(+min) : AGE_MIN;
  const hi = max.trim() ? clamp(+max) : AGE_MAX;

  // Край диапазона => "" (граница не задана).
  const emit = (nlo: number, nhi: number) =>
    onChange(
      nlo <= AGE_MIN ? "" : String(nlo),
      nhi >= AGE_MAX ? "" : String(nhi),
    );

  const pct = (v: number) => ((v - AGE_MIN) / (AGE_MAX - AGE_MIN)) * 100;
  const active = lo > AGE_MIN || hi < AGE_MAX;
  const label = !active
    ? "любой"
    : `${lo}–${hi >= AGE_MAX ? "100+" : hi}`;

  return (
    <div className="field full age-range">
      <span>
        Возраст <small className="muted">· {label}</small>
      </span>
      <div className="range-slider">
        <div className="range-track" />
        <div
          className="range-fill"
          style={{ left: `${pct(lo)}%`, right: `${100 - pct(hi)}%` }}
        />
        <input
          type="range" min={AGE_MIN} max={AGE_MAX} value={lo}
          aria-label="Возраст от"
          onChange={(e) => emit(Math.min(+e.target.value, hi), hi)}
        />
        <input
          type="range" min={AGE_MIN} max={AGE_MAX} value={hi}
          aria-label="Возраст до"
          onChange={(e) => emit(lo, Math.max(+e.target.value, lo))}
        />
      </div>
    </div>
  );
}
