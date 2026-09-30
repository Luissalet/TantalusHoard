// Number, price and time formatting shared by the pages. es-ES by default (1.234,56 €), en-GB when English is chosen.
export const locale = (lang) => (lang === "en" ? "en-GB" : "es-ES");

export function num(value, digits = 0, lang = "es") {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(locale(lang), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function money(value, currency, lang = "es") {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
  // es-ES drops the thousands separator for 4-digit numbers unless told otherwise; the owner wants 1.234,56 €.
  try {
    return new Intl.NumberFormat(locale(lang), { style: "currency", currency: currency || "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: "always" }).format(Number(value));
  } catch {
    return `${num(value, 2, lang)} ${currency || "€"}`;
  }
}

export function clock(ts, lang) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(locale(lang), { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function shortClock(ts, lang) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(locale(lang), { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

// "hace 5 min" / "dentro de 2 h": Intl does the wording, we pick the unit.
export function rel(ts, lang, nowMs = Date.now()) {
  if (!ts) return "—";
  const diff = ts * 1000 - nowMs;
  const abs = Math.abs(diff);
  const formatter = new Intl.RelativeTimeFormat(locale(lang), { numeric: "auto", style: "short" });
  if (abs < 45_000) return formatter.format(0, "second");
  const units = [["day", 86_400_000], ["hour", 3_600_000], ["minute", 60_000]];
  for (const [unit, size] of units) {
    if (abs >= size || unit === "minute") return formatter.format(Math.round(diff / size), unit);
  }
  return "—";
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 90) return `${seconds.toFixed(1)} s`;
  return `${Math.round(seconds / 60)} min`;
}

// Only http(s) links from third-party data become hrefs.
export function safeUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

export function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url || "";
  }
}

// "a, b\nc" -> ["a","b","c"]
export const splitList = (text) => (text || "").split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
export const splitLines = (text) => (text || "").split(/\n+/).map((s) => s.trim()).filter(Boolean);
export const joinList = (list) => (Array.isArray(list) ? list.join(", ") : "");
export const joinLines = (list) => (Array.isArray(list) ? list.join("\n") : "");

// "12,5" or "12.5" -> 12.5; "" -> undefined.
export function parseNum(text) {
  if (text === null || text === undefined) return undefined;
  const s = String(text).trim().replace(/\s/g, "").replace(",", ".");
  if (s === "") return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
}
