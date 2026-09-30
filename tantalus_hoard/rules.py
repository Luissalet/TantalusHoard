"""The decision layer: confidence scoring and state transitions -> typed events. Pure functions, no I/O.

Adapters and extractors only produce observations; whether something deserves an alert is decided here, the
same way for every retailer (spec §8.2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from .model import (BUYABLE, IN_STOCK, LOCAL_PICKUP, LOCAL_RESTOCK, MARKETPLACE_ONLY, OUT_OF_STOCK, PREORDER,
                    PREORDER_OPEN, PRICE_DROP, PRICE_THRESHOLD_CROSSED, RESTOCK, RESTOCK_DATE_CONFIRMED,
                    RESTOCK_SCHEDULED, SELLER_ANY_BELOW, SELLER_RETAIL_ONLY, SOLD_OUT, UNKNOWN, Offer)

ALERT_THRESHOLD = 75
REVALIDATE_THRESHOLD = 55

DEFAULT_POLICIES: dict[str, Any] = {
    "alert_on": [RESTOCK, LOCAL_RESTOCK, PREORDER_OPEN, PRICE_DROP, PRICE_THRESHOLD_CROSSED, RESTOCK_DATE_CONFIRMED],
    "require_confidence": ALERT_THRESHOLD,
    "revalidate_seconds": 60,
    "cooldown_minutes": 20,
    "min_drop_pct": 5.0,
    "scalper_multiplier": 1.5,
    "notify_sold_out": False,
}

SEVERITY = {RESTOCK: "high", LOCAL_RESTOCK: "high", PREORDER_OPEN: "high", PRICE_THRESHOLD_CROSSED: "high",
            PRICE_DROP: "medium", RESTOCK_DATE_CONFIRMED: "medium", SOLD_OUT: "low"}

STATE_LABEL_ES = {IN_STOCK: "en stock", LOCAL_PICKUP: "recogida en tienda", PREORDER: "reserva abierta",
                  RESTOCK_SCHEDULED: "reposición anunciada", OUT_OF_STOCK: "agotado", "UNAVAILABLE_REGION": "no disponible en la región",
                  MARKETPLACE_ONLY: "solo vendedores externos", UNKNOWN: "desconocido"}


def policies_for(watcher_config: dict[str, Any]) -> dict[str, Any]:
    raw = watcher_config.get("policies") if isinstance(watcher_config.get("policies"), dict) else {}
    out = {**DEFAULT_POLICIES, **{k: v for k, v in raw.items() if v is not None}}
    req = out.get("require_confidence")
    if isinstance(req, (int, float)) and req <= 1:  # the spec writes 0.75
        out["require_confidence"] = int(round(req * 100))
    return out


# ============================================================================ confidence
@dataclass
class Confidence:
    score: int
    factors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def band(self) -> str:
        if self.score >= ALERT_THRESHOLD:
            return "alert"
        if self.score >= REVALIDATE_THRESHOLD:
            return "revalidate"
        return "log"


def score_confidence(offer: Offer, *, source_level: int, tier: str, target_store_ids: list[str],
                     blocked: bool = False, corroborated: bool = False, price_ok: Optional[bool] = None) -> Confidence:
    """Spec §5 table. ``source_level`` 1 = the retailer's / manufacturer's own page or stock endpoint."""
    factors: list[dict[str, Any]] = []

    def add(key: str, points: int, label: str) -> None:
        factors.append({"key": key, "points": points, "label": label})

    method = offer.method or ""
    if source_level <= 2 and not blocked:
        add("official_page", 45, "Página oficial del vendedor o fabricante")
    structured = method.startswith(("jsonld", "microdata", "opengraph", "api:", "site:")) and offer.availability != UNKNOWN
    if offer.buy_button is True or (method.startswith("api:") and offer.availability in BUYABLE):
        add("buy_control", 30, "Botón de compra activo o stock positivo en el endpoint")
    elif structured:
        add("structured_state", 30, "Disponibilidad declarada en datos estructurados de la página")
    elif offer.buy_button is False and offer.availability in (OUT_OF_STOCK,):
        add("explicit_sold_out", 25, "La página dice explícitamente que está agotado")
    if target_store_ids and any(s in offer.store_availability and offer.store_availability[s] in BUYABLE for s in target_store_ids):
        add("target_store", 20, "Stock ligado a una tienda objetivo")
    sku_match = offer.extra.get("sku_match")
    ean_match = offer.extra.get("ean_match")
    if offer.price is not None and sku_match is not False and ean_match is not False and price_ok is not False:
        add("coherent", 10, "Precio y SKU coherentes")
    if corroborated:
        add("second_source", 15, "Confirmado por una segunda comprobación")
    if tier == "cache" or method == "snippet":
        add("snippet_only", -25, "Solo un fragmento de buscador o caché")
    if offer.seller_is_retailer is False:
        add("marketplace", -35, "Vendedor externo (marketplace)")
    if blocked:
        add("blocked", -20, "Login o CAPTCHA: no se pudo verificar")
    if offer.extra.get("conflict"):
        add("contradictory", -20, "Datos contradictorios en la página")
    if sku_match is False or ean_match is False:
        add("sku_mismatch", -20, "El SKU/EAN no coincide con el objetivo")
    score = max(0, min(100, sum(f["points"] for f in factors)))
    return Confidence(score, factors)


# ============================================================================ effective state
def effective_state(offer: Offer, *, seller_policy: str, price_ceiling: Optional[float]) -> tuple[str, list[str]]:
    """Apply the seller / price policy to the raw availability. Returns (state, notes)."""
    notes: list[str] = []
    state = offer.availability or UNKNOWN
    if state in BUYABLE and offer.seller_is_retailer is False:
        if seller_policy == SELLER_RETAIL_ONLY:
            notes.append(f"solo vendedor externo ({offer.seller or 'marketplace'})")
            return MARKETPLACE_ONLY, notes
        if seller_policy == SELLER_ANY_BELOW and price_ceiling and offer.price is not None and offer.price > price_ceiling:
            notes.append(f"vendedor externo por encima del techo ({offer.price:g} > {price_ceiling:g})")
            return MARKETPLACE_ONLY, notes
    if state == PREORDER and not offer.buy_button and offer.method == "phrases":
        notes.append("reserva detectada solo por texto")
    return state, notes


def price_ceiling_for(target: dict[str, Any], policies: dict[str, Any]) -> Optional[float]:
    if target.get("price_ceiling"):
        return float(target["price_ceiling"])
    if target.get("msrp"):
        return round(float(target["msrp"]) * float(policies.get("scalper_multiplier") or 1.5), 2)
    ceiling = policies.get("price_ceiling")
    return float(ceiling) if ceiling else None


# ============================================================================ transitions
def dedupe_key(target_id: str, event_type: str, state: str, price: Optional[float], store: str = "") -> str:
    rounded = "" if price is None else str(int(round(price)))
    raw = f"{target_id}|{event_type}|{state}|{rounded}|{store}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def transitions(*, target: dict[str, Any], prev_state: str, prev_price: Optional[float], state: str, offer: Offer,
                policies: dict[str, Any], ceiling: Optional[float], first_check: bool) -> list[dict[str, Any]]:
    """Event drafts for the change prev -> now. Identical states produce nothing (spec §3.5 step 5)."""
    out: list[dict[str, Any]] = []
    price = offer.price
    threshold = target.get("price_threshold") or policies.get("price_threshold") or policies.get("price_threshold_eur")
    was_buyable = prev_state in BUYABLE
    over = bool(ceiling and price is not None and price > ceiling)

    def draft(kind: str, **extra: Any) -> None:
        out.append({"type": kind, "old_state": prev_state, "new_state": state, "price": price, "old_price": prev_price,
                    "severity": SEVERITY.get(kind, "medium"), "over_ceiling": over, **extra})

    if state in (IN_STOCK,) and not was_buyable:
        draft(RESTOCK, first_check=first_check)
    elif state == LOCAL_PICKUP and prev_state != LOCAL_PICKUP:
        draft(LOCAL_RESTOCK if not was_buyable else RESTOCK, first_check=first_check)
    elif state == PREORDER and prev_state not in (PREORDER, IN_STOCK):
        draft(PREORDER_OPEN, first_check=first_check)
    elif state == RESTOCK_SCHEDULED and prev_state != RESTOCK_SCHEDULED and (offer.restock_date or offer.preorder_date):
        draft(RESTOCK_DATE_CONFIRMED, restock_date=offer.restock_date or offer.preorder_date)
    elif was_buyable and state in (OUT_OF_STOCK, MARKETPLACE_ONLY) and not first_check:
        draft(SOLD_OUT)

    if price is not None and prev_price is not None and state in BUYABLE and not first_check:
        min_drop = float(policies.get("min_drop_pct") or 0)
        if price <= prev_price * (1 - min_drop / 100.0) and price < prev_price:
            pct = round((prev_price - price) / prev_price * 100, 1)
            draft(PRICE_DROP, drop_pct=pct, all_time_low=bool(target.get("min_price") and price < float(target["min_price"])))
    if threshold and price is not None and state in BUYABLE:
        thr = float(threshold)
        crossed = price <= thr and (first_check or prev_price is None or prev_price > thr or not was_buyable)
        if crossed:
            draft(PRICE_THRESHOLD_CROSSED, threshold=thr)
    return out


def summary_for(kind: str, *, title: str, retailer: str, state: str, price: Optional[float], currency: str,
                old_price: Optional[float] = None, extra: Optional[dict[str, Any]] = None) -> str:
    extra = extra or {}
    money = f"{price:,.2f} {currency or 'EUR'}".replace(",", "X").replace(".", ",").replace("X", ".") if price is not None else "precio desconocido"
    where = f" en {retailer}" if retailer else ""
    if kind == RESTOCK:
        base = f"{title}: disponible{where} por {money}"
        if extra.get("first_check"):
            base += " (ya estaba disponible en la primera comprobación)"
    elif kind == LOCAL_RESTOCK:
        base = f"{title}: recogida en tienda{where} por {money}"
    elif kind == PREORDER_OPEN:
        base = f"{title}: reserva abierta{where} por {money}"
    elif kind == RESTOCK_DATE_CONFIRMED:
        base = f"{title}: reposición anunciada{where} para {extra.get('restock_date')}"
    elif kind == SOLD_OUT:
        base = f"{title}: agotado{where} ({STATE_LABEL_ES.get(state, state)})"
    elif kind == PRICE_DROP:
        old = f"{old_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if old_price is not None else "?"
        base = f"{title}: baja de {old} a {money}{where} (−{extra.get('drop_pct')} %)"
        if extra.get("all_time_low"):
            base += ", mínimo histórico"
    elif kind == PRICE_THRESHOLD_CROSSED:
        base = f"{title}: {money}{where}, por debajo de tu umbral de {extra.get('threshold'):g}"
    else:
        base = f"{title}: {STATE_LABEL_ES.get(state, state)}{where}"
    if extra.get("over_ceiling"):
        base += " — ojo: por encima del precio razonable (posible reventa)"
    return base
