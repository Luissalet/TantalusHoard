import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { CandidateRow, EventCard, InfoRow, ListingCard } from "../components/cards.jsx";
import WatcherForm from "../components/WatcherForm.jsx";
import { Busy, Chip, Confidence, Empty, ErrorBox, ExtLink, Field, Icon, ModeChip, Price, Rel, Section, StatePill, Switch, Tabs, useBusy, useLoad } from "../components/ui.jsx";
import { clock, hostOf, joinList, parseNum, safeUrl, splitLines } from "../format.js";
import { MODE_META, SELLER_POLICIES } from "../meta.js";

const go = (hash) => { window.location.hash = hash; };

// ------------------------------------------------------------------ list
function Presets({ onInstalled }) {
  const { t, notify } = useApp();
  const [busy, run] = useBusy();
  const { data, error, reload } = useLoad(() => api.call("presets_list"), []);
  const install = (ids) => run(ids ? ids[0] : "all", async () => {
    const r = await api.call("presets_install", ids ? { ids } : {});
    notify(t("presets_installed", { n: r.created.length }));
    reload();
    onInstalled?.();
  });
  const presets = data?.presets || [];
  const pending = presets.filter((p) => !p.installed);
  return (
    <Section id="sec-presets" title={t("templates")} count={presets.length} actions={pending.length > 1 && <Busy className="btn btn-sm" busy={busy.all} onClick={() => install(null)}>{t("install_all")}</Busy>}>
      <p className="help">{t("templates_hint")}</p>
      <ErrorBox error={error} />
      <div className="card-grid">
        {presets.map((p) => (
          <article key={p.id} className="panel space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <ModeChip mode={p.mode} />
              {!p.enabled && <Chip>{t("starts_disabled")}</Chip>}
              {p.targets > 0 && <Chip>{t("n_targets", { n: p.targets })}</Chip>}
            </div>
            <h3>{p.name}</h3>
            {p.notes && <p className="help clamp2" title={p.notes}>{p.notes}</p>}
            {p.installed
              ? <Chip className="chip-ok">{t("installed")}</Chip>
              : <Busy className="btn btn-sm btn-primary" busy={busy[p.id]} onClick={() => install([p.id])}>{t("install")}</Busy>}
          </article>
        ))}
      </div>
    </Section>
  );
}

function ImportExport({ onImported }) {
  const { t, notify, toastError } = useApp();
  const [busy, run] = useBusy();
  const fileRef = useRef(null);
  const exportConfig = () => run("export", async () => {
    const data = await api.call("config_export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `tantalus-config-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify(t("exported", { n: data.watchers?.length || 0 }));
  });
  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch {
      toastError(new Error(t("import_bad_json")));
      return;
    }
    run("import", async () => {
      const r = await api.call("config_import", { data });
      notify(t("imported", { created: r.created.length, updated: r.updated.length }));
      onImported?.();
    });
  };
  return (
    <>
      <Busy className="btn btn-sm" busy={busy.export} onClick={exportConfig}>{t("export_json")}</Busy>
      <Busy className="btn btn-sm" busy={busy.import} onClick={() => fileRef.current?.click()}>{t("import_json")}</Busy>
      <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={onFile} aria-label={t("import_json")} />
    </>
  );
}

function WatcherList() {
  const { t, dash, refreshDash, notify, confirm } = useApp();
  const [busy, run] = useBusy();
  if (!dash) return <p className="help">…</p>;
  const watchers = dash.watchers;
  const toggle = (w, enabled) => run(`en-${w.id}`, async () => {
    await api.call("watcher_update", { watcher_id: w.id, enabled });
    notify(enabled ? t("watcher_enabled") : t("watcher_disabled"));
    await refreshDash();
  });
  const runNow = (w) => run(`run-${w.id}`, async () => {
    const r = await api.call("watcher_run", { watcher_id: w.id });
    const n = r.checked ? r.checked.length : null;
    notify(n !== null ? t("run_done_targets", { n, name: w.name }) : t("run_done", { name: w.name }));
    await refreshDash();
  });
  const remove = async (w) => {
    const ok = await confirm({ title: t("delete_watcher"), message: t("delete_watcher_msg", { name: w.name }), confirmLabel: t("delete") });
    if (!ok) return;
    run(`del-${w.id}`, async () => {
      await api.call("watcher_delete", { id: w.id, confirm: true });
      notify(t("watcher_deleted"));
      await refreshDash();
    });
  };
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto">{t("nav_watchers")}</h1>
        <ImportExport onImported={refreshDash} />
        <a className="btn btn-primary" href="#/watchers/new"><Icon d="M12 5v14M5 12h14" size={14} />{t("new_watcher")}</a>
      </header>
      {watchers.length === 0 ? (
        <Empty>{t("no_watchers")}</Empty>
      ) : (
        <div className="panel scroll-x p-0">
          <table>
            <thead>
              <tr>
                <th>{t("name")}</th><th>{t("last_check")}</th><th>{t("enabled")}</th><th style={{ width: 130 }} />
              </tr>
            </thead>
            <tbody>
              {watchers.map((w) => (
                <tr key={w.id} style={{ opacity: w.enabled ? 1 : 0.6 }}>
                  <td style={{ minWidth: 200 }}>
                    <div className="flex flex-wrap items-center gap-2">
                      <a href={`#/watchers/${encodeURIComponent(w.id)}`} className="font-semibold">{w.name}</a>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      <ModeChip mode={w.mode} />
                      <Chip>{t("every")} {w.interval_min} min</Chip>
                      {w.mode === "availability" && <Chip>{t("n_targets", { n: w.targets })}</Chip>}
                      {w.buyable > 0 && <Chip className="chip-ok">{t("n_buyable", { n: w.buyable })}</Chip>}
                      {w.needs_human > 0 && <Chip className="chip-danger">{t("n_needs_human", { n: w.needs_human })}</Chip>}
                      {w.listings_new > 0 && <Chip className="chip-accent">{t("n_listings_new", { n: w.listings_new })}</Chip>}
                      {w.info_material > 0 && <Chip className="chip-accent">{t("n_info_material", { n: w.info_material })}</Chip>}
                      {w.unseen > 0 && <Chip className="chip-accent">{t("unseen_n", { n: w.unseen })}</Chip>}
                    </div>
                    {w.last_error && <div className="trunc" style={{ color: "#ffb9ac", maxWidth: 420 }} title={w.last_error}>{w.last_error}</div>}
                  </td>
                  <td>
                    <div>{t("last_check")}: <Rel ts={w.last_check_ts} /></div>
                    <div className="help">{t("next_run")}: <Rel ts={w.next_run_ts} /></div>
                  </td>
                  <td><Switch checked={w.enabled} disabled={busy[`en-${w.id}`]} onChange={(v) => toggle(w, v)} label={`${t("enabled")}: ${w.name}`} /></td>
                  <td style={{ width: 130 }}>
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <Busy className="btn btn-sm" busy={busy[`run-${w.id}`]} onClick={() => runNow(w)}>{t("check_now")}</Busy>
                      <a className="btn btn-sm" href={`#/watchers/${encodeURIComponent(w.id)}/edit`}>{t("edit")}</a>
                      <Busy className="btn btn-sm btn-danger" busy={busy[`del-${w.id}`]} onClick={() => remove(w)}>{t("delete")}</Busy>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Presets onInstalled={refreshDash} />
    </div>
  );
}

function NewWatcher() {
  const { t, draft, setDraft, refreshDash } = useApp();
  const [initial] = useState(draft);
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-2">
        <a className="btn btn-sm" href="#/watchers">← {t("nav_watchers")}</a>
        <h1>{t("new_watcher")}</h1>
      </header>
      {initial && <div className="banner banner-info">{t("prefilled_from_search")}</div>}
      <WatcherForm draft={initial} onCancel={() => { setDraft(null); go("#/watchers"); }} onSaved={(w) => { setDraft(null); refreshDash(); go(`#/watchers/${encodeURIComponent(w.id)}`); }} />
    </div>
  );
}

// ------------------------------------------------------------------ detail
function ConfigSummary({ watcher }) {
  const { t, lang } = useApp();
  const c = watcher.config || {};
  const rows = [];
  const add = (label, value) => { if (value !== undefined && value !== null && value !== "" && !(Array.isArray(value) && !value.length)) rows.push([label, value]); };
  if (watcher.mode === "availability") {
    const p = c.product || {};
    const pol = c.policies || {};
    const d = c.discovery || {};
    add(t("terms"), joinList(p.terms)); add(t("must"), joinList(p.must)); add(t("exclude"), joinList(p.exclude));
    add(t("region"), c.region); add(t("stores"), joinList(c.stores));
    add(t("seller_policy"), pol.seller ? t(`seller_${pol.seller}`) : ""); add(t("alert_on"), (pol.alert_on || []).map((a) => t(`ev_${a}`)).join(", "));
    add(t("require_confidence"), pol.require_confidence); add(t("revalidate_seconds"), pol.revalidate_seconds); add(t("cooldown_minutes"), pol.cooldown_minutes);
    add(t("min_drop_pct"), pol.min_drop_pct); add(t("price_threshold"), pol.price_threshold); add(t("msrp"), pol.msrp); add(t("scalper_multiplier"), pol.scalper_multiplier);
    add(t("disc_queries"), (d.queries || []).length ? t("n_queries", { n: d.queries.length }) : ""); add(t("disc_retailers"), joinList(d.retailers));
    add(t("discovery_interval_h"), watcher.discovery_interval_h ? `${watcher.discovery_interval_h} h` : t("off"));
  } else if (watcher.mode === "secondhand") {
    add(t("pack"), c.pack); add(t("sources"), (c.sources || []).map((s) => t(`src_${s}`)).join(", ")); add(t("queries"), (c.queries || []).join(" · "));
    const known = { origin_location: t("origin_location"), radius_km: t("radius_km"), price_ceiling: t("price_ceiling"), msrp: t("msrp") };
    for (const [k, v] of Object.entries(c.settings || {})) add(known[k] || k, Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? t("yes") : t("no")) : String(v));
  } else {
    add(t("sources"), t("n_sources", { n: (c.sources || []).length }));
    const i = c.info || {};
    add(t("must_terms"), joinList(i.must_terms)); add(t("boost_terms"), joinList(i.boost_terms)); add(t("exclude_terms"), joinList(i.exclude_terms));
    add(t("official_domains"), joinList(i.official_domains)); add(t("freshness_days"), i.freshness_days);
  }
  return (
    <div className="panel space-y-3">
      <dl className="m-0 grid gap-x-6 gap-y-1.5 md:grid-cols-2">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[150px_minmax(0,1fr)] gap-2">
            <dt className="help">{k}</dt>
            <dd className="m-0 break-words">{String(v)}</dd>
          </div>
        ))}
      </dl>
      {watcher.notes && <p className="help">{watcher.notes}</p>}
      <details>
        <summary className="help">{t("raw_config")}</summary>
        <pre className="mono mt-2 max-h-72 overflow-auto rounded p-2" style={{ background: "var(--field)" }}>{JSON.stringify(c, null, 2)}</pre>
      </details>
      <div className="help" title={clock(watcher.updated_ts, lang)}>{t("updated")}: <Rel ts={watcher.updated_ts} /></div>
    </div>
  );
}

function TargetAddForm({ watcher, onAdded }) {
  const { t, notify } = useApp();
  const [busy, run] = useBusy();
  const blank = { url: "", label: "", sku: "", ean: "", msrp: "", price_ceiling: "", price_threshold: "", seller_policy: watcher.config?.policies?.seller || "retail_only", fetch_tier: "auto", check_now: true };
  const [f, setF] = useState(blank);
  const [error, setError] = useState(null);
  const inp = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const submit = (e) => {
    e.preventDefault();
    if (!/^(https?:\/\/|nvidia-api:)/i.test(f.url.trim())) { setError(new Error(t("err_url"))); return; }
    setError(null);
    run("add", async () => {
      const args = { watcher_id: watcher.id, url: f.url.trim(), label: f.label.trim(), sku: f.sku.trim(), ean: f.ean.trim(), seller_policy: f.seller_policy, fetch_tier: f.fetch_tier, check_now: f.check_now };
      for (const k of ["msrp", "price_ceiling", "price_threshold"]) { const n = parseNum(f[k]); if (n !== undefined) args[k] = n; }
      const r = await api.call("target_add", args);
      const chk = r.check;
      notify(chk && chk.ok === false ? `${t("target_added")} — ${chk.error || chk.blocked || ""}` : t("target_added"));
      setF(blank);
      onAdded?.();
    });
  };
  return (
    <form className="panel space-y-3" onSubmit={submit} noValidate>
      <h3>{t("add_target")}</h3>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Field label="URL" className="md:col-span-2"><input className="field" value={f.url} onChange={inp("url")} placeholder="https://…" required /></Field>
        <Field label={t("label")}><input className="field" value={f.label} onChange={inp("label")} /></Field>
        <Field label="SKU"><input className="field" value={f.sku} onChange={inp("sku")} /></Field>
        <Field label="EAN"><input className="field" value={f.ean} onChange={inp("ean")} /></Field>
        <Field label={t("msrp")}><input className="field num" inputMode="decimal" value={f.msrp} onChange={inp("msrp")} /></Field>
        <Field label={t("price_ceiling")}><input className="field num" inputMode="decimal" value={f.price_ceiling} onChange={inp("price_ceiling")} /></Field>
        <Field label={t("price_threshold")}><input className="field num" inputMode="decimal" value={f.price_threshold} onChange={inp("price_threshold")} /></Field>
        <Field label={t("seller_policy")}>
          <select className="field" value={f.seller_policy} onChange={inp("seller_policy")}>{SELLER_POLICIES.map((s) => <option key={s} value={s}>{t(`seller_${s}`)}</option>)}</select>
        </Field>
        <Field label={t("fetch_tier")} hint={t("fetch_tier_hint")}>
          <select className="field" value={f.fetch_tier} onChange={inp("fetch_tier")}>{["auto", "http", "browser", "window"].map((s) => <option key={s} value={s}>{t(`tier_${s}`)}</option>)}</select>
        </Field>
      </div>
      <ErrorBox error={error} />
      <div className="flex flex-wrap items-center gap-3">
        <Busy type="submit" className="btn btn-primary" busy={busy.add}>{t("add")}</Busy>
        <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={f.check_now} onChange={(e) => setF((x) => ({ ...x, check_now: e.target.checked }))} />{t("check_first")}</label>
        {busy.add && <span className="help">{t("may_take_long")}</span>}
      </div>
    </form>
  );
}

export function TargetsTable({ targets, onChanged }) {
  const { t, notify, confirm } = useApp();
  const [busy, run] = useBusy();
  const check = (tg) => run(`c-${tg.id}`, async () => {
    const r = await api.call("target_check", { target_id: tg.id });
    notify(r.ok === false ? `${t("check_failed")}: ${r.error || r.blocked || ""}` : `${t("check_done")}: ${r.state ? t(`state_${r.state}`) : "OK"}`);
    onChanged?.();
  });
  const pause = (tg) => run(`p-${tg.id}`, async () => {
    await api.call("target_update", { target_id: tg.id, status: tg.status === "paused" ? "active" : "paused" });
    notify(tg.status === "paused" ? t("target_resumed") : t("target_paused"));
    onChanged?.();
  });
  const remove = async (tg) => {
    const ok = await confirm({ title: t("delete_target"), message: t("delete_target_msg", { name: tg.label || tg.last_title || hostOf(tg.url) }) });
    if (!ok) return;
    run(`d-${tg.id}`, async () => {
      await api.call("target_delete", { id: tg.id, confirm: true });
      notify(t("target_deleted"));
      onChanged?.();
    });
  };
  const resolve = (tg) => run(`r-${tg.id}`, async () => {
    const r = await api.call("target_resolve", { target_id: tg.id });
    notify(r.check?.ok ? t("resolve_done") : `${t("resolve_done")}${r.check?.error ? ` — ${r.check.error}` : ""}`);
    onChanged?.();
  });
  if (!targets.length) return <Empty>{t("no_targets")}</Empty>;
  return (
    <div className="panel scroll-x p-0">
      <table>
        <thead>
          <tr><th>{t("product")}</th><th>{t("retailer")}</th><th>{t("state")}</th><th className="r">{t("price")}</th><th className="r">{t("min_price")}</th><th>{t("confidence")}</th><th>{t("last_check")}</th><th style={{ width: 190 }} /></tr>
        </thead>
        <tbody>
          {targets.map((tg) => (
            <tr key={tg.id} style={{ opacity: tg.status === "paused" ? 0.6 : 1 }}>
              <td style={{ minWidth: 220 }}>
                <a href={`#/target/${encodeURIComponent(tg.id)}`} className="font-semibold">{tg.label || tg.last_title || hostOf(tg.url)}</a>
                {tg.label && tg.last_title && tg.label !== tg.last_title && <div className="help trunc" style={{ maxWidth: 320 }} title={tg.last_title}>{tg.last_title}</div>}
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {tg.status === "needs_human" && <Chip className="chip-danger">{t("needs_human")}</Chip>}
                  {tg.status === "paused" && <Chip>{t("paused")}</Chip>}
                  {tg.status === "error" && <Chip className="chip-danger">{t("tg_error")}</Chip>}
                </div>
                {tg.last_error && <div className="trunc" style={{ color: "#ffb9ac", maxWidth: 320 }} title={tg.last_error}>{tg.last_error}</div>}
              </td>
              <td>{tg.retailer || tg.host}</td>
              <td><StatePill state={tg.last_state} /></td>
              <td className="r"><Price value={tg.last_price} currency={tg.last_currency} /></td>
              <td className="r"><Price value={tg.min_price} currency={tg.last_currency} className="font-normal" /></td>
              <td><Confidence value={tg.last_confidence} /></td>
              <td className="whitespace-nowrap"><Rel ts={tg.last_check_ts} /></td>
              <td style={{ width: 190 }}>
                <div className="flex flex-wrap justify-end gap-1.5">
                  {tg.status === "needs_human" && <Busy className="btn btn-sm btn-primary" busy={busy[`r-${tg.id}`]} onClick={() => resolve(tg)}>{t("resolve")}</Busy>}
                  <Busy className="btn btn-sm" busy={busy[`c-${tg.id}`]} onClick={() => check(tg)}>{t("check_now")}</Busy>
                  <Busy className="btn btn-sm" busy={busy[`p-${tg.id}`]} onClick={() => pause(tg)}>{tg.status === "paused" ? t("resume") : t("pause")}</Busy>
                  <a className="btn btn-sm" href={`#/target/${encodeURIComponent(tg.id)}/edit`}>{t("edit")}</a>
                  <ExtLink href={tg.url}>{t("open")}</ExtLink>
                  <Busy className="btn btn-sm btn-danger" busy={busy[`d-${tg.id}`]} onClick={() => remove(tg)}>{t("delete")}</Busy>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ListingsPanel({ watcher }) {
  const { t, notify, toastError } = useApp();
  const [rescoring, setRescoring] = useState(false);
  const rescore = async () => {
    setRescoring(true);
    try {
      const r = await api.call("watcher_rescore", { watcher_id: watcher.id });
      notify(t("rescore_done", { n: r.changed, total: r.listings }));
      reload();
    } catch (e) {
      toastError(e);
    } finally {
      setRescoring(false);
    }
  };
  const [relevant, setRelevant] = useState(true);
  const [order, setOrder] = useState("score");
  const [status, setStatus] = useState("active");
  const statuses = status === "active" ? ["new", "seen", "saved"] : status === "all" ? undefined : [status];
  const { data, error, reload } = useLoad(() => api.call("listings_list", { watcher_id: watcher.id, relevant_only: relevant, order, limit: 120, ...(statuses ? { statuses } : {}) }), [watcher.id, relevant, order, status]);
  const rows = data?.listings || [];
  return (
    <Section id="sec-listings" title={t("sec_listings_w")} count={data ? rows.length : null}
      actions={(
        <>
          <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={relevant} onChange={(e) => setRelevant(e.target.checked)} />{t("only_relevant")}</label>
          <select className="field" style={{ width: "auto" }} aria-label={t("order")} value={order} onChange={(e) => setOrder(e.target.value)}>
            <option value="score">{t("order_score")}</option><option value="recent">{t("order_recent")}</option>
          </select>
          <select className="field" style={{ width: "auto" }} aria-label={t("status")} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">{t("lst_active")}</option><option value="saved">{t("lst_saved")}</option><option value="dismissed">{t("lst_dismissed")}</option><option value="gone">{t("lst_gone")}</option><option value="all">{t("all")}</option>
          </select>
          <button type="button" className="btn btn-sm" disabled={rescoring} title={t("rescore_hint")} onClick={rescore}>{t("rescore")}</button>
        </>
      )}>
      <ErrorBox error={error} />
      {data && !rows.length && <Empty>{t("no_listings")}</Empty>}
      <div className="card-grid">{rows.map((l) => <ListingCard key={l.id} listing={l} showStatus onChanged={reload} />)}</div>
    </Section>
  );
}

function InfoPanel({ watcher, sources }) {
  const { t } = useApp();
  const [material, setMaterial] = useState(true);
  const [dismissed, setDismissed] = useState(false);
  const { data, error, reload } = useLoad(() => api.call("info_items_list", { watcher_id: watcher.id, material_only: material, include_dismissed: dismissed, limit: 100 }), [watcher.id, material, dismissed]);
  const items = data?.items || [];
  return (
    <>
      <Section id="sec-info-items" title={t("sec_info_items")} count={data ? items.length : null}
        actions={<>
          <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={material} onChange={(e) => setMaterial(e.target.checked)} />{t("only_material")}</label>
          <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={dismissed} onChange={(e) => setDismissed(e.target.checked)} />{t("show_dismissed")}</label>
        </>}>
        <ErrorBox error={error} />
        {data && !items.length && <Empty>{t("no_info")}</Empty>}
        <div className="card-grid">{items.map((i) => <InfoRow key={i.id} item={i} onChanged={reload} />)}</div>
      </Section>
      <Section id="sec-info-sources" title={t("sec_info_sources")} count={sources.length}>
        <div className="panel scroll-x p-0">
          <table>
            <thead><tr><th>{t("kind")}</th><th>{t("value")}</th><th>{t("label")}</th><th>{t("last_check")}</th><th>{t("last_error")}</th></tr></thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td><Chip>{t(`kind_${s.kind}`)}</Chip></td>
                  <td className="mono" style={{ maxWidth: 360 }}>{safeUrl(s.value) ? <a href={s.value} target="_blank" rel="noopener noreferrer">{s.value}</a> : s.value}</td>
                  <td>{s.label}</td>
                  <td><Rel ts={s.last_check_ts} /></td>
                  <td style={{ color: "#ffb9ac" }}>{s.last_error || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}

function WatcherDetail({ id, editing }) {
  const { t, refreshDash, notify, confirm, packs, loadPacks } = useApp();
  const [busy, run] = useBusy();
  const { data: w, error, reload } = useLoad(() => api.call("watcher_get", { watcher_id: id }), [id]);
  useEffect(() => { loadPacks(); }, [loadPacks]);
  const refreshAll = () => { reload(); refreshDash(); };
  if (error && !w) return <div className="space-y-3"><a className="btn btn-sm" href="#/watchers">← {t("nav_watchers")}</a><ErrorBox error={error} /></div>;
  if (!w) return <p className="help">…</p>;

  const toggle = (enabled) => run("enable", async () => {
    await api.call("watcher_update", { watcher_id: w.id, enabled });
    notify(enabled ? t("watcher_enabled") : t("watcher_disabled"));
    refreshAll();
  });
  const runNow = () => run("run", async () => {
    const r = await api.call("watcher_run", { watcher_id: w.id });
    notify(r.checked ? t("run_done_targets", { n: r.checked.length, name: w.name }) : t("run_done", { name: w.name }));
    refreshAll();
  });
  const discover = () => run("discover", async () => {
    const r = await api.call("discovery_run", { watcher_id: w.id });
    notify(t("discovery_done", { n: (r.proposed || []).length }));
    refreshAll();
  });
  const remove = async () => {
    const ok = await confirm({ title: t("delete_watcher"), message: t("delete_watcher_msg", { name: w.name }) });
    if (!ok) return;
    run("delete", async () => {
      await api.call("watcher_delete", { id: w.id, confirm: true });
      notify(t("watcher_deleted"));
      refreshDash();
      go("#/watchers");
    });
  };
  const targets = Array.isArray(w.targets) ? w.targets : [];
  const proposed = (w.candidates || []).filter((c) => c.status === "proposed");
  const needPacks = editing && w.mode === "secondhand" && !packs;

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <a className="btn btn-sm" href="#/watchers">← {t("nav_watchers")}</a>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="mr-2">{w.name}</h1>
          <ModeChip mode={w.mode} />
          <label className="inline-flex items-center gap-2"><Switch checked={w.enabled} disabled={busy.enable} onChange={toggle} label={t("enabled")} /><span>{t("enabled")}</span></label>
          <div className="ml-auto flex flex-wrap gap-2">
            <Busy className="btn btn-primary" busy={busy.run} onClick={runNow}>{t("check_now")}</Busy>
            {!editing && <a className="btn" href={`#/watchers/${encodeURIComponent(w.id)}/edit`}>{t("edit")}</a>}
            <Busy className="btn btn-danger" busy={busy.delete} onClick={remove}>{t("delete")}</Busy>
          </div>
        </div>
        <div className="help flex flex-wrap gap-x-4">
          <span>{t("every")}: {w.interval_min} min</span>
          <span>{t("last_check")}: <Rel ts={w.last_run_ts} /></span>
          <span>{t("next_run")}: <Rel ts={w.next_run_ts} /></span>
        </div>
        {busy.run && <p className="help" role="status">{t("may_take_long")}</p>}
        {w.last_error && <div className="banner banner-danger">{w.last_error}</div>}
      </header>

      {editing ? (
        needPacks ? <p className="help">…</p> : <WatcherForm watcher={w} onCancel={() => go(`#/watchers/${encodeURIComponent(w.id)}`)} onSaved={() => { refreshAll(); go(`#/watchers/${encodeURIComponent(w.id)}`); }} />
      ) : (
        <ConfigSummary watcher={w} />
      )}

      {!editing && w.mode === "availability" && (
        <>
          <Section id="sec-targets" title={t("sec_targets")} count={targets.length}>
            <TargetsTable targets={targets} onChanged={refreshAll} />
          </Section>
          <TargetAddForm watcher={w} onAdded={refreshAll} />
          <Section id="sec-candidates-w" title={t("sec_candidates")} count={proposed.length}
            actions={<Busy className="btn btn-sm" busy={busy.discover} onClick={discover}>{t("discover_now")}</Busy>}>
            <p className="help">{t("candidates_explain")}</p>
            {busy.discover && <p className="help" role="status">{t("may_take_long")}</p>}
            {!proposed.length && <Empty>{t("no_candidates")}</Empty>}
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">{proposed.map((c) => <CandidateRow key={c.id} candidate={c} onChanged={refreshAll} />)}</div>
          </Section>
        </>
      )}
      {!editing && w.mode === "secondhand" && <ListingsPanel watcher={w} />}
      {!editing && w.mode === "information" && <InfoPanel watcher={w} sources={w.info_sources || []} />}

      {!editing && (
        <Section id="sec-w-events" title={t("sec_recent_events")} count={(w.recent_events || []).length} actions={<a className="btn btn-sm" href={`#/events?watcher=${encodeURIComponent(w.id)}`}>{t("see_all")}</a>}>
          {!(w.recent_events || []).length && <Empty>{t("no_events")}</Empty>}
          <div className="card-grid">{(w.recent_events || []).map((e) => <EventCard key={e.id} event={e} compact onChanged={reload} />)}</div>
        </Section>
      )}
    </div>
  );
}

export default function Watchers({ param, sub }) {
  if (!param) return <WatcherList />;
  if (param === "new") return <NewWatcher />;
  return <WatcherDetail key={`${param}-${sub || ""}`} id={param} editing={sub === "edit"} />;
}
