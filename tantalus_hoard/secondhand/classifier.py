"""Hard rejects, borderline detection and the optional model refinement (no rule scoring here).

Order of the whole pipeline (see ``scoring.score_listing``):

1. hard rejects: a few unambiguous patterns discard a listing without scoring it, unless the listing also has a
   strong physical bulk signal (then the score decides);
2. weighted, explainable signals (always computed: this is what the app runs on without any model);
3. optional model refinement, only for borderline scores, through ``LLM.json``; any failure keeps the rules result.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from functools import lru_cache
from typing import Any, Optional

from ..llm import LLM, UNTRUSTED, page_block
from ..model import ListingScore, RawListing, ScoreSignal
from .price import format_price_display, price_info_for
from .textutil import normalize_text

LLM_MIN_CONFIDENCE = 0.6

_SYSTEM = (
    "You judge one second-hand listing for a product watcher. {goal}\n"
    "Answer ONLY with a JSON object: "
    '{{"relevant": true|false, "confidence": number 0-1, "estimated_quantity": integer or null, '
    '"category": short snake_case label, "reason": one short sentence in Spanish}}. '
    + UNTRUSTED
)


@lru_cache(maxsize=512)
def compile_pattern(regex: str) -> "re.Pattern[str]":
    return re.compile(regex)


def hard_reject(normalized_text: str, rules: dict[str, Any], matched_keys: set[str]) -> Optional[str]:
    """Reason to discard the listing outright, or None.

    A rule with ``unless`` is skipped when any of the named pattern keys matched (a strong bulk signal beats a
    blunt reject).
    """
    for rule in rules.get("hard_rejects") or []:
        unless = set(rule.get("unless") or [])
        if unless & matched_keys:
            continue
        if compile_pattern(rule["regex"]).search(normalized_text):
            return str(rule.get("reason") or "Descartado por regla")
    return None


def is_borderline(score: float, alert_min_score: float, band: float) -> bool:
    """A score close enough to the alert threshold for a second opinion to matter."""
    return (alert_min_score - band) <= score <= (alert_min_score + band)


def _points(weights: dict[str, float], key: str, default: float) -> float:
    try:
        return float(weights.get(key, default))
    except (TypeError, ValueError):
        return default


def refine_with_llm(llm: Optional[LLM], listing: RawListing, base: ListingScore, cfg: dict[str, Any]) -> ListingScore:
    """Ask the local model for a verdict; return ``base`` untouched when it is unavailable, invalid or unsure.

    The listing text is untrusted data (wrapped as such). The model can confirm or reject: it adds one explicit
    signal (so the change is visible in the breakdown) and flips ``relevant``; ``method`` becomes ``rules+llm``.
    """
    if llm is None:
        return base
    rules, weights = cfg["rules"], cfg["weights"]
    price = price_info_for(listing)
    listing_text = json.dumps({
        "title": listing.title or "",
        "description": (listing.description or "")[:1500],
        "price": format_price_display(price.price_eur, price.is_free) if price.price_eur is not None or price.is_free else "unknown",
        "location": listing.location_text or "unknown",
        "shipping": listing.shipping,
        "reserved": listing.reserved,
        "rule_score": base.score,
    }, ensure_ascii=False)
    data = llm.json(_SYSTEM.format(goal=rules.get("llm_goal", "")), page_block(listing_text),
                    required=("relevant",), max_tokens=300)
    if not data:
        return base
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    if confidence < LLM_MIN_CONFIDENCE:
        return base

    verdict = bool(data.get("relevant"))
    reason = re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:200]
    if verdict:
        points = abs(_points(weights, "llm_confirms", 4))
        signal = ScoreSignal("llm_confirms", points, "el modelo local lo confirma" + (f": {reason}" if reason else ""))
    else:
        points = -abs(_points(weights, "llm_rejects", 8))
        signal = ScoreSignal("llm_rejects", points, "el modelo local lo descarta" + (f": {reason}" if reason else ""))

    quantity_min, quantity_max = base.quantity_min, base.quantity_max
    estimated = data.get("estimated_quantity")
    if rules.get("quantity") == "books" and quantity_min is None and quantity_max is None:
        try:
            if estimated not in (None, "") and int(estimated) > 0:
                quantity_min = quantity_max = int(estimated)
        except (TypeError, ValueError):
            pass

    category = str(data.get("category") or "").strip()[:40] or base.category
    signals = sorted([*base.signals, signal], key=lambda s: s.points, reverse=True)
    return replace(
        base, relevant=verdict, score=float(base.score + points), signals=signals,
        reason=reason or base.reason, category=category, method="rules+llm",
        quantity_min=quantity_min, quantity_max=quantity_max,
    )
