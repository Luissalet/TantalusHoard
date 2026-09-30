import React, { useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Busy, Chip, Confidence, Empty, ErrorBox, EventChip, EventStatusPill, ExtLink, Price, Rel, Tabs, useBusy, useLoad } from "../components/ui.jsx";
import { clock, duration, shortClock } from "../format.js";
import { EVENT_STATUSES, EVENT_TYPES } from "../meta.js";

function EventsTab({ initialWatcher }) {
  const { t, lang, dash, notify, refreshDash } = useApp();
  const [busy, run] = useBusy();
  const [watcher, setWatcher] = useState(initialWatcher || "");
  const [types, setTypes] = useState([]);
  const [statuses, setStatuses] = useState([]);
  const [unseen, setUnseen] = useState(false);
  const [limit, setLimit] = useState(60);
  const args = { limit, unseen_only: unseen, ...(watcher ? { watcher_id: watcher } : {}), ...(types.length ? { types } : {}), ...(statuses.length ? { statuses } : {}) };
  const { data, error, loading, reload } = useLoad(() => api.call("events_list", args), [watcher, types.join(), statuses.join(), unseen, limit]);
  const wname = Object.fromEntries((dash?.watchers || []).map((w) => [w.id, w.name]));
  const toggle = (list, setList, value) => setList(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  const events = data?.events || [];
  const dismiss = (e) => run(`d-${e.id}`, async () => { await api.call("event_dismiss", { event_id: e.id }); notify(t("event_dismissed")); reload(); refreshDash(); });
  const resend = (e) => run(`n-${e.id}`, async () => {
    const r = await api.call("event_notify", { event_id: e.id });
    notify((r.results || []).map((x) => `${t(`ch_${x.channel}`)} ${x.ok ? "✓" : `✗ ${x.error || ""}`}`).join(" · ") || t("nothing_sent"));
    reload();
  });
  return (
    <div className="space-y-3">
      <div className="panel space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block min-w-[200px]">
            <span className="label">{t("watcher")}</span>
            <select className="field" value={watcher} onChange={(e) => setWatcher(e.target.value)}>
              <option value="">{t("all")}</option>
              {(dash?.watchers || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="label">{t("limit")}</span>
            <select className="field" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>{[30, 60, 100, 200, 300].map((n) => <option key={n} value={n}>{n}</option>)}</select>
          </label>
          <label className="inline-flex items-center gap-1.5 pb-1.5"><input type="checkbox" checked={unseen} onChange={(e) => setUnseen(e.target.checked)} />{t("only_unseen")}</label>
        </div>
        <div>
          <span className="label">{t("types")}</span>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("types")}>
            {EVENT_TYPES.map((ty) => (
              <button key={ty} type="button" className={`btn btn-sm ${types.includes(ty) ? "btn-primary" : ""}`} aria-pressed={types.includes(ty)} onClick={() => toggle(types, setTypes, ty)}>{t(`ev_${ty}`)}</button>
            ))}
          </div>
        </div>
        <div>
          <span className="label">{t("statuses")}</span>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("statuses")}>
            {EVENT_STATUSES.map((s) => (
              <button key={s} type="button" className={`btn btn-sm ${statuses.includes(s) ? "btn-primary" : ""}`} aria-pressed={statuses.includes(s)} onClick={() => toggle(statuses, setStatuses, s)}>{t(`evst_${s}`)}</button>
            ))}
            {(types.length > 0 || statuses.length > 0 || watcher || unseen) && (
              <button type="button" className="btn-link ml-2" onClick={() => { setTypes([]); setStatuses([]); setWatcher(""); setUnseen(false); }}>{t("clear_filters")}</button>
            )}
          </div>
        </div>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <p className="help">…</p> : !events.length ? <Empty>{t("no_events_filtered")}</Empty> : (
        <div className="panel scroll-x p-0">
          <table>
            <thead><tr><th>{t("time")}</th><th>{t("type")}</th><th>{t("status")}</th><th>{t("title")}</th><th className="r">{t("price")}</th><th>{t("confidence")}</th><th style={{ width: 110 }} /></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} style={{ opacity: e.status === "dismissed" ? 0.55 : 1 }}>
                  <td className="whitespace-nowrap" title={clock(e.detected_at, lang)}>{shortClock(e.detected_at, lang)}<div className="help"><Rel ts={e.detected_at} /></div></td>
                  <td><EventChip type={e.type} /></td>
                  <td><EventStatusPill status={e.status} />{!e.seen && e.status === "confirmed" && <Chip className="chip-accent ml-1">{t("new")}</Chip>}</td>
                  <td style={{ minWidth: 260, maxWidth: 480 }}>
                    <div className="font-semibold">{e.target_id ? <a href={`#/target/${encodeURIComponent(e.target_id)}`}>{e.title}</a> : e.title}</div>
                    {e.summary && <div className="help clamp2" title={e.summary}>{e.summary}</div>}
                    {wname[e.watcher_id] && <div className="help">{wname[e.watcher_id]}</div>}
                  </td>
                  <td className="r"><Price value={e.price} currency={e.currency} old={e.type.includes("DROP") ? e.old_price : undefined} /></td>
                  <td><Confidence value={e.confidence} factors={e.data?.factors} /></td>
                  <td style={{ width: 110 }}>
                    <div className="flex flex-col items-stretch gap-1.5">
                      <ExtLink href={e.url}>{t("open")}</ExtLink>
                      {e.status !== "dismissed" && <Busy className="btn btn-sm" busy={busy[`d-${e.id}`]} onClick={() => dismiss(e)}>{t("dismiss")}</Busy>}
                      {e.status !== "dismissed" && <Busy className="btn btn-sm" busy={busy[`n-${e.id}`]} onClick={() => resend(e)}>{t("resend")}</Busy>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RunsTab({ initialWatcher }) {
  const { t, lang, dash } = useApp();
  const [watcher, setWatcher] = useState(initialWatcher || "");
  const [limit, setLimit] = useState(60);
  const { data, error, loading, reload } = useLoad(() => api.call("runs_list", { limit, ...(watcher ? { watcher_id: watcher } : {}) }), [watcher, limit]);
  const wname = Object.fromEntries((dash?.watchers || []).map((w) => [w.id, w.name]));
  const runs = data?.runs || [];
  return (
    <div className="space-y-3">
      <div className="panel flex flex-wrap items-end gap-3">
        <label className="block min-w-[200px]">
          <span className="label">{t("watcher")}</span>
          <select className="field" value={watcher} onChange={(e) => setWatcher(e.target.value)}>
            <option value="">{t("all")}</option>
            {(dash?.watchers || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="label">{t("limit")}</span>
          <select className="field" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>{[30, 60, 100, 200, 300].map((n) => <option key={n} value={n}>{n}</option>)}</select>
        </label>
        <button type="button" className="btn btn-sm" onClick={reload}>{t("refresh")}</button>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <p className="help">…</p> : !runs.length ? <Empty>{t("no_runs")}</Empty> : (
        <div className="panel scroll-x p-0">
          <table>
            <thead><tr><th>{t("time")}</th><th>{t("kind")}</th><th>{t("watcher")}</th><th className="r">{t("duration")}</th><th>{t("result")}</th><th>{t("summary")}</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td className="whitespace-nowrap" title={clock(r.started_ts, lang)}>{shortClock(r.started_ts, lang)}</td>
                  <td><Chip className="chip-accent">{t(`run_${r.kind}`) === `run_${r.kind}` ? r.kind : t(`run_${r.kind}`)}</Chip></td>
                  <td>{wname[r.watcher_id] || r.watcher_id || "—"}{r.target_id && <a className="ml-1.5" href={`#/target/${encodeURIComponent(r.target_id)}`}>{t("see_product")}</a>}</td>
                  <td className="r num">{r.finished_ts ? duration(r.finished_ts - r.started_ts) : "…"}</td>
                  <td><Chip className={r.finished_ts ? (r.ok ? "chip-ok" : "chip-danger") : "chip-amber"}>{r.finished_ts ? (r.ok ? t("ok") : t("failed")) : t("running")}</Chip></td>
                  <td style={{ maxWidth: 420 }}>
                    <details>
                      <summary className="help">{t("show")}</summary>
                      <pre className="mono mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded p-2" style={{ background: "var(--field)" }}>{typeof r.summary === "string" ? r.summary : JSON.stringify(r.summary, null, 2)}</pre>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function Events({ query }) {
  const { t } = useApp();
  const [tab, setTab] = useState(query?.get("tab") === "runs" ? "runs" : "events");
  const watcher = query?.get("watcher") || "";
  return (
    <div className="space-y-4">
      <h1>{t("nav_events")}</h1>
      <Tabs active={tab} onChange={setTab} tabs={[{ key: "events", label: t("tab_events") }, { key: "runs", label: t("tab_runs") }]} />
      {tab === "events" ? <EventsTab initialWatcher={watcher} /> : <RunsTab initialWatcher={watcher} />}
    </div>
  );
}
