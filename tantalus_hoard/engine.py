"""The watcher engine: one check of a target, one second-hand sweep, one information sweep, one discovery pass,
the revalidation of pending events, and the dispatch of confirmed events to the notifiers.

Pipeline for a product target (spec §8): fetch ladder -> extractor -> normaliser (seller / price policy)
-> diff against the last known state -> confidence -> event (deduped, cooled down) -> revalidation queue
-> notification -> history.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import unicodedata
from typing import Any, Callable, Optional

from . import rules
from .discovery import discover, product_terms, watcher_config
from .extract import adapter_for, extract, retailer_for_host, source_level_for_host
from .model import (BUYABLE, CANDIDATE_FOUND, IN_STOCK, INFO_CHANGE, LISTING_PRICE_DROP, LOCAL_PICKUP, MARKETPLACE_ONLY,
                    MODE_AVAILABILITY, MODE_INFORMATION, MODE_SECONDHAND, NEEDS_HUMAN, NEW_LISTING, NEW_SKU,
                    OUT_OF_STOCK, PREORDER, RESTOCK_SCHEDULED, UNAVAILABLE_REGION, UNKNOWN, Extraction, FetchResult, Offer,
                    RawListing)
from .secondhand import build_source, get_pack, score_listing
from .secondhand.dedupe import DedupeIndex
from .secondhand.distance import find_municipality_coords
from .store import Store, host_of

log = logging.getLogger("tantalus.engine")

STATE_RANK = {IN_STOCK: 7, LOCAL_PICKUP: 6, PREORDER: 5, RESTOCK_SCHEDULED: 4, MARKETPLACE_ONLY: 3, OUT_OF_STOCK: 2,
              UNAVAILABLE_REGION: 1, UNKNOWN: 0}
NEEDS_HUMAN_RETRY_MIN = 180
MAX_BACKOFF_MIN = 6 * 60


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


def _has_term(term: str, text: str) -> bool:
    """Whole-word match on folded text ("30" must not match "1:30" or "2030")."""
    t = fold(term).strip()
    return bool(t) and re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text) is not None


def offer_matches(offer: Offer, terms: list[str], must: list[str], exclude: list[str]) -> bool:
    """Does a product tile belong to the watcher? Every ``must`` term and, with three terms or fewer, every product
    term; with more terms, at least 60 % of them. Any ``exclude`` term rejects the tile."""
    title = fold(" ".join(filter(None, (offer.title, (offer.url or "").replace("-", " ").replace("_", " ")))))
    if not title:
        return False
    if any(_has_term(x, title) for x in exclude):
        return False
    if must and not all(_has_term(m, title) for m in must):
        return False
    if not terms:
        return True
    hits = sum(1 for t in terms if _has_term(t, title))
    needed = len(terms) if len(terms) <= 3 else max(1, int(len(terms) * 0.6 + 0.5))
    return hits >= needed


class Engine:
    def __init__(self, store: Store, fetcher: Any, *, llm: Any, notifier: Any, websearch: Any, info_sentry: Any,
                 settings_get: Callable[[str, Optional[str]], Optional[str]], emit: Callable[[str, dict[str, Any]], None],
                 clock: Callable[[], float] = time.time, rng: Optional[random.Random] = None):
        self.store = store
        self.fetcher = fetcher
        self.llm = llm
        self.notifier = notifier
        self.websearch = websearch
        self.info = info_sentry
        self.setting = settings_get
        self.emit = emit
        self.clock = clock
        self.rng = rng or random.Random()

    # ======================================================================================== helpers
    def _next_check(self, target: dict[str, Any], watcher: dict[str, Any], *, failed: int = 0, needs_human: bool = False) -> float:
        interval = float(target.get("interval_min") or watcher.get("interval_min") or 30)
        if needs_human:
            interval = max(interval, NEEDS_HUMAN_RETRY_MIN)
        elif failed:
            interval = min(MAX_BACKOFF_MIN, interval * (2 ** min(failed, 5)))
        jitter = 1 + self.rng.uniform(-0.1, 0.1)
        return self.clock() + interval * 60 * jitter

    def _hints(self, target: dict[str, Any], watcher: dict[str, Any]) -> dict[str, Any]:
        cfg = watcher.get("config") or {}
        return {"url": target["url"], "sku": target.get("sku") or "", "ean": target.get("ean") or "",
                "store_ids": target.get("store_ids") or [], "retailer": target.get("retailer") or "",
                "adapter": target.get("adapter") or "auto", "region": cfg.get("region") or "",
                "title_hint": target.get("label") or watcher.get("name") or ""}

    def _match_config(self, watcher: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
        cfg = watcher.get("config") or {}
        product = cfg.get("product") if isinstance(cfg.get("product"), dict) else {}
        must = [str(x) for x in product.get("must") or []]
        exclude = [str(x) for x in product.get("exclude") or []]
        terms = [str(x) for x in product.get("terms") or []] or product_terms(watcher)
        return terms, must, exclude

    # ======================================================================================== targets
    def fetch_target(self, target: dict[str, Any], watcher: dict[str, Any]) -> tuple[FetchResult, Extraction]:
        hints = self._hints(target, watcher)
        api = adapter_for(target["url"], hints)
        if api:
            fr, _data = self.fetcher.get_json(api["url"], tier=api.get("tier", "http"), respect_robots=api.get("respect_robots", False),
                                              min_interval_s=api.get("min_interval_s"))
            hints["adapter"] = api["adapter"]
        else:
            fr = self.fetcher.get(target["url"], tier=target.get("fetch_tier") or "auto")
        ex = extract(fr, hints=hints, llm=self.llm)
        return fr, ex

    def check_target(self, target_id: str, *, revalidation: bool = False) -> dict[str, Any]:
        """Fetch, extract, record an observation and raise the events the change deserves."""
        target = self.store.target(target_id)
        watcher = self.store.watcher(target["watcher_id"])
        run_id = self.store.start_run("revalidate" if revalidation else "check", watcher_id=watcher["id"], target_id=target_id)
        try:
            result = self._check(target, watcher, revalidation=revalidation)
            self.store.finish_run(run_id, True, {k: v for k, v in result.items() if k in ("state", "price", "confidence", "events", "error")})
            return result
        except Exception as error:  # noqa: BLE001 — one broken target never stops the scheduler
            log.exception("check of %s failed", target_id)
            self.store.update_target(target_id, last_error=f"{type(error).__name__}: {error}"[:300], fail_count=int(target.get("fail_count") or 0) + 1,
                                     next_check_ts=self._next_check(target, watcher, failed=int(target.get("fail_count") or 0) + 1))
            self.store.finish_run(run_id, False, {"error": str(error)[:300]})
            raise

    def _check(self, target: dict[str, Any], watcher: dict[str, Any], *, revalidation: bool) -> dict[str, Any]:
        now = self.clock()
        cfg = watcher.get("config") or {}
        policies = rules.policies_for(cfg)
        fr, ex = self.fetch_target(target, watcher)
        extra = dict(target.get("extra") or {})
        fail_count = int(target.get("fail_count") or 0)

        # ---------------------------------------------------------------- could not read the page
        if not fr.ok or ex.page_kind == "blocked":
            soft = fr.block_reason in ("offline", "robots", "unsafe_url")
            blocked = (fr.blocked or ex.page_kind == "blocked") and not soft
            reason = fr.block_reason or ("blocked" if blocked else "") or fr.error or "; ".join(ex.notes)
            obs = self.store.add_observation(target["id"], {
                "checked_at": now, "source_url": fr.final_url or fr.url, "tier": fr.tier, "http_status": fr.status,
                "availability": UNKNOWN, "blocked_reason": reason if blocked else "", "error": "" if blocked else reason,
                "is_revalidation": revalidation, "evidence": ex.notes[:3]})
            status = "needs_human" if blocked else "error"
            self.store.update_target(target["id"], status=status, last_check_ts=now, last_error=reason[:300], fail_count=fail_count + 1,
                                     next_check_ts=self._next_check(target, watcher, failed=fail_count + 1, needs_human=blocked))
            events = []
            if blocked and target.get("status") != "needs_human":
                key = rules.dedupe_key(target["id"], NEEDS_HUMAN, reason, None)
                if not self.store.recent_event(key, now - 24 * 3600):
                    events.append(self.store.add_event({
                        "watcher_id": watcher["id"], "target_id": target["id"], "type": NEEDS_HUMAN, "severity": "low", "status": "logged",
                        "dedupe_key": key, "url": target["url"], "title": target.get("label") or target["url"],
                        "summary": f"{host_of(target['url'])} bloquea la lectura automática ({reason}). Ábrelo una vez con «Resolver» "
                                   f"o quita este objetivo.", "detected_at": now}))
            return {"target_id": target["id"], "ok": False, "blocked": blocked, "error": reason, "observation": obs,
                    "events": [e["id"] for e in events]}

        # ---------------------------------------------------------------- choose the offer
        terms, must, exclude = self._match_config(watcher)
        is_list = ex.page_kind in ("search", "listing") or len(ex.offers) > 1
        matched: list[Offer] = [o for o in ex.offers if offer_matches(o, terms, must, exclude)] if is_list else list(ex.offers)
        new_skus: list[Offer] = []
        if is_list:
            seen = set(extra.get("seen_offer_urls") or [])
            for o in matched:
                if o.url and o.url not in seen:
                    new_skus.append(o)
            extra["seen_offer_urls"] = sorted(seen | {o.url for o in matched if o.url})[-400:]
            extra["matched_offers"] = [{"title": o.title, "url": o.url, "price": o.price, "currency": o.currency,
                                        "availability": o.availability, "seller": o.seller} for o in matched[:40]]
        offer = self._best(matched) if matched else (ex.primary if not is_list else None)
        if offer is None:
            offer = Offer(availability=UNKNOWN, method="none", evidence=["ningún producto de la página coincide con el vigilante"] if is_list else [])

        ceiling = rules.price_ceiling_for(target, policies)
        state, notes = rules.effective_state(offer, seller_policy=target.get("seller_policy") or "retail_only", price_ceiling=ceiling)
        level = int(target.get("source_level") or source_level_for_host(target["url"]) or 3)
        if is_list and level < 2:
            level = 2
        price_ok = None
        if offer.price is not None and target.get("msrp"):
            price_ok = offer.price >= float(target["msrp"]) * 0.3
        corroborated = revalidation and state == target.get("last_state") and state != UNKNOWN
        conf = rules.score_confidence(offer, source_level=level, tier=fr.tier, target_store_ids=target.get("store_ids") or [],
                                      corroborated=corroborated, price_ok=price_ok)

        obs = self.store.add_observation(target["id"], {
            "checked_at": now, "source_url": fr.final_url or fr.url, "tier": fr.tier, "http_status": fr.status, "availability": state,
            "price": offer.price, "currency": offer.currency or "", "seller": offer.seller or "", "seller_is_retailer": offer.seller_is_retailer,
            "buy_button": offer.buy_button, "store_availability": offer.store_availability, "preorder_date": offer.preorder_date or "",
            "restock_date": offer.restock_date or "", "evidence": (offer.evidence + notes)[:8], "confidence": conf.score,
            "factors": conf.factors, "method": offer.method, "raw_hash": ex.content_hash, "is_revalidation": revalidation})

        # ---------------------------------------------------------------- transitions -> events
        prev_known = extra.get("last_known_state") or target.get("last_state") or UNKNOWN
        prev_price = target.get("last_price")
        first_check = not extra.get("checked_once")
        drafts: list[dict[str, Any]] = []
        if not revalidation:
            if state != UNKNOWN:
                drafts = rules.transitions(target=target, prev_state=prev_known, prev_price=prev_price, state=state, offer=offer,
                                           policies=policies, ceiling=ceiling, first_check=first_check)
            if is_list and not first_check:
                for o in new_skus[:10]:
                    drafts.append({"type": NEW_SKU, "old_state": "", "new_state": o.availability, "price": o.price, "old_price": None,
                                   "severity": "medium", "sku_offer": o})
        if is_list and new_skus:
            self._track_new_skus(watcher, target, new_skus, first_check=first_check)
        title = offer.title or target.get("label") or target.get("last_title") or watcher["name"]
        retailer = target.get("retailer") or retailer_for_host(target["url"]) or host_of(target["url"])
        created = [self._raise(d, target=target, watcher=watcher, offer=offer, conf=conf, policies=policies, title=title,
                               retailer=retailer) for d in drafts]
        created = [e for e in created if e]

        # ---------------------------------------------------------------- update the target
        if state != UNKNOWN:
            extra["last_known_state"] = state
        extra["checked_once"] = True
        extra["page_kind"] = ex.page_kind
        min_price = target.get("min_price")
        if offer.price is not None and state in BUYABLE and (min_price is None or offer.price < float(min_price)):
            min_price = offer.price
        self.store.update_target(
            target["id"], status="active", last_check_ts=now, last_state=state,
            last_price=offer.price if offer.price is not None else target.get("last_price"),
            last_currency=offer.currency or target.get("last_currency") or "", last_confidence=conf.score,
            last_title=(offer.title or target.get("last_title") or "")[:200], last_image=offer.image or target.get("last_image") or "",
            last_error="", fail_count=0, min_price=min_price, extra=extra,
            next_check_ts=self._next_check(target, watcher) if not revalidation else target.get("next_check_ts"))
        return {"target_id": target["id"], "ok": True, "state": state, "price": offer.price, "currency": offer.currency,
                "confidence": conf.score, "band": conf.band, "factors": conf.factors, "method": offer.method, "page_kind": ex.page_kind,
                "matched_offers": len(matched) if is_list else None, "observation": obs, "events": [e["id"] for e in created],
                "notes": notes + ex.notes}

    def _track_new_skus(self, watcher: dict[str, Any], target: dict[str, Any], offers: list[Offer], *, first_check: bool) -> None:
        """Products that appear on a watched retailer search page become candidates and, unless the watcher says otherwise,
        product targets of their own (their product page carries the reliable stock signal). Capped per watcher."""
        product = (watcher.get("config") or {}).get("product") or {}
        auto = product.get("auto_track_new_skus", True)
        cap = int(product.get("max_targets", 15))
        policies = (watcher.get("config") or {}).get("policies") or {}
        existing = self.store.targets(watcher_id=watcher["id"])
        known = {t["url"] for t in existing}
        count = len(existing)
        for o in offers:
            if not o.url or not o.url.startswith("http") or o.url in known:
                continue
            cand = self.store.insert_candidate(watcher["id"], {
                "url": o.url, "title": o.title or "", "snippet": f"{o.price:g} {o.currency or 'EUR'}" if o.price is not None else "",
                "source_level": 1, "retailer": target.get("retailer") or retailer_for_host(o.url) or "", "engine": "retailer_search",
                "query": target["url"], "score": 80, "reason": f"aparece en la búsqueda de {target.get('retailer') or host_of(target['url'])}"})
            if cand and auto and count < cap and o.seller_is_retailer is not False:
                self.store.create_target(watcher["id"], o.url, label=(o.title or "")[:120], retailer=cand["retailer"], source_level=1,
                                         seller_policy=policies.get("seller") or target.get("seller_policy") or "retail_only",
                                         price_threshold=target.get("price_threshold"), extra={"from_search": target["id"]})
                self.store.set_candidate(cand["id"], "accepted", self.store.target_by_url(watcher["id"], o.url)["id"])
                count += 1
                known.add(o.url)

    @staticmethod
    def _best(offers: list[Offer]) -> Offer:
        def key(o: Offer) -> tuple:
            return (STATE_RANK.get(o.availability, 0), o.seller_is_retailer is not False, -(o.price if o.price is not None else 1e12))
        return max(offers, key=key)

    def _raise(self, draft: dict[str, Any], *, target: dict[str, Any], watcher: dict[str, Any], offer: Offer, conf: rules.Confidence,
               policies: dict[str, Any], title: str, retailer: str) -> Optional[dict[str, Any]]:
        now = self.clock()
        kind = draft["type"]
        sku_offer: Optional[Offer] = draft.pop("sku_offer", None)
        store = ""
        key = rules.dedupe_key(target["id"] if not sku_offer else (sku_offer.url or target["id"]), kind, draft.get("new_state") or "",
                               draft.get("price"), store)
        cooldown = float(policies.get("cooldown_minutes") or 0) * 60
        if self.store.recent_event(key, now - max(cooldown, 60)):
            return None
        if kind == rules.RESTOCK and cooldown and self.store.last_event_of(target["id"], [rules.RESTOCK], now - cooldown):
            return None  # IN -> OUT -> IN flap inside the cooldown window
        require = int(policies.get("require_confidence") or rules.ALERT_THRESHOLD)
        alert_on = set(policies.get("alert_on") or [])
        if kind == rules.SOLD_OUT and policies.get("notify_sold_out"):
            alert_on.add(kind)
        revalidate_s = float(policies.get("revalidate_seconds") or 0)
        if sku_offer is not None:
            status, revalidate_at, confidence = "confirmed", None, 60
            summary = f"Nuevo producto en {retailer}: {sku_offer.title or sku_offer.url}" + (
                f" — {sku_offer.price:g} {sku_offer.currency or 'EUR'}" if sku_offer.price is not None else "")
            url = sku_offer.url or target["url"]
            ev_title = sku_offer.title or title
            if kind not in alert_on and NEW_SKU not in alert_on:
                status = "confirmed" if policies.get("alert_new_sku", True) else "logged"
        else:
            confidence = conf.score
            if kind not in alert_on or draft.get("over_ceiling"):
                status, revalidate_at = "logged", None
            elif confidence >= require:
                if draft.get("severity") == "high" and revalidate_s > 0:
                    status, revalidate_at = "pending", now + revalidate_s
                else:
                    status, revalidate_at = "confirmed", None
            elif confidence >= rules.REVALIDATE_THRESHOLD and revalidate_s > 0:
                status, revalidate_at = "pending", now + revalidate_s
            else:
                status, revalidate_at = "logged", None
            summary = rules.summary_for(kind, title=title, retailer=retailer, state=draft.get("new_state") or "", price=draft.get("price"),
                                        currency=offer.currency or target.get("last_currency") or "EUR", old_price=draft.get("old_price"),
                                        extra=draft)
            url = offer.url if (offer.url and offer.url.startswith("http")) else target["url"]
            ev_title = title
        data = {k: v for k, v in draft.items() if k not in ("type", "old_state", "new_state", "price", "old_price", "severity")}
        data.update({"retailer": retailer, "factors": conf.factors, "method": offer.method, "evidence": offer.evidence[:4],
                     "image": offer.image or target.get("last_image") or ""})
        event = self.store.add_event({
            "watcher_id": watcher["id"], "target_id": target["id"], "type": kind, "severity": draft.get("severity") or "medium",
            "status": status, "old_state": draft.get("old_state") or "", "new_state": draft.get("new_state") or "",
            "price": draft.get("price"), "old_price": draft.get("old_price"), "currency": offer.currency or target.get("last_currency") or "",
            "confidence": confidence, "dedupe_key": key, "url": url, "title": ev_title, "summary": summary, "data": data,
            "detected_at": now, "revalidate_at": revalidate_at})
        if status == "confirmed":
            self.dispatch(event, watcher)
        return event

    # ======================================================================================== revalidation
    def revalidate(self, event_id: str) -> dict[str, Any]:
        event = self.store.event(event_id)
        if event["status"] != "pending":
            return event
        try:
            result = self.check_target(event["target_id"], revalidation=True)
        except Exception as error:  # noqa: BLE001
            return self.store.update_event(event_id, status="logged", data={**event["data"], "revalidation": f"error: {error}"})
        watcher = self.store.watcher(event["watcher_id"])
        policies = rules.policies_for(watcher.get("config") or {})
        require = int(policies.get("require_confidence") or rules.ALERT_THRESHOLD)
        still = result.get("ok") and self._still_holds(event, result)
        data = {**event["data"], "revalidation": {"state": result.get("state"), "price": result.get("price"),
                                                   "confidence": result.get("confidence"), "at": self.clock()}}
        if not still:
            return self.store.update_event(event_id, status="dismissed", data={**data, "dismissed_reason": "no se confirmó al revalidar"})
        confidence = min(100, max(int(event["confidence"]), int(result.get("confidence") or 0)) + 15)
        status = "confirmed" if confidence >= require else "logged"
        event = self.store.update_event(event_id, status=status, confidence=confidence, data=data)
        if status == "confirmed":
            self.dispatch(event, watcher)
        return event

    @staticmethod
    def _still_holds(event: dict[str, Any], result: dict[str, Any]) -> bool:
        state = result.get("state")
        kind = event["type"]
        if kind in (rules.PRICE_DROP, rules.PRICE_THRESHOLD_CROSSED):
            price = result.get("price")
            return state in BUYABLE and price is not None and event.get("price") is not None and price <= float(event["price"]) * 1.01
        if kind == rules.SOLD_OUT:
            return state not in BUYABLE
        return state == event.get("new_state") or (kind == rules.RESTOCK and state in BUYABLE)

    # ======================================================================================== notification
    def dispatch(self, event: dict[str, Any], watcher: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        watcher = watcher or (self.store.watcher(event["watcher_id"]) if event.get("watcher_id") else {"name": "", "config": {}})
        cfg = watcher.get("config") or {}
        channels = cfg.get("channels") or ["toast", "hub", "ntfy", "telegram", "email"]
        already = self.store.notified_channels(event["id"])
        channels = [c for c in channels if c not in already]
        payload = {"id": event["id"], "type": event["type"], "severity": event["severity"], "title": event["title"],
                   "summary": event["summary"], "url": event["url"], "price": event.get("price"), "currency": event.get("currency"),
                   "confidence": event.get("confidence"), "watcher_name": watcher.get("name", ""),
                   "image": (event.get("data") or {}).get("image", "")}
        results: list[dict[str, Any]] = []
        try:
            results = self.notifier.send(payload, channels) if channels else []
        except Exception as error:  # noqa: BLE001
            log.warning("notify failed: %s", error)
        any_ok = False
        for r in results:
            if r.get("skipped"):
                continue
            self.store.record_notification(event["id"], r.get("channel", "?"), bool(r.get("ok")), str(r.get("error") or ""))
            any_ok = any_ok or bool(r.get("ok"))
        if any_ok:
            self.store.update_event(event["id"], notified=True)
        self.emit(f"tantalus.event.{event['type'].lower()}", {"id": event["id"], "watcher": watcher.get("name", ""),
                                                               "title": event["title"], "summary": event["summary"], "url": event["url"],
                                                               "price": event.get("price"), "severity": event["severity"]})
        return results

    # ======================================================================================== second-hand
    def run_secondhand(self, watcher_id: str) -> dict[str, Any]:
        watcher = self.store.watcher(watcher_id)
        cfg = watcher.get("config") or {}
        pack = get_pack(cfg.get("pack") or "generic") or get_pack("generic")
        settings = {**(pack.get("settings_defaults") or {}), **(cfg.get("settings") or {})}
        if settings.get("latitude") is None and settings.get("origin_location"):
            coords = find_municipality_coords(settings["origin_location"])
            if coords:
                settings["latitude"], settings["longitude"] = coords[0], coords[1]
        if cfg.get("alert_min_score") is not None:
            settings["alert_min_score"] = cfg["alert_min_score"]
        alert_min = float(settings.get("alert_min_score") or pack.get("alert_min_score") or 8)
        queries = [q for q in (cfg.get("queries") or pack.get("default_queries") or [])[:12] if str(q).strip()]
        sources = cfg.get("sources") or ["wallapop"]
        first_run = watcher.get("last_run_ts") is None
        run_id = self.store.start_run("secondhand", watcher_id=watcher_id)
        index = DedupeIndex(self.store.listing_rows_for_dedupe(watcher_id))
        errors: list[str] = []
        stats = {"fetched": 0, "new": 0, "relevant_new": 0, "updated": 0, "events": 0}
        for source_name in sources:
            try:
                source = build_source(source_name, self.fetcher, **(cfg.get("source_options", {}).get(source_name, {})))
            except KeyError as error:
                errors.append(str(error))
                continue
            for query in queries:
                items, error = source.search(query, latitude=settings.get("latitude"), longitude=settings.get("longitude"),
                                             location_text=settings.get("origin_location"), radius_km=float(settings.get("radius_km") or 30),
                                             max_price=settings.get("price_ceiling") or settings.get("max_price"),
                                             min_price=settings.get("min_price"), limit=int(cfg.get("limit_per_query") or 40))
                if error:
                    errors.append(f"{source_name} «{query}»: {error}")
                for item in items:
                    item.matched_query = item.matched_query or query
                    stats["fetched"] += 1
                    self._ingest_listing(watcher, item, pack, settings, alert_min, index, first_run, stats)
        now = self.clock()
        self.store.update_watcher(watcher_id, last_run_ts=now, last_error="; ".join(errors)[:500],
                                  next_run_ts=now + float(watcher.get("interval_min") or 45) * 60 * (1 + self.rng.uniform(-0.1, 0.1)))
        summary = {**stats, "errors": errors[:10], "first_run": first_run, "queries": len(queries), "sources": sources}
        self.store.finish_run(run_id, not errors or stats["fetched"] > 0, summary)
        if stats["relevant_new"]:
            self.emit("tantalus.secondhand.found", {"watcher": watcher["name"], "count": stats["relevant_new"]})
        return summary

    def _ingest_listing(self, watcher: dict[str, Any], item: RawListing, pack: dict[str, Any], settings: dict[str, Any], alert_min: float,
                        index: DedupeIndex, first_run: bool, stats: dict[str, int]) -> None:
        dup, _reason = index.find(item)
        if dup is not None:
            old = self.store.listing(dup["id"])
            updated = self.store.touch_listing(dup["id"], price=item.price, reserved=item.reserved)
            stats["updated"] += 1
            if (old.get("relevant") and item.price is not None and old.get("price") is not None and item.price < float(old["price"]) - 0.01):
                key = rules.dedupe_key(dup["id"], LISTING_PRICE_DROP, "", item.price)
                if not self.store.recent_event(key, self.clock() - 3600):
                    ev = self.store.add_event({
                        "watcher_id": watcher["id"], "listing_id": dup["id"], "type": LISTING_PRICE_DROP, "severity": "medium",
                        "status": "confirmed", "price": item.price, "old_price": old["price"], "currency": item.currency or "EUR",
                        "confidence": 70, "dedupe_key": key, "url": updated["url"], "title": updated["title"],
                        "summary": f"{updated['title']}: baja de {old['price']:g} a {item.price:g} € ({item.source})",
                        "data": {"image": updated.get("image_url") or "", "source": item.source}})
                    stats["events"] += 1
                    self.dispatch(ev, watcher)
            return
        score = score_listing(item, pack, settings, llm=self.llm)
        relevant = bool(score.relevant and score.score >= alert_min)
        row = self.store.insert_listing(watcher["id"], {
            "source": item.source, "external_id": item.external_id or "", "url": item.url, "title": item.title,
            "description": (item.description or "")[:4000], "price": item.price, "currency": item.currency or "EUR",
            "price_raw": item.price_raw or "", "location_text": item.location_text or "",
            "distance_km": score.distance_km if score.distance_km is not None else item.distance_km, "image_url": item.image_url or "",
            "seller": item.seller_name or "", "shipping": item.shipping, "reserved": item.reserved, "score": score.score,
            "relevant": relevant, "signals": [{"key": s.key, "points": s.points, "label": s.label} for s in score.signals],
            "reason": score.reason, "category": score.category, "method": score.method, "quantity_min": score.quantity_min,
            "quantity_max": score.quantity_max, "matched_query": item.matched_query or "",
            "extra": {"listing_date": item.listing_date, **(item.extra or {})}})
        index.add(row)
        stats["new"] += 1
        if not relevant:
            return
        stats["relevant_new"] += 1
        severity = "high" if score.score >= alert_min * 1.5 else "medium"
        price = f"{item.price:g} €" if item.price is not None else "precio ?"
        where = f" · {item.location_text}" if item.location_text else ""
        dist = f" · {score.distance_km:.0f} km" if score.distance_km is not None else ""
        ev = self.store.add_event({
            "watcher_id": watcher["id"], "listing_id": row["id"], "type": NEW_LISTING, "severity": severity,
            "status": "logged" if first_run else "confirmed", "price": item.price, "currency": item.currency or "EUR",
            "confidence": int(min(100, 50 + score.score * 2)), "dedupe_key": rules.dedupe_key(row["id"], NEW_LISTING, "", item.price),
            "url": item.url, "title": item.title, "summary": f"{item.title} — {price}{where}{dist} (puntuación {score.score:g}, {item.source})",
            "data": {"image": item.image_url or "", "source": item.source, "score": score.score, "reason": score.reason,
                     "first_run": first_run}})
        stats["events"] += 1
        if ev["status"] == "confirmed":
            self.dispatch(ev, watcher)

    def rescore_listings(self, watcher_id: str) -> dict[str, Any]:
        """Score the stored listings again with the watcher's current pack and settings (after tuning a pack). No events."""
        watcher = self.store.watcher(watcher_id)
        cfg = watcher.get("config") or {}
        pack = get_pack(cfg.get("pack") or "generic") or get_pack("generic")
        settings = {**(pack.get("settings_defaults") or {}), **(cfg.get("settings") or {})}
        if settings.get("latitude") is None and settings.get("origin_location"):
            coords = find_municipality_coords(settings["origin_location"])
            if coords:
                settings["latitude"], settings["longitude"] = coords[0], coords[1]
        alert_min = float(cfg.get("alert_min_score") or settings.get("alert_min_score") or pack.get("alert_min_score") or 8)
        changed = 0
        rows = self.store.listings(watcher_id=watcher_id, limit=5000)
        for row in rows:
            item = RawListing(source=row["source"], url=row["url"], title=row["title"], external_id=row["external_id"],
                              description=row["description"], price=row["price"], currency=row["currency"],
                              location_text=row["location_text"], distance_km=row["distance_km"], shipping=row["shipping"],
                              reserved=row["reserved"], listing_date=(row.get("extra") or {}).get("listing_date"))
            score = score_listing(item, pack, {**settings, "use_llm": False})
            relevant = bool(score.relevant and score.score >= alert_min)
            if relevant != bool(row["relevant"]) or abs(score.score - float(row["score"])) > 0.01:
                changed += 1
                self.store.db.execute(
                    "UPDATE listings SET score = ?, relevant = ?, signals = ?, reason = ?, category = ?, method = ? WHERE id = ?",
                    (score.score, 1 if relevant else 0, json.dumps([{"key": x.key, "points": x.points, "label": x.label}
                                                                   for x in score.signals], ensure_ascii=False),
                     score.reason, score.category, score.method, row["id"]))
        return {"listings": len(rows), "changed": changed}

    # ======================================================================================== information
    def run_information(self, watcher_id: str) -> dict[str, Any]:
        watcher = self.store.watcher(watcher_id)
        cfg = watcher.get("config") or {}
        sources = self.store.sync_info_sources(watcher_id, cfg.get("sources") or [])
        run_id = self.store.start_run("information", watcher_id=watcher_id)
        stats = {"sources": len(sources), "findings": 0, "new": 0, "material": 0, "events": 0}
        errors: list[str] = []
        for source in sources:
            baseline = source.get("last_check_ts") is None
            findings, update = self.info.check_source(source, watcher)
            self.store.update_info_source(source["id"], update)
            if update.get("last_error"):
                errors.append(f"{source['kind']} {source['value'][:60]}: {update['last_error']}")
            if not findings:
                continue
            findings = self.info.judge(findings, watcher)
            stats["findings"] += len(findings)
            for f in findings:
                if f.verdict == "irrelevant":
                    stats["irrelevant"] = stats.get("irrelevant", 0) + 1
                    continue
                item = self.store.insert_info_item(watcher_id, source["id"], f)
                if item is None:
                    continue
                stats["new"] += 1
                if not f.material:
                    continue
                stats["material"] += 1
                confirmed = f.verdict == "confirmed" and not baseline
                ev = self.store.add_event({
                    "watcher_id": watcher_id, "info_id": item["id"], "type": INFO_CHANGE,
                    "severity": "medium" if f.verdict == "confirmed" else "low", "status": "confirmed" if confirmed else "logged",
                    "confidence": int(min(100, max(0, f.score))), "dedupe_key": rules.dedupe_key(watcher_id, INFO_CHANGE, f.content_hash, None),
                    "url": f.url, "title": f.title or f.url,
                    "summary": f"[{ {'confirmed': 'confirmado', 'leak': 'filtración', 'estimate': 'estimación'}.get(f.verdict, f.verdict) }] "
                               f"{f.title} — {(f.diff or f.snippet)[:220]}",
                    "data": {"verdict": f.verdict, "reason": f.reason, "kind": f.kind, "source_level": f.source_level, "baseline": baseline}})
                stats["events"] += 1
                if ev["status"] == "confirmed":
                    self.dispatch(ev, watcher)
        now = self.clock()
        self.store.update_watcher(watcher_id, last_run_ts=now, last_error="; ".join(errors)[:500],
                                  next_run_ts=now + float(watcher.get("interval_min") or 120) * 60 * (1 + self.rng.uniform(-0.1, 0.1)))
        summary = {**stats, "errors": errors[:10]}
        self.store.finish_run(run_id, not errors or stats["findings"] > 0, summary)
        return summary

    # ======================================================================================== discovery
    def run_discovery(self, watcher_id: str) -> dict[str, Any]:
        watcher = self.store.watcher(watcher_id)
        cfg = watcher.get("config") or {}
        run_id = self.store.start_run("discovery", watcher_id=watcher_id)
        known = self.store.known_urls(watcher_id)
        view = {**watcher, "config": {**cfg, "seller_policy": (cfg.get("policies") or {}).get("seller") or cfg.get("seller_policy")
                                      or "retail_only"}}
        candidates, errors = discover(view, self.websearch, existing_urls=known, llm=self.llm)
        created = []
        auto = bool((cfg.get("discovery") or {}).get("auto_accept"))
        for c in candidates:
            row = self.store.insert_candidate(watcher_id, c)
            if row is None:
                continue
            created.append(row)
            if auto and int(row["source_level"]) <= 1 and float(row["score"]) >= 70:
                self.accept_candidate(row["id"])
        now = self.clock()
        self.store.update_watcher(watcher_id, last_discovery_ts=now)
        if created:
            best = created[0]
            ev = self.store.add_event({
                "watcher_id": watcher_id, "candidate_id": best["id"], "type": CANDIDATE_FOUND, "severity": "low", "status": "confirmed",
                "confidence": int(best["score"]), "dedupe_key": rules.dedupe_key(watcher_id, CANDIDATE_FOUND, str(len(created)), now),
                "url": best["url"], "title": f"{len(created)} página(s) nueva(s) para «{watcher['name']}»",
                "summary": "; ".join(f"{c['retailer'] or c['host']}: {c['title'][:60]}" for c in created[:4]),
                "data": {"count": len(created), "candidates": [c["id"] for c in created[:20]]}})
            self.dispatch(ev, watcher)
        summary = {"candidates": len(created), "errors": errors}
        self.store.finish_run(run_id, bool(created) or not errors, summary)
        return summary

    def accept_candidate(self, candidate_id: str, **overrides: Any) -> dict[str, Any]:
        cand = self.store.candidate(candidate_id)
        watcher = self.store.watcher(cand["watcher_id"])
        cfg = watcher.get("config") or {}
        policies = cfg.get("policies") or {}
        target = self.store.create_target(
            cand["watcher_id"], overrides.get("url") or cand["url"], label=overrides.get("label") or cand["title"][:120],
            retailer=cand["retailer"], source_level=int(cand["source_level"] or 1),
            seller_policy=overrides.get("seller_policy") or policies.get("seller") or "retail_only",
            msrp=overrides.get("msrp") or policies.get("msrp"), price_threshold=overrides.get("price_threshold"),
            extra={"from_candidate": candidate_id})
        self.store.set_candidate(candidate_id, "accepted", target["id"])
        return target
