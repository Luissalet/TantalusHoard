import React, { useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Busy, Check, Chip, Empty, ErrorBox, ExtLink, Price, Rel, Tabs, useBusy, useLoad } from "../components/ui.jsx";
import { clock, num, shortClock } from "../format.js";

const STATUSES = ["active", "expired", "dismissed", "all"];

function Discount({ deal }) {
  const { t } = useApp();
  if (!deal.discount_pct) return <span className="help">—</span>;
  return <Chip className={deal.discount_pct >= 50 && !deal.up_to ? "chip-ok" : "chip-accent"}>{deal.up_to ? `${t("mail_up_to")} ` : ""}-{deal.discount_pct}%</Chip>;
}

function Ends({ deal }) {
  const { t, lang } = useApp();
  if (deal.ends_known && deal.ends_ts) {
    return <div><span title={clock(deal.ends_ts, lang)}>{shortClock(deal.ends_ts, lang)}</span><div className="help"><Rel ts={deal.ends_ts} /></div></div>;
  }
  return <div className="help">{t("mail_no_end_date")}{deal.expires_ts ? <div>{t("mail_expires", { when: shortClock(deal.expires_ts, lang) })}</div> : null}</div>;
}

function Match({ deal }) {
  const { t } = useApp();
  if (deal.owned) return <Chip title={deal.match_label}>{t("mail_owned")}</Chip>;
  if (!deal.matched) return <span className="help">—</span>;
  return (
    <div className="space-y-0.5">
      <Chip className="chip-ok">{t(`mail_match_${deal.match_source}`)}</Chip>
      {deal.match_label && deal.match_source !== "store" && <div className="help">{deal.match_label}</div>}
    </div>
  );
}

function Sources({ wishlist }) {
  const { t } = useApp();
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label={t("mail_lists")}>
      <span className="label m-0">{t("mail_lists")}</span>
      {(wishlist?.sources || []).map((s) => (
        <Chip key={s.source} className={s.reachable ? "chip-ok" : "chip-amber"} title={s.detail}>
          {t(`mail_src_${s.source}`)}: {s.reachable ? t("mail_src_counts", { n: s.wanted, m: s.owned }) : t("mail_unreachable")}
        </Chip>
      ))}
    </div>
  );
}

function DealsTab() {
  const { t, lang, notify, refreshDash } = useApp();
  const [busy, run] = useBusy();
  const [status, setStatus] = useState("active");
  const [matchedOnly, setMatchedOnly] = useState(false);
  const [store, setStore] = useState("");
  const [query, setQuery] = useState("");
  const args = { status, matched_only: matchedOnly, limit: 150, ...(store ? { store } : {}), ...(query.trim() ? { query: query.trim() } : {}) };
  const { data, error, loading, reload } = useLoad(() => api.call("mail_deals", args), [status, matchedOnly, store, query]);
  const mail = data?.mail;
  const deals = data?.deals || [];
  const scan = () => run("scan", async () => {
    const r = (await api.call("mail_deals_scan", {})).result || {};
    if (r.skipped) notify(r.skipped);
    else if (!r.ok) throw new Error((r.errors || []).join("; ") || t("mail_error"));
    else notify(t("mail_scan_done", { scanned: r.scanned, deals: r.deals_new, matched: r.matched_new, notified: r.notified }) + (r.quiet ? t("mail_scan_quiet") : ""));
    await reload();
    refreshDash();
  });
  const rematch = () => run("rematch", async () => {
    const r = (await api.call("mail_deals_scan", { rematch_only: true })).result || {};
    notify(t("mail_rematch_done", { changed: r.changed ?? 0, notified: r.notified ?? 0 }));
    await reload();
    refreshDash();
  });
  const setDeal = (d, next) => run(`s-${d.id}`, async () => { await api.call("mail_deal_set", { deal_id: d.id, status: next }); await reload(); });
  return (
    <div className="space-y-3">
      <p className="help">{t("mail_intro")}</p>
      {mail && (
        <div className="panel space-y-3">
          <div className="kpis">
            <div className="kpi"><b className="num">{num(mail.counts?.active ?? 0, 0, lang)}</b><span>{t("mail_kpi_active")}</span></div>
            <div className="kpi"><b className="num">{num(mail.counts?.matched ?? 0, 0, lang)}</b><span>{t("mail_kpi_matched")}</span></div>
            <div className="kpi"><b className="num">{num(mail.counts?.mails_seen ?? 0, 0, lang)}</b><span>{t("mail_kpi_seen")}</span></div>
            <div className="kpi"><b><Rel ts={mail.last_run_ts} /></b><span>{t("mail_last_scan")}</span></div>
            <div className="kpi"><b>{mail.enabled ? <Rel ts={mail.next_run_ts} /> : "—"}</b><span>{t("mail_next_scan")}</span></div>
          </div>
          {!mail.enabled && <div className="banner banner-warn" role="status">{t("mail_disabled")}</div>}
          {mail.enabled && !mail.first_scan_done && <div className="banner banner-info" role="status">{t("mail_first_scan_pending")}</div>}
          {mail.last_error && <div className="banner banner-danger" role="alert">{t("mail_error")}: {mail.last_error}</div>}
          <Sources wishlist={mail.wishlist} />
          <div className="flex flex-wrap items-center gap-2">
            <Busy className="btn btn-primary btn-sm" busy={busy.scan || mail.running} onClick={scan}>{t("mail_scan_now")}</Busy>
            <Busy className="btn btn-sm" busy={busy.rematch} onClick={rematch}>{t("mail_rematch")}</Busy>
            <button type="button" className="btn btn-sm" onClick={reload}>{t("refresh")}</button>
          </div>
        </div>
      )}
      <div className="panel flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="label">{t("status")}</span>
          <select className="field" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value)}>{STATUSES.map((s) => <option key={s} value={s}>{t(`mst_${s}`)}</option>)}</select>
        </label>
        <label className="block">
          <span className="label">{t("mail_store")}</span>
          <select className="field" style={{ width: "auto" }} value={store} onChange={(e) => setStore(e.target.value)}>
            <option value="">{t("all")}</option>
            {(mail?.stores || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label className="block min-w-[200px]">
          <span className="label">{t("mail_search")}</span>
          <input className="field" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <div className="pb-1.5"><Check checked={matchedOnly} onChange={setMatchedOnly}>{t("mail_matched_only")}</Check></div>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <p className="help">…</p> : !deals.length ? <Empty>{t("mail_no_deals")}</Empty> : (
        <div className="panel scroll-x p-0">
          <table>
            <thead><tr><th>{t("mail_store")}</th><th>{t("mail_col_deal")}</th><th>{t("mail_col_discount")}</th><th className="r">{t("price")}</th><th>{t("mail_col_ends")}</th><th>{t("mail_col_match")}</th><th style={{ width: 110 }} /></tr></thead>
            <tbody>
              {deals.map((d) => (
                <tr key={d.id} style={{ opacity: d.status === "active" ? 1 : 0.55 }}>
                  <td className="whitespace-nowrap font-semibold">{d.store}<div className="help">{t("mail_age", { d: d.age_days })}</div></td>
                  <td style={{ minWidth: 240, maxWidth: 460 }}>
                    <div className="font-semibold">{d.title}</div>
                    {d.kind === "campaign" && d.titles?.length > 0 && <div className="help clamp2" title={d.titles.join(" · ")}>{d.titles.join(" · ")}</div>}
                  </td>
                  <td><Discount deal={d} /></td>
                  <td className="r"><Price value={d.price} currency={d.currency} old={d.old_price} /></td>
                  <td><Ends deal={d} /></td>
                  <td><Match deal={d} /></td>
                  <td style={{ width: 110 }}>
                    <div className="flex flex-col items-stretch gap-1.5">
                      <ExtLink href={d.url}>{t("open")}</ExtLink>
                      {d.status === "active"
                        ? <Busy className="btn btn-sm" busy={busy[`s-${d.id}`]} onClick={() => setDeal(d, "dismissed")}>{t("dismiss")}</Busy>
                        : d.status === "dismissed" && <Busy className="btn btn-sm" busy={busy[`s-${d.id}`]} onClick={() => setDeal(d, "active")}>{t("mail_restore")}</Busy>}
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

function Unsubscribe({ info }) {
  const { t } = useApp();
  if (!info?.available) return <span className="help">{t("noise_unsub_none")}</span>;
  // Shown as text on purpose: the app never opens or follows an unsubscribe link, and a click on a one-click link could unsubscribe at once.
  return (
    <details>
      <summary className="help">{t("noise_unsub_show")}{info.one_click ? ` · ${t("noise_one_click")}` : ""}</summary>
      {info.http && <div className="mono mt-1 max-w-[360px] select-all break-all">{info.http}</div>}
      {info.mailto && <div className="mono mt-1 max-w-[360px] select-all break-all">{info.mailto}</div>}
    </details>
  );
}

function NoiseTab() {
  const { t, lang } = useApp();
  const [days, setDays] = useState(30);
  const [onlyNoise, setOnlyNoise] = useState(true);
  const [nonce, setNonce] = useState(0);
  const { data, error, loading } = useLoad(() => api.call("mail_noise_report", { days, top: 60, refresh: nonce > 0 }), [days, nonce]);
  const rows = (onlyNoise ? data?.noise : data?.top) || [];
  const biggest = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div className="space-y-3">
      <p className="help">{t("noise_readonly")}</p>
      <div className="panel flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="label">{t("noise_window")}</span>
          <select className="field" style={{ width: "auto" }} value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 180].map((n) => <option key={n} value={n}>{t("noise_days", { n })}</option>)}</select>
        </label>
        <div className="pb-1.5"><Check checked={onlyNoise} onChange={setOnlyNoise}>{t("noise_only")}</Check></div>
        <button type="button" className="btn btn-sm" onClick={() => setNonce((n) => n + 1)} disabled={loading}>{t("noise_refresh")}</button>
        {loading && <span className="help" role="status">{t("noise_reading")}</span>}
        {data?.ok && data.cached && <span className="help">{t("noise_cached")}</span>}
      </div>
      <ErrorBox error={error} />
      {data && !data.ok && <div className="banner banner-danger" role="alert">{t("noise_error")}: {data.error}</div>}
      {data?.ok && (
        <>
          <div className="kpis">
            <div className="kpi"><b className="num">{num(data.inbound, 0, lang)}</b><span>{t("noise_inbound")}</span></div>
            <div className="kpi"><b className="num">{num(data.promotional, 0, lang)} · {num(data.promotional_share * 100, 0, lang)}%</b><span>{t("noise_promo")}</span></div>
            <div className="kpi"><b className="num">{num(data.noise_count, 0, lang)}</b><span>{t("noise_noise")}</span></div>
            <div className="kpi"><b className="num">{num(data.noise_domains, 0, lang)}</b><span>{t("noise_domains")}</span></div>
          </div>
          <p className="help">{t("noise_unsub_hint")} {t("noise_reader_hint")}</p>
          {!rows.length ? <Empty>{t("noise_empty")}</Empty> : (
            <div className="panel scroll-x p-0">
              <table>
                <thead><tr><th>{t("noise_col_domain")}</th><th className="r">{t("noise_col_count")}</th><th style={{ width: 120 }}>{t("noise_col_share")}</th><th>{t("noise_col_cat")}</th><th>{t("noise_col_unsub")}</th><th>{t("noise_col_last")}</th><th>{t("noise_col_reader")}</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.domain}>
                      <td><div className="font-semibold">{r.domain}</div><div className="help clamp2" title={r.senders.join(", ")}>{r.senders.join(", ")}</div></td>
                      <td className="r num">{num(r.count, 0, lang)}<div className="help">{t("noise_promo_n", { n: r.promotional })}</div></td>
                      <td>
                        <div aria-hidden="true" style={{ height: 6, borderRadius: 3, background: "var(--field)" }}><div style={{ width: `${Math.max(3, (r.count / biggest) * 100)}%`, height: 6, borderRadius: 3, background: "var(--accent)" }} /></div>
                        <div className="help">{num(r.share * 100, 1, lang)}%</div>
                      </td>
                      <td className="help">{Object.entries(r.categories).map(([k, v]) => `${k} ${v}`).join(" · ")}</td>
                      <td><Unsubscribe info={r.unsubscribe} /></td>
                      <td className="whitespace-nowrap">{r.last_date || "—"}</td>
                      <td>{r.consumers.length ? r.consumers.map((c) => <Chip key={c} className="chip-ok">{c}</Chip>) : <Chip className="chip-amber">{t("noise_none")}</Chip>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function Mail({ query }) {
  const { t } = useApp();
  const [tab, setTab] = useState(query?.get("tab") === "noise" ? "noise" : "deals");
  return (
    <div className="space-y-4">
      <h1>{t("nav_mail")}</h1>
      <Tabs active={tab} onChange={setTab} tabs={[{ key: "deals", label: t("tab_mail_deals") }, { key: "noise", label: t("tab_mail_noise") }]} />
      {tab === "deals" ? <DealsTab /> : <NoiseTab />}
    </div>
  );
}
