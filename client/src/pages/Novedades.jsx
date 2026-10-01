import React from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { CandidateRow, EventCard, InfoRow, ListingCard } from "../components/cards.jsx";
import { Busy, Chip, Confidence, Empty, ErrorBox, EventChip, ExtLink, Icon, Price, Rel, Section, StatePill, Thumb, useBusy } from "../components/ui.jsx";
import { clock, shortClock } from "../format.js";
import { MODE_META } from "../meta.js";

function BuyableCard({ card }) {
  const { t } = useApp();
  return (
    <article className="panel flex gap-3" aria-label={card.label || card.last_title}>
      <Thumb src={card.last_image} />
      <div className="min-w-0 flex-1 space-y-1">
        <h3 className="clamp2">{card.label || card.last_title || card.url}</h3>
        <div className="flex flex-wrap items-center gap-2">
          <StatePill state={card.last_state} />
          <Price value={card.last_price} currency={card.last_currency} />
          <Confidence value={card.last_confidence} />
        </div>
        <div className="help">{card.retailer || card.host} · <Rel ts={card.last_check_ts} /></div>
        <div className="flex flex-wrap gap-2 pt-0.5">
          <ExtLink href={card.url}><Icon d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" size={13} />{t("open")}</ExtLink>
          <a className="btn btn-sm" href={`#/target/${encodeURIComponent(card.id)}`}>{t("see_product")}</a>
        </div>
      </div>
    </article>
  );
}

function NeedsHelpCard({ card, onDone }) {
  const { t, notify } = useApp();
  const [busy, run] = useBusy();
  const resolve = () => run("resolve", async () => {
    const r = await api.call("target_resolve", { target_id: card.id });
    const chk = r.check;
    notify(chk && chk.ok ? `${t("resolve_done")}: ${chk.state ? t(`state_${chk.state}`) : "OK"}` : `${t("resolve_done")}${chk?.error ? ` — ${chk.error}` : ""}`);
    onDone?.();
  });
  return (
    <article className="panel space-y-1.5" style={{ borderColor: "#e5604b66" }} aria-label={card.label || card.last_title}>
      <div className="flex items-start gap-3">
        <Thumb src={card.last_image} />
        <div className="min-w-0 flex-1">
          <h3 className="clamp2">{card.label || card.last_title || card.url}</h3>
          <div className="help">{card.retailer || card.host}</div>
          <div className="mt-1" style={{ color: "#ffb9ac" }}>{card.last_error || t("blocked_generic")}</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Busy className="btn btn-sm btn-primary" busy={busy.resolve} onClick={resolve}>{t("resolve")}</Busy>
        <ExtLink href={card.url}>{t("open")}</ExtLink>
        <a className="btn btn-sm" href={`#/target/${encodeURIComponent(card.id)}`}>{t("see_product")}</a>
      </div>
      {busy.resolve && <p className="help" role="status">{t("resolve_waiting")}</p>}
    </article>
  );
}


function relWhen(t, days) {
  if (days === null || days === undefined) return t("rel_no_date");
  if (days === 0) return t("rel_today");
  if (days === 1) return t("rel_tomorrow");
  if (days === -1) return t("rel_yesterday");
  return days > 0 ? t("rel_in_days", { n: days }) : t("rel_days_ago", { n: -days });
}

function dayLabel(iso, lang) {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString(lang === "en" ? "en-GB" : "es-ES", { weekday: "short", day: "numeric", month: "short" });
}

function ReleaseCard({ rel }) {
  const { t, lang } = useApp();
  const data = rel.data || {};
  const where = data.where || {};
  const chains = where.chains || [];
  const shops = where.shops || [];
  const hot = rel.days === 0;
  return (
    <article className="panel space-y-2" style={hot ? { borderColor: "var(--accent)" } : undefined} aria-label={rel.title}>
      <div className="flex items-start gap-3">
        <div className="shrink-0 rounded-md px-2 py-1 text-center" style={{ background: hot ? "var(--accent)" : "var(--panel-2, #ffffff10)", color: hot ? "#1a1205" : "var(--ink)", minWidth: 64 }}>
          <div className="text-xs font-semibold uppercase">{relWhen(t, rel.days)}</div>
          <div className="text-xs">{dayLabel(rel.date, lang)}</div>
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="clamp2">{rel.title}</h3>
          <div className="help">{[rel.kind, (data.products || []).join(" · ")].filter(Boolean).join(" — ")}</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {data.stores_total != null && <Chip>{t("rel_stores_n", { n: data.stores_total })}</Chip>}
            {data.stores_buyable > 0 && <Chip className="chip-ok">{t("rel_shops")}: {data.stores_buyable}</Chip>}
            {data.stores_soldout > 0 && <Chip className="chip-danger">{t("rel_soldout_n", { n: data.stores_soldout })}</Chip>}
          </div>
        </div>
      </div>
      {chains.length > 0 && (
        <div>
          <span className="label">{t("rel_chains")}</span>
          <div className="flex flex-wrap gap-1.5">
            {chains.map((c) => {
              const buy = c.state === "IN_STOCK" || c.state === "PREORDER";
              const chip = <Chip className={buy ? (c.state === "PREORDER" ? "chip-info" : "chip-ok") : "chip-danger"}>{c.store} · {t(`state_${c.state}`)}{c.price ? ` · ${c.price.toFixed(2).replace(".", ",")} €` : ""}</Chip>;
              return buy && c.url ? <a key={c.slug} href={c.url} target="_blank" rel="noopener noreferrer" className="no-underline">{chip}</a> : <span key={c.slug}>{chip}</span>;
            })}
          </div>
        </div>
      )}
      <div>
        <span className="label">{t("rel_shops")}</span>
        {shops.length ? (
          <ul className="m-0 list-none space-y-0.5 p-0">
            {shops.slice(0, 8).map((sh, i) => (
              <li key={`${sh.slug}-${i}`} className="flex items-center gap-2">
                <a className="trunc flex-1" href={sh.url} target="_blank" rel="noopener noreferrer">{sh.store}</a>
                <span className="help trunc">{[sh.fmt, sh.lang].filter(Boolean).join(" · ")}</span>
                {sh.state === "PREORDER" && <Chip className="chip-info">{t("state_PREORDER")}</Chip>}
                <Price value={sh.price} currency={sh.currency} />
              </li>
            ))}
            {where.shops_total > 8 && <li className="help">{t("rel_more_shops", { n: where.shops_total - 8 })}</li>}
          </ul>
        ) : <p className="help m-0">{t("rel_no_shops")}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        {rel.url && <ExtLink href={rel.url}>{t("rel_see_page")}</ExtLink>}
        {data.buy_url && data.buy_url !== rel.url && <ExtLink href={data.buy_url}>{t("open")}</ExtLink>}
      </div>
    </article>
  );
}

function ChainsPanel({ chains }) {
  const { t } = useApp();
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
      {chains.map((c) => (
        <article key={c.slug} className="panel panel-tight space-y-1.5" aria-label={c.store}>
          <div className="flex items-center gap-2">
            <span className="font-semibold">{c.store}</span>
            {c.buyable.length > 0 ? <Chip className="chip-ok">{t("n_buyable", { n: c.buyable.length })}</Chip> : <Chip>{t("chain_none")}</Chip>}
          </div>
          {c.buyable.length > 0 && (
            <ul className="m-0 list-none space-y-0.5 p-0">
              {c.buyable.slice(0, 8).map((b, i) => (
                <li key={i} className="flex items-center gap-2">
                  <a className="trunc flex-1" href={b.url} target="_blank" rel="noopener noreferrer" title={b.title}>{b.title}</a>
                  <StatePill state={b.state} />
                  <Price value={b.price} currency={b.currency} />
                </li>
              ))}
            </ul>
          )}
          {c.soldout.length > 0 && (
            <details>
              <summary className="help cursor-pointer">{t("chain_recent_out")} ({c.soldout.length})</summary>
              <ul className="m-0 list-none space-y-0.5 p-0 pt-1">
                {c.soldout.map((b, i) => (
                  <li key={i} className="flex items-center gap-2 help">
                    <span className="trunc flex-1" title={b.title}>{b.title}</span>
                    {b.lang && <span>{b.lang}</span>}
                    <Price value={b.price} currency={b.currency} />
                    <Rel ts={b.last_change_ts} />
                  </li>
                ))}
              </ul>
            </details>
          )}
        </article>
      ))}
    </div>
  );
}

function WatcherStrip({ watchers }) {
  const { t } = useApp();
  if (!watchers.length) return null;
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
      {watchers.map((w) => (
        <a key={w.id} href={`#/watchers/${encodeURIComponent(w.id)}`} className="panel panel-tight block min-w-0 space-y-1 no-underline" style={{ color: "var(--ink)", opacity: w.enabled ? 1 : 0.6 }}>
          <div className="flex items-center gap-2">
            <Icon d={MODE_META[w.mode]?.icon || ""} size={15} color="var(--accent)" />
            <span className="trunc font-semibold">{w.name}</span>
            {!w.enabled && <Chip>{t("paused")}</Chip>}
            {w.unseen > 0 && <span className="nav-badge" title={t("unseen_n", { n: w.unseen })}>{w.unseen}</span>}
          </div>
          <div className="help flex flex-wrap gap-x-3">
            <span>{t("last_check")}: <Rel ts={w.last_check_ts} /></span>
            <span>{t("next_run")}: <Rel ts={w.next_run_ts} /></span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {w.mode === "availability" && <Chip>{t("n_targets", { n: w.targets })}</Chip>}
            {w.buyable > 0 && <Chip className="chip-ok">{t("n_buyable", { n: w.buyable })}</Chip>}
            {w.needs_human > 0 && <Chip className="chip-danger">{t("n_needs_human", { n: w.needs_human })}</Chip>}
            {w.listings_new > 0 && <Chip className="chip-accent">{t("n_listings_new", { n: w.listings_new })}</Chip>}
            {w.info_material > 0 && <Chip className="chip-accent">{t("n_info_material", { n: w.info_material })}</Chip>}
          </div>
          {w.last_error && <div className="trunc" style={{ color: "#ffb9ac" }} title={w.last_error}>{w.last_error}</div>}
        </a>
      ))}
    </div>
  );
}

export default function Novedades() {
  const { t, lang, dash, dashError, refreshDash, refreshHealth, notify, toastError } = useApp();
  const [busy, run] = useBusy();
  if (!dash) return <div className="space-y-3"><h1>{t("news_title")}</h1>{dashError ? <ErrorBox error={dashError} /> : <p className="help">…</p>}</div>;

  const wname = Object.fromEntries(dash.watchers.map((w) => [w.id, w.name]));
  const markSeen = () => run("visit", async () => {
    const r = await api.visit();
    notify(t("marked_seen", { n: r.marked_seen }));
    await refreshDash();
    refreshHealth();
  });
  const reload = () => refreshDash();
  const releases = (dash.releases || []).filter((r) => r.days === null || r.days === undefined || r.days >= -1);
  const chains = dash.chains || [];
  const radarOn = (dash.radar?.watchers || []).length > 0;
  const runRadar = () => run("radar", async () => {
    const r = await api.call("radar_run", {});
    const n = (r.result?.watchers || []).reduce((acc, w) => acc + (w.events || 0), 0);
    notify(t("radar_done", { n }));
    await refreshDash();
  });
  const nothing = !(dash.releases || []).length && !dash.news.length && !dash.buyable.length && !dash.needs_human.length && !dash.listings.length && !dash.info.length && !dash.candidates.length;
  const noWatchers = dash.watchers.length === 0;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <h1>{t("news_title")}</h1>
          <p className="help">
            {dash.last_visit_ts
              ? <>{t("last_visit")}: <Rel ts={dash.last_visit_ts} /> <span title={clock(dash.last_visit_ts, lang)}>({shortClock(dash.last_visit_ts, lang)})</span></>
              : t("first_visit")}
            {" · "}
            <strong style={{ color: dash.news_count ? "var(--light)" : undefined }}>{t("news_count", { n: dash.news_count })}</strong>
          </p>
        </div>
        <Busy className="btn btn-primary" busy={busy.visit} onClick={markSeen}>
          <Icon d="M5 12l5 5 9-10" size={15} />{t("mark_all_seen")}
        </Busy>
      </header>

      {nothing && (
        <Empty>
          <div className="mx-auto max-w-[560px] space-y-2 py-4 text-left">
            <h2 style={{ color: "var(--ink)" }}>{noWatchers ? t("empty_nowatch_title") : t("empty_title")}</h2>
            <p>{noWatchers ? t("empty_nowatch_body") : t("empty_body")}</p>
            <ul className="help m-0 list-disc pl-5">
              <li>{t("empty_li_news")}</li>
              <li>{t("empty_li_buyable")}</li>
              <li>{t("empty_li_second")}</li>
              <li>{t("empty_li_info")}</li>
            </ul>
            <div className="flex flex-wrap gap-2 pt-3">
              <a className="btn btn-primary" href="#/watchers/new">{t("new_watcher")}</a>
              <a className="btn" href="#/watchers">{t("templates")}</a>
              <a className="btn" href="#/search">{t("nav_search")}</a>
            </div>
          </div>
        </Empty>
      )}

      {radarOn && releases.length > 0 && (
        <Section id="sec-releases" title={t("sec_releases")} count={releases.length}
          actions={<Busy className="btn btn-sm" busy={busy.radar} onClick={runRadar}>{t("radar_run")}</Busy>}>
          <p className="help">{t("releases_explain")}{dash.radar?.last_run_ts ? <> · {t("radar_last_run")}: <Rel ts={dash.radar.last_run_ts} /></> : null}</p>
          <div className="card-grid">{releases.map((r) => <ReleaseCard key={r.id} rel={r} />)}</div>
        </Section>
      )}

      {dash.news.length > 0 && (
        <Section id="sec-news" title={t("sec_news")} count={dash.news.length}>
          <div className="card-grid">
            {dash.news.map((e) => <EventCard key={e.id} event={e} watcherName={wname[e.watcher_id]} onChanged={reload} />)}
          </div>
        </Section>
      )}

      {radarOn && chains.length > 0 && (
        <Section id="sec-chains" title={t("sec_chains")} count={chains.reduce((a, c) => a + c.buyable.length, 0)}
          actions={releases.length ? null : <Busy className="btn btn-sm" busy={busy.radar} onClick={runRadar}>{t("radar_run")}</Busy>}>
          <p className="help">{t("chains_explain")}</p>
          <ChainsPanel chains={chains} />
        </Section>
      )}

      {dash.buyable.length > 0 && (
        <Section id="sec-buyable" title={t("sec_buyable")} count={dash.buyable.length}>
          <div className="card-grid">{dash.buyable.map((c) => <BuyableCard key={c.id} card={c} />)}</div>
        </Section>
      )}

      {dash.needs_human.length > 0 && (
        <Section id="sec-help" title={t("sec_help")} count={dash.needs_human.length}>
          <p className="help">{t("help_explain")}</p>
          <div className="card-grid">{dash.needs_human.map((c) => <NeedsHelpCard key={c.id} card={c} onDone={reload} />)}</div>
        </Section>
      )}

      {dash.watchers.length > 0 && (
        <Section id="sec-watchers" title={t("sec_watchers")} count={dash.watchers.length} actions={<a className="btn btn-sm" href="#/watchers">{t("manage")}</a>}>
          <WatcherStrip watchers={dash.watchers} />
        </Section>
      )}

      {dash.listings.length > 0 && (
        <Section id="sec-listings" title={t("sec_listings")} count={dash.listings.length}>
          <div className="card-grid">{dash.listings.map((l) => <ListingCard key={l.id} listing={l} watcherName={wname[l.watcher_id]} onChanged={reload} />)}</div>
        </Section>
      )}

      {dash.info.length > 0 && (
        <Section id="sec-info" title={t("sec_info")} count={dash.info.length}>
          <div className="card-grid">{dash.info.map((i) => <InfoRow key={i.id} item={i} watcherName={wname[i.watcher_id]} />)}</div>
        </Section>
      )}

      {dash.candidates.length > 0 && (
        <Section id="sec-candidates" title={t("sec_candidates")} count={dash.candidates.length}>
          <p className="help">{t("candidates_explain")}</p>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">{dash.candidates.map((c) => <CandidateRow key={c.id} candidate={c} watcherName={wname[c.watcher_id]} onChanged={reload} />)}</div>
        </Section>
      )}

      {dash.recent.length > 0 && (
        <Section id="sec-recent" title={t("sec_recent")} count={dash.recent.length} actions={<a className="btn btn-sm" href="#/events">{t("see_all")}</a>}>
          <div className="panel scroll-x p-0">
            <table>
              <tbody>
                {dash.recent.map((e) => (
                  <tr key={e.id}>
                    <td className="whitespace-nowrap" style={{ width: 1 }}><Rel ts={e.detected_at} /></td>
                    <td style={{ width: 1 }}><EventChip type={e.type} /></td>
                    <td><span className="clamp2">{e.title}</span></td>
                    <td className="r"><Price value={e.price} currency={e.currency} /></td>
                    <td className="r"><Confidence value={e.confidence} factors={e.data?.factors} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
