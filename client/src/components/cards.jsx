import React from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { hostOf, num, safeUrl } from "../format.js";
import { Busy, Chip, Confidence, EventChip, EventStatusPill, ExtLink, Icon, Price, Rel, ScoreBadge, StatePill, Thumb, VerdictChip, signalsTitle, useBusy } from "./ui.jsx";

const summarizeSend = (results, t) =>
  (results || []).map((r) => `${t(`ch_${r.channel}`)} ${r.ok ? "✓" : `✗ ${r.error || ""}`}`.trim()).join(" · ") || t("nothing_sent");

// One event as a card: type chip, title, summary, price, confidence, time, image, link and actions.
export function EventCard({ event, watcherName, onChanged, compact }) {
  const { t, notify } = useApp();
  const [busy, run] = useBusy();
  const image = event.data?.image;
  const dismiss = () => run("dismiss", async () => {
    await api.call("event_dismiss", { event_id: event.id });
    notify(t("event_dismissed"));
    onChanged?.();
  });
  const resend = () => run("notify", async () => {
    const r = await api.call("event_notify", { event_id: event.id });
    notify(summarizeSend(r.results, t));
    onChanged?.();
  });
  const showOld = event.old_price !== null && event.old_price !== undefined && event.type.includes("DROP");
  return (
    <article className="panel flex gap-3" aria-label={event.title}>
      {!compact && (image ? <Thumb src={image} large /> : null)}
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <EventChip type={event.type} />
          {event.status !== "confirmed" && <EventStatusPill status={event.status} />}
          <Confidence value={event.confidence} factors={event.data?.factors} />
          <span className="help ml-auto"><Rel ts={event.detected_at} /></span>
        </div>
        <h3 className="clamp2">{event.title}</h3>
        {event.summary && <p className="help clamp2" title={event.summary}>{event.summary}</p>}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {event.price !== null && event.price !== undefined && <Price value={event.price} currency={event.currency} old={showOld ? event.old_price : undefined} />}
          {event.new_state && <StatePill state={event.new_state} />}
          {watcherName && <span className="help trunc" style={{ maxWidth: 220 }}>{watcherName}</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <ExtLink href={event.url}><Icon d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" size={13} />{t("open")}</ExtLink>
          {event.target_id && <a className="btn btn-sm" href={`#/target/${encodeURIComponent(event.target_id)}`}>{t("see_product")}</a>}
          {event.status !== "dismissed" && (
            <>
              <Busy className="btn btn-sm" busy={busy.dismiss} onClick={dismiss}>{t("dismiss")}</Busy>
              <Busy className="btn btn-sm" busy={busy.notify} onClick={resend} title={t("resend_hint")}>{t("resend")}</Busy>
            </>
          )}
        </div>
      </div>
    </article>
  );
}

// A second-hand listing: thumb, price, distance, score (signals in the tooltip), reason and save / dismiss.
export function ListingCard({ listing, onChanged, watcherName, showStatus }) {
  const { t, lang, notify } = useApp();
  const [busy, run] = useBusy();
  const set = (status) => run(status, async () => {
    await api.call("listing_set", { listing_id: listing.id, status });
    notify(t(`listing_${status}`));
    onChanged?.();
  });
  const url = safeUrl(listing.url);
  return (
    <article className="panel flex gap-3" aria-label={listing.title}>
      <Thumb src={listing.image_url} large />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <ScoreBadge score={listing.score} signals={listing.signals} />
          <Chip>{listing.source}</Chip>
          {listing.reserved && <Chip className="chip-amber">{t("reserved")}</Chip>}
          {listing.shipping && <Chip>{t("shipping")}</Chip>}
          {showStatus && listing.status !== "new" && <Chip className="chip-accent">{t(`lst_${listing.status}`)}</Chip>}
          <span className="help ml-auto"><Rel ts={listing.first_seen_ts} /></span>
        </div>
        <h3 className="clamp2">{url ? <a href={url} target="_blank" rel="noopener noreferrer" style={{ color: "inherit" }}>{listing.title || url}</a> : listing.title}</h3>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          <Price value={listing.price} currency={listing.currency} />
          {listing.distance_km !== null && listing.distance_km !== undefined && <span className="help num">{num(listing.distance_km, 0, lang)} km</span>}
          {listing.location_text && <span className="help trunc" style={{ maxWidth: 200 }}>{listing.location_text}</span>}
          {watcherName && <span className="help trunc" style={{ maxWidth: 200 }}>{watcherName}</span>}
        </div>
        {listing.reason && <p className="help clamp2" title={`${listing.reason}\n${signalsTitle(listing.signals)}`}>{listing.reason}</p>}
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <ExtLink href={listing.url}>{t("open")}</ExtLink>
          {listing.status !== "saved" && <Busy className="btn btn-sm" busy={busy.saved} onClick={() => set("saved")}>{t("save")}</Busy>}
          {listing.status === "saved" && <Busy className="btn btn-sm" busy={busy.seen} onClick={() => set("seen")}>{t("unsave")}</Busy>}
          {listing.status !== "dismissed" && <Busy className="btn btn-sm" busy={busy.dismissed} onClick={() => set("dismissed")}>{t("dismiss")}</Busy>}
          {listing.status === "dismissed" && <Busy className="btn btn-sm" busy={busy.new} onClick={() => set("new")}>{t("restore")}</Busy>}
        </div>
      </div>
    </article>
  );
}

export function InfoRow({ item, watcherName, onChanged }) {
  const { t, notify, refreshDash } = useApp();
  const [busy, run] = useBusy();
  const set = (status) => run(status, async () => {
    await api.call("info_item_set", { item_id: item.id, status });
    notify(t(`info_${status}`));
    await refreshDash();
    onChanged?.();
  });
  const url = safeUrl(item.url);
  return (
    <article className="panel space-y-1" aria-label={item.title}>
      <div className="flex flex-wrap items-center gap-2">
        <VerdictChip verdict={item.verdict} />
        {item.material && <Chip className="chip-accent">{t("material")}</Chip>}
        <Chip>{t(`kind_${item.kind}`)}</Chip>
        <span className="help">{hostOf(item.url)}</span>
        <span className="help ml-auto"><Rel ts={item.first_seen_ts} /></span>
      </div>
      <h3>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{item.title || url}</a> : item.title}</h3>
      {(item.reason || item.snippet) && <p className="help clamp2" title={item.snippet}>{item.reason || item.snippet}</p>}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <ExtLink href={item.url}>{t("open")}</ExtLink>
        {item.status === "new" && <Busy className="btn btn-sm" busy={busy.seen} onClick={() => set("seen")}>{t("mark_seen")}</Busy>}
        {item.status === "seen" && <Chip className="chip-accent">{t("info_seen_chip")}</Chip>}
        {item.status !== "dismissed" && <Busy className="btn btn-sm" busy={busy.dismissed} onClick={() => set("dismissed")}>{t("dismiss")}</Busy>}
        {item.status === "dismissed" && <Busy className="btn btn-sm" busy={busy.new} onClick={() => set("new")}>{t("restore")}</Busy>}
        {watcherName && <span className="help ml-auto">{watcherName}</span>}
      </div>
    </article>
  );
}

export function CandidateRow({ candidate, onChanged, watcherName }) {
  const { t, notify } = useApp();
  const [busy, run] = useBusy();
  const accept = () => run("accept", async () => {
    const r = await api.call("candidate_accept", { candidate_id: candidate.id, check_now: true });
    const chk = r.check;
    notify(chk && chk.ok === false ? `${t("candidate_accepted")} — ${chk.error || ""}` : t("candidate_accepted"));
    onChanged?.();
  });
  const reject = () => run("reject", async () => {
    await api.call("candidate_reject", { candidate_id: candidate.id });
    notify(t("candidate_rejected"));
    onChanged?.();
  });
  const url = safeUrl(candidate.url);
  return (
    <article className="panel panel-tight flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1" aria-label={candidate.title || candidate.url}>
      <div className="min-w-[200px] flex-1">
        <div className="trunc font-semibold" title={candidate.title}>{url ? <a href={url} target="_blank" rel="noopener noreferrer" style={{ color: "inherit" }}>{candidate.title || url}</a> : candidate.title}</div>
        <div className="help trunc" title={candidate.url}>{candidate.retailer || candidate.host} · {hostOf(candidate.url)}{watcherName ? ` · ${watcherName}` : ""}</div>
        {candidate.reason && <div className="help clamp2" title={candidate.snippet}>{candidate.reason}</div>}
      </div>
      <Chip title={t("candidate_score")}>{num(candidate.score, 0)}</Chip>
      <div className="flex gap-2">
        <Busy className="btn btn-sm btn-primary" busy={busy.accept} onClick={accept}>{t("accept")}</Busy>
        <Busy className="btn btn-sm" busy={busy.reject} onClick={reject}>{t("reject")}</Busy>
      </div>
    </article>
  );
}
