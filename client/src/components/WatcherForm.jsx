import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { joinLines, joinList, parseNum, splitLines, splitList } from "../format.js";
import { ALERT_TYPES, MODES, MODE_META, SELLER_POLICIES } from "../meta.js";
import { Busy, Check, ErrorBox, Field, Icon, Switch, Tabs, useBusy } from "./ui.jsx";

const DEFAULT_INTERVAL = { availability: "30", secondhand: "45", information: "180" };
const DEFAULT_ALERTS = ["RESTOCK", "LOCAL_RESTOCK", "PREORDER_OPEN", "SALE_OPEN", "NEW_SKU", "RESTOCK_DATE_CONFIRMED", "PRICE_DROP", "PRICE_THRESHOLD_CROSSED"];
const CORE_SH_FIELDS = [
  { key: "origin_location", type: "text", label_es: "Ubicación de referencia", label_en: "Origin location" },
  { key: "radius_km", type: "number", label_es: "Radio (km)", label_en: "Radius (km)" },
  { key: "price_ceiling", type: "number", label_es: "Precio máximo (€)", label_en: "Max price (€)" },
  { key: "msrp", type: "number", label_es: "PVP oficial (€)", label_en: "MSRP (€)" },
  { key: "include_any", type: "list", label_es: "Palabras clave (alguna)", label_en: "Keywords (any)" },
  { key: "include_all", type: "list", label_es: "Palabras clave (todas)", label_en: "Keywords (all)" },
  { key: "exclude", type: "list", label_es: "Palabras excluidas", label_en: "Excluded words" },
];

// Empty values disappear from the config so the backend defaults apply.
const empty = (v) => v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0) || (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);
function put(obj, key, value) {
  if (empty(value)) delete obj[key];
  else obj[key] = value;
  return obj;
}
const str = (v) => (v === undefined || v === null ? "" : String(v));

export function fieldsForPack(pack) {
  const fields = [...(pack?.settings_fields || [])];
  for (const core of CORE_SH_FIELDS) if (!fields.some((f) => f.key === core.key)) fields.push(core);
  return fields;
}

function settingToForm(field, value) {
  if (field.type === "list") return joinList(value);
  if (field.type === "bool") return !!value;
  return str(value);
}
function settingFromForm(field, value) {
  if (field.type === "list") return splitList(value);
  if (field.type === "number") return parseNum(value);
  if (field.type === "bool") return !!value;
  return typeof value === "string" ? value.trim() : value;
}

function shFormFromSettings(fields, settings) {
  const out = {};
  for (const f of fields) out[f.key] = settingToForm(f, settings?.[f.key]);
  return out;
}

export function emptyForm(mode = "availability") {
  return {
    mode, name: "", interval_min: DEFAULT_INTERVAL[mode], discovery_interval_h: "24", enabled: true, notes: "",
    terms: "", must: "", exclude: "", region: "", stores: "", seller: "retail_only", alert_on: [...DEFAULT_ALERTS],
    require_confidence: "75", revalidate_seconds: "60", cooldown_minutes: "20", min_drop_pct: "5", price_threshold: "", msrp: "", scalper_multiplier: "",
    disc_queries: "", disc_retailers: "", targets: "",
    radar_enabled: false, radar_chains: "game, carrefour, el-corte-ingles, alcampo, amazon, toys-r-us, toy-planet", radar_languages: "ES, EN", radar_other_shops: true,
    pack: "generic", sh_sources: ["wallapop"], sh_queries: "", sh: {},
    info_sources: [{ kind: "page", value: "", label: "" }], must_terms: "", boost_terms: "", exclude_terms: "", official_domains: "", freshness_days: "",
  };
}

export function formFromWatcher(w, packs) {
  const c = w.config || {};
  const f = emptyForm(w.mode);
  f.name = w.name;
  f.interval_min = str(w.interval_min);
  f.discovery_interval_h = str(w.discovery_interval_h);
  f.enabled = !!w.enabled;
  f.notes = w.notes || "";
  if (w.mode === "availability") {
    const p = c.product || {};
    const pol = c.policies || {};
    const d = c.discovery || {};
    Object.assign(f, {
      terms: joinList(p.terms), must: joinList(p.must), exclude: joinList(p.exclude), region: str(c.region), stores: joinList(c.stores),
      seller: pol.seller || "retail_only", alert_on: Array.isArray(pol.alert_on) ? pol.alert_on : [...DEFAULT_ALERTS],
      require_confidence: str(pol.require_confidence), revalidate_seconds: str(pol.revalidate_seconds), cooldown_minutes: str(pol.cooldown_minutes),
      min_drop_pct: str(pol.min_drop_pct), price_threshold: str(pol.price_threshold), msrp: str(pol.msrp), scalper_multiplier: str(pol.scalper_multiplier),
      disc_queries: joinLines(d.queries), disc_retailers: joinList(d.retailers),
    });
    const r = c.radar || {};
    if (Object.keys(r).length) {
      f.radar_enabled = !!r.enabled;
      if (Array.isArray(r.chains)) f.radar_chains = joinList(r.chains);
      if (Array.isArray(r.languages)) f.radar_languages = joinList(r.languages);
      if (r.alert_other_shops !== undefined) f.radar_other_shops = !!r.alert_other_shops;
    }
  } else if (w.mode === "secondhand") {
    const pack = (packs || []).find((p) => p.id === (c.pack || "generic"));
    f.pack = c.pack || "generic";
    f.sh_sources = Array.isArray(c.sources) && c.sources.length ? c.sources : ["wallapop"];
    f.sh_queries = joinLines(c.queries);
    f.sh = shFormFromSettings(fieldsForPack(pack), c.settings);
  } else {
    f.info_sources = (c.sources || []).map((s) => ({ kind: s.kind || "page", value: s.value || "", label: s.label || "" }));
    if (!f.info_sources.length) f.info_sources = [{ kind: "page", value: "", label: "" }];
    const i = c.info || {};
    Object.assign(f, {
      must_terms: joinList(i.must_terms), boost_terms: joinList(i.boost_terms), exclude_terms: joinList(i.exclude_terms),
      official_domains: joinList(i.official_domains), freshness_days: str(i.freshness_days),
    });
  }
  return f;
}

// A "Vigilar esta búsqueda" draft from the search page.
function formFromDraft(draft, packs) {
  const f = emptyForm("secondhand");
  const pack = (packs || []).find((p) => p.id === draft.pack);
  f.name = draft.name || "";
  f.pack = draft.pack || "generic";
  f.sh_sources = draft.sources?.length ? draft.sources : ["wallapop"];
  f.sh_queries = joinLines(draft.queries);
  const fields = fieldsForPack(pack);
  f.sh = shFormFromSettings(fields, { ...(pack?.settings_defaults || {}), ...(draft.settings || {}) });
  return f;
}

export function buildConfig(form, base, packs) {
  const cfg = { ...(base || {}) };
  if (form.mode === "availability") {
    const product = { ...(cfg.product || {}) };
    put(product, "terms", splitList(form.terms));
    put(product, "must", splitList(form.must));
    put(product, "exclude", splitList(form.exclude));
    put(cfg, "product", product);
    put(cfg, "region", form.region.trim());
    put(cfg, "stores", splitList(form.stores));
    const pol = { ...(cfg.policies || {}) };
    pol.seller = form.seller;
    put(pol, "alert_on", form.alert_on);
    put(pol, "require_confidence", parseNum(form.require_confidence));
    put(pol, "revalidate_seconds", parseNum(form.revalidate_seconds));
    put(pol, "cooldown_minutes", parseNum(form.cooldown_minutes));
    put(pol, "min_drop_pct", parseNum(form.min_drop_pct));
    put(pol, "price_threshold", parseNum(form.price_threshold));
    put(pol, "msrp", parseNum(form.msrp));
    put(pol, "scalper_multiplier", parseNum(form.scalper_multiplier));
    cfg.policies = pol;
    const disc = { ...(cfg.discovery || {}) };
    put(disc, "queries", splitLines(form.disc_queries));
    put(disc, "retailers", splitList(form.disc_retailers));
    put(cfg, "discovery", disc);
    const radar = { ...(cfg.radar || {}) };
    radar.enabled = !!form.radar_enabled;
    put(radar, "chains", splitList(form.radar_chains).map((x) => x.toLowerCase()));
    radar.languages = splitList(form.radar_languages).map((x) => x.toUpperCase());
    radar.alert_other_shops = !!form.radar_other_shops;
    if (radar.enabled || base?.radar) cfg.radar = radar;
  } else if (form.mode === "secondhand") {
    cfg.pack = form.pack;
    cfg.sources = form.sh_sources;
    put(cfg, "queries", splitLines(form.sh_queries));
    const pack = (packs || []).find((p) => p.id === form.pack);
    const settings = { ...(cfg.settings || {}) };
    for (const field of fieldsForPack(pack)) {
      const value = settingFromForm(field, form.sh[field.key]);
      if (field.type === "bool") settings[field.key] = value;
      else put(settings, field.key, value);
    }
    put(cfg, "settings", settings);
  } else {
    cfg.sources = form.info_sources.filter((s) => s.value.trim()).map((s) => ({ kind: s.kind, value: s.value.trim(), ...(s.label.trim() ? { label: s.label.trim() } : {}) }));
    const info = { ...(cfg.info || {}) };
    put(info, "must_terms", splitList(form.must_terms));
    put(info, "boost_terms", splitList(form.boost_terms));
    put(info, "exclude_terms", splitList(form.exclude_terms));
    put(info, "official_domains", splitList(form.official_domains));
    put(info, "freshness_days", parseNum(form.freshness_days));
    put(cfg, "info", info);
  }
  return cfg;
}

function Grid({ children, cols = 3 }) {
  const cls = cols === 2 ? "md:grid-cols-2" : cols === 4 ? "md:grid-cols-2 xl:grid-cols-4" : "md:grid-cols-2 xl:grid-cols-3";
  return <div className={`grid gap-3 ${cls}`}>{children}</div>;
}

function Block({ title, hint, children }) {
  return (
    <fieldset className="panel space-y-3 min-w-0">
      <legend className="px-1 text-[13px] font-semibold">{title}</legend>
      {hint && <p className="help">{hint}</p>}
      {children}
    </fieldset>
  );
}

export default function WatcherForm({ watcher, draft, onSaved, onCancel }) {
  const { t, lang, packs, loadPacks, notify } = useApp();
  const editing = !!watcher;
  const [busy, run] = useBusy();
  const [error, setError] = useState(null);
  const [form, setForm] = useState(() => (watcher ? formFromWatcher(watcher, packs) : draft ? formFromDraft(draft, packs) : emptyForm("availability")));
  const queriesTouched = useRef(!!draft || editing);
  useEffect(() => { loadPacks(); }, [loadPacks]);

  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));
  const setInput = (key) => (e) => set(key)(e.target.value);
  const pack = useMemo(() => (packs || []).find((p) => p.id === form.pack), [packs, form.pack]);
  const fields = useMemo(() => fieldsForPack(pack), [pack]);

  // Once the packs arrive: prefill the queries and settings of a new second-hand watcher from its pack.
  const packsReady = !!packs;
  useEffect(() => {
    if (!packsReady) return;
    setForm((f) => {
      if (f.mode !== "secondhand") return f;
      const p = packs.find((x) => x.id === f.pack);
      if (!p) return f;
      const next = { ...f };
      if (editing) {
        next.sh = { ...shFormFromSettings(fieldsForPack(p), watcher.config?.settings), ...Object.fromEntries(Object.entries(f.sh).filter(([, v]) => v !== "" && v !== false)) };
      } else if (!Object.keys(f.sh).length) {
        next.sh = shFormFromSettings(fieldsForPack(p), p.settings_defaults);
      }
      if (!queriesTouched.current && !f.sh_queries) next.sh_queries = joinLines(p.default_queries);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [packsReady]);

  const switchMode = (mode) => {
    setForm((f) => {
      const next = { ...f, mode };
      if (f.interval_min === DEFAULT_INTERVAL[f.mode]) next.interval_min = DEFAULT_INTERVAL[mode];
      if (mode === "secondhand" && packs) {
        const p = packs.find((x) => x.id === f.pack) || packs[0];
        if (p) {
          next.pack = p.id;
          next.sh = shFormFromSettings(fieldsForPack(p), p.settings_defaults);
          if (!queriesTouched.current) next.sh_queries = joinLines(p.default_queries);
        }
      }
      return next;
    });
  };

  const changePack = (id) => {
    const p = (packs || []).find((x) => x.id === id);
    setForm((f) => ({
      ...f, pack: id,
      sh: shFormFromSettings(fieldsForPack(p), { ...(p?.settings_defaults || {}), ...(editing ? watcher.config?.settings : {}) }),
      sh_queries: queriesTouched.current ? f.sh_queries : joinLines(p?.default_queries),
    }));
  };

  const validate = () => {
    if (!form.name.trim()) return t("err_name");
    const iv = parseNum(form.interval_min);
    if (iv === undefined || iv < 5) return t("err_interval");
    if (form.mode === "secondhand" && !form.sh_sources.length) return t("err_sources_sh");
    if (form.mode === "information" && !form.info_sources.some((s) => s.value.trim())) return t("err_sources_info");
    return null;
  };

  const submit = (e) => {
    e.preventDefault();
    const problem = validate();
    if (problem) { setError(new Error(problem)); return; }
    setError(null);
    run("save", async () => {
      const config = buildConfig(form, watcher?.config, packs);
      const common = {
        name: form.name.trim(), config, interval_min: Math.round(parseNum(form.interval_min)), enabled: form.enabled, notes: form.notes,
        discovery_interval_h: form.mode === "availability" ? Math.round(parseNum(form.discovery_interval_h) ?? 24) : watcher?.discovery_interval_h ?? 24,
      };
      let result;
      try {
        if (editing) result = await api.call("watcher_update", { watcher_id: watcher.id, ...common });
        else result = await api.call("watcher_create", { ...common, mode: form.mode, targets: form.mode === "availability" ? splitLines(form.targets) : [] });
      } catch (err) {
        setError(err);
        throw err;
      }
      notify(editing ? t("watcher_saved") : t("watcher_created"));
      onSaved?.(result);
    });
  };

  const toggleIn = (key, value, on) => setForm((f) => ({ ...f, [key]: on ? [...new Set([...f[key], value])] : f[key].filter((x) => x !== value) }));
  const setInfoSource = (i, patch) => setForm((f) => ({ ...f, info_sources: f.info_sources.map((s, n) => (n === i ? { ...s, ...patch } : s)) }));

  return (
    <form className="space-y-4" onSubmit={submit} noValidate>
      {!editing && (
        <div>
          <Tabs active={form.mode} onChange={switchMode} tabs={MODES.map((m) => ({ key: m, label: t(`mode_${m}`), icon: MODE_META[m].icon }))} />
          <p className="help mt-2 mb-0">{t(`mode_${form.mode}_hint`)}</p>
        </div>
      )}

      <Block title={t("form_general")}>
        <Grid>
          <Field label={t("name")}><input className="field" value={form.name} onChange={setInput("name")} required autoFocus={!editing} /></Field>
          <Field label={t("interval_min")} hint={t("interval_hint")}><input className="field num" inputMode="numeric" value={form.interval_min} onChange={setInput("interval_min")} /></Field>
          {form.mode === "availability" && (
            <Field label={t("discovery_interval_h")} hint={t("discovery_interval_hint")}><input className="field num" inputMode="numeric" value={form.discovery_interval_h} onChange={setInput("discovery_interval_h")} /></Field>
          )}
        </Grid>
        <Field label={t("notes")}><textarea className="field" rows={2} value={form.notes} onChange={setInput("notes")} /></Field>
        <label className="inline-flex items-center gap-2"><Switch checked={form.enabled} onChange={set("enabled")} label={t("enabled")} /><span>{t("enabled")}</span></label>
      </Block>

      {form.mode === "availability" && (
        <>
          <Block title={t("form_product")} hint={t("form_product_hint")}>
            <Grid>
              <Field label={t("terms")} hint={t("list_hint")}><input className="field" value={form.terms} onChange={setInput("terms")} placeholder="pokemon, 30, aniversario" /></Field>
              <Field label={t("must")} hint={t("must_hint")}><input className="field" value={form.must} onChange={setInput("must")} /></Field>
              <Field label={t("exclude")}><input className="field" value={form.exclude} onChange={setInput("exclude")} placeholder="funda, protector" /></Field>
              <Field label={t("region")}><input className="field" value={form.region} onChange={setInput("region")} placeholder="ES, ES-MD" /></Field>
              <Field label={t("stores")} hint={t("stores_hint")}><input className="field" value={form.stores} onChange={setInput("stores")} /></Field>
            </Grid>
          </Block>
          <Block title={t("form_policies")}>
            <Grid>
              <Field label={t("seller_policy")} hint={t(`seller_${form.seller}_hint`)}>
                <select className="field" value={form.seller} onChange={setInput("seller")}>
                  {SELLER_POLICIES.map((s) => <option key={s} value={s}>{t(`seller_${s}`)}</option>)}
                </select>
              </Field>
              <Field label={t("require_confidence")} hint={t("require_confidence_hint")}><input className="field num" inputMode="numeric" value={form.require_confidence} onChange={setInput("require_confidence")} /></Field>
              <Field label={t("revalidate_seconds")}><input className="field num" inputMode="numeric" value={form.revalidate_seconds} onChange={setInput("revalidate_seconds")} /></Field>
              <Field label={t("cooldown_minutes")}><input className="field num" inputMode="numeric" value={form.cooldown_minutes} onChange={setInput("cooldown_minutes")} /></Field>
              <Field label={t("min_drop_pct")}><input className="field num" inputMode="decimal" value={form.min_drop_pct} onChange={setInput("min_drop_pct")} /></Field>
              <Field label={t("price_threshold")} hint={t("price_threshold_hint")}><input className="field num" inputMode="decimal" value={form.price_threshold} onChange={setInput("price_threshold")} /></Field>
              <Field label={t("msrp")}><input className="field num" inputMode="decimal" value={form.msrp} onChange={setInput("msrp")} /></Field>
              <Field label={t("scalper_multiplier")} hint={t("scalper_hint")}><input className="field num" inputMode="decimal" value={form.scalper_multiplier} onChange={setInput("scalper_multiplier")} placeholder="1.3" /></Field>
            </Grid>
            <div>
              <span className="label">{t("alert_on")}</span>
              <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                {ALERT_TYPES.map((a) => <Check key={a} checked={form.alert_on.includes(a)} onChange={(on) => toggleIn("alert_on", a, on)}>{t(`ev_${a}`)}</Check>)}
              </div>
            </div>
          </Block>
          <Block title={t("form_radar")} hint={t("form_radar_hint")}>
            <label className="inline-flex items-center gap-2"><Switch checked={form.radar_enabled} onChange={set("radar_enabled")} label={t("radar_enabled")} /><span>{t("radar_enabled")}</span></label>
            {form.radar_enabled && (
              <Grid cols={2}>
                <Field label={t("radar_chains")} hint={t("radar_chains_hint")}><input className="field" value={form.radar_chains} onChange={setInput("radar_chains")} /></Field>
                <Field label={t("radar_languages")} hint={t("radar_languages_hint")}><input className="field" value={form.radar_languages} onChange={setInput("radar_languages")} placeholder="ES, EN" /></Field>
              </Grid>
            )}
            {form.radar_enabled && (
              <label className="inline-flex items-center gap-2"><Switch checked={form.radar_other_shops} onChange={set("radar_other_shops")} label={t("radar_other_shops")} /><span>{t("radar_other_shops")}</span></label>
            )}
          </Block>
          <Block title={t("form_discovery")} hint={t("form_discovery_hint")}>
            <Grid cols={2}>
              <Field label={t("disc_queries")} hint={t("one_per_line")}><textarea className="field" rows={4} value={form.disc_queries} onChange={setInput("disc_queries")} placeholder={"site:game.es Pokémon 30 aniversario ETB"} /></Field>
              <Field label={t("disc_retailers")} hint={t("retailers_hint")}><textarea className="field" rows={4} value={form.disc_retailers} onChange={setInput("disc_retailers")} placeholder="game.es, carrefour.es" /></Field>
            </Grid>
          </Block>
          {!editing && (
            <Block title={t("initial_targets")} hint={t("initial_targets_hint")}>
              <Field label={t("urls")}><textarea className="field" rows={3} value={form.targets} onChange={setInput("targets")} placeholder="https://…" /></Field>
            </Block>
          )}
        </>
      )}

      {form.mode === "secondhand" && (
        <>
          <Block title={t("form_pack")}>
            <Grid cols={2}>
              <Field label={t("pack")}>
                <select className="field" value={form.pack} onChange={(e) => changePack(e.target.value)}>
                  {(packs || [{ id: form.pack, name_es: form.pack, name_en: form.pack }]).map((p) => <option key={p.id} value={p.id}>{lang === "en" ? p.name_en : p.name_es}</option>)}
                </select>
              </Field>
              <div>
                <span className="label">{t("sources")}</span>
                <div className="flex gap-4 pt-1">
                  {["wallapop", "facebook"].map((s) => <Check key={s} checked={form.sh_sources.includes(s)} onChange={(on) => toggleIn("sh_sources", s, on)}>{t(`src_${s}`)}</Check>)}
                </div>
                {form.sh_sources.includes("facebook") && <p className="help mt-1 mb-0">{t("facebook_needs_login")}</p>}
              </div>
            </Grid>
            {pack && <p className="help">{lang === "en" ? pack.description_en : pack.description} · {t("alert_min_score", { n: pack.alert_min_score })}</p>}
            <Field label={t("queries")} hint={t("one_per_line")}>
              <textarea className="field" rows={5} value={form.sh_queries} onChange={(e) => { queriesTouched.current = true; set("sh_queries")(e.target.value); }} />
            </Field>
          </Block>
          <Block title={t("form_sh_settings")}>
            <Grid>
              {fields.map((f) => {
                const label = lang === "en" ? f.label_en : f.label_es;
                const value = form.sh[f.key];
                const onChange = (v) => setForm((cur) => ({ ...cur, sh: { ...cur.sh, [f.key]: v } }));
                if (f.type === "bool") return <div key={f.key} className="flex items-end pb-1"><Check checked={!!value} onChange={onChange}>{label}</Check></div>;
                if (f.type?.startsWith("enum:")) {
                  return (
                    <Field key={f.key} label={label}>
                      <select className="field" value={value || ""} onChange={(e) => onChange(e.target.value)}>
                        <option value="">—</option>
                        {f.type.slice(5).split("|").map((o) => <option key={o} value={o}>{t(`opt_${o}`) === `opt_${o}` ? o : t(`opt_${o}`)}</option>)}
                      </select>
                    </Field>
                  );
                }
                return (
                  <Field key={f.key} label={label} hint={f.type === "list" ? t("list_hint") : undefined}>
                    <input className={`field ${f.type === "number" ? "num" : ""}`} inputMode={f.type === "number" ? "decimal" : undefined} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
                  </Field>
                );
              })}
            </Grid>
          </Block>
        </>
      )}

      {form.mode === "information" && (
        <>
          <Block title={t("form_info_sources")} hint={t("info_sources_hint")}>
            <div className="space-y-2">
              {form.info_sources.map((s, i) => (
                <div key={i} className="grid gap-2 md:grid-cols-[120px_minmax(0,2fr)_minmax(0,1fr)_auto]">
                  <select className="field" aria-label={t("kind")} value={s.kind} onChange={(e) => setInfoSource(i, { kind: e.target.value })}>
                    {["page", "feed", "search"].map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
                  </select>
                  <input className="field" aria-label={t("value")} value={s.value} onChange={(e) => setInfoSource(i, { value: e.target.value })} placeholder={s.kind === "search" ? '"RTX Spark" 128GB Europe' : "https://…"} />
                  <input className="field" aria-label={t("label")} value={s.label} onChange={(e) => setInfoSource(i, { label: e.target.value })} placeholder={t("label")} />
                  <button type="button" className="btn btn-sm" onClick={() => setForm((f) => ({ ...f, info_sources: f.info_sources.filter((_, n) => n !== i) }))} aria-label={t("remove")}>{t("remove")}</button>
                </div>
              ))}
              <button type="button" className="btn btn-sm" onClick={() => setForm((f) => ({ ...f, info_sources: [...f.info_sources, { kind: "page", value: "", label: "" }] }))}>
                <Icon d="M12 5v14M5 12h14" size={13} />{t("add_source")}
              </button>
            </div>
          </Block>
          <Block title={t("form_info_terms")}>
            <Grid>
              <Field label={t("must_terms")} hint={t("must_terms_hint")}><input className="field" value={form.must_terms} onChange={setInput("must_terms")} /></Field>
              <Field label={t("boost_terms")} hint={t("boost_terms_hint")}><input className="field" value={form.boost_terms} onChange={setInput("boost_terms")} /></Field>
              <Field label={t("exclude_terms")}><input className="field" value={form.exclude_terms} onChange={setInput("exclude_terms")} /></Field>
              <Field label={t("official_domains")} hint={t("official_domains_hint")}><input className="field" value={form.official_domains} onChange={setInput("official_domains")} placeholder="nvidia.com, asus.com" /></Field>
              <Field label={t("freshness_days")}><input className="field num" inputMode="numeric" value={form.freshness_days} onChange={setInput("freshness_days")} placeholder="30" /></Field>
            </Grid>
          </Block>
        </>
      )}

      <ErrorBox error={error} />
      <div className="flex flex-wrap gap-2">
        <Busy type="submit" className="btn btn-primary" busy={busy.save}>{editing ? t("save") : t("create")}</Busy>
        {onCancel && <button type="button" className="btn" onClick={onCancel}>{t("cancel")}</button>}
      </div>
    </form>
  );
}
