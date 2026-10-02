"""Price normalisation: free / zero / numeric / unknown, and price per unit when a quantity is known."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..hoard_link.money import find_prices, format_money

_FREE_WORDS = ("gratis", "regalo", "free")



@dataclass
class PriceInfo:
    price_eur: Optional[float]
    is_free: bool
    price_text: Optional[str]


def normalize_price(raw_price: Optional[str]) -> PriceInfo:
    """Turn the price text a source shows ("Gratis", "0 €", "5,50 €", "€5", "10 EUR") into data.

    Anything that cannot be read stays unknown (never invented).
    """
    if raw_price is None:
        return PriceInfo(None, False, None)
    text = str(raw_price).strip()
    if not text:
        return PriceInfo(None, False, None)
    lowered = text.lower()
    if any(word in lowered for word in _FREE_WORDS):
        return PriceInfo(0.0, True, text)
    hit = next((h for h in find_prices(text) if h.currency == "EUR" and h.amount >= 0), None)
    if hit is None:
        return PriceInfo(None, False, text)
    value = float(hit.amount)
    return PriceInfo(value, value == 0.0, text)


def price_info_for(listing: Any) -> PriceInfo:
    """Price of a RawListing: the numeric ``price`` when the source gave one, else parse ``price_raw``."""
    price = getattr(listing, "price", None)
    raw = getattr(listing, "price_raw", None)
    if price is not None:
        try:
            value = float(price)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            return PriceInfo(value, value == 0.0, raw)
    return normalize_price(raw)


def compute_price_per_book(price_eur: Optional[float], quantity_min: Optional[int],
                           quantity_max: Optional[int] = None) -> Optional[float]:
    """Price per unit when both a price and some quantity estimate exist; midpoint of a range; else None."""
    if price_eur is None:
        return None
    if quantity_min and quantity_max:
        quantity = (quantity_min + quantity_max) / 2
    else:
        quantity = quantity_min or quantity_max
    if not quantity or quantity <= 0:
        return None
    return round(price_eur / quantity, 3)


compute_price_per_unit = compute_price_per_book


def format_price_display(price_eur: Optional[float], is_free: bool = False) -> str:
    """Human readable price for the UI."""
    if is_free or price_eur == 0.0:
        return "Gratis"
    if price_eur is None:
        return "Precio desconocido"
    return format_money(price_eur, "EUR", trim_zero_cents=True)
