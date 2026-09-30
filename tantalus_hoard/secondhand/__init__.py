"""Second-hand watching: sources (Wallapop, Facebook Marketplace), scoring packs, distance, price, dedupe."""

from __future__ import annotations

from typing import Any, Callable

from .base import SecondhandSource
from .classifier import LLM_MIN_CONFIDENCE, is_borderline
from .dedupe import DedupeIndex, dedupe_batch, find_duplicate
from .distance import (estimate_distance_km, estimate_listing_distance, find_municipality_coords, haversine_km,
                       is_in_active_municipalities, list_known_municipalities)
from .facebook import FacebookSource
from .packs import PACKS, get_pack, list_packs
from .price import compute_price_per_book, format_price_display, normalize_price, price_info_for
from .quantity import QuantityEstimate, estimate_quantity
from .scoring import resolve_config, score_listing, score_rules
from .wallapop import WallapopSource

SOURCES: dict[str, Callable[..., SecondhandSource]] = {
    "wallapop": WallapopSource,
    "facebook": FacebookSource,
}


def build_source(name: str, fetcher: Any, **options: Any) -> SecondhandSource:
    """Instantiate a source by name (``wallapop`` | ``facebook``); raises ``KeyError`` for an unknown name."""
    try:
        cls = SOURCES[name]
    except KeyError:
        raise KeyError(f"unknown second-hand source {name!r}; known: {', '.join(SOURCES)}") from None
    return cls(fetcher, **options)


__all__ = [
    "SOURCES", "PACKS", "build_source", "get_pack", "list_packs", "score_listing", "score_rules", "resolve_config",
    "SecondhandSource", "WallapopSource", "FacebookSource", "DedupeIndex", "dedupe_batch", "find_duplicate",
    "estimate_distance_km", "estimate_listing_distance", "find_municipality_coords", "haversine_km",
    "is_in_active_municipalities", "list_known_municipalities", "compute_price_per_book", "format_price_display",
    "normalize_price", "price_info_for", "QuantityEstimate", "estimate_quantity", "LLM_MIN_CONFIDENCE", "is_borderline",
]
