"""Optional LLM fallback for pages the deterministic extractors could not decide.

The model may only *quote* the page: every claim needs evidence snippets that literally occur in the text it was
shown (case- and whitespace-insensitive). An answer whose evidence cannot be found is dropped entirely, a price or
seller that is not in the text is dropped, and the state must be supported by a matching phrase in the kept
evidence. Everything works when the model is absent: ``None`` is returned.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..llm import LLM, UNTRUSTED, page_block
from ..model import IN_STOCK, LOCAL_PICKUP, OUT_OF_STOCK, PREORDER, Offer
from .phrases import (BUY, NOTIFY, PICKUP, PREORDER_PH, POSITIVE_STOCK, SOLDOUT, _has_phrase, norm, snippet)

SYSTEM = (
    "You read the visible text of one online-shop product page for a stock monitor. " + UNTRUSTED + " "
    "Reply with a single JSON object with exactly these keys: "
    '"availability" (one of "in_stock", "out_of_stock", "preorder", "pickup", "unknown"), '
    '"price" (number or null), "currency" (ISO code or null), "seller" (string or null), '
    '"buy_button" (true, false or null: is there an active add-to-cart / buy / reserve control), '
    '"evidence" (a list of 1 to 4 short snippets COPIED EXACTLY from the page text that justify the answer). '
    'Use "unknown" and an empty evidence list when the page does not say. Never guess and never invent text.'
)
_STATES = {"in_stock": IN_STOCK, "out_of_stock": OUT_OF_STOCK, "preorder": PREORDER, "pickup": LOCAL_PICKUP}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _supports(state: str, evidence: list[str]) -> bool:
    joined = " | ".join(evidence)
    if state == IN_STOCK:
        return _has_phrase(joined, BUY) or _has_phrase(joined, POSITIVE_STOCK) or _has_phrase(joined, PREORDER_PH)
    if state == OUT_OF_STOCK:
        return _has_phrase(joined, SOLDOUT) or _has_phrase(joined, NOTIFY)
    if state == PREORDER:
        return _has_phrase(joined, PREORDER_PH)
    if state == LOCAL_PICKUP:
        return _has_phrase(joined, PICKUP)
    return False


def _price_in_text(price: float, text_squashed: str) -> bool:
    forms = {f"{price:.2f}", f"{price:.2f}".replace(".", ","), f"{price:,.2f}", f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")}
    if float(price).is_integer():
        forms.add(str(int(price)))
    return any(re.search(rf"(?<![\d.,]){re.escape(form)}(?![\d])", text_squashed) for form in forms)


def llm_extract(llm: Optional[LLM], text: str, *, url: str = "", title_hint: str = "") -> Optional[Offer]:
    """One Offer from the model's quoted answer, or ``None`` (no model / unknown / unverifiable)."""
    if llm is None or not text.strip():
        return None
    user = (f"URL: {url}\nProduct we are looking for: {title_hint or '(not given)'}\n\n" + page_block(text, 6000))
    data = llm.json(SYSTEM, user, required=("availability", "evidence"), max_tokens=500)
    return validate_answer(data, text)


def validate_answer(data: Optional[dict[str, Any]], text: str) -> Optional[Offer]:
    """Turn a model answer into an Offer, enforcing the literal-evidence rule. Separate for testing."""
    if not isinstance(data, dict):
        return None
    state = _STATES.get(str(data.get("availability", "")).strip().lower())
    if state is None:
        return None
    haystack = _squash(text)
    raw_evidence = data.get("evidence")
    if not isinstance(raw_evidence, list):
        return None
    evidence = [snippet(str(e)) for e in raw_evidence if isinstance(e, (str, int, float)) and len(str(e).strip()) >= 3
                and _squash(str(e)) in haystack]
    if not evidence or not _supports(state, evidence):
        return None
    offer = Offer(availability=state, method="llm", evidence=evidence[:4], extra={"llm": True})
    price = data.get("price")
    if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0 and _price_in_text(float(price), haystack):
        offer.price = float(price)
        currency = str(data.get("currency") or "").strip().upper()
        offer.currency = currency[:3] if currency else None
    seller = data.get("seller")
    if isinstance(seller, str) and 2 <= len(seller.strip()) <= 80 and _squash(seller) in haystack:
        offer.seller = seller.strip()
    if isinstance(data.get("buy_button"), bool) and data["buy_button"] and any(_has_phrase(e, BUY) or _has_phrase(e, PREORDER_PH) for e in evidence):
        offer.buy_button = True
    elif data.get("buy_button") is False and state == OUT_OF_STOCK:
        offer.buy_button = False
    return offer
