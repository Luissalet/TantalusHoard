"""Search / listing pages: many offers from repeated product cards.

Two ways to find the cards:

* a per-site CSS ``tile_selector`` from ``sites.py`` (exact, used when it matches);
* a generic detector: text nodes that are *only* a price ("59,99 €") are located, and for each one the
  smallest ancestor that contains a link and has at least three same-tag siblings that also contain a price
  and a link is taken as the card. Menus ("Tarjetas a 10€") do not qualify: their text is not a bare price.

Each card becomes an Offer (price, title, url, image, sku, and an availability read from short lines inside
the card). JSON-LD ``ItemList`` results are handled in ``jsonld.py`` and win when present.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from ..model import IN_STOCK, OUT_OF_STOCK, PREORDER, UNKNOWN, Offer
from .phrases import (NOTIFY, PREORDER_PH, SOLDOUT, _has_phrase, _hidden_chain, classify_control_text, norm,
                      parse_number, snippet, standalone_price)

_OLD_CLASS = re.compile(r"(^|[-_ ])(crossed|strike|strikethrough|line-through|old|was|before|antes|previous|original|regular|"
                        r"text-price|list-price|msrp|rrp|pvp|precio-anterior)([-_ ]|$)", re.I)
_BARE_NUMBER = re.compile(r"^\s*\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\s*$")
_ASIN = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
_TITLE_CLASS = re.compile(r"title|name|nombre|titulo", re.I)
_SKU_ATTRS = ("data-asin", "data-sku", "data-product-id", "data-productid", "data-list-item-click", "data-id", "data-item-id")
_PRICE_ATTRS = ("data-list-item-price", "data-price", "data-product-price", "data-item-price", "data-price-amount")
_TRAILING_ID = re.compile(r"/(\d{4,10})/?$")
_POSITIVE_LINE = ("solo queda", "quedan", "en stock", "in stock", "ultimas unidades", "disponible ya")
_NEGATED_LINE = ("sin stock", "no disponible", "out of stock", "agotado")


def _real_href(tag: Tag) -> bool:
    href = str(tag.get("href") or "").strip().lower()
    return bool(href) and not href.startswith(("#", "javascript:", "mailto:", "tel:"))


def _has_link(el: Tag) -> bool:
    if el.name == "a" and _real_href(el):
        return True
    return any(_real_href(a) for a in el.find_all("a", href=True))


def _next_string(node: NavigableString) -> str:
    nxt = node.next_element
    hops = 0
    while nxt is not None and hops < 6:
        if isinstance(nxt, NavigableString) and str(nxt).strip():
            return str(nxt).strip()
        nxt = nxt.next_element
        hops += 1
    return ""


def _is_old(node: NavigableString) -> bool:
    el: Optional[Tag] = node.parent
    for _ in range(4):
        if el is None or el.name in ("body", "html"):
            break
        if el.name in ("del", "s", "strike"):
            return True
        classes = el.get("class") or []
        classes = classes.split() if isinstance(classes, str) else classes
        if any(_OLD_CLASS.search(c) for c in classes):
            return True
        el = el.parent
    prev = node.previous_element
    hops = 0
    while prev is not None and hops < 4:
        if isinstance(prev, NavigableString) and str(prev).strip():
            return norm(str(prev)).rstrip(": ") in ("antes", "was", "pvp", "pvpr", "precio anterior", "rrp")
        prev = prev.previous_element
        hops += 1
    return False


def price_nodes(root: Tag) -> Iterator[tuple[NavigableString, float, str, bool]]:
    """(text node, amount, currency, is_old_price) for every text node that is only a price."""
    for node in root.find_all(string=True):
        if node.parent is None or node.parent.name in ("script", "style", "noscript", "template"):
            continue
        text = str(node).strip()
        if not text or len(text) > 32:
            continue
        found = standalone_price(text)
        if found is None and _BARE_NUMBER.match(text) and norm(_next_string(node)) in ("€", "eur", "euro", "euros"):
            value = parse_number(text)
            found = (value, "EUR") if value else None
        if found is None:
            continue
        if node.find_parent(["nav", "footer", "header"]) is not None or _hidden_chain(node.parent, 5):
            continue
        yield node, found[0], found[1], _is_old(node)


def _find_generic_cards(soup: BeautifulSoup, nodes: list) -> list[Tag]:
    priced: set[int] = set()
    for node, *_rest in nodes:
        parent = node.parent
        while parent is not None and parent.name not in ("[document]",):
            if id(parent) in priced:
                break
            priced.add(id(parent))
            parent = parent.parent
    cards: dict[int, Tag] = {}
    for node, *_rest in nodes:
        anc = node.parent
        steps = 0
        while anc is not None and steps < 10 and anc.name not in ("body", "html", "[document]"):
            if _has_link(anc):
                break
            anc, steps = anc.parent, steps + 1
        else:
            continue
        cand = anc
        for _ in range(8):
            if cand is None or cand.parent is None or cand.name in ("body", "html", "[document]"):
                break
            siblings = [s for s in cand.parent.find_all(cand.name, recursive=False) if id(s) in priced and _has_link(s)]
            if len(siblings) >= 3:
                cards[id(cand)] = cand
                break
            cand = cand.parent
    ordered = sorted(cards.values(), key=lambda t: t.sourceline or 0) if all(c.sourceline for c in cards.values()) else list(cards.values())
    # keep document order using the soup's own traversal
    order = {id(el): i for i, el in enumerate(soup.find_all(True)) if id(el) in cards}
    return sorted(ordered, key=lambda t: order.get(id(t), 0))


def _canonical(url: str) -> tuple[str, Optional[str]]:
    match = _ASIN.search(url)
    if match:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}/dp/{match.group(1)}", match.group(1)
    return url, None


def _title_of(card: Tag, anchors: list[Tag]) -> Optional[str]:
    for heading in card.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = heading.get_text(" ", strip=True)
        if len(text) >= 3:
            return snippet(text, 200)
    for el in card.find_all(attrs={"class": _TITLE_CLASS}):
        text = el.get_text(" ", strip=True)
        if 3 <= len(text) <= 200:
            return snippet(text, 200)
    for anchor in anchors:
        text = anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" ", strip=True)
        if text and len(text.strip()) >= 3 and norm(text) not in ("ver ficha", "ver mas", "comprar"):
            return snippet(text, 200)
    img = card.find("img", alt=True)
    return snippet(img["alt"], 200) if img is not None and len(img["alt"]) >= 3 else None


def _availability(card: Tag) -> tuple[str, list[str], Optional[bool]]:
    evidence: list[str] = []
    lines = [ln.strip() for ln in card.get_text("\n", strip=True).splitlines() if ln.strip()]
    for line in lines:
        if len(line) > 60:
            continue
        if _has_phrase(line, SOLDOUT) or _has_phrase(line, NOTIFY):
            return OUT_OF_STOCK, [snippet(line)], False
    buy = preorder = False
    for control in card.select("button, input[type=submit], [role=button], a[class]"):
        if _hidden_chain(control, 4):
            continue
        text = control.get_text(" ", strip=True) or str(control.get("value") or control.get("aria-label") or "")
        if control.has_attr("disabled") or str(control.get("aria-disabled", "")).lower() == "true":
            continue
        kind = classify_control_text(text)
        if kind == "buy":
            buy = True
            evidence.append(snippet(f"control: {text}"))
        elif kind == "preorder":
            preorder = True
            evidence.append(snippet(f"control: {text}"))
    if preorder:
        return PREORDER, evidence, True
    # inside a card a bare "Reserva" label (xtralife: "Reserva 699,95 €") is a pre-order marker
    hit = next((line for line in lines if len(line) <= 24 and (_has_phrase(line, PREORDER_PH) or norm(line) == "reserva")), None)
    if hit is not None:
        return PREORDER, [snippet(hit)], None
    if buy:
        return IN_STOCK, evidence, True
    for line in lines:
        n = norm(line)
        if len(line) <= 50 and any(p in n for p in _POSITIVE_LINE) and not any(x in n for x in _NEGATED_LINE):
            return IN_STOCK, [snippet(line)], None
    return UNKNOWN, evidence, None


def offer_from_card(card: Tag, base_url: str, position: int, profile: Any = None) -> Optional[Offer]:
    anchors = [a for a in ([card] if card.name == "a" else []) + card.find_all("a", href=True) if _real_href(a)]
    if not anchors:
        return None
    product_re = re.compile(getattr(profile, "product_url", "") or r"$^")
    anchors.sort(key=lambda a: (0 if product_re.search(urlsplit(str(a["href"])).path) else 1,
                                0 if a.get_text(strip=True) or a.get("title") else 1))
    url, asin = _canonical(urljoin(base_url, str(anchors[0]["href"]).strip()))
    offer = Offer(url=url, method="tile", title=_title_of(card, anchors))
    prices = list(price_nodes(card))
    current = [p for p in prices if not p[3]]
    old = [p for p in prices if p[3]]
    if current:
        offer.price, offer.currency = current[0][1], current[0][2]
        offer.evidence.append(snippet(f"price {current[0][0].strip()}"))
    if old:
        offer.extra["list_price"] = old[0][1]
    if offer.price is None:   # structured price attributes some themes put on the card / its links
        for el in [card, *anchors]:
            for attr in _PRICE_ATTRS:
                value = parse_number(el.get(attr)) if el.get(attr) else None
                if value and offer.price is None:
                    offer.price = value
                    offer.currency = "EUR" if profile is not None else None
                    offer.evidence.append(snippet(f"{attr}={el.get(attr)}"))
    image = next((img for img in card.find_all("img") if (img.get("src") or img.get("data-src") or "").strip()), None)
    if image is not None:
        src = (image.get("src") or "").strip()
        if not src or src.startswith("data:"):
            src = (image.get("data-src") or "").strip()
        offer.image = urljoin(base_url, src) if src and not src.startswith("data:") else None
    sku = asin
    for el in [card, *anchors]:
        for attr in _SKU_ATTRS:
            if not sku and el.get(attr):
                sku = str(el[attr]).strip()
    if not sku:
        trailing = _TRAILING_ID.search(urlsplit(url).path)
        sku = trailing.group(1) if trailing else None
    offer.sku = sku
    offer.availability, evidence, offer.buy_button = _availability(card)
    offer.evidence.extend(evidence)
    text = card.get_text(" ", strip=True)
    if re.search(r"\b(patrocinado|sponsored)\b", text, re.I):
        offer.extra["sponsored"] = True
    offer.extra["position"] = position
    if offer.price is None and offer.title is None:
        return None
    return offer


def extract_tiles(soup: BeautifulSoup, base_url: str = "", *, profile: Any = None, limit: int = 80) -> list[Offer]:
    cards: list[Tag] = []
    selector = getattr(profile, "tile_selector", "") or ""
    if selector:
        try:
            cards = [c for c in soup.select(selector) if not _hidden_chain(c, 3) and _has_link(c) and (c.get("data-asin") != "" )]
        except Exception:  # noqa: BLE001 — a bad selector must not break extraction
            cards = []
    if not cards:
        nodes = list(price_nodes(soup.body or soup))
        if len(nodes) >= 3:
            cards = _find_generic_cards(soup, nodes)
    offers: list[Offer] = []
    seen: set[str] = set()
    for index, card in enumerate(cards[: limit * 2]):
        offer = offer_from_card(card, base_url, index + 1, profile)
        if offer is None or (offer.url in seen):
            continue
        seen.add(offer.url or "")
        offers.append(offer)
        if len(offers) >= limit:
            break
    return offers
