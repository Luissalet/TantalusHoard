import React, { useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { EventCard } from "../components/cards.jsx";
import { Busy, Chip, Confidence, Empty, ErrorBox, ExtLink, Field, Icon, PriceChart, Price, Rel, Section, StatePill, Thumb, useBusy, useLoad } from "../components/ui.jsx";
import { clock, hostOf, joinList, parseNum, shortClock, splitList } from "../format.js";
import { SELLER_POLICIES } from "../meta.js";

const go = (hash) => { window.location.hash = hash; };

function TargetEditForm({ target, onSaved, onCancel }) {
  const { t, notify } = useApp();
  const [busy, run] = useBusy();
  const s = (v) => (v === null || v === undefined ? "" : String(v));
  const [f, setF] = useState({
    label: s(target.label), url: s(target.url), retailer: s(target.retailer), sku: s(target.sku), ean: s(target.ean), store_ids: joinList(target.store_ids),
    msrp: s(target.msrp), price_ceiling: s(target.price_ceiling), price_threshold: s(target.price_threshold), seller_policy: target.seller_policy || "retail_only",
    fetch_tier: target.fetch_tier || "auto", adapter: s(target.adapter) || "auto", interval_min: s(target.interval_min), status: target.status === "paused" ? "paused" : "active",
  });
  const [error, setError] = useState(null);
  const inp = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const submit = (e) => {
    e.preventDefault();
    if (!/^(https?:\/\/|nvidia-api:)/i.test(f.url.trim())) { setError(new Error(t("err_url"))); return; }
    setError(null);
    run("save", async () => {
      const args = {
        target_id: target.id, label: f.label.trim(), url: f.url.trim(), retailer: f.retailer.trim(),
        store_ids: splitList(f.store_ids), seller_policy: f.seller_policy, fetch_tier: f.fetch_tier, adapter: f.adapter.trim() || "auto", status: f.status,
      };
      const clear = [];
      for (const k of ["msrp", "price_ceiling", "price_threshold", "interval_min"]) {
        const n = parseNum(f[k]);
        if (n !== undefined) args[k] = k === "interval_min" ? Math.round(n) : n;
        else if (target[k] !== null && target[k] !== undefined) clear.push(k);
      }
      for (const k of ["sku", "ean"]) {
        const v = f[k].trim();
        if (v) args[k] = v; else if (target[k]) clear.push(k);
      }
      if (clear.length) args.clear = clear;
      await api.call("target_update", args);
      notify(t("target_saved"));
      onSaved?.();
    });
  };
  return (
    <form className="panel space-y-3" onSubmit={submit} noValidate>
      <h2>{t("edit_target")}</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Field label="URL" className="md:col-span-2"><input className="field" value={f.url} onChange={inp("url")} required /></Field>
        <Field label={t("label")}><input className="field" value={f.label} onChange={inp("label")} /></Field>
        <Field label={t("retailer")}><input className="field" value={f.retailer} onChange={inp("retailer")} /></Field>
        <Field label="SKU"><input className="field" value={f.sku} onChange={inp("sku")} /></Field>
        <Field label="EAN"><input className="field" value={f.ean} onChange={inp("ean")} /></Field>
        <Field label={t("store_ids")} hint={t("list_hint")}><input className="field" value={f.store_ids} onChange={inp("store_ids")} /></Field>
        <Field label={t("msrp")}><input className="field num" inputMode="decimal" value={f.msrp} onChange={inp("msrp")} /></Field>
        <Field label={t("price_ceiling")}><input className="field num" inputMode="decimal" value={f.price_ceiling} onChange={inp("price_ceiling")} /></Field>
        <Field label={t("price_threshold")}><input className="field num" inputMode="decimal" value={f.price_threshold} onChange={inp("price_threshold")} /></Field>
        <Field label={t("seller_policy")}>
          <select className="field" value={f.seller_policy} onChange={inp("seller_policy")}>{SELLER_POLICIES.map((p) => <option key={p} value={p}>{t(`seller_${p}`)}</option>)}</select>
        </Field>
        <Field label={t("fetch_tier")} hint={t("fetch_tier_hint")}>
          <select className="field" value={f.fetch_tier} onChange={inp("fetch_tier")}>{["auto", "http", "browser"].map((p) => <option key={p} value={p}>{t(`tier_${p}`)}</option>)}</select>
        </Field>
        <Field label={t("adapter")}>
          <select className="field" value={f.adapter} onChange={inp("adapter")}>{["auto", "html", "nvidia"].map((p) => <option key={p} value={p}>{p}</option>)}</select>
        </Field>
        <Field label={t("interval_min")} hint={t("target_interval_hint")}><input className="field num" inputMode="numeric" value={f.interval_min} onChange={inp("interval_min")} /></Field>
        <Field label={t("status")}>
          <select className="field" value={f.status} onChange={inp("status")}><option value="active">{t("active")}</option><option value="paused">{t("paused")}</option></select>
        </Field>
      </div>
      <ErrorBox error={error} />
      <div className="flex gap-2">
        <Busy type="submit" className="btn btn-primary" busy={busy.save}>{t("save")}</Busy>
        <button type="button" className="btn" onClick={onCancel}>{t("cancel")}</button>
      </div>
    </form>
  );
}

function ObservationsTable({ observations: all }) {
  const { t, lang } = useApp();
  const [shown, setShown] = useState(40);
  const observations = all.slice(0, shown);
  if (!all.length) return <Empty>{t("no_observations")}</Empty>;
  return (
    <div className="space-y-2">
    <div className="panel scroll-x p-0">
      <table>
        <thead>
          <tr><th>{t("time")}</th><th>{t("tier")}</th><th className="r">HTTP</th><th>{t("state")}</th><th className="r">{t("price")}</th><th>{t("seller")}</th><th>{t("confidence")}</th><th>{t("method")}</th><th>{t("evidence")}</th></tr>
        </thead>
        <tbody>
          {observations.map((o) => (
            <tr key={o.id}>
              <td className="whitespace-nowrap" title={clock(o.checked_at, lang)}>{shortClock(o.checked_at, lang)}{o.is_revalidation && <Chip className="chip-amber ml-1.5">{t("revalidation")}</Chip>}</td>
              <td>{o.tier || "—"}</td>
              <td className="r num">{o.http_status || "—"}</td>
              <td><StatePill state={o.availability} /></td>
              <td className="r"><Price value={o.price} currency={o.currency} /></td>
              <td>
                {o.seller || "—"}
                {o.seller_is_retailer === true && <Chip className="chip-ok ml-1.5">{t("retailer")}</Chip>}
                {o.seller_is_retailer === false && <Chip className="chip-amber ml-1.5">{t("third_party")}</Chip>}
                {o.buy_button === true && <Chip className="ml-1.5">{t("buy_button")}</Chip>}
              </td>
              <td><Confidence value={o.confidence} factors={o.factors} /></td>
              <td className="mono whitespace-nowrap">{o.method || "—"}</td>
              <td style={{ minWidth: 220, maxWidth: 420 }}>
                {(o.blocked_reason || o.error) && <div style={{ color: "#ffb9ac" }}>{o.blocked_reason ? `${t("blocked")}: ${o.blocked_reason}` : ""} {o.error}</div>}
                {Array.isArray(o.evidence) && o.evidence.length > 0 && (
                  <details>
                    <summary className="help">{t("n_snippets", { n: o.evidence.length })}</summary>
                    <ul className="help m-0 mt-1 list-disc pl-4">{o.evidence.map((e, i) => <li key={i} className="break-words">“{e}”</li>)}</ul>
                  </details>
                )}
                {(o.preorder_date || o.restock_date) && <div className="help">{o.preorder_date && `${t("preorder_date")}: ${o.preorder_date} `}{o.restock_date && `${t("restock_date")}: ${o.restock_date}`}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    {all.length > shown && <button type="button" className="btn btn-sm" onClick={() => setShown((n) => n + 60)}>{t("show_more", { n: all.length - shown })}</button>}
    </div>
  );
}

export default function Target({ param: id, sub }) {
  const { t, dash, refreshDash, notify, confirm } = useApp();
  const [busy, run] = useBusy();
  const editing = sub === "edit";
  const hist = useLoad(() => api.history(id, 300), [id]);
  const evs = useLoad(() => api.call("events_list", { target_id: id, limit: 50 }), [id]);
  const reload = () => { hist.reload(); evs.reload(); refreshDash(); };
  if (!id) { go("#/watchers"); return null; }
  if (hist.error && !hist.data) return <div className="space-y-3"><a className="btn btn-sm" href="#/watchers">← {t("nav_watchers")}</a><ErrorBox error={hist.error} /></div>;
  if (!hist.data) return <p className="help">…</p>;

  const { target: tg, observations, price_history: prices } = hist.data;
  const watcher = dash?.watchers.find((w) => w.id === tg.watcher_id);
  const check = () => run("check", async () => {
    const r = await api.call("target_check", { target_id: tg.id });
    notify(r.ok === false ? `${t("check_failed")}: ${r.error || r.blocked || ""}` : `${t("check_done")}: ${r.state ? t(`state_${r.state}`) : "OK"}`);
    reload();
  });
  const resolve = () => run("resolve", async () => {
    const r = await api.call("target_resolve", { target_id: tg.id });
    notify(r.check?.ok ? t("resolve_done") : `${t("resolve_done")}${r.check?.error ? ` — ${r.check.error}` : ""}`);
    reload();
  });
  const pause = () => run("pause", async () => {
    await api.call("target_update", { target_id: tg.id, status: tg.status === "paused" ? "active" : "paused" });
    notify(tg.status === "paused" ? t("target_resumed") : t("target_paused"));
    reload();
  });
  const remove = async () => {
    const ok = await confirm({ title: t("delete_target"), message: t("delete_target_msg", { name: tg.label || tg.last_title || hostOf(tg.url) }) });
    if (!ok) return;
    run("delete", async () => {
      await api.call("target_delete", { id: tg.id, confirm: true });
      notify(t("target_deleted"));
      refreshDash();
      go(`#/watchers/${encodeURIComponent(tg.watcher_id)}`);
    });
  };
  const events = evs.data?.events || [];
  const info = [
    [t("retailer"), tg.retailer || tg.host], ["SKU", tg.sku], ["EAN", tg.ean], [t("msrp"), tg.msrp != null ? <Price value={tg.msrp} currency={tg.last_currency} className="font-normal" /> : ""],
    [t("price_ceiling"), tg.price_ceiling != null ? <Price value={tg.price_ceiling} currency={tg.last_currency} className="font-normal" /> : ""],
    [t("price_threshold"), tg.price_threshold != null ? <Price value={tg.price_threshold} currency={tg.last_currency} className="font-normal" /> : ""],
    [t("min_price"), tg.min_price != null ? <Price value={tg.min_price} currency={tg.last_currency} className="font-normal" /> : ""],
    [t("seller_policy"), t(`seller_${tg.seller_policy}`)], [t("fetch_tier"), t(`tier_${tg.fetch_tier}`)], [t("adapter"), tg.adapter],
    [t("interval_min"), tg.interval_min ? `${tg.interval_min} min` : t("watcher_default")], [t("stores"), joinList(tg.store_ids)],
  ].filter(([, v]) => v !== "" && v !== null && v !== undefined);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <a className="btn btn-sm max-w-full" href={`#/watchers/${encodeURIComponent(tg.watcher_id)}`}>←&nbsp;<span className="trunc">{watcher?.name || t("nav_watchers")}</span></a>
        <div className="flex gap-3">
          <Thumb src={tg.last_image} large />
          <div className="min-w-0 flex-1 space-y-1.5">
            <h1 className="break-words">{tg.label || tg.last_title || hostOf(tg.url)}</h1>
            {tg.label && tg.last_title && tg.label !== tg.last_title && <div className="help">{tg.last_title}</div>}
            <div className="flex flex-wrap items-center gap-2">
              <StatePill state={tg.last_state} />
              <Price value={tg.last_price} currency={tg.last_currency} />
              <Confidence value={tg.last_confidence} />
              {tg.status === "needs_human" && <Chip className="chip-danger">{t("needs_human")}</Chip>}
              {tg.status === "paused" && <Chip>{t("paused")}</Chip>}
              {tg.status === "error" && <Chip className="chip-danger">{t("tg_error")}</Chip>}
              <span className="help">{t("last_check")}: <Rel ts={tg.last_check_ts} /></span>
            </div>
            <div className="help trunc" title={tg.url}>{tg.url}</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Busy className="btn btn-primary" busy={busy.check} onClick={check}>{t("check_now")}</Busy>
          {tg.status === "needs_human" && <Busy className="btn btn-primary" busy={busy.resolve} onClick={resolve}>{t("resolve")}</Busy>}
          {!editing && <a className="btn" href={`#/target/${encodeURIComponent(tg.id)}/edit`}>{t("edit")}</a>}
          <Busy className="btn" busy={busy.pause} onClick={pause}>{tg.status === "paused" ? t("resume") : t("pause")}</Busy>
          <ExtLink href={tg.url} className="btn"><Icon d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" size={13} />{t("open")}</ExtLink>
          <Busy className="btn btn-danger" busy={busy.delete} onClick={remove}>{t("delete")}</Busy>
        </div>
        {(busy.check || busy.resolve) && <p className="help" role="status">{busy.resolve ? t("resolve_waiting") : t("may_take_long")}</p>}
        {tg.last_error && <div className="banner banner-danger">{tg.last_error}</div>}
      </header>

      {editing && <TargetEditForm target={tg} onCancel={() => go(`#/target/${encodeURIComponent(tg.id)}`)} onSaved={() => { reload(); go(`#/target/${encodeURIComponent(tg.id)}`); }} />}

      {!editing && (
        <div className="panel">
          <dl className="m-0 grid gap-x-6 gap-y-1.5 md:grid-cols-2 xl:grid-cols-3">
            {info.map(([k, v]) => (
              <div key={k} className="grid grid-cols-[120px_minmax(0,1fr)] gap-2"><dt className="help">{k}</dt><dd className="m-0 break-words">{v}</dd></div>
            ))}
          </dl>
        </div>
      )}

      <Section id="sec-chart" title={t("price_history")} count={prices.length}>
        <div className="panel"><PriceChart points={prices} threshold={tg.price_threshold ?? undefined} minPrice={tg.min_price ?? undefined} /></div>
      </Section>

      <Section id="sec-obs" title={t("observations")} count={observations.length}>
        <ObservationsTable observations={observations} />
      </Section>

      <Section id="sec-t-events" title={t("sec_events")} count={events.length}>
        <ErrorBox error={evs.error} />
        {evs.data && !events.length && <Empty>{t("no_events")}</Empty>}
        <div className="card-grid">{events.map((e) => <EventCard key={e.id} event={e} compact onChanged={reload} />)}</div>
      </Section>
    </div>
  );
}
