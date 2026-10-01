import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Busy, Chip, Empty, ErrorBox, ExtLink, Field, Icon, Price, ScoreBadge, StatePill, Tabs, Thumb, useBusy } from "../components/ui.jsx";
import { clock, hostOf, num, parseNum, safeUrl } from "../format.js";

function FetchInfo({ fetch: fr }) {
  const { t, lang } = useApp();
  const items = [
    [t("http_status"), fr.status || "—"], [t("tier"), fr.tier], [t("elapsed"), fr.elapsed_ms != null ? `${fr.elapsed_ms} ms` : "—"],
    [t("content_type"), fr.content_type || "—"], [t("text_len"), fr.text_len != null ? num(fr.text_len, 0, lang) : "—"], [t("fetched_at"), clock(fr.fetched_at, lang)],
  ];
  return (
    <div className="panel space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3>{t("fetch_info")}</h3>
        <Chip className={fr.ok ? "chip-ok" : "chip-danger"}>{fr.ok ? t("fetch_ok") : t("fetch_failed")}</Chip>
        {fr.blocked && <Chip className="chip-danger">{t("blocked")}: {fr.block_reason || "?"}</Chip>}
        {fr.not_modified && <Chip>{t("not_modified")}</Chip>}
      </div>
      <dl className="m-0 grid gap-x-6 gap-y-1 md:grid-cols-3">
        {items.map(([k, v]) => <div key={k} className="grid grid-cols-[110px_minmax(0,1fr)] gap-2"><dt className="help">{k}</dt><dd className="m-0 break-words">{String(v)}</dd></div>)}
      </dl>
      {fr.final_url && fr.final_url !== fr.url && <div className="help break-words">→ {fr.final_url}</div>}
      {fr.error && <div style={{ color: "#ffb9ac" }}>{fr.error}</div>}
    </div>
  );
}

function InspectTab() {
  const { t } = useApp();
  const [busy, run] = useBusy();
  const [f, setF] = useState({ url: "", tier: "auto", sku: "", show_text: false });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const submit = (e) => {
    e.preventDefault();
    if (!/^(https?:\/\/|nvidia-api:)/i.test(f.url.trim())) { setError(new Error(t("err_url"))); return; }
    setError(null);
    run("inspect", async () => {
      try {
        setResult(await api.call("inspect_url", { url: f.url.trim(), tier: f.tier, sku: f.sku.trim(), show_text: f.show_text }));
      } catch (err) {
        setResult(null);
        setError(err);
        throw err;
      }
    });
  };
  return (
    <div className="space-y-4">
      <form className="panel space-y-3" onSubmit={submit} noValidate>
        <p className="help">{t("inspect_hint")}</p>
        <div className="grid gap-3 md:grid-cols-[minmax(0,3fr)_140px_minmax(0,1fr)]">
          <Field label="URL"><input className="field" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://…" required /></Field>
          <Field label={t("fetch_tier")}><select className="field" value={f.tier} onChange={(e) => setF({ ...f, tier: e.target.value })}>{["auto", "http", "browser", "window"].map((x) => <option key={x} value={x}>{t(`tier_${x}`)}</option>)}</select></Field>
          <Field label="SKU"><input className="field" value={f.sku} onChange={(e) => setF({ ...f, sku: e.target.value })} /></Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Busy type="submit" className="btn btn-primary" busy={busy.inspect}>{t("inspect")}</Busy>
          <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={f.show_text} onChange={(e) => setF({ ...f, show_text: e.target.checked })} />{t("show_text")}</label>
          {busy.inspect && <span className="help">{t("may_take_long")}</span>}
        </div>
      </form>
      <ErrorBox error={error} />
      {result && (
        <div className="space-y-4">
          <FetchInfo fetch={result.fetch} />
          <div className="panel space-y-1">
            <div className="flex flex-wrap items-center gap-2"><h3>{result.title || hostOf(f.url)}</h3><Chip>{t("page_kind")}: {result.page_kind}</Chip>{(result.methods || []).map((m) => <Chip key={m} className="chip-accent">{m}</Chip>)}</div>
            {(result.notes || []).length > 0 && <ul className="help m-0 list-disc pl-5">{result.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
          </div>
          <div className="space-y-2">
            <div className="flex items-center gap-2"><h2>{t("offers")}</h2><span className="chip">{result.offer_count}</span></div>
            {!result.offers.length ? <Empty>{t("no_offers")}</Empty> : (
              <div className="panel scroll-x p-0">
                <table>
                  <thead><tr><th /><th>{t("product")}</th><th>{t("state")}</th><th className="r">{t("price")}</th><th>{t("seller")}</th><th>{t("method")}</th><th>{t("evidence")}</th></tr></thead>
                  <tbody>
                    {result.offers.map((o, i) => (
                      <tr key={i}>
                        <td style={{ width: 1 }}><Thumb src={o.image} /></td>
                        <td style={{ minWidth: 220 }}>
                          <div className="font-semibold">{safeUrl(o.url) ? <a href={o.url} target="_blank" rel="noopener noreferrer">{o.title || o.url}</a> : o.title || "—"}</div>
                          <div className="help">{[o.brand, o.sku && `SKU ${o.sku}`, o.ean && `EAN ${o.ean}`].filter(Boolean).join(" · ")}</div>
                        </td>
                        <td>
                          <StatePill state={o.availability} />
                          {o.buy_button && <div className="help">{t("buy_button")}</div>}
                          {(o.preorder_date || o.restock_date) && <div className="help">{o.preorder_date || o.restock_date}</div>}
                        </td>
                        <td className="r"><Price value={o.price} currency={o.currency} /></td>
                        <td>
                          {o.seller || "—"}
                          {o.seller_is_retailer === true && <Chip className="chip-ok ml-1.5">{t("retailer")}</Chip>}
                          {o.seller_is_retailer === false && <Chip className="chip-amber ml-1.5">{t("third_party")}</Chip>}
                        </td>
                        <td className="mono">{o.method}</td>
                        <td style={{ maxWidth: 360 }}>
                          {(o.evidence || []).length > 0 && (
                            <details><summary className="help">{t("n_snippets", { n: o.evidence.length })}</summary><ul className="help m-0 mt-1 list-disc pl-4">{o.evidence.map((e, j) => <li key={j} className="break-words">“{e}”</li>)}</ul></details>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {result.text && (
            <details className="panel" open>
              <summary className="font-semibold">{t("page_text")}</summary>
              <pre className="mono mt-2 max-h-80 overflow-auto whitespace-pre-wrap">{result.text}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function SecondhandTab() {
  const { t, lang, packs, loadPacks, setDraft } = useApp();
  const [busy, run] = useBusy();
  const [f, setF] = useState({ query: "", source: "wallapop", pack: "generic", origin_location: "Madrid", radius_km: "30", max_price: "", msrp: "", limit: "20" });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { loadPacks(); }, [loadPacks]);
  const inp = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const submit = (e) => {
    e.preventDefault();
    if (f.query.trim().length < 2) { setError(new Error(t("err_query"))); return; }
    setError(null);
    run("sh", async () => {
      const args = { query: f.query.trim(), source: f.source, pack: f.pack, origin_location: f.origin_location.trim() || "Madrid", radius_km: parseNum(f.radius_km) ?? 30, limit: Math.round(parseNum(f.limit) ?? 20) };
      const mp = parseNum(f.max_price); if (mp !== undefined) args.max_price = mp;
      const ms = parseNum(f.msrp); if (ms !== undefined) args.msrp = ms;
      try {
        setResult({ ...(await api.call("secondhand_search", args)), args });
      } catch (err) {
        setResult(null);
        setError(err);
        throw err;
      }
    });
  };
  const watch = () => {
    const a = result?.args || {};
    const settings = { origin_location: a.origin_location, radius_km: a.radius_km };
    if (a.max_price !== undefined) settings.price_ceiling = a.max_price;
    if (a.msrp !== undefined) settings.msrp = a.msrp;
    setDraft({ name: `${a.query} (${t(`src_${a.source}`)})`, pack: a.pack, sources: [a.source], queries: [a.query], settings });
    window.location.hash = "#/watchers/new";
  };
  const rows = result?.results || [];
  return (
    <div className="space-y-4">
      <form className="panel space-y-3" onSubmit={submit} noValidate>
        <p className="help">{t("secondhand_hint")}</p>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Field label={t("query")} className="xl:col-span-2"><input className="field" value={f.query} onChange={inp("query")} required /></Field>
          <Field label={t("source")}><select className="field" value={f.source} onChange={inp("source")}><option value="wallapop">{t("src_wallapop")}</option><option value="facebook">{t("src_facebook")}</option></select></Field>
          <Field label={t("pack")}>
            <select className="field" value={f.pack} onChange={inp("pack")}>
              {(packs || [{ id: "generic", name_es: "generic", name_en: "generic" }]).map((p) => <option key={p.id} value={p.id}>{lang === "en" ? p.name_en : p.name_es}</option>)}
            </select>
          </Field>
          <Field label={t("origin_location")}><input className="field" value={f.origin_location} onChange={inp("origin_location")} /></Field>
          <Field label={t("radius_km")}><input className="field num" inputMode="decimal" value={f.radius_km} onChange={inp("radius_km")} /></Field>
          <Field label={t("max_price")}><input className="field num" inputMode="decimal" value={f.max_price} onChange={inp("max_price")} /></Field>
          <Field label={t("msrp")}><input className="field num" inputMode="decimal" value={f.msrp} onChange={inp("msrp")} /></Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Busy type="submit" className="btn btn-primary" busy={busy.sh}>{t("search")}</Busy>
          {busy.sh && <span className="help">{t("may_take_long")}</span>}
        </div>
      </form>
      <ErrorBox error={error} />
      {result && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2>{t("results")}</h2><span className="chip">{rows.length}</span>
            <button type="button" className="btn btn-primary ml-auto" onClick={watch}><Icon d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z" size={14} />{t("watch_this_search")}</button>
          </div>
          {result.error && <div className="banner banner-warn">{result.error}</div>}
          {!rows.length ? <Empty>{t("no_results")}</Empty> : (
            <div className="panel scroll-x p-0">
              <table>
                <thead><tr><th>{t("score")}</th><th>{t("title")}</th><th className="r">{t("price")}</th><th className="r">{t("distance")}</th><th>{t("location")}</th><th>{t("reason")}</th></tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} style={{ opacity: r.relevant ? 1 : 0.6 }}>
                      <td><ScoreBadge score={r.score} /></td>
                      <td style={{ minWidth: 240 }}>
                        <div className="font-semibold">{safeUrl(r.url) ? <a href={r.url} target="_blank" rel="noopener noreferrer">{r.title}</a> : r.title}</div>
                        <div className="flex gap-1 pt-0.5">{r.reserved && <Chip className="chip-amber">{t("reserved")}</Chip>}{r.shipping && <Chip>{t("shipping")}</Chip>}{!r.relevant && <Chip>{t("not_relevant")}</Chip>}</div>
                      </td>
                      <td className="r"><Price value={r.price} currency="EUR" /></td>
                      <td className="r num">{r.distance_km != null ? `${num(r.distance_km, 0, lang)} km` : "—"}</td>
                      <td>{r.location || "—"}</td>
                      <td className="help" style={{ maxWidth: 340 }}>{r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WebTab() {
  const { t } = useApp();
  const [busy, run] = useBusy();
  const [f, setF] = useState({ query: "", limit: "10", freshness_days: "" });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const submit = (e) => {
    e.preventDefault();
    if (f.query.trim().length < 2) { setError(new Error(t("err_query"))); return; }
    setError(null);
    run("web", async () => {
      const args = { query: f.query.trim(), limit: Math.round(parseNum(f.limit) ?? 10) };
      const fd = parseNum(f.freshness_days); if (fd !== undefined) args.freshness_days = Math.round(fd);
      try {
        setResult(await api.call("web_search", args));
      } catch (err) {
        setResult(null);
        setError(err);
        throw err;
      }
    });
  };
  const hits = result?.hits || [];
  const errors = result?.errors;
  const errorList = Array.isArray(errors) ? errors : errors && typeof errors === "object" ? Object.entries(errors).map(([k, v]) => `${k}: ${v}`) : errors ? [String(errors)] : [];
  return (
    <div className="space-y-4">
      <form className="panel space-y-3" onSubmit={submit} noValidate>
        <p className="help">{t("web_hint")}</p>
        <div className="grid gap-3 md:grid-cols-[minmax(0,3fr)_110px_150px]">
          <Field label={t("query")}><input className="field" value={f.query} onChange={(e) => setF({ ...f, query: e.target.value })} required /></Field>
          <Field label={t("limit")}><input className="field num" inputMode="numeric" value={f.limit} onChange={(e) => setF({ ...f, limit: e.target.value })} /></Field>
          <Field label={t("freshness_days")}><input className="field num" inputMode="numeric" value={f.freshness_days} onChange={(e) => setF({ ...f, freshness_days: e.target.value })} /></Field>
        </div>
        <div className="flex items-center gap-3"><Busy type="submit" className="btn btn-primary" busy={busy.web}>{t("search")}</Busy>{busy.web && <span className="help">{t("may_take_long")}</span>}</div>
      </form>
      <ErrorBox error={error} />
      {result && (
        <div className="space-y-3">
          {errorList.length > 0 && <div className="banner banner-warn"><strong>{t("engine_errors")}</strong><ul className="m-0 list-disc pl-5">{errorList.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
          <div className="flex items-center gap-2"><h2>{t("results")}</h2><span className="chip">{hits.length}</span></div>
          {!hits.length ? <Empty>{t("no_results")}</Empty> : (
            <ol className="m-0 list-none space-y-2 p-0">
              {hits.map((h, i) => (
                <li key={i} className="panel space-y-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Chip className="chip-accent">{h.engine || "?"}</Chip><Chip>#{h.rank}</Chip>{h.published && <span className="help">{h.published}</span>}
                    <span className="help ml-auto">{hostOf(h.url)}</span>
                  </div>
                  <h3>{safeUrl(h.url) ? <a href={h.url} target="_blank" rel="noopener noreferrer">{h.title || h.url}</a> : h.title}</h3>
                  {h.snippet && <p className="help">{h.snippet}</p>}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

export default function Search({ query }) {
  const { t } = useApp();
  const [tab, setTab] = useState(query?.get("tab") || "url");
  return (
    <div className="space-y-4">
      <h1>{t("nav_search")}</h1>
      <Tabs active={tab} onChange={setTab} tabs={[{ key: "url", label: t("tab_url") }, { key: "secondhand", label: t("tab_secondhand") }, { key: "web", label: t("tab_web") }]} />
      <div hidden={tab !== "url"}><InspectTab /></div>
      <div hidden={tab !== "secondhand"}><SecondhandTab /></div>
      <div hidden={tab !== "web"}><WebTab /></div>
    </div>
  );
}
