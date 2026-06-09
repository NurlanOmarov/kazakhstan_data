import { useEffect, useMemo, useRef, useState } from "react";
import type { Resident, Connections } from "./api";
import { ageFromDob, apartmentOf } from "./format";

/**
 * Интерактивный граф связей человека.
 *
 * В центре — текущий человек, вокруг лучами разлетаются связанные записи:
 * родственники (по дому/фамилии/отчеству) и владельцы того же телефона.
 * Узлы оживают пружинной физикой (requestAnimationFrame), с лёгким дрейфом,
 * staggered-появлением и «прорастающими» из центра рёбрами.
 *
 * Управление: клик по узлу-спутнику — раскрыть его связи (граф пере-укореняется,
 * прежний корень кладётся в стек «назад»); клик по центру — открыть карточку.
 */

const VIEW_W = 820;
const VIEW_H = 540;
const CX = VIEW_W / 2;
const CY = VIEW_H / 2 - 8;
const MAX_PER_GROUP = 26; // больше — визуальный шум; остаток показываем числом

type Kind = "center" | "relative" | "phone";

interface NodeMeta {
  id: string;
  kind: Kind;
  rowid: number;
  row: Resident | null;
  label: string;
  sub: string;
  relation: string;
  r: number;
  tone: string; // CSS-класс цвета
}

interface PhysNode extends NodeMeta {
  x: number; y: number; vx: number; vy: number;
  tx: number; ty: number;
  born: number; delay: number;
  ctrl: number; // знак/амплитуда изгиба ребра
}

interface Root {
  rowid: number;
  label: string;
  row: Resident | null;
  data: Connections;
}

function shortLabel(row: Resident | null): string {
  if (!row) return "—";
  const s = String(row["Фамилия"] ?? "").trim();
  const n = String(row["Имя"] ?? "").trim();
  const p = String(row["Отчество"] ?? "").trim();
  const initials = [n, p].filter(Boolean).map((x) => x[0].toUpperCase() + ".").join(" ");
  return [s, initials].filter(Boolean).join(" ") || n || "—";
}

function subLabel(row: Resident | null): string {
  if (!row) return "";
  const age = ageFromDob(row["Дата рождения"]);
  const apt = apartmentOf(row["Адрес"]);
  return [age != null ? `${age} лет` : "", apt ? `кв. ${apt}` : ""]
    .filter(Boolean).join(" · ");
}

/** Цветовая «тональность» родственной связи по причине. */
function relationTone(relation: string): string {
  if (/родитель|ребёнок/.test(relation)) return "lineage";
  if (/отчество/.test(relation)) return "sibling";
  if (/улица/.test(relation)) return "weak";
  return "family";
}

function buildNodes(root: Root): { center: NodeMeta; sats: NodeMeta[] } {
  const center: NodeMeta = {
    id: "center", kind: "center", rowid: root.rowid, row: root.row,
    label: root.label, sub: subLabel(root.row), relation: "", r: 34, tone: "center",
  };
  const sats: NodeMeta[] = [];
  root.data.relatives.slice(0, MAX_PER_GROUP).forEach((row, i) => {
    const relation = String(row._relation ?? "");
    sats.push({
      id: `r${row._rowid}_${i}`, kind: "relative", rowid: Number(row._rowid),
      row, label: shortLabel(row), sub: subLabel(row), relation,
      r: 17, tone: relationTone(relation),
    });
  });
  root.data.phone.slice(0, MAX_PER_GROUP).forEach((row, i) => {
    sats.push({
      id: `p${row._rowid}_${i}`, kind: "phone", rowid: Number(row._rowid),
      row, label: shortLabel(row), sub: subLabel(row), relation: "тот же телефон",
      r: 17, tone: "phone",
    });
  });
  return { center, sats };
}

/** Радиальная раскладка по кольцам — «взрыв» лучей из центра. */
function layout(sats: NodeMeta[]): { id: string; tx: number; ty: number }[] {
  const out: { id: string; tx: number; ty: number }[] = [];
  const base = 96, step = 74;
  let ring = 0, placed = 0, cap = 7;
  for (let i = 0; i < sats.length; i++) {
    const angle = (placed / cap) * Math.PI * 2 + ring * 0.6 - Math.PI / 2;
    const radius = base + ring * step;
    out.push({
      id: sats[i].id,
      tx: CX + radius * Math.cos(angle),
      ty: CY + radius * Math.sin(angle) * 0.82,
    });
    placed++;
    if (placed >= cap) { ring++; placed = 0; cap = Math.round(cap * 1.7); }
  }
  return out;
}

export function ConnectionsGraph({
  initial, onOpenCard, onExpand,
}: {
  initial: Root;
  onOpenCard: (row: Resident) => void;
  onExpand: (rowid: number) => Promise<Connections>;
}) {
  const [stack, setStack] = useState<Root[]>([]);
  const [root, setRoot] = useState<Root>(initial);
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<string | null>(null);

  const { center, sats } = useMemo(() => buildNodes(root), [root]);

  // Физика живёт в ref, чтобы не дёргать React-рендер на каждом кадре.
  const physRef = useRef<PhysNode[]>([]);
  const gRefs = useRef<Map<string, SVGGElement>>(new Map());
  const eRefs = useRef<Map<string, SVGPathElement>>(new Map());
  const rafRef = useRef<number>(0);

  // Перестроить физические узлы при смене корня (вылетают из центра).
  useEffect(() => {
    const pos = layout(sats);
    const posById = new Map(pos.map((p) => [p.id, p]));
    const now = performance.now();
    physRef.current = sats.map((m, i) => {
      const p = posById.get(m.id)!;
      const jitter = (Math.random() - 0.5) * 24;
      return {
        ...m,
        x: CX + jitter, y: CY + jitter, vx: 0, vy: 0,
        tx: p.tx, ty: p.ty, born: now, delay: i * 26,
        ctrl: (i % 2 === 0 ? 1 : -1) * (18 + (i % 5) * 6),
      };
    });
  }, [sats]);

  // Анимационный цикл: пружина к цели + лёгкий дрейф + проявление.
  useEffect(() => {
    const k = 0.09, damping = 0.82;
    const tick = () => {
      const now = performance.now();
      for (const n of physRef.current) {
        const wob = Math.sin((now - n.born) / 900 + n.delay) * 0.5;
        n.vx += (n.tx + wob - n.x) * k;
        n.vy += (n.ty + wob - n.y) * k;
        n.vx *= damping; n.vy *= damping;
        n.x += n.vx; n.y += n.vy;
        const appear = Math.min(1, Math.max(0, (now - n.born - n.delay) / 360));
        const g = gRefs.current.get(n.id);
        if (g) {
          const scale = 0.4 + 0.6 * appear;
          g.setAttribute("transform", `translate(${n.x} ${n.y}) scale(${scale})`);
          g.setAttribute("opacity", String(appear));
        }
        const e = eRefs.current.get(n.id);
        if (e) {
          const mx = (CX + n.x) / 2 + n.ctrl;
          const my = (CY + n.y) / 2 - n.ctrl;
          e.setAttribute("d", `M ${CX} ${CY} Q ${mx} ${my} ${n.x} ${n.y}`);
          e.setAttribute("opacity", String(appear * (hover === n.id ? 0.95 : 0.45)));
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [hover]);

  const explore = async (n: NodeMeta) => {
    if (loading || !n.row) return;
    setLoading(true);
    try {
      const data = await onExpand(n.rowid);
      setStack((s) => [...s, root]);
      setRoot({ rowid: n.rowid, label: shortLabel(n.row), row: n.row, data });
    } finally {
      setLoading(false);
    }
  };

  const back = () => {
    setStack((s) => {
      if (!s.length) return s;
      setRoot(s[s.length - 1]);
      return s.slice(0, -1);
    });
  };

  const relCount = root.data.relatives_total;
  const phCount = root.data.phone_total;
  const hidden =
    Math.max(0, relCount - Math.min(MAX_PER_GROUP, root.data.relatives.length)) +
    Math.max(0, phCount - Math.min(MAX_PER_GROUP, root.data.phone.length));

  return (
    <div className="cgraph">
      <div className="cgraph-bar">
        <button className="chip" onClick={back} disabled={!stack.length}>
          ← Назад
        </button>
        <span className="cgraph-title" title={root.label}>{root.label}</span>
        <span className="cgraph-legend">
          <span><i className="cdot family" /> родня</span>
          <span><i className="cdot lineage" /> родитель/ребёнок</span>
          <span><i className="cdot phone" /> телефон</span>
        </span>
      </div>

      <div className="cgraph-stage">
        {loading && <div className="cgraph-loading"><span className="btn-spin" /> загрузка связей…</div>}
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="cgraph-svg"
          role="img" aria-label="Граф связей">
          <defs>
            <radialGradient id="cg-core" cx="50%" cy="40%" r="70%">
              <stop offset="0%" stopColor="var(--cg-core-1)" />
              <stop offset="100%" stopColor="var(--cg-core-2)" />
            </radialGradient>
          </defs>

          {/* Рёбра (под узлами) */}
          <g className="cgraph-edges">
            {sats.map((n) => (
              <path key={n.id} className={`cedge ${n.tone}`}
                ref={(el) => { if (el) eRefs.current.set(n.id, el); else eRefs.current.delete(n.id); }}
                d={`M ${CX} ${CY} ${CX} ${CY}`} fill="none" />
            ))}
          </g>

          {/* Спутники */}
          {sats.map((n) => (
            <g key={n.id} className={`cnode ${n.tone}${hover === n.id ? " hover" : ""}`}
              ref={(el) => { if (el) gRefs.current.set(n.id, el); else gRefs.current.delete(n.id); }}
              onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
              onClick={() => explore(n)}
              style={{ cursor: "pointer" }}>
              <title>{`${n.label}${n.sub ? " · " + n.sub : ""}${n.relation ? "\n" + n.relation : ""}\n(клик — раскрыть связи)`}</title>
              <circle className="cnode-halo" r={n.r + 6} />
              <circle className="cnode-core" r={n.r} />
              <text className="cnode-label" y={n.r + 14}>{n.label}</text>
            </g>
          ))}

          {/* Центр */}
          <g className="cnode center" transform={`translate(${CX} ${CY})`}
            onClick={() => root.row && onOpenCard(root.row)}
            style={{ cursor: root.row ? "pointer" : "default" }}>
            <title>{`${center.label}${center.sub ? " · " + center.sub : ""}\n(клик — карточка)`}</title>
            <circle className="cnode-pulse" r={center.r + 10} />
            <circle r={center.r} fill="url(#cg-core)" stroke="var(--cg-core-stroke)" strokeWidth={2} />
            <text className="cnode-label center" y={center.r + 18}>{center.label}</text>
          </g>
        </svg>
      </div>

      <div className="cgraph-foot">
        <span><b>{relCount}</b> родня · <b>{phCount}</b> по телефону</span>
        {hidden > 0 && <span className="muted">показаны не все: +{hidden} скрыто</span>}
        <span className="muted">клик по узлу — раскрыть связи · по центру — карточка</span>
      </div>
    </div>
  );
}
