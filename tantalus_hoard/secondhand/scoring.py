"""Explainable listing scoring driven by a pack (see ``packs.py``).

``score_listing(listing, pack, settings, llm=None)`` is the public entry point. Every point comes from a named
signal with a Spanish label, so the UI can always show why a listing scored what it scored. Weights, patterns and
thresholds live in the pack (data), never as magic numbers here.

``settings`` (all optional) understood keys, with aliases:

    origin_location | origin_text | location_text | location   text of the origin ("Getafe, Madrid")
    latitude | origin_lat | lat, longitude | origin_lon | lon | lng   origin coordinates
    radius_km                    search radius; also the reach of the proximity bonus
    municipalities | active_municipalities   towns that add a bonus
    alert_min_score              threshold for alerts (also centres the borderline band of the model)
    weights | scoring_weights    per-key weight overrides
    rules                        per-key rule overrides
    include_any, include_all, exclude, price_ceiling (max_price), msrp, scalper_multiplier, max_distance_km,
    shipping ("any" | "required" | "forbidden"), sealed_bonus, recency_bonus   generic rule overrides
    use_llm (default True), now (epoch seconds, for tests)
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any, Optional

from ..llm import LLM
from ..model import ListingScore, RawListing, ScoreSignal
from .classifier import compile_pattern, hard_reject, is_borderline, refine_with_llm
from .distance import estimate_listing_distance, is_in_active_municipalities
from .price import compute_price_per_book, price_info_for
from .quantity import QuantityEstimate, estimate_quantity
from .textutil import normalize_text, parse_iso_utc

_SETTING_ALIASES: dict[str, tuple[str, ...]] = {
    "origin_text": ("origin_location", "origin_text", "location_text", "location"),
    "origin_lat": ("latitude", "origin_lat", "lat"),
    "origin_lon": ("longitude", "origin_lon", "lon", "lng"),
    "radius_km": ("radius_km",),
    "municipalities": ("municipalities", "active_municipalities"),
    "alert_min_score": ("alert_min_score",),
    "price_ceiling": ("price_ceiling", "max_price", "max_price_eur"),
    "msrp": ("msrp", "msrp_price"),
}

# generic rule keys that a watcher may set at the top level of ``settings``
_RULE_OVERRIDES = ("include_any", "include_all", "exclude", "scalper_multiplier", "max_distance_km", "shipping",
                   "sealed_bonus", "recency_bonus", "enforce_radius", "suspicious_ratio")


def setting(settings: Optional[dict[str, Any]], name: str, default: Any = None) -> Any:
    """Read a normalised setting through its aliases (first non-empty value wins)."""
    if not settings:
        return default
    for key in _SETTING_ALIASES.get(name, (name,)):
        value = settings.get(key)
        if value is not None and value != "":
            return value
    return default


def _as_float(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _as_terms(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[,\n;]", value)
    return [normalize_text(v) for v in value if normalize_text(v)]


def resolve_config(pack: dict[str, Any], settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Merge pack defaults with per-watcher settings into the effective ``{rules, weights, ...}``."""
    settings = settings or {}
    rules = copy.deepcopy(pack.get("rules") or {})
    for key, value in (settings.get("rules") or {}).items():
        rules[key] = value
    for key in _RULE_OVERRIDES:
        if settings.get(key) is not None:
            rules[key] = settings[key]
    for key in ("price_ceiling", "msrp"):
        value = setting(settings, key)
        if value is not None:
            rules[key] = value

    weights = dict(pack.get("weights") or {})
    for key, value in (settings.get("weights") or settings.get("scoring_weights") or {}).items():
        weights[key] = value

    alert = _as_float(setting(settings, "alert_min_score"))
    return {
        "rules": rules,
        "weights": weights,
        "alert_min_score": alert if alert is not None else float(pack.get("alert_min_score", 8)),
    }


def _term_regex(term: str) -> "re.Pattern[str]":
    # whole words; an optional plural so "sobre" also matches "sobres" and "caja" also "cajas"
    return compile_pattern(r"(?<![a-z0-9])" + re.escape(term) + r"(?:s|es)?(?![a-z0-9])")


def _find_terms(text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if _term_regex(t).search(text)]


def _distance_points(distance_km: Optional[float], weight: float, very_close_km: float, max_relevant_km: float) -> float:
    if distance_km is None or weight == 0:
        return 0
    if distance_km <= very_close_km:
        return weight
    if distance_km <= max_relevant_km:
        return round(weight / 2)
    return 0


def _clean(number: float) -> float:
    return int(number) if float(number).is_integer() else round(number, 2)


def score_rules(listing: RawListing, pack: dict[str, Any], settings: Optional[dict[str, Any]] = None) -> ListingScore:
    """Rule-based score (no model). Deterministic and free of I/O."""
    settings = settings or {}
    cfg = resolve_config(pack, settings)
    rules, weights = cfg["rules"], cfg["weights"]

    title = listing.title or ""
    combined = f"{title} {listing.description or ''}"
    normalized = normalize_text(combined)
    title_norm = normalize_text(title)

    price = price_info_for(listing)
    is_free = price.is_free or price.price_eur == 0.0
    quantity: Optional[QuantityEstimate] = estimate_quantity(combined) if rules.get("quantity") == "books" else None
    per_unit = (compute_price_per_book(price.price_eur, quantity.minimum, quantity.maximum) if quantity else None)

    distance_km = listing.distance_km
    if distance_km is None:
        distance_km = estimate_listing_distance(
            origin_text=setting(settings, "origin_text"), origin_lat=setting(settings, "origin_lat"),
            origin_lon=setting(settings, "origin_lon"), location_text=listing.location_text,
            listing_lat=listing.latitude, listing_lon=listing.longitude)

    def result(relevant: bool, score: float, signals: list[ScoreSignal], reason: str, category: str) -> ListingScore:
        return ListingScore(
            relevant=relevant, score=float(score), signals=signals, reason=reason, category=category, method="rules",
            quantity_min=quantity.minimum if quantity and quantity.has_estimate else None,
            quantity_max=quantity.maximum if quantity and quantity.has_estimate else None,
            distance_km=distance_km,
        )

    # ---- pattern signals (also feed the "unless" logic of the hard rejects)
    patterns: dict[str, dict[str, str]] = dict(rules.get("patterns") or {})
    if rules.get("sealed_bonus"):
        patterns.update(rules.get("sealed_patterns") or {})
    matched: set[str] = {key for key, spec in patterns.items() if compile_pattern(spec["regex"]).search(normalized)}
    labels: dict[str, str] = {**{k: v.get("label", k) for k, v in patterns.items()}, **(rules.get("labels") or {})}

    # ---- 1. hard rejects
    reject = hard_reject(normalized, rules, matched)
    excluded = _find_terms(normalized, _as_terms(rules.get("exclude")))
    if not reject and excluded:
        reject = f"Contiene una palabra excluida: «{excluded[0]}»"
    include_any = _as_terms(rules.get("include_any"))
    include_all = _as_terms(rules.get("include_all"))
    # include_scope "title_lead": the topic word must be in the title or the opening of the description, so seller
    # boilerplate at the end ("también vendo libros, se hacen lotes") does not make a video game a book lot.
    scope_text = normalized if rules.get("include_scope") != "title_lead" else normalize_text(
        f"{title} {(listing.description or '')[:int(rules.get('include_lead_chars', 200))]}")
    found_any = _find_terms(scope_text, include_any)
    found_all = _find_terms(normalized, include_all)
    if not reject and include_any and not found_any:
        reject = "No menciona ninguna de las palabras clave (" + ", ".join(include_any[:4]) + ")"
    if not reject and include_all and len(found_all) < len(include_all):
        missing = [t for t in include_all if t not in found_all]
        reject = "Falta alguna palabra clave obligatoria: «" + missing[0] + "»"
    if reject:
        return result(False, 0, [ScoreSignal("hard_reject", 0, reject)], reject, rules.get("category_reject", "rejected"))

    # ---- 2. signals
    signals: list[ScoreSignal] = []

    def add(key: str, condition: bool, label: Optional[str] = None, points: Optional[float] = None) -> None:
        if not condition:
            return
        value = float(weights.get(key, 0)) if points is None else points
        if value == 0:
            return
        signals.append(ScoreSignal(key=key, points=_clean(value), label=label or labels.get(key, key)))

    add("free_or_zero_price", is_free)
    for key in patterns:
        if key in matched:
            add(key, True)

    if quantity is not None:
        add("high_quantity_detected", (quantity.representative or 0) >= float(rules.get("high_quantity_threshold", 20)))

    in_active = is_in_active_municipalities(listing.location_text, setting(settings, "municipalities"))
    add("in_active_municipality", in_active)

    radius = _as_float(setting(settings, "radius_km"))
    max_relevant = radius if radius is not None else float(rules.get("distance_max_relevant_km", 40.0))
    near = _distance_points(distance_km, float(weights.get("very_close_location", 0)),
                            float(rules.get("distance_very_close_km", 5.0)), max_relevant)
    if near:
        signals.append(ScoreSignal("very_close_location", _clean(near), labels.get("very_close_location", "ubicación cercana")))

    # bulk pricing rules (Radar de Libros): a high price for a lot, and a single book at a normal price
    bulk_keys = set(rules.get("bulk_keys") or [])
    has_bulk = any(s.key in bulk_keys for s in signals)
    high_threshold = _as_float(rules.get("high_price_threshold"))
    if high_threshold is not None and price.price_eur is not None:
        cheap_limit = float(rules.get("cheap_per_unit_threshold", 1.0))
        cheap_each = per_unit is not None and per_unit <= cheap_limit
        add("high_price", price.price_eur > high_threshold and not cheap_each)
    if rules.get("individual_sale_penalty"):
        add("individual_sale", not has_bulk and price.price_eur is not None and price.price_eur > 0 and not is_free)
    if rules.get("require_bulk"):
        plural = rules.get("plural_bulk_regex")
        several = bool(plural and compile_pattern(plural).search(title_norm))  # "libros de cocina": several, not one
        add("not_bulk", not has_bulk and not several, "no parece un lote, una colección ni una biblioteca",
            -abs(float(rules.get("not_bulk_penalty", 12))))

    # keywords (generic)
    if include_any and found_any:
        add("include_any_match", True, "coincide con «" + found_any[0] + "»")
        if _find_terms(title_norm, found_any):
            add("title_match", True, "la palabra clave está en el título")
    if include_all and len(found_all) == len(include_all):
        add("include_all_match", True, "menciona todas las palabras clave")
        if not (include_any and found_any) and _find_terms(title_norm, found_all):
            add("title_match", True, "la palabra clave está en el título")

    # price policy: ceiling, MSRP, anti-scalper
    value = price.price_eur
    ceiling = _as_float(rules.get("price_ceiling"))
    if ceiling is not None and value is not None and value > ceiling:
        add("over_ceiling", True, f"{_clean(value)} € supera tu máximo de {_clean(ceiling)} €")
    msrp = _as_float(rules.get("msrp"))
    scalper_mult = _as_float(rules.get("scalper_multiplier")) or 1.5
    scalper = False
    if msrp and msrp > 0 and value is not None and value > 0:
        if value > msrp * scalper_mult:
            scalper = True
            add("scalper", True, f"precio de reventa: {_clean(value)} € es más de {_clean(scalper_mult)}× el PVP ({_clean(msrp)} €)")
        elif value > msrp:
            add("above_msrp", True, f"{_clean(value)} € por encima del PVP ({_clean(msrp)} €)")
        else:
            discount = (msrp - value) / msrp
            suspicious = float(rules.get("suspicious_ratio", 0.4))
            if value < msrp * suspicious and float(weights.get("suspiciously_cheap", 0)) != 0:
                add("suspiciously_cheap", True, "precio sospechosamente bajo: riesgo de fraude o falsificación")
            elif float(weights.get("below_msrp", 0)) != 0:
                full = float(weights["below_msrp"])
                points = max(2.0, round(full * min(1.0, discount / 0.25)))
                text = "al precio del PVP" if discount < 0.005 else f"un {round(discount * 100)} % bajo el PVP"
                add("below_msrp", True, text, min(points, full))

    # distance
    max_distance = _as_float(rules.get("max_distance_km"))
    if max_distance is None and rules.get("enforce_radius"):
        max_distance = radius
    if max_distance is not None and distance_km is not None and distance_km > max_distance:
        add("too_far", True, f"a {distance_km:g} km, fuera de tu radio de {max_distance:g} km")

    # shipping / reserved / recency
    mode = str(rules.get("shipping") or "any").lower()
    if listing.shipping is not None:
        add("no_shipping", mode == "required" and not listing.shipping)
        add("shipping_offered", mode == "forbidden" and bool(listing.shipping))
    add("reserved", bool(listing.reserved) and float(weights.get("reserved", 0)) != 0)
    if rules.get("recency_bonus") and listing.listing_date:
        posted = parse_iso_utc(listing.listing_date)
        if posted is not None:
            age_h = ((_as_float(settings.get("now")) or time.time()) - posted.timestamp()) / 3600.0
            full = float(weights.get("recent", 0))
            if 0 <= age_h <= 3:
                add("recent", True, "publicado hace menos de 3 horas")
            elif 3 < age_h <= 24 and full:
                add("recent", True, "publicado hoy", max(1.0, round(full / 3)))

    total = sum(s.points for s in signals)
    signals.sort(key=lambda s: s.points, reverse=True)
    relevant = total > float(rules.get("relevant_above", 0))

    positives = [s.label for s in signals if s.points > 0]
    negatives = [s.label for s in sorted((x for x in signals if x.points < 0), key=lambda x: x.points)]  # worst first
    if relevant and positives:
        reason = ", ".join(positives[:4])
    elif not relevant and negatives:
        reason = "Descartado: " + ", ".join(negatives[:2])
    else:
        reason = str(rules.get("no_signal_reason") or "Sin señales claras")
    reason = reason[:1].upper() + reason[1:]

    if scalper and not relevant:
        category = "scalper"
    else:
        category = rules.get("category_relevant", "match") if relevant else rules.get("category_irrelevant", "no_match")
    return result(relevant, total, signals, reason, category)


def score_listing(listing: RawListing, pack: dict[str, Any], settings: Optional[dict[str, Any]] = None, *,
                  llm: Optional[LLM] = None) -> ListingScore:
    """Score one listing with ``pack`` and ``settings``; refine borderline scores with the local model if given.

    Never raises for a missing model: ``llm`` may be None, unavailable or return garbage and the rules result
    stands (``method == "rules"``).
    """
    settings = settings or {}
    base = score_rules(listing, pack, settings)
    if llm is None or settings.get("use_llm") is False:
        return base
    if base.category == (pack.get("rules") or {}).get("category_reject", "rejected") and any(
            s.key == "hard_reject" for s in base.signals):
        return base
    cfg = resolve_config(pack, settings)
    band = float(cfg["rules"].get("llm_band", 4))
    if not is_borderline(base.score, cfg["alert_min_score"], band):
        return base
    try:
        return refine_with_llm(llm, listing, base, cfg)
    except Exception:  # noqa: BLE001 — the model is optional; keep the rules answer
        return base
