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
  const nothing = !dash.news.length && !dash.buyable.length && !dash.needs_human.length && !dash.listings.length && !dash.info.length && !dash.candidates.length;
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

      {dash.news.length > 0 && (
        <Section id="sec-news" title={t("sec_news")} count={dash.news.length}>
          <div className="card-grid">
            {dash.news.map((e) => <EventCard key={e.id} event={e} watcherName={wname[e.watcher_id]} onChanged={reload} />)}
          </div>
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
