"""NVIDIA partners API adapter (a documented-by-use JSON API the marketplace frontend itself calls).

``https://api.nvidia.partners/edge/product/search?page=1&limit=12&locale=es-es&search=<term>`` answers plain
HTTP with JSON, while marketplace.nvidia.com (HTML) and api.store.nvidia.com answer Akamai 403 to plain clients
and reset headless browsers (verified 2026-09-30, Spain).

Target URL forms supported:

* ``nvidia-api:search?term=DGX%20Spark&locale=es-es``
* a ``marketplace.nvidia.com`` / ``store.nvidia.com`` page URL — the search term is derived from its slug and the
  offers are narrowed to that product.

Response shape: ``searchedProducts.productDetails[]`` (exact matches) and ``.suggestedProductDetails[]``
(look-alikes shown when nothing matches; flagged ``extra["suggested"]``). Per product: ``prdStatus``
(``out_of_stock`` / ``buy_now`` / ...), ``productAvailable`` (a founder-edition flag, false for partner listings),
``retailers[]`` with ``salePrice``, ``directPurchaseLink``, ``retailerName`` and ``stock``. ``retailers[].isAvailable``
only says the listing exists, so availability comes from ``prdStatus`` / ``stock``.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..model import IN_STOCK, OUT_OF_STOCK, PREORDER, UNKNOWN, Offer
from .phrases import parse_number, parse_price
from .sites import is_first_party_seller, retailer_for_host

API_BASE = "https://api.nvidia.partners/edge/product/search"
SCHEME = "nvidia-api:"
NAME = "nvidia"
DEFAULT_LOCALE = "es-es"
_SLUG_NOISE = {"nvidia", "geforce", "tarjeta", "gr", "fica", "de", "la", "el", "en", "es", "consumer", "graphics", "cards"}


def build_search_url(term: str, *, locale: str = DEFAULT_LOCALE, limit: int = 12, page: int = 1) -> str:
    return f"{API_BASE}?page={page}&limit={limit}&locale={quote(locale)}&search={quote(term)}"


def _locale_from_path(path: str) -> str:
    match = re.match(r"^/([a-z]{2}-[a-z]{2})/", path, re.I)
    return match.group(1).lower() if match else DEFAULT_LOCALE


def term_from_slug(slug: str) -> str:
    """'nvidia-geforce-rtx-5090' -> 'rtx 5090'; 'dgx-spark' -> 'dgx spark'."""
    words = [w for w in re.split(r"[-_]+", unquote(slug).strip("/").lower()) if w]
    kept = [w for w in words if w not in _SLUG_NOISE] or words
    return " ".join(kept[:8])


def adapter_url(url: str, hints: Optional[dict] = None) -> Optional[str]:
    """The API URL to fetch for a target URL, or ``None`` when this adapter does not apply."""
    hints = hints or {}
    url = (url or "").strip()
    if url.lower().startswith(SCHEME):
        query = parse_qs(url[len(SCHEME):].split("?", 1)[1] if "?" in url else "")
        term = (query.get("term") or query.get("search") or [""])[0]
        locale = (query.get("locale") or [DEFAULT_LOCALE])[0]
        limit = int((query.get("limit") or ["12"])[0] or 12)
        return build_search_url(term, locale=locale, limit=min(max(limit, 1), 48)) if term else None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.endswith("api.nvidia.partners"):
        return url
    if host.endswith(("marketplace.nvidia.com", "store.nvidia.com")):
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 2:
            term = hints.get("title_hint") or term_from_slug(segments[-1])
            return build_search_url(term, locale=_locale_from_path(parts.path))
    if str(hints.get("adapter", "")).lower() == NAME and hints.get("title_hint"):
        return build_search_url(str(hints["title_hint"]))
    return None


def is_payload(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("searchedProducts"), dict)


def _state(status: str, available: Any, stock: Any) -> tuple[str, str]:
    status_l = (status or "").lower()
    try:
        in_hand = float(stock or 0) > 0
    except (TypeError, ValueError):
        in_hand = False
    if "out_of_stock" in status_l or "sold_out" in status_l:
        return OUT_OF_STOCK, f"prdStatus={status}"
    if re.search(r"pre.?order|pre.?sale", status_l):
        return PREORDER, f"prdStatus={status}"
    if re.search(r"buy|add_to_cart|in_stock|available_now", status_l) or in_hand:
        return IN_STOCK, f"prdStatus={status}" if status_l else f"stock={stock}"
    if available is True:
        return IN_STOCK, "productAvailable=true"
    if re.search(r"coming_soon|notify|upcoming|check", status_l):
        return OUT_OF_STOCK, f"prdStatus={status}"
    return UNKNOWN, f"prdStatus={status}" if status_l else ""


def _host_of(link: str) -> str:
    match = re.match(r"^(?:https?://)?(?:www\.)?([^/\s]+)", (link or "").strip(), re.I)
    return match.group(1).lower() if match else ""


def _price(text: Any) -> Optional[float]:
    if text in (None, ""):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    parsed = parse_price(str(text))
    return parsed[0] if parsed else parse_number(text)


def _terms(term: str) -> list[str]:
    return [t for t in re.split(r"\W+", term.lower()) if t]


def parse(payload: dict, *, term: str = "", product_url: str = "") -> list[Offer]:
    """Map an API payload to Offers (one per retailer listing; one per product when it lists no retailer).

    ``term_match`` in ``extra`` tells whether every word of the search term occurs in the product's title, so the
    caller can ignore look-alikes. ``product_url``, when given, narrows the result to that page's product.
    """
    offers: list[Offer] = []
    sp = payload.get("searchedProducts") or {}
    wanted = _terms(term)
    slug = (urlsplit(product_url).path.rstrip("/").rsplit("/", 1)[-1] if product_url else "").lower()
    groups = (("productDetails", False), ("suggestedProductDetails", True))
    for key, suggested in groups:
        for product in sp.get(key) or []:
            if not isinstance(product, dict):
                continue
            title = product.get("productTitle") or product.get("displayName") or ""
            internal = product.get("internalLink") or ""
            if slug and slug not in internal.lower():
                continue
            haystack = f"{title} {product.get('displayName', '')} {product.get('gpu', '')}".lower()
            term_match = all(w in haystack for w in wanted) if wanted else True
            base = dict(title=title or None, sku=product.get("productSKU") or None, brand=product.get("manufacturer") or None,
                        image=product.get("imageURL") or None)
            status = str(product.get("prdStatus") or "")
            retailers = [r for r in (product.get("retailers") or []) if isinstance(r, dict)]
            common = {"prd_status": status, "product_url": internal, "is_founder_edition": bool(product.get("isFounderEdition")),
                      "suggested": suggested, "term_match": term_match, "msrp": _price(product.get("mrp")),
                      "product_available_flag": product.get("productAvailable")}
            if not retailers:
                state, why = _state(status, product.get("productAvailable"), 0)
                offer = Offer(availability=state, price=_price(product.get("productPrice")), currency="EUR",
                              url=internal or None, method="api:nvidia", extra=dict(common), **base)
                offer.evidence = [e for e in (why, f"productPrice={product.get('productPrice')}") if e]
                offers.append(offer)
                continue
            for retailer in retailers:
                name = str(retailer.get("retailerName") or "")
                host = _host_of(name) or _host_of(retailer.get("directPurchaseLink", ""))
                direct = "nvidia.com" in host
                # The status is per product for NVIDIA's own listing; for a partner it means "some partner has it",
                # so a listing flagged unavailable is not in stock.
                if direct:
                    state, why = _state(status, product.get("productAvailable"), retailer.get("stock"))
                elif retailer.get("isAvailable") is False:
                    state, why = OUT_OF_STOCK, "retailers[].isAvailable=false"
                else:
                    state, why = _state(status, product.get("productAvailable"), retailer.get("stock"))
                price = _price(retailer.get("salePrice")) or _price(product.get("productPrice"))
                offer = Offer(availability=state, price=price, currency="EUR", seller=host or None,
                              url=retailer.get("directPurchaseLink") or retailer.get("purchaseLink") or internal or None,
                              method="api:nvidia", extra={**common, "partner_id": retailer.get("partnerId"),
                                                            "retailer_stock": retailer.get("stock"), "retailer": host},
                              **base)
                known = is_first_party_seller(name, "marketplace.nvidia.com") if direct else None
                offer.seller_is_retailer = True if (direct or retailer_for_host(host)) else known
                offer.evidence = [e for e in (why, f"retailer={host}", f"salePrice={retailer.get('salePrice')}",
                                              f"stock={retailer.get('stock')}") if e]
                offers.append(offer)
    return offers


def extract_payload(payload: dict, *, url: str = "", hints: Optional[dict] = None) -> tuple[list[Offer], str, list[str]]:
    """(offers, page_kind, notes) for an API payload fetched for ``url``."""
    hints = hints or {}
    notes: list[str] = []
    term = ""
    target = hints.get("url") or url
    if target.lower().startswith(SCHEME) or "api.nvidia.partners" in target:
        query = parse_qs(urlsplit(target if not target.lower().startswith(SCHEME) else "x://x?" + target.split("?", 1)[-1]).query)
        term = (query.get("search") or query.get("term") or [""])[0]
    product_url = ""
    if "nvidia.com" in target and not target.lower().startswith(SCHEME) and "api.nvidia.partners" not in target:
        product_url = target
        segments = [s for s in urlsplit(target).path.split("/") if s]
        term = hints.get("title_hint") or (term_from_slug(segments[-1]) if segments else "")
    offers = parse(payload, term=term or str(hints.get("title_hint") or ""), product_url=product_url)
    sp = payload.get("searchedProducts") or {}
    if not (sp.get("productDetails") or []) and (sp.get("suggestedProductDetails") or []):
        notes.append(f"no exact match for {term!r}: the API returned look-alike suggestions only")
    kind = "product" if product_url and offers else "search"
    return offers, kind, notes
