"""schema.org JSON-LD -> Offers.

Tolerant on purpose: invalid JSON blocks are repaired when trivial and skipped otherwise, ``@graph`` / arrays /
``ItemList`` / ``ProductGroup.hasVariant`` / ``BuyAction.object`` are walked, ``AggregateOffer`` and lists of
offers become one Offer per seller. Only bs4's stdlib ``html.parser`` is needed.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..model import (IN_STOCK, LOCAL_PICKUP, OUT_OF_STOCK, PREORDER, RESTOCK_SCHEDULED, UNKNOWN, Offer)
from .phrases import parse_number

PRODUCT_TYPES = {"product", "productgroup", "individualproduct", "productmodel", "someproducts", "vehicle", "car"}
OFFER_TYPES = {"offer", "aggregateoffer", "offerforpurchase", "offerforlease"}
_SKIP_KEYS = {"review", "reviews", "aggregaterating", "isrelatedto", "issimilarto", "isaccessoryorsparepartfor",
              "isconsumablefor", "mainentityofpage", "potentialaction", "breadcrumb", "publisher", "author"}

# schema.org availability (last path segment, lower-cased, letters only) -> our state
AVAILABILITY_MAP = {
    "instock": IN_STOCK,
    "onlineonly": IN_STOCK,
    "limitedavailability": IN_STOCK,
    "instoreonly": LOCAL_PICKUP,
    "outofstock": OUT_OF_STOCK,
    "soldout": OUT_OF_STOCK,
    "discontinued": OUT_OF_STOCK,
    "preorder": PREORDER,
    "presale": PREORDER,
    "backorder": PREORDER,
}


def map_availability(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("@id") or value.get("name") or ""
    if not isinstance(value, str):
        return UNKNOWN
    key = re.sub(r"[^a-z]", "", value.strip().rstrip("/").rsplit("/", 1)[-1].lower())
    return AVAILABILITY_MAP.get(key, UNKNOWN)


@dataclass
class JsonLdResult:
    products: list[Offer] = field(default_factory=list)   # top-level Product / variants
    listed: list[Offer] = field(default_factory=list)     # products inside an ItemList
    has_product: bool = False
    blocks: int = 0
    invalid_blocks: int = 0
    breadcrumbs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------- loading
def _load(raw: str) -> Optional[Any]:
    raw = (raw or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^\s*<!--|-->\s*$", "", raw).strip()
    for candidate in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
        try:
            return json.loads(candidate, strict=False)
        except ValueError:
            continue
    return None


def load_blocks(soup: BeautifulSoup) -> tuple[list[Any], int]:
    """All parseable ld+json payloads and the number of blocks that could not be parsed."""
    payloads: list[Any] = []
    invalid = 0
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        data = _load(tag.string if tag.string is not None else tag.get_text())
        if data is None:
            invalid += 1
        else:
            payloads.append(data)
    return payloads, invalid


# ---------------------------------------------------------------------------------------------- helpers
def _types(node: dict) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).rsplit("/", 1)[-1].lower() for v in values if v}


def _text(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or value.get("value")
    if isinstance(value, list):
        value = next((v for v in value if v), None)
        return _text(value)
    if value is None:
        return None
    text = htmllib.unescape(str(value)).strip()
    return text or None


def _image(value: Any) -> Optional[str]:
    if isinstance(value, list):
        return _image(value[0]) if value else None
    if isinstance(value, dict):
        return _text(value.get("url") or value.get("contentUrl"))
    return _text(value)


def _ean(*values: Any) -> Optional[str]:
    for value in values:
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) in (8, 12, 13, 14):
            return digits
    return None


def _date(value: Any) -> Optional[str]:
    text = _text(value)
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text or "")
    return match.group(1) if match else None


def _price_of(offer: dict) -> tuple[Optional[float], Optional[str]]:
    currency = _text(offer.get("priceCurrency"))
    for key in ("price", "lowPrice"):
        if offer.get(key) not in (None, ""):
            return parse_number(offer[key]), currency
    spec = offer.get("priceSpecification")
    specs = spec if isinstance(spec, list) else [spec] if isinstance(spec, dict) else []
    for item in specs:
        kind = str(item.get("priceType", "")).lower()
        if "list" in kind or "strikethrough" in kind or "srp" in kind:
            continue
        if item.get("price") not in (None, ""):
            return parse_number(item["price"]), currency or _text(item.get("priceCurrency"))
    return None, currency


# ---------------------------------------------------------------------------------------------- products
def _offers_of(node: dict) -> list[dict]:
    """The offer dicts of a Product; an AggregateOffer is replaced by its nested offers when it has any."""
    raw = node.get("offers")
    if raw is None:
        raw = node.get("offer")
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    flat: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nested = _offers_of(item) if "aggregateoffer" in _types(item) else []
        flat.extend(nested if nested else [item])
    return flat


def _build_offer(product: dict, offer: Optional[dict], base_url: str, *, method: str = "jsonld") -> Offer:
    out = Offer(method=method)
    out.title = _text(product.get("name"))
    out.brand = _text(product.get("brand"))
    out.image = _image(product.get("image"))
    out.sku = _text(product.get("sku")) or _text(product.get("productID"))
    out.ean = _ean(product.get("gtin13"), product.get("gtin"), product.get("gtin14"), product.get("gtin12"),
                   product.get("gtin8"), product.get("ean"))
    out.extra["mpn"] = _text(product.get("mpn"))
    url = _text(product.get("url"))
    if offer is not None:
        agg = "aggregateoffer" in _types(offer)
        out.price, out.currency = _price_of(offer)
        out.availability = map_availability(offer.get("availability"))
        out.seller = _text(offer.get("seller")) or _text(offer.get("offeredBy"))
        out.sku = _text(offer.get("sku")) or out.sku
        out.ean = _ean(offer.get("gtin13"), offer.get("gtin")) or out.ean
        url = _text(offer.get("url")) or url
        starts = _date(offer.get("availabilityStarts"))
        if starts:
            if out.availability == PREORDER:
                out.preorder_date = starts
            elif out.availability == OUT_OF_STOCK:
                out.restock_date = starts
                out.availability = RESTOCK_SCHEDULED
        raw_avail = offer.get("availability")
        raw_avail = raw_avail.get("@id") if isinstance(raw_avail, dict) else raw_avail
        if raw_avail:
            out.evidence.append(f"availability={str(raw_avail).rsplit('/', 1)[-1]}"[:160])
        if out.price is not None:
            out.evidence.append(f"price={offer.get('price', offer.get('lowPrice'))} {out.currency or ''}".strip()[:160])
        if str(offer.get("availability", "")).lower().endswith("discontinued"):
            out.extra["discontinued"] = True
        if agg:
            out.extra["aggregate"] = True
            out.extra["offer_count"] = offer.get("offerCount")
        if "instockpickup" in str(offer.get("availabilityAtOrFrom", "")).lower():
            out.extra["pickup"] = True
        cond = _text(offer.get("itemCondition"))
        if cond:
            out.extra["condition"] = cond.rsplit("/", 1)[-1]
    out.url = urljoin(base_url, url) if url else None
    return out


def _visit(node: Any, base_url: str, result: JsonLdResult, in_list: bool, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(node, list):
        for item in node:
            _visit(item, base_url, result, in_list, depth + 1)
        return
    if not isinstance(node, dict):
        return
    types = _types(node)
    if "itemlist" in types:
        for element in node.get("itemListElement") or []:
            if isinstance(element, dict):
                inner = element.get("item", element)
                if isinstance(inner, dict):
                    _visit(inner, base_url, result, True, depth + 1)
        return
    if "breadcrumblist" in types:
        for element in node.get("itemListElement") or []:
            if isinstance(element, dict):
                name = _text(element.get("name")) or _text((element.get("item") or {}).get("name") if isinstance(element.get("item"), dict) else None)
                if name:
                    result.breadcrumbs.append(name)
        return
    if types & PRODUCT_TYPES:
        target = result.listed if in_list else result.products
        if not in_list:
            result.has_product = True
        variants = node.get("hasVariant")
        if isinstance(variants, list) and variants:
            group_name = _text(node.get("name"))
            for variant in variants:
                if isinstance(variant, dict):
                    merged = {**{k: v for k, v in node.items() if k in ("brand", "image", "url")}, **variant}
                    for offer_dict in _offers_of(variant) or [None]:
                        built = _build_offer(merged, offer_dict, base_url)
                        built.extra["variant"] = True
                        built.extra["group"] = group_name
                        target.append(built)
            if _offers_of(node):
                for offer_dict in _offers_of(node):
                    target.append(_build_offer(node, offer_dict, base_url))
            return
        offers = _offers_of(node)
        if offers:
            for offer_dict in offers:
                target.append(_build_offer(node, offer_dict, base_url))
        elif in_list or node.get("name"):
            target.append(_build_offer(node, None, base_url))
        return
    if types & OFFER_TYPES:   # a lone Offer (no Product wrapper): still worth an Offer
        result.products.append(_build_offer({}, node, base_url))
        return
    for key, value in node.items():
        if key.lower() in _SKIP_KEYS or key.startswith("@") and key != "@graph":
            continue
        if isinstance(value, (dict, list)):
            _visit(value, base_url, result, in_list, depth + 1)


def _dedupe(offers: list[Offer]) -> list[Offer]:
    seen: set[tuple] = set()
    out: list[Offer] = []
    for offer in offers:
        key = (offer.sku, offer.url, offer.price, offer.availability, offer.seller, offer.title)
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


def extract_jsonld(soup: BeautifulSoup, base_url: str = "") -> JsonLdResult:
    payloads, invalid = load_blocks(soup)
    result = JsonLdResult(blocks=len(payloads) + invalid, invalid_blocks=invalid)
    for payload in payloads:
        _visit(payload, base_url, result, False)
    result.products = _dedupe(result.products)
    result.listed = _dedupe(result.listed)
    return result


def iter_product_nodes(payloads: list[Any]) -> Iterator[dict]:  # pragma: no cover — debugging helper
    for payload in payloads:
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if _types(item) & PRODUCT_TYPES:
                    yield item
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
