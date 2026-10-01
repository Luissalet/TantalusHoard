"""The aggregator radar: shop-by-shop stock and the release calendar for availability watchers.

Some shops cannot be read directly (Carrefour answers Cloudflare 403 even to a headless browser; Toys R Us, Fnac and
PcComponentes too), and a product page per shop never says that something new comes out today. Public stock aggregators
already watch ~190 Spanish and European TCG shops, the big chains included, and publish a release calendar. The radar
reads them for every availability watcher that turns it on (``config.radar.enabled``):

* every cycle: the live feeds (stocktcg.net ``/api/pulse.json``, stocktcg.es home), the pages of the chains the watcher
  cares about (``/tiendas/game``, ``/tiendas/carrefour``...) and the release calendar;
* the release pages of matching releases from a week before to a week after their date (every cycle on release day);
* the product pages (``/p/<slug>``) of the matching products, a few per cycle, oldest first.

Each (shop, product link) is one ``radar_offers`` row. A row that turns buyable raises ``RESTOCK`` / ``PREORDER_OPEN``
with the shop as the retailer; a chain always alerts, any other shop only at a sane price (the cheapest chain price
or the watcher's MSRP × the scalper multiplier) and in an allowed edition language. A chain offer on a site the fetcher
can read (GAME, El Corte Inglés) is checked on the shop's own page before alerting. Releases raise ``RELEASE``: when a
matching release enters the calendar, three days before, and on the day (with where to buy and where it is already gone).
The first cycle of a watcher records everything quietly except a release that is today or within three days.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from ..engine import fold, offer_matches
from ..extract import extract, profile_for_host
from ..mail.parse import madrid_offset
from ..model import (BUYABLE, IN_STOCK, MODE_AVAILABILITY, OUT_OF_STOCK, PREORDER, PREORDER_OPEN, RELEASE, RESTOCK, SOLD_OUT,
                     UNKNOWN, Offer)
from ..store import host_of
from .. import rules
from . import stocktcg as st

log = logging.getLogger("tantalus.radar")

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "sources": [st.SOURCE_NET, st.SOURCE_ES],
    "chains": list(st.CHAIN_SLUGS),          # shop slugs whose stock always alerts
    "languages": ["ES", "EN"],               # edition languages that may alert ("" = unknown is accepted for chains)
    "alert_other_shops": True,               # small shops alert too, at a sane price
    "price_multiplier": None,                # None = the watcher's scalper_multiplier
    "max_products": 24,                      # product pages followed per watcher
    "release_days_before": 3,
    "track_chain_products": True,            # chain products on readable sites become direct targets
}
INTERVAL_MIN = 10          # normal cycle
RELEASE_DAY_INTERVAL_MIN = 5   # a matching release is today or tomorrow
PRODUCT_REFRESH_MIN = 30   # a product page is fetched at most this often (10 on release day)
RELEASE_PAGE_REFRESH_MIN = 60
ES_CALENDAR_REFRESH_MIN = 120
PRODUCTS_PER_CYCLE = 6
POLITE_INTERVAL_S = 4.0    # between two requests to the same aggregator
READABLE_CHAIN_HOSTS = ("game.es", "elcorteingles.es", "carrefour.es")  # carrefour.es through a normal browser window
SLOW_HOSTS = {"carrefour.es": 60}  # check interval (min) for direct targets read through the window

_STATE_ES = {IN_STOCK: "en stock", PREORDER: "preventa", OUT_OF_STOCK: "agotado", UNKNOWN: "sin datos"}
_WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def radar_config(watcher: dict[str, Any]) -> dict[str, Any]:
    raw = (watcher.get("config") or {}).get("radar")
    raw = raw if isinstance(raw, dict) else {}
    return {**DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}


def local_today(ts: float) -> date:
    moment = datetime.fromtimestamp(ts, timezone.utc)
    return (moment + timedelta(hours=madrid_offset(moment))).date()


def nice_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def money(price: Optional[float], currency: str = "EUR") -> str:
    if price is None:
        return ""
    text = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} €" if (currency or "EUR") == "EUR" else f"{text} {currency}"


def _augment(text: str, source: str = "", game: str = "") -> str:
    """Matching text: the aggregators write English set names ("30th Celebration"), the watchers Spanish terms."""
    low = fold(text)
    extra = []
    if re.search(r"\b30\s*(?:th|º|o|\.º)\b|30th", low):
        extra.append("30 aniversario")
    if "anniversary" in low or "celebration" in low:
        extra.append("aniversario")
    if source == st.SOURCE_NET or game == "pokemon":
        extra.append("pokemon")
    return " ".join([text, *extra])


class Radar:
    def __init__(self, db: Any, store: Any, engine: Any, fetcher: Any, settings_get: Callable[[str, Optional[str]], Optional[str]],
                 set_setting: Callable[[str, str], None], clock: Callable[[], float] = time.time):
        self.db = db
        self.store = store
        self.engine = engine
        self.fetcher = fetcher
        self.get = settings_get
        self.set = set_setting
        self.clock = clock
        self._lock = threading.Lock()
        self.last_summary: dict[str, Any] = {}

    # ================================================================================== scheduling
    def watchers(self) -> list[dict[str, Any]]:
        return [w for w in self.store.watchers(mode=MODE_AVAILABILITY, enabled=True) if radar_config(w)["enabled"]]

    def interval_min(self) -> float:
        today = local_today(self.clock())
        soon = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
        row = self.db.one("SELECT COUNT(*) AS n FROM radar_releases WHERE date IN (?, ?)", tuple(sorted(soon)))
        return RELEASE_DAY_INTERVAL_MIN if row and row["n"] else INTERVAL_MIN

    def due(self, now: float) -> bool:
        if self.get("radar.paused", "0") == "1" or not self.watchers():
            return False
        nxt = float(self.get("radar.next_run_ts", "0") or 0)
        return now >= nxt

    def run_job(self, _ref: str = "all") -> dict[str, Any]:
        return self.run()

    def status(self) -> dict[str, Any]:
        ws = self.watchers()
        counts = self.db.one("SELECT COUNT(*) AS n, SUM(CASE WHEN state IN ('IN_STOCK','PREORDER') THEN 1 ELSE 0 END) AS b "
                             "FROM radar_offers") or {"n": 0, "b": 0}
        return {"watchers": [{"id": w["id"], "name": w["name"]} for w in ws], "paused": self.get("radar.paused", "0") == "1",
                "last_run_ts": float(self.get("radar.last_run_ts", "0") or 0) or None,
                "next_run_ts": float(self.get("radar.next_run_ts", "0") or 0) or None, "interval_min": self.interval_min(),
                "offers": int(counts["n"] or 0), "buyable": int(counts["b"] or 0), "last": self.last_summary}

    # ================================================================================== the cycle
    def run(self, watcher_id: str = "") -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            run_id = self.store.start_run("radar", watcher_id=watcher_id)
            cache: dict[str, Any] = {}
            summaries = []
            errors: list[str] = []
            for watcher in self.watchers():
                if watcher_id and watcher["id"] != watcher_id:
                    continue
                try:
                    summaries.append(self._run_watcher(watcher, cache))
                except Exception as error:  # noqa: BLE001 - one broken watcher never stops the others
                    log.exception("radar for %s failed", watcher["id"])
                    errors.append(f"{watcher['name']}: {type(error).__name__}: {error}")
            errors += [f"{url}: {err}" for url, err in cache.get("_errors", {}).items()]
            summary = {"watchers": summaries, "errors": errors[:10], "fetched": cache.get("_fetched", 0)}
            self.last_summary = {"ts": now, **{k: summary[k] for k in ("errors", "fetched")},
                                 "events": sum(s.get("events", 0) for s in summaries)}
            self.set("radar.last_run_ts", str(now))
            self.set("radar.next_run_ts", str(now + self.interval_min() * 60))
            self.store.finish_run(run_id, not errors or bool(summaries), summary)
            return summary

    def _run_watcher(self, watcher: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
        cfg = radar_config(watcher)
        now = self.clock()
        today = local_today(now)
        baseline = not (watcher.get("config") or {}).get("radar_baseline_done") and not self.db.one(
            "SELECT 1 FROM radar_offers WHERE watcher_id = ? LIMIT 1", (watcher["id"],))
        sources = set(cfg["sources"])
        chains = [str(c) for c in cfg["chains"]]
        offers: list[st.RadarOffer] = []
        complete_lists: dict[str, set[str]] = {}   # store_slug -> okeys of the in-stock list we read entirely
        releases: list[st.Release] = []
        stats = {"offers": 0, "matched": 0, "events": 0, "releases": 0, "products": 0}

        if st.SOURCE_NET in sources:
            data = self._json(f"{st.NET}/api/pulse.json", cache)
            if data is not None:
                offers += self._quiet_if_new(f"{st.NET}/api/pulse.json", st.parse_pulse(data), cache)
            for slug in chains:
                html = self._html(f"{st.NET}/tiendas/{slug}", cache)
                if html is None:
                    continue
                _head, rows = st.parse_store_page(html, slug)
                offers += self._quiet_if_new(f"{st.NET}/tiendas/{slug}", rows, cache)
                complete_lists[slug] = {r.key for r in rows if r.state in BUYABLE}
            html = self._html(f"{st.NET}/lanzamientos", cache)
            if html is not None:
                releases += st.parse_releases(html, today=today)
        if st.SOURCE_ES in sources:
            html = self._html(f"{st.ES}/", cache)
            if html is not None:
                offers += self._quiet_if_new(f"{st.ES}/", st.parse_es_feed(html), cache)
            if self._page_due(f"{st.ES}/lanzamientos", ES_CALENDAR_REFRESH_MIN, now, cache):
                html = self._html(f"{st.ES}/lanzamientos", cache)
                if html is not None:
                    releases += st.parse_es_releases(html)

        # ------------------------------------------------------------------ releases
        matched_releases = [r for r in releases if self._release_matches(r, watcher)]
        window_lo, window_hi = today - timedelta(days=10), today + timedelta(days=120)
        matched_releases = [r for r in matched_releases if not r.date or window_lo <= date.fromisoformat(r.date) <= window_hi]
        detailed: list[st.Release] = []
        for rel in matched_releases:
            if rel.source != st.SOURCE_NET or not rel.date or not rel.url:
                continue
            days = (date.fromisoformat(rel.date) - today).days
            if -7 <= days <= 14:
                refresh = 0 if abs(days) <= 1 else RELEASE_PAGE_REFRESH_MIN
                if refresh == 0 or self._page_due(rel.url, refresh, now, cache):
                    html = self._html(rel.url, cache)
                    if html is not None:
                        full = st.parse_release_page(html, rel.url, today=today)
                        full.date = full.date or rel.date
                        detailed.append(full)
                        offers += self._quiet_if_new(rel.url, full.offers, cache)
        stats["releases"] = len(matched_releases)

        # ------------------------------------------------------------------ product pages
        keys = self._product_keys(watcher, offers, cfg)
        due_keys = []
        hot = {o.product_key for r in detailed for o in r.offers if r.date and abs((date.fromisoformat(r.date) - today).days) <= 1}
        for key in keys:
            url = f"{st.NET}/p/{key}"
            if self._page_due(url, 10 if key in hot else PRODUCT_REFRESH_MIN, now, cache):
                due_keys.append(key)
        for key in due_keys[:PRODUCTS_PER_CYCLE]:
            url = f"{st.NET}/p/{key}"
            html = self._html(url, cache)
            if html is None:
                continue
            _head, rows = st.parse_product_page(html, url)
            offers += self._quiet_if_new(url, rows, cache)
            stats["products"] += 1
            complete_lists[f"p:{key}"] = {r.key for r in rows if r.state in BUYABLE and r.kind == "listing"}

        # ------------------------------------------------------------------ offers -> rows -> events
        terms, must, exclude = self.engine._match_config(watcher)
        stats["offers"] = len(offers)
        merged: dict[str, st.RadarOffer] = {}
        for o in offers:
            game = (o.extra or {}).get("game", "")
            probe = Offer(title=_augment(o.match_text(), o.source, game), url=o.url)
            if game and game != "pokemon" and "pokemon" in [fold(m) for m in must]:
                continue
            if not offer_matches(probe, terms, must, exclude):
                continue
            prev = merged.get(o.key)
            merged[o.key] = _merge(prev, o) if prev else o
        stats["matched"] = len(merged)
        events = 0
        seen_keys = set()
        for o in merged.values():
            seen_keys.add(o.key)
            events += self._upsert(watcher, o, cfg, baseline=baseline, now=now)
        stats["gone"] = self._mark_gone(watcher, complete_lists, seen_keys, now)
        for rel in self._merge_releases(matched_releases, detailed):
            events += self._release(watcher, rel, cfg, baseline=baseline, now=now, today=today)
        if baseline:
            cfg_all = dict(watcher.get("config") or {})
            cfg_all["radar_baseline_done"] = True
            self.store.update_watcher(watcher["id"], config=cfg_all)
        if cfg.get("track_chain_products"):
            self._track_chain_products(watcher, merged.values())
        stats["events"] = events
        stats["baseline"] = baseline
        return {"watcher": watcher["name"], **stats}

    # ================================================================================== fetching
    def _page_due(self, url: str, minutes: float, now: float, cache: dict[str, Any]) -> bool:
        if url in cache:
            return False
        row = self.db.one("SELECT last_fetch_ts, ok FROM radar_pages WHERE url = ?", (url,))
        return row is None or now - float(row["last_fetch_ts"]) >= minutes * 60 - 5

    def _note_page(self, url: str, ok: bool, error: str = "") -> None:
        self.db.execute("INSERT INTO radar_pages(url, last_fetch_ts, ok, error) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(url) DO UPDATE SET last_fetch_ts = excluded.last_fetch_ts, ok = excluded.ok, error = excluded.error",
                        (url, self.clock(), 1 if ok else 0, error[:300]))

    def _first_time(self, url: str, cache: dict[str, Any]) -> None:
        """A page read for the first time is a baseline: what it lists was there before we looked."""
        if self.db.one("SELECT 1 FROM radar_pages WHERE url = ? AND ok = 1", (url,)) is None:
            cache.setdefault("_new_pages", set()).add(url)

    def _quiet_if_new(self, url: str, rows: list[st.RadarOffer], cache: dict[str, Any]) -> list[st.RadarOffer]:
        if url in cache.get("_new_pages", set()):
            for r in rows:
                r.extra = {**(r.extra or {}), "quiet": True}
        return rows

    def _html(self, url: str, cache: dict[str, Any]) -> Optional[str]:
        if url in cache:
            return cache[url]
        self._first_time(url, cache)
        fr = self.fetcher.get(url, tier="http", min_interval_s=POLITE_INTERVAL_S)
        cache["_fetched"] = cache.get("_fetched", 0) + 1
        ok = bool(fr.ok and fr.text and not fr.blocked)
        cache[url] = fr.text if ok else None
        if not ok:
            cache.setdefault("_errors", {})[url] = fr.block_reason or fr.error or f"HTTP {fr.status}"
        self._note_page(url, ok, "" if ok else (fr.block_reason or fr.error or f"HTTP {fr.status}"))
        return cache[url]

    def _json(self, url: str, cache: dict[str, Any]) -> Any:
        if url in cache:
            return cache[url]
        self._first_time(url, cache)
        fr, data = self.fetcher.get_json(url, tier="http", min_interval_s=POLITE_INTERVAL_S)
        cache["_fetched"] = cache.get("_fetched", 0) + 1
        if data is None and fr.ok and fr.text:
            data = st.parse_json(fr.text)
        cache[url] = data
        if data is None:
            cache.setdefault("_errors", {})[url] = fr.block_reason or fr.error or f"HTTP {fr.status}"
        self._note_page(url, data is not None, "" if data is not None else (fr.error or f"HTTP {fr.status}"))
        return data

    # ================================================================================== matching
    def _release_matches(self, rel: st.Release, watcher: dict[str, Any]) -> bool:
        terms, must, exclude = self.engine._match_config(watcher)
        if rel.game and rel.game != "pokemon" and "pokemon" in [fold(m) for m in must]:
            return False
        probe = Offer(title=_augment(rel.match_text(), rel.source, rel.game or "pokemon"), url=rel.url)
        return offer_matches(probe, terms, must, exclude)

    def _product_keys(self, watcher: dict[str, Any], offers: list[st.RadarOffer], cfg: dict[str, Any]) -> list[str]:
        terms, must, exclude = self.engine._match_config(watcher)
        keys: list[str] = []
        for o in offers:
            if not o.product_key or o.product_key in keys:
                continue
            probe = Offer(title=_augment(o.match_text(), o.source), url=o.url)
            if offer_matches(probe, terms, must, exclude):
                keys.append(o.product_key)
        for row in self.db.query("SELECT DISTINCT product_key FROM radar_offers WHERE watcher_id = ? AND product_key != '' "
                                 "ORDER BY last_change_ts DESC", (watcher["id"],)):
            if row["product_key"] not in keys:
                keys.append(row["product_key"])
        keys = keys[: int(cfg.get("max_products") or 24)]
        # oldest fetch first, so every product page comes round
        def last(key: str) -> float:
            row = self.db.one("SELECT last_fetch_ts FROM radar_pages WHERE url = ?", (f"{st.NET}/p/{key}",))
            return float(row["last_fetch_ts"]) if row else 0.0
        return sorted(keys, key=last)

    # ================================================================================== offers
    def _row(self, watcher_id: str, okey: str) -> Optional[dict[str, Any]]:
        row = self.db.one("SELECT * FROM radar_offers WHERE watcher_id = ? AND okey = ?", (watcher_id, okey))
        if row is None:
            return None
        d = dict(row)
        d["extra"] = json.loads(d.get("extra") or "{}")
        return d

    def _upsert(self, watcher: dict[str, Any], o: st.RadarOffer, cfg: dict[str, Any], *, baseline: bool, now: float) -> int:
        chain = o.store_slug in set(cfg["chains"])
        row = self._row(watcher["id"], o.key)
        state = o.state if o.state in (IN_STOCK, PREORDER, OUT_OF_STOCK) else UNKNOWN
        extra = {**(row["extra"] if row else {}), **{k: v for k, v in (o.extra or {}).items() if k != "quiet"}}
        if o.seen_ts:
            extra["aggregator_seen_ts"] = o.seen_ts
        if row is None:
            self.db.execute(
                "INSERT INTO radar_offers(watcher_id, okey, source, store, store_slug, chain, title, url, product_key, release, set_name, fmt, "
                "lang, image, kind, state, price, currency, first_seen_ts, last_seen_ts, last_change_ts, last_buyable_ts, extra) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (watcher["id"], o.key, o.source, o.store, o.store_slug, 1 if chain else 0, o.title[:300], o.url, o.product_key, o.release,
                 o.set_name, o.fmt, o.lang, o.image, o.kind, state, o.price, o.currency or "EUR", now, now, now,
                 now if state in BUYABLE else None, json.dumps(extra, ensure_ascii=False)))
            prev_state = None
        else:
            prev_state = row["state"]
            changed = state != prev_state and state != UNKNOWN
            self.db.execute(
                "UPDATE radar_offers SET source = ?, store = ?, chain = ?, title = ?, url = CASE WHEN ? != '' THEN ? ELSE url END, "
                "product_key = CASE WHEN ? != '' THEN ? ELSE product_key END, release = CASE WHEN ? != '' THEN ? ELSE release END, "
                "set_name = CASE WHEN ? != '' THEN ? ELSE set_name END, fmt = CASE WHEN ? != '' THEN ? ELSE fmt END, "
                "lang = CASE WHEN ? != '' THEN ? ELSE lang END, image = CASE WHEN ? != '' THEN ? ELSE image END, kind = ?, "
                "state = CASE WHEN ? != 'UNKNOWN' THEN ? ELSE state END, price = COALESCE(?, price), currency = ?, last_seen_ts = ?, "
                "last_change_ts = CASE WHEN ? THEN ? ELSE last_change_ts END, "
                "last_buyable_ts = CASE WHEN ? THEN ? ELSE last_buyable_ts END, extra = ? WHERE id = ?",
                (o.source, o.store, 1 if chain else 0, o.title[:300], o.url, o.url, o.product_key, o.product_key, o.release, o.release,
                 o.set_name, o.set_name, o.fmt, o.fmt, o.lang, o.lang, o.image, o.image, o.kind, state, state, o.price, o.currency or "EUR",
                 now, 1 if changed else 0, now, 1 if state in BUYABLE else 0, now, json.dumps(extra, ensure_ascii=False), row["id"]))
        if baseline or state not in BUYABLE or (prev_state in BUYABLE):
            return 0
        if prev_state is None and (o.extra or {}).get("quiet"):
            return 0  # first time this page is read: its offers were already there
        # a feed row older than a day is history, not news
        if o.seen_ts and now - o.seen_ts > 24 * 3600:
            return 0
        return self._offer_event(watcher, o, cfg, chain=chain, prev_state=prev_state, now=now)

    def _mark_gone(self, watcher: dict[str, Any], complete: dict[str, set[str]], seen: set[str], now: float) -> int:
        """A chain's in-stock list (or a product page's shop table) was read entirely: matching rows that were buyable
        there and are missing now are sold out."""
        n = 0
        for scope, keys in complete.items():
            if scope.startswith("p:"):
                rows = self.db.query("SELECT id, okey FROM radar_offers WHERE watcher_id = ? AND product_key = ? AND state IN ('IN_STOCK','PREORDER') "
                                     "AND source = ? AND kind = 'listing' AND release = ''", (watcher["id"], scope[2:], st.SOURCE_NET))
            else:
                rows = self.db.query("SELECT id, okey FROM radar_offers WHERE watcher_id = ? AND store_slug = ? AND state IN ('IN_STOCK','PREORDER') "
                                     "AND source = ? AND release = ''", (watcher["id"], scope, st.SOURCE_NET))
            for row in rows:
                if row["okey"] in keys or row["okey"] in seen:
                    continue
                self.db.execute("UPDATE radar_offers SET state = 'OUT_OF_STOCK', last_change_ts = ? WHERE id = ?", (now, row["id"]))
                n += 1
        return n

    def reference_price(self, watcher: dict[str, Any], o: st.RadarOffer) -> Optional[float]:
        """What the product costs at a chain (the cheapest chain price ever seen for it), else the watcher's MSRP."""
        if o.product_key:
            row = self.db.one("SELECT MIN(price) AS p FROM radar_offers WHERE watcher_id = ? AND product_key = ? AND chain = 1 "
                              "AND price > 0 AND currency = 'EUR' AND kind != 'variant'", (watcher["id"], o.product_key))
            if row and row["p"]:
                return float(row["p"])
        msrp = ((watcher.get("config") or {}).get("policies") or {}).get("msrp")
        return float(msrp) if msrp else None

    def _offer_event(self, watcher: dict[str, Any], o: st.RadarOffer, cfg: dict[str, Any], *, chain: bool, prev_state: Optional[str],
                     now: float) -> int:
        policies = rules.policies_for(watcher.get("config") or {})
        kind = PREORDER_OPEN if o.state == PREORDER else RESTOCK
        langs = [str(x).upper() for x in cfg.get("languages") or []]
        lang_ok = (not langs) or (o.lang.upper() in langs) or (not o.lang and chain)
        reasons: list[str] = []
        status, severity, confidence = "confirmed", ("high" if chain else "medium"), (85 if chain else 75)
        ref = self.reference_price(watcher, o)
        mult = float(cfg.get("price_multiplier") or policies.get("scalper_multiplier") or 1.5)
        if o.kind == "variant":
            status = "logged"
            reasons.append("variante no comparable (case, media caja o edición asiática)")
        if not lang_ok:
            status = "logged"
            reasons.append(f"edición {o.lang or '?'}")
        if not chain:
            if not cfg.get("alert_other_shops", True):
                status = "logged"
                reasons.append("tienda pequeña (solo avisan las cadenas)")
            elif (o.currency or "EUR") != "EUR":
                status = "logged"
                reasons.append(f"precio en {o.currency}")
            elif o.price is None or o.price <= 0:
                status = "logged"
                reasons.append("sin precio")
            elif ref is None:
                status = "logged"
                reasons.append("sin precio de referencia de una cadena")
            elif o.price > ref * mult:
                status = "logged"
                reasons.append(f"precio de reventa ({money(o.price)} > {money(ref)} × {mult:g})")
        direct = None
        if chain and status == "confirmed" and o.url and host_of(o.url) in READABLE_CHAIN_HOSTS:
            direct = self._direct_check(o.url, watcher)
            if direct in BUYABLE:
                confidence = 95
            elif direct == OUT_OF_STOCK:
                status = "logged"
                reasons.append("la web de la tienda lo da agotado")
        key = rules.dedupe_key(f"radar:{watcher['id']}:{o.key}", kind, o.state, o.price)
        cooldown = float(policies.get("cooldown_minutes") or 20) * 60
        if self.store.recent_event(key, now - max(cooldown, 3600)):
            return 0
        what = " · ".join(filter(None, (o.fmt, o.lang)))
        verb = "preventa abierta" if kind == PREORDER_OPEN else "en stock"
        if (o.extra or {}).get("invite"):
            verb = "venta por invitación"
        summary = f"{o.store}: {verb} — {o.title}" + (f" ({what})" if what else "") + (f" · {money(o.price, o.currency)}" if o.price else "")
        if ref and not chain and o.price:
            summary += f" · en cadena {money(ref)}"
        summary += f" · según {o.source}"
        if direct in BUYABLE:
            summary += " · comprobado en la web de la tienda"
        if reasons:
            summary += f" · no avisa: {', '.join(reasons)}"
        event = self.store.add_event({
            "watcher_id": watcher["id"], "type": kind, "severity": severity, "status": status, "old_state": prev_state or "",
            "new_state": o.state, "price": o.price, "currency": o.currency or "EUR", "confidence": confidence, "dedupe_key": key,
            "url": o.url or (f"{st.NET}/p/{o.product_key}" if o.product_key else st.NET), "title": f"{o.store} · {o.title}"[:200],
            "summary": summary[:600], "detected_at": now,
            "data": {"retailer": o.store, "store_slug": o.store_slug, "chain": chain, "source": o.source, "aggregator": True,
                     "product_key": o.product_key, "lang": o.lang, "fmt": o.fmt, "reference_price": ref, "direct_check": direct,
                     "not_alerted_because": reasons, "image": o.image,
                     "aggregator_url": f"{st.NET}/p/{o.product_key}" if o.product_key else ""}})
        if status == "confirmed":
            self.engine.dispatch(event, watcher)
        return 1

    def _direct_check(self, url: str, watcher: dict[str, Any]) -> Optional[str]:
        try:
            fr = self.fetcher.get(url, tier="auto")
            ex = extract(fr, hints={"url": url, "title_hint": watcher.get("name") or ""})
        except Exception as error:  # noqa: BLE001
            log.info("direct check of %s failed: %s", url, error)
            return None
        if not fr.ok or ex.page_kind == "blocked" or not ex.offers:
            return None
        state = ex.offers[0].availability
        return state if state != UNKNOWN else None

    def _track_chain_products(self, watcher: dict[str, Any], offers: Any) -> None:
        """Chain products on sites the fetcher reads (GAME, El Corte Inglés) become product targets of the watcher, so the
        shop page itself is checked too. Same cap as the search pages."""
        product = (watcher.get("config") or {}).get("product") or {}
        cap = int(product.get("max_targets", 40))
        policies = (watcher.get("config") or {}).get("policies") or {}
        existing = self.store.targets(watcher_id=watcher["id"])
        known = {t["url"] for t in existing}
        count = sum(1 for t in existing if (t.get("extra") or {}).get("page_kind") not in ("search", "listing"))
        for o in offers:
            if not o.url or host_of(o.url) not in READABLE_CHAIN_HOSTS or o.url in known or o.kind == "variant":
                continue
            prof = profile_for_host(o.url)
            cand = self.store.insert_candidate(watcher["id"], {
                "url": o.url, "title": o.title[:200], "snippet": money(o.price, o.currency), "source_level": 1,
                "retailer": prof.name if prof else o.store, "engine": "radar", "query": o.source, "score": 80,
                "reason": f"{o.store} lo tiene en {o.source}"})
            if cand and count < cap:
                extra_fields = {"interval_min": SLOW_HOSTS[host_of(o.url)]} if host_of(o.url) in SLOW_HOSTS else {}
                self.store.create_target(watcher["id"], o.url, label=o.title[:120], retailer=cand["retailer"], source_level=1,
                                         seller_policy=policies.get("seller") or "retail_only", extra={"from_radar": o.source},
                                         **extra_fields)
                self.store.set_candidate(cand["id"], "accepted", self.store.target_by_url(watcher["id"], o.url)["id"])
                count += 1
                known.add(o.url)

    # ================================================================================== releases
    def _merge_releases(self, calendar: list[st.Release], detailed: list[st.Release]) -> list[st.Release]:
        by_slug = {r.slug: r for r in detailed}
        out: list[st.Release] = []
        net_dates: dict[str, list[st.Release]] = {}
        for r in calendar:
            full = by_slug.get(r.slug) if r.source == st.SOURCE_NET else None
            if full is not None:
                full.date = full.date or r.date
                full.products = full.products or r.products
                if not full.offers:
                    full.offers = r.offers
            item = full or r
            out.append(item)
            if item.source == st.SOURCE_NET and item.date:
                net_dates.setdefault(item.date, []).append(item)
        # a stocktcg.es release on the same date as a stocktcg.net one is the same release
        result = []
        for r in out:
            if r.source == st.SOURCE_ES and r.date in net_dates:
                for twin in net_dates[r.date]:
                    if not twin.products:
                        twin.products = r.products
                    if not twin.buy_url:
                        twin.buy_url = r.buy_url
                continue
            result.append(r)
        return result

    def _release(self, watcher: dict[str, Any], rel: st.Release, cfg: dict[str, Any], *, baseline: bool, now: float, today: date) -> int:
        rkey = f"{rel.source}:{rel.slug}"
        row = self.db.one("SELECT * FROM radar_releases WHERE watcher_id = ? AND rkey = ?", (watcher["id"], rkey))
        where = self.where(watcher, rel, cfg)
        data = {"products": rel.products, "stores_total": rel.stores_total, "stores_buyable": rel.stores_buyable,
                "stores_soldout": rel.stores_soldout, "soldout": rel.soldout, "where": where, "buy_url": rel.buy_url, "game": rel.game}
        if row is None:
            self.db.execute("INSERT INTO radar_releases(watcher_id, rkey, source, slug, title, date, kind, url, data, first_seen_ts, last_seen_ts, "
                            "detail_ts, alerted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (watcher["id"], rkey, rel.source, rel.slug, rel.title, rel.date, rel.kind, rel.url, json.dumps(data, ensure_ascii=False),
                             now, now, now if rel.offers else None, "[]"))
            alerted: list[str] = []
        else:
            old = json.loads(row["data"] or "{}")
            if not rel.offers and old.get("where"):
                data["where"] = old["where"]  # keep the last detailed view when this cycle only read the calendar
            self.db.execute("UPDATE radar_releases SET title = ?, date = CASE WHEN ? != '' THEN ? ELSE date END, kind = ?, url = ?, data = ?, "
                            "last_seen_ts = ?, detail_ts = CASE WHEN ? THEN ? ELSE detail_ts END WHERE id = ?",
                            (rel.title, rel.date, rel.date, rel.kind, rel.url, json.dumps(data, ensure_ascii=False), now,
                             1 if rel.offers else 0, now, row["id"]))
            alerted = json.loads(row["alerted"] or "[]")
        if not rel.date:
            return 0
        days = (date.fromisoformat(rel.date) - today).days
        before = int(cfg.get("release_days_before") or 3)
        stage = ""
        if days == 0:
            stage = "today"
        elif 0 < days <= before:
            stage = "soon"
        elif days > before and row is None and not baseline:
            stage = "announced"
        if not stage or stage in alerted or days < 0:
            return 0
        if stage == "soon" and "today" in alerted:
            return 0
        alerted.append(stage)
        self.db.execute("UPDATE radar_releases SET alerted = ? WHERE watcher_id = ? AND rkey = ?",
                        (json.dumps(alerted), watcher["id"], rkey))
        when = {"today": f"Hoy ({nice_date(rel.date)})", "soon": f"El {nice_date(rel.date)} (en {days} día{'s' if days != 1 else ''})",
                "announced": f"Nuevo en el calendario: {nice_date(rel.date)}"}[stage]
        summary = f"{when} sale «{rel.title}»." + (f" {', '.join(rel.products)}." if rel.products else "")
        summary += " " + self.where_text(data["where"])
        key = rules.dedupe_key(f"radar:{watcher['id']}:{rkey}", RELEASE, stage, None)
        event = self.store.add_event({
            "watcher_id": watcher["id"], "type": RELEASE, "severity": {"today": "high", "soon": "medium", "announced": "low"}[stage],
            "status": "confirmed", "confidence": 90 if rel.source == st.SOURCE_NET else 75, "dedupe_key": key,
            "url": rel.url or rel.buy_url or st.NET, "title": rel.title[:200], "summary": summary[:900], "detected_at": now,
            "data": {**data, "stage": stage, "date": rel.date, "source": rel.source, "aggregator": True}})
        self.engine.dispatch(event, watcher)
        return 1

    def where(self, watcher: dict[str, Any], rel: st.Release, cfg: dict[str, Any]) -> dict[str, Any]:
        """Where a release can be bought: per chain (state and price) and the other shops with stock or pre-orders."""
        chains = [str(c) for c in cfg["chains"]]
        soldout = {s["slug"] for s in rel.soldout}
        names = {s["slug"]: s["store"] for s in rel.soldout}
        chain_view = []
        for slug in chains:
            rows = [o for o in rel.offers if o.store_slug == slug]
            buy = [o for o in rows if o.state in BUYABLE]
            if buy:
                best = min(buy, key=lambda o: o.price if o.price is not None else 1e9)
                chain_view.append({"store": best.store, "slug": slug, "state": best.state, "price": best.price, "url": best.url,
                                   "fmt": best.fmt, "lang": best.lang})
            elif slug in soldout or rows:
                chain_view.append({"store": names.get(slug) or (rows[0].store if rows else slug), "slug": slug, "state": OUT_OF_STOCK})
        # a 0 € price is a placeholder on the shop's page (reservation form, "consultar"), not a price
        others = [o for o in rel.offers if o.state in BUYABLE and o.store_slug not in chains and not (o.price is not None and o.price <= 0)]
        others.sort(key=lambda o: (0 if (o.currency or "EUR") == "EUR" else 1, o.price if o.price is not None else 1e9))
        langs = [str(x).upper() for x in cfg.get("languages") or []]
        pref = [o for o in others if (not langs or o.lang.upper() in langs) and (o.currency or "EUR") == "EUR"]
        shops = []
        for o in (pref or others):
            if any(s["slug"] == o.store_slug and s["fmt"] == o.fmt for s in shops):
                continue
            shops.append({"store": o.store, "slug": o.store_slug, "state": o.state, "price": o.price, "currency": o.currency,
                          "url": o.url, "fmt": o.fmt, "lang": o.lang})
        return {"chains": chain_view, "shops": shops[:12], "shops_total": len({o.store_slug for o in others}),
                "soldout_total": len(soldout)}

    @staticmethod
    def where_text(where: dict[str, Any]) -> str:
        parts = []
        if where.get("chains"):
            parts.append("Cadenas: " + " · ".join(
                f"{c['store']} {_STATE_ES.get(c['state'], c['state'])}" + (f" {money(c.get('price'))}" if c.get("price") else "")
                for c in where["chains"]) + ".")
        shops = where.get("shops") or []
        if shops:
            parts.append("Preventa o stock: " + " · ".join(
                f"{s['store']} {money(s.get('price'), s.get('currency') or 'EUR')}" + (f" ({s['fmt']}{', ' + s['lang'] if s.get('lang') else ''})" if s.get("fmt") else "")
                for s in shops[:6]) + (f" (+{where['shops_total'] - 6} tiendas más)" if where.get("shops_total", 0) > 6 else "") + ".")
        elif where.get("chains") is not None and not where.get("chains"):
            parts.append("Aún sin tiendas con preventa o stock.")
        return " ".join(parts)

    # ================================================================================== reads for the UI / MCP
    def releases(self, *, watcher_id: str = "", upcoming_days: int = 60, past_days: int = 7) -> list[dict[str, Any]]:
        today = local_today(self.clock())
        lo, hi = (today - timedelta(days=past_days)).isoformat(), (today + timedelta(days=upcoming_days)).isoformat()
        sql = "SELECT * FROM radar_releases WHERE ((date >= ? AND date <= ?) OR date = '')"
        params: list[Any] = [lo, hi]
        if watcher_id:
            sql += " AND watcher_id = ?"
            params.append(watcher_id)
        out = []
        for row in self.db.query(sql + " ORDER BY date = '', date, title", params):
            d = dict(row)
            d["data"] = json.loads(d.get("data") or "{}")
            d["alerted"] = json.loads(d.get("alerted") or "[]")
            d["days"] = (date.fromisoformat(d["date"]) - today).days if d["date"] else None
            out.append(d)
        return out

    def offers(self, *, watcher_id: str = "", buyable: Optional[bool] = None, chain: Optional[bool] = None, product_key: str = "",
               store: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM radar_offers WHERE 1 = 1", []
        if watcher_id:
            sql += " AND watcher_id = ?"
            params.append(watcher_id)
        if buyable is not None:
            sql += " AND state IN ('IN_STOCK','PREORDER')" if buyable else " AND state NOT IN ('IN_STOCK','PREORDER')"
        if chain is not None:
            sql += " AND chain = ?"
            params.append(1 if chain else 0)
        if product_key:
            sql += " AND product_key = ?"
            params.append(product_key)
        if store:
            sql += " AND (store_slug = ? OR LOWER(store) = LOWER(?))"
            params += [store, store]
        sql += " ORDER BY chain DESC, CASE WHEN state IN ('IN_STOCK','PREORDER') THEN 0 ELSE 1 END, last_change_ts DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        out = []
        for row in self.db.query(sql, params):
            d = dict(row)
            d["extra"] = json.loads(d.get("extra") or "{}")
            d["chain"] = bool(d["chain"])
            out.append(d)
        return out

    def chains_view(self, watcher_id: str = "") -> list[dict[str, Any]]:
        """Per chain: the matching products it has now and the ones that sold out recently."""
        rows = self.offers(watcher_id=watcher_id, chain=True, limit=500)
        by: dict[str, dict[str, Any]] = {}
        for r in rows:
            v = by.setdefault(r["store_slug"], {"store": r["store"], "slug": r["store_slug"], "buyable": [], "soldout": []})
            (v["buyable"] if r["state"] in BUYABLE else v["soldout"]).append(
                {k: r[k] for k in ("title", "url", "price", "currency", "state", "fmt", "lang", "product_key", "last_change_ts", "image")})
        for v in by.values():
            have = {fold(b["title"]) for b in v["buyable"]}
            v["soldout"] = [x for x in v["soldout"] if fold(x["title"]) not in have][:12]
        return sorted(by.values(), key=lambda v: (-len(v["buyable"]), v["store"]))


def _merge(a: st.RadarOffer, b: st.RadarOffer) -> st.RadarOffer:
    """Two sources describe the same shop offer: keep the most informative fields, buyable wins over sold out when both
    come from the same cycle (a feed can lag a page; a product page is fresher than a store page)."""
    rank = {IN_STOCK: 3, PREORDER: 2, OUT_OF_STOCK: 1, UNKNOWN: 0}
    best = b if rank.get(b.state, 0) >= rank.get(a.state, 0) else a
    other = a if best is b else b
    for f in ("lang", "set_name", "fmt", "product_key", "image", "release", "url"):
        if not getattr(best, f) and getattr(other, f):
            setattr(best, f, getattr(other, f))
    if best.price is None:
        best.price = other.price
    return best
