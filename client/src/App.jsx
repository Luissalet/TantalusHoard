import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "./api.js";
import { initialLang, makeT, saveLang } from "./i18n.js";
import { AppContext } from "./context.js";
import { ConfirmDialog, Icon } from "./components/ui.jsx";
import Novedades from "./pages/Novedades.jsx";
import Watchers from "./pages/Watchers.jsx";
import Target from "./pages/Target.jsx";
import Search from "./pages/Search.jsx";
import Events from "./pages/Events.jsx";
import Mail from "./pages/Mail.jsx";
import Settings from "./pages/Settings.jsx";

export { useApp } from "./context.js";

const PAGES = [
  { path: "", key: "nav_news", icon: "M6 9a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8M10 21h4", component: Novedades, badge: true },
  { path: "watchers", key: "nav_watchers", icon: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z", component: Watchers },
  { path: "search", key: "nav_search", icon: "M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-5-5", component: Search },
  { path: "events", key: "nav_events", icon: "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2", component: Events },
  { path: "mail", key: "nav_mail", icon: "M3 6h18v12H3zM3 7l9 7 9-7", component: Mail },
  { path: "settings", key: "nav_settings", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1-1-3-2 .3-1.4-1.4.3-2-3-1-1 2h-2l-1-2-3 1 .3 2L6.8 7.3 5 7 4 10l2 1v2l-2 1 1 3 2-.3 1.4 1.4-.3 2 3 1 1-2h2l1 2 3-1-.3-2 1.4-1.4 2 .3 1-3-2-1z", component: Settings },
];
// Not in the sidebar: reached from the tables.
const HIDDEN = [{ path: "target", component: Target, parent: "watchers" }];

function useHashRoute() {
  const read = () => {
    const [path, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    return { page: parts[0] || "", param: parts[1] || null, sub: parts[2] || null, query: new URLSearchParams(query) };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function Toast({ toast, onClose }) {
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(onClose, toast.kind === "error" ? 9000 : 4500);
    return () => clearTimeout(timer);
  }, [toast, onClose]);
  if (!toast) return null;
  return (
    <div className={`toast ${toast.kind === "error" ? "toast-error" : "toast-ok"}`} role={toast.kind === "error" ? "alert" : "status"} onClick={onClose}>
      {toast.message}
    </div>
  );
}

function SchedulerLight({ scheduler, t }) {
  if (!scheduler) return null;
  const state = !scheduler.running ? "off" : scheduler.paused ? "paused" : scheduler.current ? "busy" : "idle";
  const color = { off: "var(--muted)", paused: "var(--warn)", busy: "var(--accent)", idle: "var(--ok)" }[state];
  const cur = scheduler.current;
  return (
    <div className="space-y-0.5" aria-live="polite">
      <div className="flex items-center gap-2 font-semibold text-[12px]" style={{ color: "var(--ink)" }}>
        <span className="dot" style={{ background: color }} />
        {t(`sched_${state}`)}
      </div>
      {cur && <div className="help trunc" title={`${cur.kind} ${cur.ref}`}>{t("sched_current")}: {cur.kind} {cur.ref}</div>}
      <div className="help">{t("sched_queue", { n: scheduler.queue ?? 0 })} · {t("sched_done", { n: scheduler.jobs_done ?? 0 })}</div>
    </div>
  );
}

export default function App() {
  const route = useHashRoute();
  const [lang, setLang] = useState(initialLang);
  const t = useMemo(() => makeT(lang), [lang]);
  const [health, setHealth] = useState(null);
  const [dash, setDash] = useState(null);
  const [dashError, setDashError] = useState(null);
  const [toast, setToast] = useState(null);
  const [confirmReq, setConfirmReq] = useState(null);
  const [draft, setDraft] = useState(null);
  const [packs, setPacks] = useState(null);
  const confirmResolve = useRef(null);
  const packsLoading = useRef(false);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  useEffect(() => {
    const n = dash?.news_count || 0;
    document.title = n ? `(${n}) Tantalus's Hoard` : "Tantalus's Hoard";
  }, [dash]);

  const refreshDash = useCallback(async () => {
    try {
      setDash(await api.dashboard());
      setDashError(null);
    } catch (e) {
      setDashError(e);
    }
  }, []);
  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await api.health());
    } catch {
      // the dashboard call reports the unreachable backend
    }
  }, []);

  // Poll the dashboard every 30 s while the tab is visible; refresh when it becomes visible again.
  useEffect(() => {
    refreshDash();
    refreshHealth();
    const timer = setInterval(() => { if (!document.hidden) refreshDash(); }, 30000);
    const healthTimer = setInterval(() => { if (!document.hidden) refreshHealth(); }, 60000);
    const onVisible = () => { if (!document.hidden) { refreshDash(); refreshHealth(); } };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      clearInterval(healthTimer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshDash, refreshHealth]);

  const notify = useCallback((message, kind = "ok") => setToast({ message, kind, id: Math.random() }), []);
  const toastError = useCallback((error) => setToast({ message: errorText(error), kind: "error", id: Math.random() }), []);
  const confirm = useCallback((request) => new Promise((resolve) => {
    confirmResolve.current = resolve;
    setConfirmReq(request);
  }), []);
  const closeConfirm = useCallback((answer) => {
    setConfirmReq(null);
    if (confirmResolve.current) confirmResolve.current(answer);
    confirmResolve.current = null;
  }, []);
  const loadPacks = useCallback(async () => {
    if (packs || packsLoading.current) return packs;
    packsLoading.current = true;
    try {
      const result = await api.call("packs_list");
      setPacks(result.packs || []);
      return result.packs;
    } catch (e) {
      toastError(e);
      return null;
    } finally {
      packsLoading.current = false;
    }
  }, [packs, toastError]);

  const changeLang = useCallback((next) => {
    saveLang(next);
    setLang(next);
    api.call("settings_set", { values: { "notify.language": next } }).catch(() => {});
  }, []);

  const value = useMemo(() => ({
    t, lang, setLang: changeLang, health, dash, dashError, refreshDash, refreshHealth, notify, toastError, confirm, draft, setDraft, packs, loadPacks, route,
  }), [t, lang, changeLang, health, dash, dashError, refreshDash, refreshHealth, notify, toastError, confirm, draft, packs, loadPacks, route]);

  const hidden = HIDDEN.find((p) => p.path === route.page);
  const page = hidden || PAGES.find((p) => p.path === route.page) || PAGES[0];
  const activePath = hidden ? hidden.parent : page.path;
  const Component = page.component;
  const unseen = dash?.news_count || 0;

  return (
    <AppContext.Provider value={value}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-black focus:p-2">{t("skip")}</a>
      <div className="min-h-dvh md:grid md:grid-cols-[210px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-10 border-b md:flex md:h-dvh md:flex-col md:self-start md:border-b-0 md:border-r" style={{ background: "var(--sidebar)", borderColor: "var(--line)" }}>
          <div className="flex items-center gap-3 px-4 py-3 md:py-4">
            <img src="/icon-192.png" alt="" width="30" height="30" className="rounded-lg" />
            <div className="text-[14px] font-semibold leading-tight">Tantalus's Hoard</div>
          </div>
          <nav aria-label={t("sections")} className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col">
            {PAGES.map((p) => (
              <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0 text-[13px]" aria-current={p.path === activePath ? "page" : undefined}>
                <Icon d={p.icon} />
                {t(p.key)}
                {p.badge && unseen > 0 && <span className="nav-badge" aria-label={t("unseen_n", { n: unseen })}>{unseen > 99 ? "99+" : unseen}</span>}
              </a>
            ))}
          </nav>
          <div className="hidden flex-1 md:block" />
          <div className="hidden space-y-3 border-t px-4 py-3 md:block" style={{ borderColor: "var(--line)" }}>
            <SchedulerLight scheduler={dash?.scheduler} t={t} />
            {health?.offline && <span className="chip chip-amber">{t("offline_on")}</span>}
            <button type="button" className="btn btn-sm" onClick={() => changeLang(lang === "es" ? "en" : "es")}>{t("language")}</button>
          </div>
        </aside>
        <main id="main" className="min-w-0 px-4 py-4 md:px-7 md:py-6">
          {dashError && !dash && (
            <div className="banner banner-danger mb-4" role="alert">
              {t("unreachable")}: {dashError.message}. <button type="button" className="btn-link" onClick={refreshDash}>{t("retry")}</button>
            </div>
          )}
          {dashError && dash && (
            <div className="banner banner-warn mb-4" role="status">
              {t("stale")}: {dashError.message}. <button type="button" className="btn-link" onClick={refreshDash}>{t("retry")}</button>
            </div>
          )}
          <Component key={`${route.page}/${route.param || ""}`} param={route.param} sub={route.sub} query={route.query} />
          <div className="mt-8 flex flex-wrap items-center gap-3 border-t pt-3 md:hidden" style={{ borderColor: "var(--line)" }}>
            <SchedulerLight scheduler={dash?.scheduler} t={t} />
            <button type="button" className="btn btn-sm" onClick={() => changeLang(lang === "es" ? "en" : "es")}>{t("language")}</button>
          </div>
        </main>
      </div>
      <Toast toast={toast} onClose={() => setToast(null)} />
      <ConfirmDialog request={confirmReq} onClose={closeConfirm} />
    </AppContext.Provider>
  );
}
