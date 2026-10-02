"""Turn a ``FetchResult`` into an ``Extraction``: offers, page kind, readable text, quality gate and hash.

    extract(fr, *, hints=None, llm=None) -> Extraction
    adapter_for(url, hints=None) -> {"adapter", "url", "accept", ...} | None

Order of evidence (CONTRACT.md): site API adapter -> JSON-LD -> microdata / OpenGraph ``product:*`` meta ->
phrase rules (buttons + short availability lines) -> optional LLM with literal evidence. A list / search page
yields many offers (JSON-LD ``ItemList`` first, then repeated product cards).

``hints``: ``url, sku, ean, store_ids, retailer, adapter, region, title_hint`` (all optional) and ``today``
(ISO date, tests). Nothing here touches the network.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..hoard_link.web.blocks import HTTP_5XX, detect_block
from ..llm import LLM
from ..model import (COMING_SOON, IN_STOCK, LOCAL_PICKUP, OUT_OF_STOCK, PREORDER, RESTOCK_SCHEDULED, UNKNOWN, Extraction,
                     FetchResult, Offer)
from . import jsonld, listing, meta as meta_mod, moonshine, nvidia, phrases, sites, text as text_mod
from .llm_extract import llm_extract
from .sites import (is_first_party_seller, is_marketplace_host, needs_browser, profile_for_host, retailer_for_host,
                    source_level_for_host)

__all__ = ["extract", "adapter_for", "retailer_for_host", "source_level_for_host", "needs_browser", "profile_for_host",
           "is_marketplace_host"]

_SEARCH_URL = re.compile(r"/(?:buscar|busqueda|search|resultados|sch)\b|[?&](?:q|s|k|query|search|keyword|keywords|term|_nkw)=", re.I)
TEXT_EXCERPT_MAX = 20_000


# ====================================================================================== adapters
def adapter_for(url: str, hints: Optional[dict] = None) -> Optional[dict[str, Any]]:
    """Is there an API adapter for this target? Returns what to fetch, or ``None``.

    ``{"adapter": "nvidia", "url": <API url>, "accept": "json", "respect_robots": False, "tier": "http",
    "min_interval_s": 10.0}`` — the engine calls ``fetcher.get_json(url, ...)`` with those and hands the
    result to ``extract`` with ``hints["adapter"] = "nvidia"``.
    """
    hints = hints or {}
    name = str(hints.get("adapter") or "").lower()
    if name not in ("", "auto", nvidia.NAME):
        return None
    api = nvidia.adapter_url(url, hints)
    if api:
        return {"adapter": nvidia.NAME, "url": api, "accept": "json", "respect_robots": False, "tier": "http",
                "min_interval_s": 10.0}
    return None


# ====================================================================================== entry point
def extract(fr: FetchResult, *, hints: Optional[dict] = None, llm: Optional[LLM] = None) -> Extraction:
    hints = dict(hints or {})
    ex = Extraction()
    url = fr.final_url or fr.url or str(hints.get("url") or "")
    body = fr.text or ""

    # ---- unusable input
    if fr.blocked or fr.block_reason in ("robots", "offline", "unsafe_url"):
        return _degenerate(ex, "blocked", f"fetch was blocked: {fr.block_reason or fr.error}")
    if not body.strip():
        return _degenerate(ex, "unknown", fr.error or "empty response")
    reason = detect_block(fr.status or 200, body, fr.headers, url) if fr.ok or fr.status in (0, 200) else ""
    if reason and reason != HTTP_5XX:
        return _degenerate(ex, "blocked", f"block page detected: {reason}")

    # ---- JSON (API adapters)
    stripped = body.lstrip()
    if stripped[:1] in "{[" or "json" in (fr.content_type or "").lower():
        return _extract_json(ex, body, url, hints)

    return _extract_html(ex, body, url, fr, hints, llm)


def _degenerate(ex: Extraction, kind: str, note: str) -> Extraction:
    ex.page_kind = kind
    ex.quality_ok = False
    ex.notes.append(note)
    return ex


# ====================================================================================== JSON
def _extract_json(ex: Extraction, body: str, url: str, hints: dict) -> Extraction:
    try:
        data = json.loads(body)
    except ValueError as error:
        return _degenerate(ex, "unknown", f"invalid JSON: {error}")
    if nvidia.is_payload(data):
        offers, kind, notes = nvidia.extract_payload(data, url=url, hints=hints)
        ex.offers = offers
        ex.page_kind = kind if offers else "search"
        ex.methods = ["api:nvidia"]
        ex.notes.extend(notes)
        ex.title = str(hints.get("title_hint") or (offers[0].title if offers else "") or "")
        ex.quality_ok = bool(offers)
        _annotate(ex.offers, url, hints)
        return ex
    return _degenerate(ex, "unknown", "JSON document not recognised by any adapter")


# ====================================================================================== HTML
def _extract_html(ex: Extraction, body: str, url: str, fr: FetchResult, hints: dict, llm: Optional[LLM]) -> Extraction:
    soup = BeautifulSoup(body, "html.parser")
    profile = profile_for_host(url)
    today = _today(hints)

    ld = jsonld.extract_jsonld(soup, url)
    meta = meta_mod.extract_meta(soup, url)
    full = text_mod.full_text(soup)
    readable = text_mod.readable_text(soup)
    headline = _headline(soup, profile)
    ex.title = (headline or meta.title or (soup.title.get_text(" ", strip=True) if soup.title else "")).strip()[:300]

    kind_hint = sites.kind_of_url(url)
    path_q = urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")
    is_search_url = kind_hint == "search" or bool(_SEARCH_URL.search(path_q))
    has_product_markup = bool(ld.has_product or meta.has_product_markup or kind_hint == "product")

    # ---- list / search pages
    if is_search_url or not has_product_markup or len(ld.listed) >= 3 and not ld.products:
        listed = ld.listed if len(ld.listed) >= 2 else []
        tiles = [] if (listed and not is_search_url) else (moonshine.extract_moonshine(soup, url)
                                                            or listing.extract_tiles(soup, url, profile=profile))
        candidates = listed if (listed and (len(listed) >= len(tiles) or not tiles)) else tiles
        if (is_search_url or not has_product_markup) and len(candidates) >= 2:
            ex.offers = candidates
            ex.page_kind = "search" if is_search_url else "listing"
            ex.methods = ["jsonld" if candidates is listed else
                          ("site:state" if tiles and tiles[0].method.startswith("site:") else "tiles")]
            _finish(ex, readable, url, hints, profile, decided=any(o.price is not None for o in candidates))
            return ex

    # ---- product page
    scan = phrases.scan(soup, full, headline=headline, profile=profile, hints=hints, today=today)
    offers = _product_offers(ld, meta, ex)
    if profile is not None and getattr(profile, "price_selectors", ()):
        price = _selector_price(soup, profile.price_selectors)
        if price is not None:
            for offer in offers:
                if offer.price is None:
                    offer.price, offer.currency = price
                    offer.evidence.append(phrases.snippet(f"price selector: {price[0]}"))

    expected = bool(hints.get("sku") or hints.get("ean") or hints.get("title_hint") or hints.get("page_kind") == "product")
    if not offers and (has_product_markup or scan.decided or expected or (scan.price is not None and headline)):
        few_controls = sum(1 for c in scan.controls if not c.hidden) <= 6
        if has_product_markup or expected or few_controls:
            offer = Offer(method="phrases", title=ex.title or None, image=meta.image or None)
            offers = [offer]
            has_product_markup = True

    for offer in offers:
        _enrich(offer, scan, meta, ex, url, hints, profile)

    if offers and all(o.availability == UNKNOWN for o in offers) and llm is not None and has_product_markup:
        llm_text = "\n".join([headline, phrases.window_after(full, headline, 4000),
                              "BUTTONS: " + "; ".join(c.text for c in scan.controls if not c.hidden)[:600]])
        answer = llm_extract(llm, llm_text, url=url, title_hint=str(hints.get("title_hint") or ex.title))
        if answer is not None:
            target = offers[0]
            target.availability, target.method = answer.availability, "llm"
            target.price = target.price if target.price is not None else answer.price
            target.currency = target.currency or answer.currency
            target.seller = target.seller or answer.seller
            target.buy_button = answer.buy_button if answer.buy_button is not None else target.buy_button
            target.evidence.extend(answer.evidence)
            target.extra["llm"] = True
            ex.methods.append("llm")
            ex.notes.append("availability from the model, checked against literal evidence")

    ex.offers = offers
    if offers:
        ex.page_kind = "product"
    elif ld.listed or meta.og_type == "article" or len(readable) > 800:
        ex.page_kind = "article" if meta.og_type == "article" or not ld.listed else "listing"
    else:
        ex.page_kind = "unknown"
    if not ex.methods:
        ex.methods = sorted({o.method for o in offers}) or []
    if ld.invalid_blocks:
        ex.notes.append(f"{ld.invalid_blocks} JSON-LD block(s) could not be parsed")
    if not offers and not ex.notes:
        ex.notes.append("no product signals found on this page")
    _finish(ex, readable, url, hints, profile, decided=any(o.availability != UNKNOWN or o.price is not None for o in offers))
    return ex


# ====================================================================================== helpers
def _today(hints: dict) -> Optional[date]:
    value = hints.get("today")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _headline(soup: BeautifulSoup, profile: Any) -> str:
    for selector in getattr(profile, "title_selectors", ()) or ():
        try:
            tag = soup.select_one(selector)
        except Exception:  # noqa: BLE001
            tag = None
        if tag is not None and tag.get_text(strip=True):
            return tag.get_text(" ", strip=True)
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 is not None else ""


def _selector_price(soup: BeautifulSoup, selectors: Any) -> Optional[tuple[float, str]]:
    for selector in selectors:
        try:
            for tag in soup.select(selector):
                found = phrases.parse_price(tag.get_text(" ", strip=True))
                if found:
                    return found
        except Exception:  # noqa: BLE001
            continue
    return None


def _product_offers(ld: jsonld.JsonLdResult, meta: meta_mod.PageMeta, ex: Extraction) -> list[Offer]:
    """Best structured offers: JSON-LD, then microdata, then OpenGraph."""
    if ld.products:
        informed = [o for o in ld.products if o.price is not None or o.availability != UNKNOWN]
        chosen = informed or ld.products[:1]
        ex.methods.append("jsonld")
        return list(chosen)
    informed_micro = [o for o in meta.microdata if o.price is not None or o.availability != UNKNOWN]
    if informed_micro:
        ex.methods.append("microdata")
        return informed_micro
    if meta.offer is not None and (meta.offer.price is not None or meta.offer.availability != UNKNOWN):
        ex.methods.append("opengraph")
        return [meta.offer]
    return []


def _enrich(offer: Offer, scan: phrases.PhraseScan, meta: meta_mod.PageMeta, ex: Extraction, url: str, hints: dict,
            profile: Any) -> None:
    """Fill what the structured data did not say with the page's own controls / lines; flag contradictions."""
    if not offer.title:
        offer.title = ex.title or None
    if not offer.image and meta.image:
        offer.image = meta.image
    if not offer.url:
        offer.url = url
    # availability + button
    if offer.availability == UNKNOWN and scan.decided:
        offer.availability = scan.state
        offer.method = f"{offer.method}+phrases" if offer.method not in ("phrases", "") else "phrases"
        offer.evidence.extend(scan.evidence[:4])
        if "phrases" not in ex.methods:
            ex.methods.append("phrases")
    if scan.state == COMING_SOON and offer.availability in (OUT_OF_STOCK, RESTOCK_SCHEDULED):
        # shops mark unreleased products "OutOfStock" in their structured data; the page itself says "Próximamente"
        offer.availability = COMING_SOON
        offer.method = f"{offer.method}+phrases" if offer.method not in ("phrases", "") else "phrases"
        offer.evidence.append(scan.coming_soon or "próximamente")
        if scan.preorder_date:
            offer.extra["release_date"] = scan.preorder_date
    if offer.buy_button is None and scan.buy_button is not None:
        offer.buy_button = scan.buy_button
    if scan.buy_button is not None and offer.method.startswith(("jsonld", "microdata", "opengraph")):
        if offer.availability in (IN_STOCK,) and scan.state == OUT_OF_STOCK:
            offer.extra["conflict"] = "structured data says in stock but the page shows a sold-out control"
        elif offer.availability == OUT_OF_STOCK and scan.state in (IN_STOCK, PREORDER) and scan.buy_button:
            offer.extra["conflict"] = "structured data says sold out but the page has an active buy control"
        if offer.extra.get("conflict"):
            ex.notes.append(offer.extra["conflict"])
    if scan.mixed_signals:
        offer.extra["mixed_signals"] = True
    if offer.price is None and scan.price is not None and offer.availability != UNKNOWN:
        offer.price, offer.currency = scan.price, offer.currency or scan.currency
        offer.evidence.append("price from page text")
    if not offer.seller and scan.seller:
        offer.seller = scan.seller
        offer.evidence.append(scan.seller[:80])
    # dates
    if scan.restock_date and offer.availability in (OUT_OF_STOCK, RESTOCK_SCHEDULED):
        offer.restock_date = offer.restock_date or scan.restock_date
        offer.availability = RESTOCK_SCHEDULED
    if scan.preorder_date and offer.availability == PREORDER and not offer.preorder_date:
        offer.preorder_date = scan.preorder_date
    # pickup / stores
    if scan.pickup_hint:
        offer.extra["pickup_hint"] = True
    if scan.store_availability:
        offer.store_availability.update(scan.store_availability)
        if offer.availability in (OUT_OF_STOCK, UNKNOWN):
            offer.availability = LOCAL_PICKUP
            offer.evidence.append("pickup phrase next to a target store")
    if scan.buy_button is not None and offer.availability != UNKNOWN and not offer.evidence:
        offer.evidence.extend(scan.evidence[:2])
    offer.evidence = list(dict.fromkeys(e[:160] for e in offer.evidence if e))[:8]
    _annotate([offer], url, hints)


def _annotate(offers: list[Offer], url: str, hints: dict) -> None:
    """Seller classification and SKU / EAN agreement with the target's hints."""
    profile = profile_for_host(url)
    sku_hint = _key(hints.get("sku"))
    ean_hint = re.sub(r"\D", "", str(hints.get("ean") or ""))
    for offer in offers:
        if offer.seller_is_retailer is None:
            if profile is not None and profile.is_marketplace:
                offer.seller_is_retailer = False
            elif offer.seller:
                offer.seller_is_retailer = is_first_party_seller(offer.seller, offer.url or url)
        if sku_hint:
            candidates = {_key(offer.sku)} - {""}
            offer.extra["sku_match"] = (sku_hint in candidates) or (sku_hint in _key(offer.url) if offer.url else False) or (
                False if candidates else None)
        if ean_hint:
            offer.extra["ean_match"] = (offer.ean == ean_hint) if offer.ean else None
        if not offer.currency and offer.price is not None and profile is not None:
            offer.currency = "EUR"


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _finish(ex: Extraction, readable: str, url: str, hints: dict, profile: Any, *, decided: bool) -> None:
    ex.text_excerpt = readable[:TEXT_EXCERPT_MAX]
    text_ok = text_mod.quality_ok(readable)
    ex.content_hash = text_mod.content_hash(readable) if text_ok else ""
    ex.quality_ok = text_ok or decided
    if not text_ok and not decided:
        ex.notes.append("readable text is degenerate (empty, tiny or navigation-only)")
    if profile is not None and profile.needs_browser and not decided and not text_ok:
        ex.notes.append("this host needs the browser tier (the page looks like a JS shell)")
    if ex.offers:
        _annotate(ex.offers, url, hints)
