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
  // Размещение подписи (вычисляется по углу узла, чтобы текст уходил наружу).
  labelDx: number;
  labelDy: number;
  labelAnchor: "start" | "middle" | "end";
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
  // Фамилия + полное имя (отличает однофамильцев лучше инициалов), с обрезкой.
  let label = [s, n].filter(Boolean).join(" ") || n || "—";
  if (label.length > 20) label = label.slice(0, 19) + "…";
  return label;
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
    label: shortLabel(root.row) || root.label, sub: subLabel(root.row),
    relation: "", r: 34, tone: "center",
    labelDx: 0, labelDy: 52, labelAnchor: "middle",
  };
  const sats: NodeMeta[] = [];
  root.data.relatives.slice(0, MAX_PER_GROUP).forEach((row, i) => {
    const relation = String(row._relation ?? "");
    sats.push({
      id: `r${row._rowid}_${i}`, kind: "relative", rowid: Number(row._rowid),
      row, label: shortLabel(row), sub: subLabel(row), relation,
      r: 17, tone: relationTone(relation),
      labelDx: 0, labelDy: 31, labelAnchor: "middle",
    });
  });
  root.data.phone.slice(0, MAX_PER_GROUP).forEach((row, i) => {
    sats.push({
      id: `p${row._rowid}_${i}`, kind: "phone", rowid: Number(row._rowid),
      row, label: shortLabel(row), sub: subLabel(row), relation: "тот же телефон",
      r: 17, tone: "phone",
      labelDx: 0, labelDy: 31, labelAnchor: "middle",
    });
  });
  return { center, sats };
}

/** Радиальная раскладка по кольцам — «взрыв» лучей из центра. */
function layout(sats: NodeMeta[]): { id: string; tx: number; ty: number; angle: number }[] {
  const out: { id: string; tx: number; ty: number; angle: number }[] = [];
  const base = 118, step = 88;
  // Первое кольцо вмещает все узлы, если их немного (равномерно по кругу),
  // иначе до 9, остальные — на внешних кольцах.
  let ring = 0, placed = 0, cap = Math.min(Math.max(sats.length, 1), 9);
  for (let i = 0; i < sats.length; i++) {
    const angle = (placed / cap) * Math.PI * 2 + ring * 0.5 - Math.PI / 2;
    const radius = base + ring * step;
    out.push({
      id: sats[i].id,
      tx: CX + radius * Math.cos(angle),
      ty: CY + radius * Math.sin(angle) * 0.84,
      angle,
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

  const { center, sats, posById } = useMemo(() => {
    const built = buildNodes(root);
    const pos = layout(built.sats);
    const byId = new Map(pos.map((p) => [p.id, p]));
    // Подпись уходит радиально наружу: текст вправо/влево от узла и выше/ниже,
    // чтобы не наезжать на центр и соседей.
    for (const s of built.sats) {
      const p = byId.get(s.id)!;
      const ca = Math.cos(p.angle), sa = Math.sin(p.angle);
      s.labelAnchor = ca > 0.4 ? "start" : ca < -0.4 ? "end" : "middle";
      s.labelDx = Math.round(ca * (s.r + 6));
      s.labelDy = sa < -0.35 ? -(s.r + 9) : (s.r + 17);
    }
    return { center: built.center, sats: built.sats, posById: byId };
  }, [root]);

  // Физика живёт в ref, чтобы не дёргать React-рендер на каждом кадре.
  const physRef = useRef<PhysNode[]>([]);
  const gRefs = useRef<Map<string, SVGGElement>>(new Map());
  const eRefs = useRef<Map<string, SVGPathElement>>(new Map());
  const rafRef = useRef<number>(0);
  // Камера (зум/пан) — тоже в ref, применяется к обёртке <g> в цикле кадров.
  const svgRef = useRef<SVGSVGElement>(null);
  const viewRef = useRef<SVGGElement>(null);
  const cam = useRef({ scale: 1, tx: 0, ty: 0 });
  // Активные указатели (для пана одним пальцем и pinch-зума двумя).
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ dist: number; cx: number; cy: number } | null>(null);

  /** Клиентские координаты курсора → координаты пространства viewBox. */
  const clientToSvg = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const pt = svg.createSVGPoint();
    pt.x = clientX; pt.y = clientY;
    const m = svg.getScreenCTM();
    if (!m) return { x: 0, y: 0 };
    const p = pt.matrixTransform(m.inverse());
    return { x: p.x, y: p.y };
  };

  /** Масштабирование вокруг точки (px,py) в координатах viewBox. */
  const zoomAround = (px: number, py: number, factor: number) => {
    const c = cam.current;
    const ns = Math.min(3.5, Math.max(0.45, c.scale * factor));
    const wx = (px - c.tx) / c.scale, wy = (py - c.ty) / c.scale;
    c.tx = px - wx * ns; c.ty = py - wy * ns; c.scale = ns;
  };

  // Колесо мыши — зум к курсору (passive:false, чтобы можно было preventDefault).
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const p = clientToSvg(e.clientX, e.clientY);
      zoomAround(p.x, p.y, e.deltaY < 0 ? 1.12 : 1 / 1.12);
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as Element).closest(".cnode")) return; // клик по узлу — не пан
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    pinch.current = null; // переинициализируется при первом move с двумя пальцами
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    if (pts.length >= 2) {
      // Pinch-зум двумя пальцами: масштаб по изменению расстояния + пан середины.
      const [a, b] = pts;
      const dist = Math.hypot(b.x - a.x, b.y - a.y);
      const mid = clientToSvg((a.x + b.x) / 2, (a.y + b.y) / 2);
      if (pinch.current && pinch.current.dist > 0) {
        zoomAround(mid.x, mid.y, dist / pinch.current.dist);
        cam.current.tx += mid.x - pinch.current.cx;
        cam.current.ty += mid.y - pinch.current.cy;
      }
      pinch.current = { dist, cx: mid.x, cy: mid.y };
    } else {
      // Пан одним пальцем/мышью.
      const p1 = clientToSvg(e.clientX, e.clientY);
      const p0 = clientToSvg(prev.x, prev.y);
      cam.current.tx += p1.x - p0.x;
      cam.current.ty += p1.y - p0.y;
    }
  };
  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
  };
  const resetCam = () => { cam.current = { scale: 1, tx: 0, ty: 0 }; };

  // Перестроить физические узлы при смене корня (вылетают из центра).
  useEffect(() => {
    resetCam(); // новый корень — возвращаем камеру в исходный масштаб/центр
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
  }, [sats, posById]);

  // Анимационный цикл: пружина к цели + лёгкий дрейф + проявление.
  useEffect(() => {
    const k = 0.09, damping = 0.82;
    const tick = () => {
      const now = performance.now();
      const v = viewRef.current;
      if (v) {
        const c = cam.current;
        v.setAttribute("transform", `translate(${c.tx} ${c.ty}) scale(${c.scale})`);
      }
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
        <div className="cgraph-zoom">
          <button title="Приблизить" onClick={() => zoomAround(VIEW_W / 2, VIEW_H / 2, 1.25)}>+</button>
          <button title="Отдалить" onClick={() => zoomAround(VIEW_W / 2, VIEW_H / 2, 1 / 1.25)}>−</button>
          <button title="Сбросить вид" onClick={resetCam} className="reset">⟲</button>
        </div>
        <svg ref={svgRef} viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="cgraph-svg"
          role="img" aria-label="Граф связей"
          onPointerDown={onPointerDown} onPointerMove={onPointerMove}
          onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
          onPointerLeave={onPointerUp}>
          <defs>
            <radialGradient id="cg-core" cx="50%" cy="40%" r="70%">
              <stop offset="0%" stopColor="var(--cg-core-1)" />
              <stop offset="100%" stopColor="var(--cg-core-2)" />
            </radialGradient>
          </defs>

          <g ref={viewRef}>
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
                <text className="cnode-label" x={n.labelDx} y={n.labelDy}
                  textAnchor={n.labelAnchor}>{n.label}</text>
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
          </g>
        </svg>
      </div>

      <div className="cgraph-foot">
        <span><b>{relCount}</b> родня · <b>{phCount}</b> по телефону</span>
        {hidden > 0 && <span className="muted">показаны не все: +{hidden} скрыто</span>}
        <span className="muted">клик по узлу — связи · по центру — карточка · колесо — зум · тяни — перемещение</span>
      </div>
    </div>
  );
}
