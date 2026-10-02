"""Mail deals: sale mails from game stores and book retailers, checked against what the user wants.

One scan reads the mailbox through Faustus (read-only), parses every new sale mail into ``mail_deals`` rows ("Ofertas del
correo"), marks the rows that match a wishlist or a watcher, and raises a ``MAIL_DEAL`` event only for those. Rules:

* the first scan is quiet: it fills the table and notifies nothing, so an old backlog never floods the phone;
* a deal notifies once, only when it matches, is not already owned, is still on and the mail is at most three days old;
* a deal ends at the sale's end date, or ``mail.deals.ttl_days`` after the mail when the mail states none;
* mails are never altered and links are never opened.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from ..engine import offer_matches
from ..model import MAIL_DEAL, Offer
from ..notify.labels import format_price
from . import noise as noise_mod
from .parse import madrid_offset, parse_mail, slug
from .repo import MailRepo
from .source import MailSource
from .stores import DEFAULT_STORES, domain_of, selected_stores, store_for
from .wishlist import SOURCE_STORE, SOURCE_WATCH, Wishlist, evaluate

log = logging.getLogger("tantalus.mail")

DEFAULTS = {"mail.deals.enabled": "1", "mail.deals.interval_min": "180", "mail.deals.history_days": "30", "mail.deals.ttl_days": "7",
            "mail.deals.stores": "", "mail.deals.domains": "", "mail.deals.gamerhoard_file": "", "mail.deals.wishlist": "",
            "mail.noise.days": "30"}
NUMERIC = {"mail.deals.interval_min": (15, 1440), "mail.deals.history_days": (1, 365), "mail.deals.ttl_days": (1, 60), "mail.noise.days": (1, 365)}
MAX_PER_RUN = 150
NOTIFY_AGE_DAYS = 3
EVENT_DEDUPE_DAYS = 14
HIGH_DISCOUNT = 50


def local_date(ts: float | None, lang: str = "es") -> str:
    if not ts:
        return ""
    moment = datetime.fromtimestamp(float(ts), timezone.utc)
    local = moment + timedelta(hours=madrid_offset(moment))
    months = ("ene feb mar abr may jun jul ago sep oct nov dic" if lang == "es" else "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec").split()
    return f"{local.day} {months[local.month - 1]} {local:%H:%M}"


class MailDeals:
    def __init__(self, store: Any, engine: Any, repo: MailRepo, source: MailSource, settings_get: Callable[[str, Optional[str]], Optional[str]],
                 set_setting: Callable[[str, str], None], clock: Callable[[], float] = time.time):
        self.store = store
        self.engine = engine
        self.repo = repo
        self.source = source
        self.get = settings_get
        self.put = set_setting
        self.clock = clock
        self._lock = threading.Lock()
        self._noise_cache: Optional[tuple[float, int, dict[str, Any]]] = None

    # ------------------------------------------------------------------ settings
    def _s(self, key: str) -> str:
        value = self.get(key, None)
        return DEFAULTS[key] if value in (None, "") else str(value)

    def _n(self, key: str) -> int:
        low, high = NUMERIC[key]
        try:
            return max(low, min(high, int(float(self._s(key)))))
        except ValueError:
            return int(DEFAULTS[key])

    def enabled(self) -> bool:
        return self._s("mail.deals.enabled") == "1"

    def _lang(self) -> str:
        lang = (self.get("notify.language", None) or self.get("ui.language", None) or "es").lower()[:2]
        return lang if lang in ("es", "en") else "es"

    def stores(self) -> list[dict[str, Any]]:
        return selected_stores(self._s("mail.deals.stores"), self._s("mail.deals.domains"))

    def wishlist(self) -> Wishlist:
        return Wishlist.load(games_file=self._s("mail.deals.gamerhoard_file"), manual=self._s("mail.deals.wishlist"))

    def watch_hits(self, haystack: str) -> list[dict[str, str]]:
        """Enabled watchers whose product terms all appear in the text (the same rule that decides which store tiles belong to them)."""
        hits = []
        offer = Offer(title=haystack)
        for watcher in self.store.watchers(enabled=True):
            try:
                terms, must, exclude = self.engine._match_config(watcher)
            except Exception:  # noqa: BLE001
                continue
            if (terms or must) and offer_matches(offer, terms, must, exclude):
                hits.append({"id": watcher["id"], "name": watcher["name"]})
        return hits

    # ------------------------------------------------------------------ schedule
    def due(self, now: float) -> bool:
        if not self.enabled() or self._lock.locked():
            return False
        last = float(self.get("mail.deals.last_attempt_ts", "0") or 0)
        return now - last >= self._n("mail.deals.interval_min") * 60

    def run_job(self, ref: str = "all") -> dict[str, Any]:
        return self.run()

    # ------------------------------------------------------------------ one scan
    def run(self, *, history_days: Optional[int] = None, limit: int = MAX_PER_RUN) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "skipped": "a scan is already running"}
        run_id = self.store.start_run("mail_deals")
        now = self.clock()
        summary: dict[str, Any] = {"ok": True, "scanned": 0, "sale_mails": 0, "deals_new": 0, "matched_new": 0, "notified": 0, "owned": 0,
                                   "quiet": False, "errors": []}
        try:
            self.put("mail.deals.last_attempt_ts", str(now))   # a failing mailbox backs off like a good run
            days = int(history_days or self._n("mail.deals.history_days"))
            stores = self.stores()
            domains = [d for s in stores for d in s["domains"]]
            first_scan = self.get("mail.deals.first_scan_done", "0") != "1"
            summary["quiet"] = first_scan
            skip = self.repo.seen_ids(since=now - (days + 60) * 86400)
            answer = self.source.scan(days, limit, domains, skip)
            messages = answer.get("messages") or []
            if not answer.get("ok") and not messages:
                summary.update(ok=False, errors=[str(answer.get("error") or "mail not readable")[:300]])
                return self._finish(run_id, summary, now)
            if answer.get("error"):
                summary["errors"].append(str(answer["error"])[:300])
            summary["accounts"] = answer.get("accounts") or []
            wl = self.wishlist()
            for msg in messages:
                summary["scanned"] += 1
                self._ingest(msg, stores, wl, first_scan, now, summary)
            if answer.get("via") == "hub":
                self.source.commit()                           # the hub's watermark moves only once its messages are stored
            if first_scan and len(messages) < limit and not answer.get("more"):
                self.put("mail.deals.first_scan_done", "1")   # the backlog is consumed: from now on a match notifies
            summary["notified"] += self._notify_pending(now)
            return self._finish(run_id, summary, now)
        except Exception as error:  # noqa: BLE001
            log.exception("mail deals scan failed")
            summary.update(ok=False, errors=[f"{type(error).__name__}: {error}"[:300]])
            return self._finish(run_id, summary, now)
        finally:
            self._lock.release()

    def _finish(self, run_id: int, summary: dict[str, Any], now: float) -> dict[str, Any]:
        summary["expired"] = self.repo.expire(self.clock())
        self.put("mail.deals.last_run_ts", str(self.clock()))
        self.put("mail.deals.last_summary", json.dumps(summary, ensure_ascii=False)[:4000])
        self.put("mail.deals.last_error", "; ".join(summary.get("errors") or [])[:300] if not summary.get("ok") else "")
        self.store.finish_run(run_id, bool(summary.get("ok")), {k: v for k, v in summary.items() if k != "accounts"})
        return summary

    def _ingest(self, msg: dict[str, Any], stores: list[dict[str, Any]], wl: Wishlist, first_scan: bool, now: float, summary: dict[str, Any]) -> None:
        sender = str(msg.get("from_address") or "")
        message_id = str(msg.get("message_id") or "") or "noid:" + hashlib.sha1(f"{sender}|{msg.get('subject')}|{msg.get('ts')}".encode()).hexdigest()[:24]
        shop = store_for(sender, stores)
        rows = parse_mail(msg, shop) if shop else []
        ttl = self._n("mail.deals.ttl_days")
        mail_ts = float(msg.get("ts") or 0) or now
        if rows:
            summary["sale_mails"] += 1
        first_saved = None
        for row in rows:
            expires = row["ends_ts"] if row.get("ends_known") and row.get("ends_ts") else mail_ts + ttl * 86400
            deal = {**row, "message_id": message_id, "store_id": shop["id"], "store": shop["name"], "subject": str(msg.get("subject") or "")[:300],
                    "sender_domain": domain_of(sender), "mail_ts": mail_ts, "first_seen_ts": now, "expires_ts": expires,
                    "status": "expired" if expires < now else "active", "quiet": first_scan}
            verdict = evaluate(deal, wl, store_wishlist=bool(shop.get("wishlist_style")), watch_hits=self.watch_hits)
            deal.update(wishlist=verdict["wishlist"], matched=verdict["matched"], match_source=verdict["source"], match_label=verdict["label"],
                        owned=verdict["owned"])
            saved, created = self.repo.add(deal)
            if first_saved is None and saved and saved.get("id") is not None:
                first_saved = saved["id"]
            if created:
                summary["deals_new"] += 1
                summary["matched_new"] += 1 if verdict["matched"] else 0
                summary["owned"] += 1 if verdict["owned"] else 0

        self.repo.mark_seen(message_id, len(rows))
        if first_saved is not None and msg.get("hub_id") is not None:
            self.source.claim(msg["hub_id"], f"hoard://tantalus/deal/{first_saved}")      # "this mail is mine" (a hub-read mail)

    # ------------------------------------------------------------------ alerts
    def _summary(self, deal: dict[str, Any], lang: str) -> str:
        es = lang == "es"
        parts = [f"{deal['store']}: {deal['title']}"]
        if deal.get("discount_pct"):
            parts.append(("hasta " if es else "up to ") + f"-{deal['discount_pct']}%" if deal.get("up_to") else f"-{deal['discount_pct']}%")
        if deal.get("price") is not None:
            now_price = format_price(deal["price"], deal.get("currency") or "EUR", lang)
            was = f" ({'antes' if es else 'was'} {format_price(deal['old_price'], deal.get('currency') or 'EUR', lang)})" if deal.get("old_price") else ""
            parts.append(now_price + was)
        text = " · ".join(parts)
        if deal.get("ends_known") and deal.get("ends_ts"):
            text += (" · hasta " if es else " · until ") + local_date(deal["ends_ts"], lang)
        label = deal.get("match_label") or ""
        if deal.get("match_source") == SOURCE_STORE:
            text += " · " + ("en tu lista de deseados de la tienda" if es else "on your store wishlist")
        elif label:
            text += " · " + (f"coincide con: {label}" if es else f"matches: {label}")
        return text

    def _notify_pending(self, now: float) -> int:
        """Raise one ``MAIL_DEAL`` event for each matched, unowned, live, fresh deal that has not notified yet."""
        lang = self._lang()
        sent = 0
        horizon = now - NOTIFY_AGE_DAYS * 86400
        for deal in self.repo.all_open():
            if not deal["matched"] or deal["owned"] or deal["notified"] or deal["quiet"]:
                continue
            if (deal.get("expires_ts") or 0) < now:
                continue
            if (deal.get("mail_ts") or 0) < horizon:
                self.repo.update(deal["id"], notified=True)     # too old to be news: remember that, never alert later
                continue
            ends_on = datetime.fromtimestamp(deal["ends_ts"], timezone.utc).strftime("%Y-%m-%d") if deal.get("ends_known") and deal.get("ends_ts") else ""
            key = hashlib.sha1("|".join([deal["store_id"], deal["item_key"] if deal["kind"] == "item" else slug(deal["title"]), ends_on,
                                         f"{deal['price']}"]).encode()).hexdigest()[:20]
            twin = self.store.recent_event("mail:" + key, now - EVENT_DEDUPE_DAYS * 86400)
            if twin:
                self.repo.update(deal["id"], notified=True, event_id=twin["id"])
                continue
            watcher = None
            if deal["match_source"] == SOURCE_WATCH:
                watcher = next((w for w in self.store.watchers() if w["name"] == deal["match_label"]), None)
            event = self.store.add_event({
                "watcher_id": watcher["id"] if watcher else "", "type": MAIL_DEAL, "status": "confirmed", "confidence": 85,
                "severity": "high" if (deal.get("discount_pct") or 0) >= HIGH_DISCOUNT and not deal.get("up_to") else "medium",
                "price": deal.get("price"), "old_price": deal.get("old_price"), "currency": deal.get("currency") or "",
                "dedupe_key": "mail:" + key, "url": deal.get("url") or "", "title": deal["title"], "summary": self._summary(deal, lang),
                "data": {"source": "mail", "store": deal["store"], "deal_id": deal["id"], "discount_pct": deal.get("discount_pct"),
                         "ends_ts": deal.get("ends_ts"), "match_source": deal["match_source"], "match_label": deal["match_label"],
                         "titles": (deal.get("titles") or [])[:6]}})
            self.repo.update(deal["id"], notified=True, event_id=event["id"])
            try:
                self.engine.dispatch(event, watcher)
            except Exception:  # noqa: BLE001 - the event is stored; a notifier failure must not hide the next deal
                log.exception("dispatch of mail deal %s failed", deal["id"])
            sent += 1
        return sent

    def rebuild(self, *, history_days: Optional[int] = None) -> dict[str, Any]:
        """Forget the stored deals, read the mailbox again and parse it with the current rules. Quiet, like a first scan."""
        if self._lock.locked():
            return {"ok": False, "skipped": "a scan is already running"}
        cleared = self.repo.clear()
        self.put("mail.deals.first_scan_done", "0")
        return {**self.run(history_days=history_days), "cleared": cleared}

    def rematch(self) -> dict[str, int]:
        """Check the stored active deals against the lists again (after the wishlist or a watcher changed). Notifies the new matches."""
        wl, changed = self.wishlist(), 0
        stores = {s["id"]: s for s in self.stores()}
        for deal in self.repo.all_open():
            shop = stores.get(deal["store_id"]) or {}
            verdict = evaluate(deal, wl, store_wishlist=bool(shop.get("wishlist_style")), watch_hits=self.watch_hits)
            if (verdict["matched"], verdict["owned"], verdict["source"], verdict["label"]) != (deal["matched"], deal["owned"], deal["match_source"], deal["match_label"]):
                self.repo.update(deal["id"], matched=verdict["matched"], wishlist=verdict["wishlist"], owned=verdict["owned"],
                                 match_source=verdict["source"], match_label=verdict["label"])
                changed += 1
        return {"changed": changed, "notified": self._notify_pending(self.clock())}

    # ------------------------------------------------------------------ reading
    def deals(self, **filters: Any) -> list[dict[str, Any]]:
        self.repo.expire(self.clock())
        return self.repo.find(**filters)

    def set_status(self, deal_id: int, status: str) -> Optional[dict[str, Any]]:
        if status not in ("active", "dismissed"):
            raise ValueError("status must be active or dismissed")
        deal = self.repo.get(deal_id)
        if deal is None:
            return None
        if status == "active" and deal.get("expires_ts") and deal["expires_ts"] < self.clock():
            status = "expired"
        return self.repo.update(deal_id, status=status)

    def status(self) -> dict[str, Any]:
        last = float(self.get("mail.deals.last_run_ts", "0") or 0)
        attempt = float(self.get("mail.deals.last_attempt_ts", "0") or 0)
        try:
            summary = json.loads(self.get("mail.deals.last_summary", "") or "{}")
        except ValueError:
            summary = {}
        wl = self.wishlist()
        return {"enabled": self.enabled(), "interval_min": self._n("mail.deals.interval_min"), "history_days": self._n("mail.deals.history_days"),
                "ttl_days": self._n("mail.deals.ttl_days"), "first_scan_done": self.get("mail.deals.first_scan_done", "0") == "1",
                "last_run_ts": last or None, "last_attempt_ts": attempt or None,
                "next_run_ts": (attempt + self._n("mail.deals.interval_min") * 60) if self.enabled() and attempt else None,
                "last_error": self.get("mail.deals.last_error", "") or "", "last_summary": summary,
                "stores": [{"id": s["id"], "name": s["name"], "kind": s["kind"], "domains": s["domains"]} for s in self.stores()],
                "available_stores": [{"id": s["id"], "name": s["name"], "kind": s["kind"]} for s in DEFAULT_STORES],
                "wishlist": wl.summary(), "counts": self.repo.counts(), "running": self._lock.locked(),
                "source": self.source_status()}

    def source_status(self) -> dict[str, Any]:
        try:
            return self.source.source_status()
        except Exception:  # noqa: BLE001
            return {}

    def refresh_interest(self, *, force: bool = True) -> dict[str, Any]:
        """Register (again) with the hub which sender domains Tantalus wants. A no-op when ``mail.source`` is ``faustus``."""
        if self.source.source_setting() == "faustus" or not hasattr(self.source, "ensure_interest"):
            return {"ok": False, "skipped": "mail.source is faustus"}
        return self.source.ensure_interest([d for s in self.stores() for d in s["domains"]], force=force)

    # ------------------------------------------------------------------ noise
    def noise(self, days: Optional[int] = None, *, top: int = 25, refresh: bool = False) -> dict[str, Any]:
        """The mail noise report (read-only; cached for ten minutes because it reads thousands of headers)."""
        days = int(days or self._n("mail.noise.days"))
        cached = self._noise_cache
        if cached and not refresh and cached[1] == days and self.clock() - cached[0] < 600:
            return {**cached[2], "cached": True, "top": cached[2]["top"][:top], "noise": cached[2]["noise"][:top]}
        answer = self.source.headers(days, 6000)
        if not answer.get("ok") and not answer.get("messages"):
            return {"ok": False, "error": str(answer.get("error") or "mail not readable")[:300], "window_days": days}
        report = noise_mod.build_report(answer.get("messages") or [], stores=self.stores(), days=days, top=60)
        report.update(ok=True, error=str(answer.get("error") or ""), accounts=answer.get("accounts") or [], cached=False,
                      generated_ts=self.clock())
        self._noise_cache = (self.clock(), days, report)
        return {**report, "top": report["top"][:top], "noise": report["noise"][:top]}
