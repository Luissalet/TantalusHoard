import React, { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../context.js";
import { EVENT_META, MODE_META, STATE_META, VERDICT_META } from "../meta.js";
import { clock, money, rel, safeUrl } from "../format.js";

export function Icon({ d, size = 17, color }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color || "currentColor"} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="…" />;
}

export function Empty({ children }) {
  return <div className="panel help text-center">{children}</div>;
}

export function Field({ label, hint, children, className = "" }) {
  return (
    <label className={`block ${className}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="help mt-1 block">{hint}</span>}
    </label>
  );
}

export function Switch({ checked, onChange, disabled, label }) {
  return (
    <label className="switch" title={label}>
      <input type="checkbox" role="switch" checked={!!checked} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

export function Check({ checked, onChange, children, disabled }) {
  return (
    <label className="inline-flex items-center gap-1.5">
      <input type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}</span>
    </label>
  );
}

export function Chip({ children, className = "", title }) {
  return <span className={`chip ${className}`} title={title}>{children}</span>;
}

export function Section({ title, count, actions, children, id }) {
  return (
    <section className="space-y-2" aria-labelledby={id}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={id}>{title}</h2>
        {count !== undefined && count !== null && <span className="chip">{count}</span>}
        <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>
      </div>
      {children}
    </section>
  );
}

export function Busy({ busy, children, ...props }) {
  return (
    <button type="button" {...props} disabled={busy || props.disabled}>
      {busy && <span className="spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}

export function ExtLink({ href, children, className = "btn btn-sm", title }) {
  const url = safeUrl(href);
  if (!url) return null;
  return <a className={className} href={url} target="_blank" rel="noopener noreferrer" title={title || url}>{children}</a>;
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 border-b" style={{ borderColor: "var(--line)" }}>
      {tabs.map((tab) => (
        <button key={tab.key} type="button" role="tab" className="tab" aria-selected={tab.key === active} onClick={() => onChange(tab.key)}>
          {tab.icon && <Icon d={tab.icon} size={15} />}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function ErrorBox({ error }) {
  const { t } = useApp();
  if (!error) return null;
  return (
    <div className="banner banner-danger" role="alert">
      {error.message || String(error)}
      {error.hint ? <span className="help block">{t("hint")}: {error.hint}</span> : null}
    </div>
  );
}

// ------------------------------------------------------------------ domain pills
export function ModeChip({ mode, icon = true }) {
  const { t } = useApp();
  const meta = MODE_META[mode];
  return (
    <span className="chip chip-accent">
      {icon && meta && <Icon d={meta.icon} size={12} />}
      {t(`mode_${mode}`)}
    </span>
  );
}

export function StatePill({ state }) {
  const { t } = useApp();
  const key = state || "UNKNOWN";
  const meta = STATE_META[key] || STATE_META.UNKNOWN;
  return (
    <span className={`chip ${meta.cls}`}>
      <span className="dot" style={{ background: meta.color }} />
      {t(`state_${key}`)}
    </span>
  );
}

export function EventChip({ type }) {
  const { t } = useApp();
  const meta = EVENT_META[type] || { color: "#9a8cab", icon: "M12 12h.01" };
  return (
    <span className="chip" style={{ background: `${meta.color}22`, color: meta.color }}>
      <Icon d={meta.icon} size={12} />
      {t(`ev_${type}`) === `ev_${type}` ? type : t(`ev_${type}`)}
    </span>
  );
}

export function EventStatusPill({ status }) {
  const { t } = useApp();
  const cls = status === "confirmed" ? "chip-ok" : status === "pending" ? "chip-amber" : status === "dismissed" ? "chip-danger" : "";
  return <span className={`chip ${cls}`}>{t(`evst_${status}`)}</span>;
}

// confidence 0-100: >=75 alert, 55-74 revalidated first, <55 logged
export function Confidence({ value, factors }) {
  const { t } = useApp();
  if (value === null || value === undefined) return <span className="help">—</span>;
  const cls = value >= 75 ? "chip-ok" : value >= 55 ? "chip-amber" : "chip-danger";
  const title = Array.isArray(factors) && factors.length
    ? factors.map((f) => `${f.points > 0 ? "+" : ""}${f.points}  ${f.label || f.key}`).join("\n")
    : t("confidence_hint");
  return <span className={`chip ${cls} tip`} title={title}>{value} %</span>;
}

export function VerdictChip({ verdict }) {
  const { t } = useApp();
  const meta = VERDICT_META[verdict] || VERDICT_META.unknown;
  return <span className={`chip ${meta.cls}`}>{t(`verdict_${verdict || "unknown"}`)}</span>;
}

export function Price({ value, currency, old, className = "" }) {
  const { lang } = useApp();
  if (value === null || value === undefined) return <span className="help">—</span>;
  return (
    <span className={`num font-semibold ${className}`}>
      {money(value, currency, lang)}
      {old !== null && old !== undefined && old !== value && <span className="help ml-1.5 font-normal line-through">{money(old, currency, lang)}</span>}
    </span>
  );
}

export function Rel({ ts }) {
  const { lang } = useApp();
  if (!ts) return <span className="help">—</span>;
  return <time dateTime={new Date(ts * 1000).toISOString()} title={clock(ts, lang)}>{rel(ts, lang)}</time>;
}

export function Thumb({ src, large, alt = "" }) {
  const url = safeUrl(src);
  const [failed, setFailed] = useState(false);
  const cls = `thumb ${large ? "thumb-lg" : ""}`;
  if (!url || failed) {
    return <span className={`${cls} thumb-ph`} aria-hidden="true"><Icon d="M4 5h16v14H4zM4 15l4-4 4 4 3-3 5 5" size={20} /></span>;
  }
  return <img className={cls} src={url} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />;
}

// Listing score signals as a tooltip: "+10 gratis".
export function signalsTitle(signals) {
  if (!Array.isArray(signals) || !signals.length) return "";
  return signals.map((s) => `${s.points > 0 ? "+" : ""}${s.points}  ${s.label || s.key}`).join("\n");
}

export function ScoreBadge({ score, signals }) {
  const cls = score >= 15 ? "chip-ok" : score >= 8 ? "chip-amber" : score < 0 ? "chip-danger" : "";
  return <span className={`chip ${cls} tip num`} title={signalsTitle(signals)}>{Number(score).toLocaleString(undefined, { maximumFractionDigits: 1 })}</span>;
}

// ------------------------------------------------------------------ confirm dialog
export function ConfirmDialog({ request, onClose }) {
  const { t } = useApp();
  const cancelRef = useRef(null);
  useEffect(() => {
    if (!request) return undefined;
    cancelRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request, onClose]);
  if (!request) return null;
  return (
    <div className="modal-backdrop" onClick={() => onClose(false)}>
      <div className="modal space-y-3" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-title">{request.title || t("confirm")}</h2>
        <p>{request.message}</p>
        <div className="flex justify-end gap-2">
          <button type="button" ref={cancelRef} className="btn" onClick={() => onClose(false)}>{t("cancel")}</button>
          <button type="button" className={`btn ${request.danger === false ? "btn-primary" : "btn-danger"}`} onClick={() => onClose(true)}>{request.confirmLabel || t("delete")}</button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ price chart (hand-drawn SVG)
function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => setWidth(Math.max(260, Math.floor(el.clientWidth)));
    update();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) return [min - 1, min, min + 1];
  const raw = (max - min) / count;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/**
 * points: [{ checked_at, price, currency, availability }] oldest first.
 * Lines: the price path, a dashed threshold line and a dotted minimum-price line. Each point is a dot coloured by its availability.
 */
export function PriceChart({ points, threshold, minPrice, height = 240 }) {
  const { t, lang } = useApp();
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const pad = { l: 58, r: 12, t: 12, b: 24 };
  const data = useMemo(() => {
    const pts = (points || []).filter((p) => Number.isFinite(p.price)).map((p) => ({ ...p, x: p.checked_at * 1000 }));
    if (!pts.length) return null;
    let x0 = Math.min(...pts.map((p) => p.x));
    let x1 = Math.max(...pts.map((p) => p.x));
    if (x0 === x1) { x0 -= 3_600_000; x1 += 3_600_000; }
    const ys = pts.map((p) => p.price);
    if (Number.isFinite(threshold)) ys.push(threshold);
    if (Number.isFinite(minPrice)) ys.push(minPrice);
    let y0 = Math.min(...ys);
    let y1 = Math.max(...ys);
    const padY = (y1 - y0) * 0.1 || Math.max(1, y1 * 0.05);
    return { pts, x0, x1, y0: y0 - padY, y1: y1 + padY };
  }, [points, threshold, minPrice]);
  if (!data) return <div className="help">{t("chart_empty")}</div>;
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const sx = (x) => pad.l + ((x - data.x0) / (data.x1 - data.x0 || 1)) * w;
  const sy = (y) => pad.t + (1 - (y - data.y0) / (data.y1 - data.y0 || 1)) * h;
  const yTicks = niceTicks(data.y0, data.y1, 4);
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => data.x0 + (data.x1 - data.x0) * f);
  const fmtX = (x) => new Date(x).toLocaleString(lang === "en" ? "en-GB" : "es-ES", { day: "2-digit", month: "short", ...(data.x1 - data.x0 < 3 * 86_400_000 ? { hour: "2-digit", minute: "2-digit" } : {}) });
  const path = data.pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.price).toFixed(1)}`).join("");
  const onMove = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = data.x0 + ((event.clientX - rect.left - pad.l) / w) * (data.x1 - data.x0);
    let best = data.pts[0];
    for (const p of data.pts) if (Math.abs(p.x - x) < Math.abs(best.x - x)) best = p;
    setHover(best);
  };
  const currency = data.pts[data.pts.length - 1].currency || "EUR";
  return (
    <div ref={ref} className="relative w-full">
      <svg className="chart" width={width} height={height} role="img" aria-label={t("price_history")} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={sy(v)} y2={sy(v)} stroke="#ffffff12" />
            <text x={pad.l - 6} y={sy(v) + 3} textAnchor="end">{money(v, currency, lang).replace(/\s?[^\d.,\s-]+$/, "")}</text>
          </g>
        ))}
        {xTicks.map((x, i) => (
          <text key={i} x={sx(x)} y={height - 6} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}>{fmtX(x)}</text>
        ))}
        {Number.isFinite(minPrice) && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={sy(minPrice)} y2={sy(minPrice)} stroke="var(--ok)" strokeDasharray="2 3" />
            <text x={width - pad.r - 2} y={sy(minPrice) - 4} textAnchor="end" style={{ fill: "var(--ok)" }}>{t("min_price")} {money(minPrice, currency, lang)}</text>
          </g>
        )}
        {Number.isFinite(threshold) && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={sy(threshold)} y2={sy(threshold)} stroke="var(--warn)" strokeDasharray="6 4" />
            <text x={pad.l + 4} y={sy(threshold) - 4} style={{ fill: "var(--warn)" }}>{t("threshold")} {money(threshold, currency, lang)}</text>
          </g>
        )}
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinejoin="round" />
        {data.pts.map((p, i) => (
          <circle key={i} cx={sx(p.x)} cy={sy(p.price)} r={hover === p ? 5 : 3.2} fill={(STATE_META[p.availability] || STATE_META.UNKNOWN).color} stroke="var(--bg)" strokeWidth="1" />
        ))}
        {hover && <line x1={sx(hover.x)} x2={sx(hover.x)} y1={pad.t} y2={pad.t + h} stroke="#ffffff44" />}
      </svg>
      {hover && (
        <div className="panel pointer-events-none absolute text-[11.5px]" style={{ top: 6, left: Math.min(Math.max(sx(hover.x) + 10, 8), Math.max(8, width - 200)), padding: "4px 8px", minWidth: 150 }}>
          <div className="mono">{clock(hover.checked_at, lang)}</div>
          <div className="num font-semibold">{money(hover.price, hover.currency || currency, lang)}</div>
          <div className="flex items-center gap-1.5"><span className="dot" style={{ background: (STATE_META[hover.availability] || STATE_META.UNKNOWN).color }} />{t(`state_${hover.availability || "UNKNOWN"}`)}</div>
        </div>
      )}
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px]">
        {Array.from(new Set(data.pts.map((p) => p.availability || "UNKNOWN"))).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5"><span className="dot" style={{ background: (STATE_META[s] || STATE_META.UNKNOWN).color }} />{t(`state_${s}`)}</span>
        ))}
      </div>
    </div>
  );
}

// Loads something on mount and when `deps` change. `reload()` runs it again without flashing the placeholder.
export function useLoad(fn, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const run = useRef(fn);
  run.current = fn;
  const seq = useRef(0);
  const load = useMemo(() => async () => {
    const mine = ++seq.current;
    try {
      const data = await run.current();
      if (mine === seq.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (mine === seq.current) setState((s) => ({ data: s.data, error, loading: false }));
    }
  }, []);
  useEffect(() => {
    setState((s) => ({ ...s, loading: true }));
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { ...state, reload: load };
}

// Busy flags keyed by name; a failing action shows the backend's error and hint in a toast.
export function useBusy() {
  const { toastError } = useApp();
  const [busy, setBusy] = useState({});
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const run = async (key, fn) => {
    setBusy((b) => ({ ...b, [key]: true }));
    try {
      return await fn();
    } catch (error) {
      toastError(error);
      return undefined;
    } finally {
      if (mounted.current) setBusy((b) => ({ ...b, [key]: false }));
    }
  };
  return [busy, run];
}
