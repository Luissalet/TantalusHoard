import React, { useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Busy, Chip, Empty, ErrorBox, Field, Icon, Rel, Section, Switch, useBusy, useLoad } from "../components/ui.jsx";
import { clock, num, shortClock } from "../format.js";
import { CHANNELS, SEVERITIES } from "../meta.js";

const CHANNEL_SECRETS = {
  telegram: [
    { name: "TELEGRAM_TOKEN", label: "telegram_token", secret: true },
    { name: "TELEGRAM_CHAT_ID", label: "telegram_chat_id", plain: true },
  ],
  ntfy: [
    { name: "NTFY_TOPIC", label: "ntfy_topic", secret: true },
    { name: "NTFY_TOKEN", label: "ntfy_token", secret: true, optional: true },
  ],
  email: [
    { name: "SMTP_HOST", label: "smtp_host", plain: true, placeholder: "smtp.gmail.com" },
    { name: "SMTP_PORT", label: "smtp_port", plain: true, placeholder: "587" },
    { name: "SMTP_USER", label: "smtp_user", secret: true },
    { name: "SMTP_PASSWORD", label: "smtp_password", secret: true },
    { name: "SMTP_FROM", label: "smtp_from", plain: true },
    { name: "SMTP_TO", label: "smtp_to", plain: true },
  ],
};
const SEARCH_SECRETS = [
  { name: "BRAVE_KEY", label: "brave_key", secret: true },
  { name: "SEARXNG_URL", label: "searxng_url", plain: true, placeholder: "http://127.0.0.1:8080" },
];

function randomTopic() {
  const bytes = new Uint8Array(12);
  (window.crypto || window.msCrypto).getRandomValues(bytes);
  return `tantalus-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

// Write-only credentials: the backend never returns the secret, only whether it is set, where from and a masked value.
function SecretsForm({ fields, secrets, onSaved, children, bar }) {
  const { t, notify, confirm } = useApp();
  const [busy, run] = useBusy();
  const original = (f) => (f.plain ? secrets[f.name]?.value || "" : "");
  const [draft, setDraft] = useState(() => Object.fromEntries(fields.map((f) => [f.name, original(f)])));
  const dirty = fields.filter((f) => draft[f.name] !== original(f));
  const set = (name, value) => setDraft((d) => ({ ...d, [name]: value }));
  const save = (e) => {
    e.preventDefault();
    if (!dirty.length) return;
    run("save", async () => {
      for (const f of dirty) await api.call("secret_set", { name: f.name, value: draft[f.name] });
      notify(t("secrets_saved"));
      await onSaved();
      setDraft((d) => Object.fromEntries(fields.map((f) => [f.name, f.plain ? d[f.name] : ""])));
    });
  };
  const remove = async (f) => {
    const ok = await confirm({ title: t("remove_secret"), message: t("remove_secret_msg", { name: t(f.label) }), confirmLabel: t("remove") });
    if (!ok) return;
    run(`rm-${f.name}`, async () => {
      await api.call("secret_set", { name: f.name, value: "" });
      notify(t("secret_removed"));
      await onSaved();
      set(f.name, "");
    });
  };
  return (
    <form className="space-y-3" onSubmit={save} noValidate autoComplete="off">
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((f) => {
          const s = secrets[f.name] || {};
          return (
            <div key={f.name} className="space-y-1">
              <Field label={t(f.label)}>
                <input className="field" type={f.secret ? "password" : "text"} autoComplete="new-password" value={draft[f.name]} onChange={(e) => set(f.name, e.target.value)}
                  placeholder={s.configured && f.secret ? `${t("configured")} ${s.value}` : f.placeholder || ""} />
              </Field>
              <div className="flex flex-wrap items-center gap-1.5">
                <Chip className={s.configured ? "chip-ok" : "chip-amber"}>{s.configured ? t("configured") : t("not_configured")}</Chip>
                {s.configured && s.source && <Chip title={t("secret_source")}>{s.source}</Chip>}
                {s.configured && s.source === "settings" && <button type="button" className="btn-link text-[11.5px]" onClick={() => remove(f)}>{t("remove")}</button>}
              </div>
            </div>
          );
        })}
      </div>
      {children && children({ draft, set })}
      <div className="flex flex-wrap items-center gap-2">
        <Busy type="submit" className="btn btn-primary btn-sm" busy={busy.save} disabled={!dirty.length}>{t("save")}</Busy>
        {bar}
      </div>
    </form>
  );
}

function ChannelCard({ channel, info, secrets, settings, onChanged, testResult, onTest, testing }) {
  const { t, notify, toastError } = useApp();
  const [busy, run] = useBusy();
  const setSetting = (key, value) => run(key, async () => {
    await api.call("settings_set", { values: { [key]: value } });
    notify(t("saved"));
    await onChanged();
  });
  const fields = CHANNEL_SECRETS[channel];
  const [chat, setChat] = useState(null);
  const findChat = (draftApi) => run("chat", async () => {
    const r = await api.call("telegram_find_chat_id");
    setChat(r);
    if (r.ok) {
      draftApi.set("TELEGRAM_CHAT_ID", r.chat_id);
      notify(t("chat_found", { name: r.name || "—", id: r.chat_id }));
    } else {
      toastError(new Error(r.error || t("chat_not_found")));
    }
  });
  const enabled = settings[`notify.${channel}.enabled`] === "1";
  const severity = settings[`notify.${channel}.min_severity`] || "low";
  return (
    <article className="panel space-y-3" aria-label={t(`ch_${channel}`)}>
      <div className="flex flex-wrap items-center gap-2">
        <h2>{t(`ch_${channel}`)}</h2>
        <Chip className={info.configured ? "chip-ok" : "chip-amber"}>{info.configured ? t("configured") : t("not_configured")}</Chip>
        <Chip className={enabled ? "chip-accent" : ""}>{enabled ? t("enabled") : t("disabled")}</Chip>
        <label className="ml-auto inline-flex items-center gap-2"><Switch checked={enabled} disabled={busy[`notify.${channel}.enabled`]} onChange={(v) => setSetting(`notify.${channel}.enabled`, v ? "1" : "0")} label={`${t("enabled")}: ${t(`ch_${channel}`)}`} /></label>
      </div>
      <p className="help">{t(`ch_${channel}_hint`)}</p>
      <div className="help break-words">{t("detail")}: {info.detail}</div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="label">{t("min_severity")}</span>
          <select className="field" style={{ width: "auto" }} value={severity} disabled={busy[`notify.${channel}.min_severity`]} onChange={(e) => setSetting(`notify.${channel}.min_severity`, e.target.value)}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{t(`sev_${s}`)}</option>)}
          </select>
        </label>
        <Busy className="btn btn-sm" busy={testing} onClick={() => onTest(channel)}>{t("test")}</Busy>
        {testResult && (
          <span className={`chip ${testResult.ok ? "chip-ok" : "chip-danger"}`} role="status">{testResult.ok ? t("test_ok") : `${t("test_failed")}: ${testResult.error}`}</span>
        )}
      </div>
      {channel === "email" && <EmailBackend info={info} settings={settings} setSetting={setSetting} busy={busy} />}
      {fields && (
        <details open={!info.configured && !(channel === "email" && info.backend === "faustus")}>
          <summary className="font-semibold">{channel === "email" ? t("smtp_credentials") : t("credentials")}</summary>
          <div className="mt-3 space-y-3">
            {channel === "telegram" && <ol className="help m-0 list-decimal pl-5"><li>{t("tg_step1")}</li><li>{t("tg_step2")}</li><li>{t("tg_step3")}</li></ol>}
            {channel === "ntfy" && <p className="help">{t("ntfy_steps")}</p>}
            {channel === "email" && <p className="help">{t("smtp_hint")}</p>}
            <SecretsForm fields={fields} secrets={secrets} onSaved={onChanged}>
              {({ draft, set }) => (
                <>
                  {channel === "telegram" && (
                    <div className="flex flex-wrap items-center gap-2">
                      <Busy type="button" className="btn btn-sm" busy={busy.chat} onClick={() => findChat({ set })}>{t("find_chat_id")}</Busy>
                      {chat && !chat.ok && <span className="help" role="status">{chat.error}</span>}
                      {chat && chat.ok && <span className="help" role="status">{chat.name} · {chat.chat_id}</span>}
                      <span className="help">{t("find_chat_id_hint")}</span>
                    </div>
                  )}
                  {channel === "ntfy" && (
                    <div className="flex flex-wrap items-center gap-2">
                      <button type="button" className="btn btn-sm" onClick={() => set("NTFY_TOPIC", randomTopic())}>{t("random_topic")}</button>
                      {draft.NTFY_TOPIC && draft.NTFY_TOPIC.startsWith("tantalus-") && <span className="mono">{draft.NTFY_TOPIC}</span>}
                    </div>
                  )}
                </>
              )}
            </SecretsForm>
            {channel === "ntfy" && <TextSetting label={t("ntfy_server")} value={settings["notify.ntfy.server"]} fallback="https://ntfy.sh" onSave={(v) => setSetting("notify.ntfy.server", v)} busy={busy["notify.ntfy.server"]} />}
          </div>
        </details>
      )}
    </article>
  );
}

function TextSetting({ label, value, fallback = "", placeholder = "", onSave, busy, className = "max-w-[420px]" }) {
  const { t } = useApp();
  const [v, setV] = useState(value || fallback);
  return (
    <div className="flex items-end gap-2">
      <Field label={label} className={`${className} flex-1`}><input className="field" value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)} /></Field>
      <Busy type="button" className="btn btn-sm" busy={busy} onClick={() => onSave(v.trim())} disabled={v.trim() === (value || fallback)}>{t("save")}</Busy>
    </div>
  );
}

// E-mail can go out through the account configured in Faustus (its password stays in Faustus) or through SMTP credentials of its own.
function EmailBackend({ info, settings, setSetting, busy }) {
  const { t } = useApp();
  const mode = settings["notify.email.backend"] || "auto";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="label">{t("email_backend")}</span>
          <select className="field" style={{ width: "auto" }} value={mode} disabled={busy["notify.email.backend"]} onChange={(e) => setSetting("notify.email.backend", e.target.value)}>
            {["auto", "faustus", "smtp"].map((m) => <option key={m} value={m}>{t(`email_backend_${m}`)}</option>)}
          </select>
        </label>
        <Chip className={info.backend === "faustus" ? "chip-accent" : ""}>{t("email_backend_now")}: {info.backend === "faustus" ? "Faustus" : "SMTP"}</Chip>
      </div>
      {info.backend === "faustus" && <p className="help">{t("email_faustus_hint")}</p>}
      {mode !== "smtp" && (
        <details open={info.backend === "faustus" && !info.configured}>
          <summary className="font-semibold">{t("email_faustus_options")}</summary>
          <div className="mt-3 space-y-3">
            <TextSetting label={t("email_faustus_dir")} value={settings["notify.email.faustus_dir"]} placeholder={info.faustus_dir || t("email_faustus_dir_auto")}
              onSave={(v) => setSetting("notify.email.faustus_dir", v)} busy={busy["notify.email.faustus_dir"]} className="max-w-[520px]" />
            <TextSetting label={t("email_faustus_owner")} value={settings["notify.email.faustus_owner"]} placeholder={t("email_faustus_owner_auto")}
              onSave={(v) => setSetting("notify.email.faustus_owner", v)} busy={busy["notify.email.faustus_owner"]} className="max-w-[260px]" />
          </div>
        </details>
      )}
    </div>
  );
}

export default function Settings() {
  const { t, lang, setLang, health, refreshDash, refreshHealth, notify } = useApp();
  const [busy, run] = useBusy();
  const ns = useLoad(() => api.call("notify_status"), []);
  const st = useLoad(() => api.status(), []);
  const sc = useLoad(() => api.call("scheduler_status"), []);
  const [tests, setTests] = useState({});
  const [fb, setFb] = useState(null);
  const reloadAll = async () => { await Promise.all([ns.reload(), st.reload(), sc.reload()]); refreshDash(); refreshHealth(); };

  if (!ns.data || !st.data || !sc.data) {
    return <div className="space-y-3"><h1>{t("nav_settings")}</h1><ErrorBox error={ns.error || st.error || sc.error} /><p className="help">…</p></div>;
  }
  const { channels, secrets, settings, recent } = ns.data;
  const status = st.data;
  const hosts = sc.data.hosts || [];

  const setSetting = (key, value) => run(key, async () => {
    await api.call("settings_set", { values: { [key]: value } });
    notify(t("saved"));
    await reloadAll();
  });
  const test = (channel) => run(`test-${channel}`, async () => {
    const r = await api.call("notify_test", { channel });
    setTests((x) => ({ ...x, [channel]: r }));
    await ns.reload();
  });
  const fbLogin = () => run("fb", async () => {
    const r = await api.call("secondhand_facebook_login");
    setFb(r);
    notify(r.ready ? t("fb_ready") : r.message || t("fb_opened"));
  });

  const uptime = (s) => (s >= 86400 ? `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h` : s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min` : `${Math.floor(s / 60)} min`);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center gap-2"><h1 className="mr-auto">{t("nav_settings")}</h1><button type="button" className="btn btn-sm" onClick={reloadAll}>{t("refresh")}</button></header>

      <Section id="sec-general" title={t("sec_general")}>
        <div className="panel grid gap-4 md:grid-cols-3">
          <label className="block">
            <span className="label">{t("language_label")}</span>
            <select className="field" value={lang} onChange={(e) => { setLang(e.target.value); notify(t("saved")); }}>
              <option value="es">Español</option><option value="en">English</option>
            </select>
            <span className="help mt-1 block">{t("language_hint")}</span>
          </label>
          <div className="space-y-1">
            <span className="label">{t("llm_enabled")}</span>
            <Switch checked={settings["llm.enabled"] === "1"} disabled={busy["llm.enabled"]} onChange={(v) => setSetting("llm.enabled", v ? "1" : "0")} label={t("llm_enabled")} />
            <span className="help block">{t("llm_hint")} {status.llm && <>· <span title={status.llm.reason || ""}>{status.llm.available ? t("model_available") : t("model_unavailable")}</span></>}</span>
          </div>
          <div className="space-y-1">
            <span className="label">{t("scheduler_paused")}</span>
            <Switch checked={settings["scheduler.paused"] === "1"} disabled={busy["scheduler.paused"]} onChange={(v) => setSetting("scheduler.paused", v ? "1" : "0")} label={t("scheduler_paused")} />
            <span className="help block">{t("scheduler_paused_hint")}</span>
          </div>
        </div>
      </Section>

      <Section id="sec-channels" title={t("sec_channels")}>
        <div className="grid gap-4 xl:grid-cols-2">
          {CHANNELS.map((c) => (
            <ChannelCard key={c} channel={c} info={channels[c] || {}} secrets={secrets} settings={settings} onChanged={reloadAll}
              testResult={tests[c]} onTest={test} testing={busy[`test-${c}`]} />
          ))}
        </div>
      </Section>

      <Section id="sec-search-keys" title={t("sec_search_keys")}>
        <div className="panel space-y-3">
          <p className="help">{t("search_keys_hint")}</p>
          <SecretsForm fields={SEARCH_SECRETS} secrets={secrets} onSaved={reloadAll} />
        </div>
      </Section>

      <Section id="sec-facebook" title={t("sec_facebook")}>
        <div className="panel space-y-2">
          <p>{t("facebook_explain")}</p>
          <div className="flex flex-wrap items-center gap-3">
            <Busy className="btn btn-primary" busy={busy.fb} onClick={fbLogin}><Icon d="M15 3h6v6M10 14L21 3M19 13v7H4V5h7" size={14} />{t("facebook_login")}</Busy>
            {busy.fb && <span className="help" role="status">{t("resolve_waiting")}</span>}
          </div>
          {fb && <div className={`banner ${fb.ready ? "banner-info" : "banner-warn"}`} role="status">{fb.ready ? t("fb_ready") : t("fb_not_ready")}{fb.message ? ` — ${fb.message}` : ""}</div>}
        </div>
      </Section>

      <Section id="sec-recent-notifs" title={t("sec_recent_notifs")} count={(recent || []).length}>
        {!(recent || []).length ? <Empty>{t("no_notifications")}</Empty> : (
          <div className="panel scroll-x p-0">
            <table>
              <thead><tr><th>{t("time")}</th><th>{t("channel")}</th><th>{t("result")}</th><th>{t("event")}</th></tr></thead>
              <tbody>
                {recent.map((n) => (
                  <tr key={n.id}>
                    <td className="whitespace-nowrap" title={clock(n.sent_at, lang)}>{shortClock(n.sent_at, lang)}</td>
                    <td>{t(`ch_${n.channel}`)}</td>
                    <td><Chip className={n.ok ? "chip-ok" : "chip-danger"}>{n.ok ? t("ok") : n.error || t("failed")}</Chip></td>
                    <td className="mono">{n.event_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section id="sec-hosts" title={t("sec_hosts")} count={hosts.length}>
        <p className="help">{t("hosts_hint")}</p>
        {!hosts.length ? <Empty>{t("no_hosts")}</Empty> : (
          <div className="panel scroll-x p-0">
            <table>
              <thead><tr><th>{t("host")}</th><th>{t("last_fetch")}</th><th>{t("blocked_until")}</th><th>{t("reason")}</th><th>{t("preferred_tier")}</th><th className="r">{t("ok_fail")}</th><th className="r">{t("min_interval")}</th></tr></thead>
              <tbody>
                {hosts.map((h) => (
                  <tr key={h.host}>
                    <td className="font-semibold">{h.host}</td>
                    <td><Rel ts={h.last_fetch_ts} /></td>
                    <td>{h.blocked_now ? <Chip className="chip-danger"><Rel ts={h.blocked_until_ts} /></Chip> : <span className="help">—</span>}</td>
                    <td>{h.block_reason || "—"}</td>
                    <td>{h.preferred_tier || "—"}</td>
                    <td className="r num">{h.ok_count} / {h.fail_count}</td>
                    <td className="r num">{h.min_interval_s} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section id="sec-status" title={t("sec_status")}>
        <div className="panel space-y-3">
          <div className="kpis">
            <div className="kpi"><b className="num">{status.version}</b><span>{t("version")}</span></div>
            <div className="kpi"><b className="num">{uptime(status.uptime_s)}</b><span>{t("uptime")}</span></div>
            {Object.entries(status.counts || {}).map(([k, v]) => <div key={k} className="kpi"><b className="num">{num(v, 0, lang)}</b><span>{t(`count_${k}`) === `count_${k}` ? k : t(`count_${k}`)}</span></div>)}
          </div>
          <dl className="m-0 grid gap-x-6 gap-y-1.5 md:grid-cols-2">
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2"><dt className="help">{t("data_dir")}</dt><dd className="m-0 mono">{status.data_dir}</dd></div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2"><dt className="help">{t("search_engines")}</dt><dd className="m-0">{(status.search_engines || []).join(", ") || "—"}</dd></div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2"><dt className="help">{t("browser")}</dt><dd className="m-0">{status.browser?.enabled ? t("enabled") : t("disabled")} · Playwright: {status.browser?.playwright ? t("yes") : t("no")}</dd></div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2"><dt className="help">{t("model")}</dt><dd className="m-0"><span title={status.llm?.reason || ""}>{status.llm?.available ? t("model_available") : t("model_unavailable")}</span> · {t("llm_calls", { calls: status.llm?.calls ?? 0, failures: status.llm?.failures ?? 0 })}</dd></div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2"><dt className="help">{t("scheduler")}</dt><dd className="m-0">{status.scheduler?.running ? (status.scheduler.paused ? t("sched_paused") : t("sched_idle")) : t("sched_off")} · {t("sched_queue", { n: status.scheduler?.queue ?? 0 })} · {t("sched_done", { n: status.scheduler?.jobs_done ?? 0 })}</dd></div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2"><dt className="help">{t("offline")}</dt><dd className="m-0">{status.offline ? t("yes") : t("no")}{health?.hoard_link ? "" : ""}</dd></div>
          </dl>
        </div>
      </Section>
    </div>
  );
}
