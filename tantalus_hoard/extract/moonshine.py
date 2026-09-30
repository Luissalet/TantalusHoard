"""El Corte Inglés search / category pages: the product list lives in an embedded state object.

The server-side rendered page carries ``window.__MOONSHINE_STATE__ = {...}`` in a ``<script>``. Its first block
has a ``_datalayer[0].products`` list with, per product, ``name``, ``code_a`` (the retailer's own id),
``gtin``, ``brand``, ``price.f_price``, ``status`` (``"ADD"`` = the add-to-cart control is offered) and
``badges.coming_soon`` and ``eci_provider`` (the seller: the retailer itself or a marketplace vendor). The cards themselves (``li.products_list-item > article#product-<code>``) hold the
detail URL in a ``data-url`` attribute and the visible "Añadir" button, but no price, so the generic card
detector finds nothing. Sponsored slots have no ``code_a`` and are ignored.

Availability is only claimed when the page says it: ``status == "ADD"`` together with the visible add button
means in stock; a ``coming_soon`` badge means pre-order; anything else stays ``unknown``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..model import IN_STOCK, PREORDER, UNKNOWN, Offer

_MARKER = "__MOONSHINE_STATE__"
_ADD_WORDS = ("añadir", "anadir", "añadir a la cesta", "comprar")


def _state(soup: BeautifulSoup) -> Optional[dict]:
    """Decode the embedded state object, or ``None`` when the page has none."""
    for script in soup.find_all("script"):
        code = script.string or script.get_text() or ""
        at = code.find(_MARKER)
        if at < 0:
            continue
        start = code.find("{", at)
        if start < 0:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(code[start:])
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _datalayer_products(state: dict) -> list[dict]:
    blocks = state.get("blocks")
    if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
        block = blocks[0]
    else:
        block = state
    layers = block.get("_datalayer") or state.get("_datalayer") or []
    for layer in layers if isinstance(layers, list) else []:
        products = layer.get("products") if isinstance(layer, dict) else None
        if isinstance(products, list) and products:
            return [p for p in products if isinstance(p, dict)]
    return []


def _cards(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    """``code -> {url, button}`` from the rendered cards."""
    cards: dict[str, dict[str, Any]] = {}
    for article in soup.select("article[id^='product-']"):
        code = str(article.get("id") or "")[len("product-"):]
        if not code:
            continue
        holder = article.select_one("[data-url]")
        url = str(holder.get("data-url") or "") if holder is not None else ""
        button: Optional[bool] = None
        for control in article.find_all("button"):
            label = control.get_text(" ", strip=True).lower()
            if label and any(label == w or label.startswith(w) for w in _ADD_WORDS):
                button = not (control.has_attr("disabled") or control.get("aria-disabled") == "true")
                break
        cards[code] = {"url": url, "button": button}
    return cards


def extract_moonshine(soup: BeautifulSoup, base_url: str, limit: int = 80) -> list[Offer]:
    state = _state(soup)
    if state is None:
        return []
    products = _datalayer_products(state)
    if not products:
        return []
    cards = _cards(soup)
    offers: list[Offer] = []
    for item in products[:limit]:
        code = str(item.get("code_a") or "").strip()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue
        price_block = item.get("price") if isinstance(item.get("price"), dict) else {}
        price = price_block.get("f_price")
        card = cards.get(code, {})
        url = urljoin(base_url, card["url"]) if card.get("url") else None
        status = str(item.get("status") or "").strip().upper()
        badges = item.get("badges") if isinstance(item.get("badges"), dict) else {}
        availability, evidence = UNKNOWN, []
        if badges.get("coming_soon"):
            availability, evidence = PREORDER, ["coming_soon badge"]
        elif status == "ADD" and card.get("button") is not False:
            availability = IN_STOCK
            evidence = ['status "ADD"'] + (['button "Añadir"'] if card.get("button") else [])
        offers.append(Offer(
            availability=availability,
            price=float(price) if isinstance(price, (int, float)) else None,
            currency=str(price_block.get("currency") or "EUR") if price is not None else None,
            title=name[:300], url=url, sku=code, brand=str(item.get("brand") or "") or None,
            ean=re.sub(r"\D", "", str(item.get("gtin") or "")) or None,
            seller=str(item.get("eci_provider") or "").strip() or None, buy_button=card.get("button"),
            evidence=evidence, method="site:elcorteingles.es",
            extra={"position": item.get("position"), "eci_status": status or None},
        ))
    return offers
