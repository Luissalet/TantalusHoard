"""Wallapop through its public JSON search API (the same endpoint the site's own frontend uses).

Verified 2026-09-30 from Spain: ``GET https://api.wallapop.com/api/v3/search`` answers 200 with the headers below
and 403 (CloudFront) without them. Findings worth knowing:

* the radius filter is ``distance_in_km``; the ``distance`` (metres) parameter is silently ignored;
* ``min_sale_price`` / ``max_sale_price`` work (``max_sale_price=0`` returns only free items);
* a page holds up to 40 items; ``meta.next_page`` is an opaque token sent back as ``next_page`` and it is present
  even on the last page (a page with no items ends the pagination);
* without ``distance_in_km`` results come from all over Spain (nearest first only with ``order_by=closest``).

The parser is defensive: every key may be missing or shaped differently. Never solves anything, never bypasses a
block: a 403 / 429 / captcha is reported as an error string.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..errors import TantalusError
from ..model import RawListing
from .distance import coords_distance_km
from .textutil import epoch_to_iso

log = logging.getLogger("tantalus.secondhand.wallapop")

SEARCH_URL = "https://api.wallapop.com/api/v3/search"
ITEM_URL = "https://es.wallapop.com/item/"
HEADERS = {
    "X-DeviceOS": "0",
    "Referer": "https://es.wallapop.com/",
    "Origin": "https://es.wallapop.com",
    "Accept": "application/json, text/plain, */*",
}
ORDERS = ("newest", "closest", "most_relevance", "price_low_to_high", "price_high_to_low")
PAGE_SIZE = 40
MAX_PAGES_CAP = 5


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _float(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" or isinstance(value, bool) else float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def extract_items(data: Any) -> list[dict[str, Any]]:
    """The items array of a search response (``data.section.payload.items``), tolerating other shapes."""
    for path in (("data", "section", "payload", "items"), ("data", "search_objects"), ("search_objects",),
                 ("data", "items"), ("items",)):
        found = _get(data, *path)
        if isinstance(found, list):
            return [i for i in found if isinstance(i, dict)]
    return []


def next_page_token(data: Any) -> str:
    token = _get(data, "meta", "next_page")
    return token if isinstance(token, str) else ""


def _image_url(item: dict[str, Any]) -> Optional[str]:
    images = item.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            urls = first.get("urls") if isinstance(first.get("urls"), dict) else first
            for key in ("big", "medium", "small", "original", "xlarge", "large"):
                if _text(urls.get(key)):
                    return urls[key]
        elif isinstance(first, str):
            return first
    for path in (("image", "urls", "big"), ("image", "urls", "medium"), ("image", "urls", "small"), ("image",)):
        found = _get(item, *path)
        if isinstance(found, str) and found:
            return found
    return None


def parse_item(item: dict[str, Any], *, query: str = "", origin: Optional[tuple[float, float]] = None
               ) -> Optional[RawListing]:
    """One API item to a RawListing, or None when it has no usable id / title."""
    ext_id = _text(item.get("id")) or (str(item["id"]) if isinstance(item.get("id"), int) else "")
    title = _text(item.get("title")) or _text(item.get("name"))
    if not ext_id or not title:
        return None

    slug = _text(item.get("web_slug")) or _text(item.get("slug"))
    url = ITEM_URL + slug if slug else ITEM_URL + ext_id

    price_obj = item.get("price")
    amount: Optional[float] = None
    currency: Optional[str] = "EUR"
    if isinstance(price_obj, dict):
        amount = _float(price_obj.get("amount"))
        if amount is None:
            amount = _float(_get(price_obj, "cash", "amount"))
        currency = _text(price_obj.get("currency")) or "EUR"
    else:
        amount = _float(price_obj)
        if amount is None:
            amount = _float(item.get("sale_price"))
        currency = _text(item.get("currency")) or "EUR"

    loc = item.get("location") if isinstance(item.get("location"), dict) else {}
    city = _text(loc.get("city")) or _text(loc.get("name"))
    area = _text(loc.get("region2")) or _text(loc.get("region"))
    location_text = ", ".join(p for p in (city, area if area.lower() != city.lower() else "") if p) or _text(loc.get("postal_code")) or None
    lat, lon = _float(loc.get("latitude")), _float(loc.get("longitude"))

    distance = coords_distance_km(origin[0], origin[1], lat, lon) if origin and lat is not None and lon is not None else None

    reserved_obj = item.get("reserved")
    reserved: Optional[bool]
    if isinstance(reserved_obj, dict):
        reserved = bool(reserved_obj.get("flag")) if "flag" in reserved_obj else None
    elif isinstance(reserved_obj, bool):
        reserved = reserved_obj
    else:
        reserved = None

    ship = item.get("shipping")
    shipping: Optional[bool] = None
    if isinstance(ship, dict):
        if "item_is_shippable" in ship:
            shipping = bool(ship["item_is_shippable"])
        elif "user_allows_shipping" in ship:
            shipping = bool(ship["user_allows_shipping"])
    elif isinstance(ship, bool):
        shipping = ship

    created = epoch_to_iso(item.get("created_at") or item.get("creation_date") or item.get("modified_at"))
    taxonomy = item.get("taxonomy")
    extra: dict[str, Any] = {
        "user_id": _text(item.get("user_id")) or None,
        "category_id": item.get("category_id"),
        "modified_at": epoch_to_iso(item.get("modified_at")),
        "postal_code": _text(loc.get("postal_code")) or None,
        "region": _text(loc.get("region")) or None,
        "image_count": len(item["images"]) if isinstance(item.get("images"), list) else 0,
        "is_top_profile": item.get("is_top_profile"),
        "has_warranty": item.get("has_warranty"),
        "is_refurbished": item.get("is_refurbished"),
    }
    if isinstance(taxonomy, list):
        extra["taxonomy"] = [t.get("name") for t in taxonomy if isinstance(t, dict) and t.get("name")]

    return RawListing(
        source="wallapop", url=url, title=title, external_id=ext_id, description=_text(item.get("description")) or None,
        price=amount, price_raw=None if amount is None else f"{amount:g} {currency}", currency=currency,
        location_text=location_text, latitude=lat, longitude=lon, distance_km=distance, listing_date=created,
        image_url=_image_url(item), seller_name=None, shipping=shipping, reserved=reserved,
        matched_query=query or None, surface="marketplace", extra={k: v for k, v in extra.items() if v is not None},
    )


def parse_search(data: Any, *, query: str = "", origin: Optional[tuple[float, float]] = None) -> list[RawListing]:
    """All parsable listings of one search response."""
    out: list[RawListing] = []
    for item in extract_items(data):
        try:
            listing = parse_item(item, query=query, origin=origin)
        except Exception as error:  # noqa: BLE001 — one odd item must not lose the page
            log.info("wallapop: skipped an item: %s", error)
            continue
        if listing is not None:
            out.append(listing)
    return out


class WallapopSource:
    """``WallapopSource(fetcher).search(query, latitude=..., longitude=...)`` -> ``(listings, error)``."""

    name = "wallapop"

    def __init__(self, fetcher: Any, *, max_pages: Optional[int] = None, min_interval_s: float = 5.0) -> None:
        self.fetcher = fetcher
        self.max_pages = max_pages  # None: as many as ``limit`` needs (capped)
        self.min_interval_s = min_interval_s

    @staticmethod
    def build_params(query: str, *, latitude: Optional[float], longitude: Optional[float], radius_km: float,
                     max_price: Optional[float], min_price: Optional[float], order_by: str) -> dict[str, Any]:
        params: dict[str, Any] = {"keywords": query, "source": "search_box",
                                  "order_by": order_by if order_by in ORDERS else "newest"}
        if latitude is not None and longitude is not None:
            params["latitude"] = latitude
            params["longitude"] = longitude
            if radius_km and radius_km > 0:
                params["distance_in_km"] = int(round(radius_km))
        if min_price is not None:
            params["min_sale_price"] = min_price
        if max_price is not None:
            params["max_sale_price"] = max_price
        return params

    def search(self, query: str, *, latitude: Optional[float] = None, longitude: Optional[float] = None,
               location_text: Optional[str] = None, radius_km: float = 30, max_price: Optional[float] = None,
               min_price: Optional[float] = None, limit: int = 40, order_by: str = "newest"
               ) -> tuple[list[RawListing], str]:
        query = (query or "").strip()
        if not query:
            return [], "wallapop: empty query"
        limit = max(1, int(limit or PAGE_SIZE))
        pages = self.max_pages or -(-limit // PAGE_SIZE)
        pages = max(1, min(pages, MAX_PAGES_CAP))
        origin = (float(latitude), float(longitude)) if latitude is not None and longitude is not None else None
        params = self.build_params(query, latitude=latitude, longitude=longitude, radius_km=radius_km,
                                   max_price=max_price, min_price=min_price, order_by=order_by)

        found: list[RawListing] = []
        seen: set[str] = set()
        error = ""
        for page in range(pages):
            try:
                result, data = self.fetcher.get_json(SEARCH_URL, headers=HEADERS, params=dict(params), accept="json",
                                                     respect_robots=False, min_interval_s=self.min_interval_s)
            except TantalusError as exc:
                error = f"wallapop: {exc.code}: {exc.message}"
                break
            except Exception as exc:  # noqa: BLE001 — sources never raise for expected failures
                error = f"wallapop: {type(exc).__name__}: {exc}"
                break
            if data is None or not getattr(result, "ok", False):
                why = getattr(result, "block_reason", "") or getattr(result, "error", "") or f"HTTP {getattr(result, 'status', 0)}"
                error = f"wallapop: {'blocked' if getattr(result, 'blocked', False) else 'fetch_failed'}: {why}"
                break
            listings = parse_search(data, query=query, origin=origin)
            if not extract_items(data):
                break
            for listing in listings:
                key = listing.external_id or listing.url
                if key not in seen:
                    seen.add(key)
                    found.append(listing)
            token = next_page_token(data)
            if len(found) >= limit or not token or page + 1 >= pages:
                break
            params["next_page"] = token
        return found[:limit], error
