"""Event type -> human label (ES / EN) and the text of a notification."""

from __future__ import annotations

from typing import Any

from ..model import (CANDIDATE_FOUND, EVENT_TYPES, INFO_CHANGE, LISTING_PRICE_DROP, LOCAL_RESTOCK, MAIL_DEAL, NEEDS_HUMAN, NEW_LISTING, NEW_SKU,
                     PREORDER_OPEN, PRICE_DROP, PRICE_THRESHOLD_CROSSED, RELEASE, RESTOCK, RESTOCK_DATE_CONFIRMED, SOLD_OUT)

LABELS: dict[str, dict[str, str]] = {
    "es": {
        RESTOCK: "Restock", LOCAL_RESTOCK: "Restock en tienda", PREORDER_OPEN: "Preventa abierta", PRICE_DROP: "Bajada de precio",
        PRICE_THRESHOLD_CROSSED: "Precio en tu umbral", NEW_SKU: "Producto nuevo", RESTOCK_DATE_CONFIRMED: "Fecha de restock confirmada",
        SOLD_OUT: "Agotado", NEW_LISTING: "Anuncio nuevo", LISTING_PRICE_DROP: "Anuncio rebajado", INFO_CHANGE: "Novedad",
        CANDIDATE_FOUND: "Candidato encontrado", NEEDS_HUMAN: "Necesita tu ayuda", MAIL_DEAL: "Oferta del correo",
        RELEASE: "Lanzamiento",
    },
    "en": {
        RESTOCK: "Restock", LOCAL_RESTOCK: "In-store restock", PREORDER_OPEN: "Pre-order open", PRICE_DROP: "Price drop",
        PRICE_THRESHOLD_CROSSED: "Price at your threshold", NEW_SKU: "New product", RESTOCK_DATE_CONFIRMED: "Restock date confirmed",
        SOLD_OUT: "Sold out", NEW_LISTING: "New listing", LISTING_PRICE_DROP: "Listing price cut", INFO_CHANGE: "News",
        CANDIDATE_FOUND: "Candidate found", NEEDS_HUMAN: "Needs your input", MAIL_DEAL: "Mail deal",
        RELEASE: "Release",
    },
}
assert all(t in LABELS["es"] and t in LABELS["en"] for t in EVENT_TYPES)

WORDS = {"es": {"confidence": "Confianza", "open": "Abrir", "watcher": "Vigía", "test_title": "Prueba de notificación",
                "test_body": "Si lo lees, este canal funciona."},
         "en": {"confidence": "Confidence", "open": "Open", "watcher": "Watcher", "test_title": "Notification test",
                "test_body": "If you can read this, the channel works."}}

TYPE_TAGS = {RESTOCK: "shopping_cart", LOCAL_RESTOCK: "round_pushpin", PREORDER_OPEN: "package", PRICE_DROP: "chart_with_downwards_trend",
             PRICE_THRESHOLD_CROSSED: "dart", NEW_SKU: "new", RESTOCK_DATE_CONFIRMED: "calendar", SOLD_OUT: "x", NEW_LISTING: "mag",
             LISTING_PRICE_DROP: "chart_with_downwards_trend", INFO_CHANGE: "newspaper", CANDIDATE_FOUND: "mag_right", NEEDS_HUMAN: "warning",
             MAIL_DEAL: "email", RELEASE: "date"}


def label(event_type: str, lang: str = "es") -> str:
    table = LABELS.get(lang, LABELS["es"])
    return table.get(event_type, event_type.replace("_", " ").capitalize() if event_type else "")


def format_price(price: Any, currency: Any, lang: str = "es") -> str:
    try:
        value = float(price)
    except (TypeError, ValueError):
        return ""
    text = f"{value:,.2f}"
    text = text.replace(",", "X").replace(".", ",").replace("X", ".") if lang == "es" else text
    if text.endswith((",00", ".00")):
        text = text[:-3]
    cur = {"EUR": "€", "USD": "$", "GBP": "£"}.get(str(currency or "EUR").upper(), str(currency or ""))
    return f"{text} {cur}".strip() if lang == "es" else f"{cur}{text}" if cur in ("$", "£", "€") else f"{text} {cur}".strip()


def compose(event: dict[str, Any], lang: str = "es") -> tuple[str, str]:
    """``(title, body)`` in plain text. Title = '<label>: <event title>'; body = summary, price, confidence, watcher."""
    lang = lang if lang in LABELS else "es"
    words = WORDS[lang]
    kind = label(str(event.get("type") or ""), lang)
    head = str(event.get("title") or "").strip()
    title = f"{kind}: {head}" if kind and head else kind or head or "Tantalus's Hoard"
    lines = []
    if event.get("summary"):
        lines.append(str(event["summary"]).strip())
    price = format_price(event.get("price"), event.get("currency"), lang) if event.get("price") not in (None, "") else ""
    if price:
        lines.append(price)
    try:
        if event.get("confidence") not in (None, ""):
            lines.append(f"{words['confidence']}: {round(float(event['confidence']))}%")
    except (TypeError, ValueError):
        pass
    if event.get("watcher_name"):
        lines.append(f"{words['watcher']}: {event['watcher_name']}")
    return title, "\n".join(lines)
