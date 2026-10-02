"""Budget line for alerts: "quedan 120 € en Ocio", asked of Ledger through the family hub.

A watcher may carry ``budget_category`` in its config. When an alert has a price, ``BudgetNote.note`` asks Ledger's
``budget_status`` (through ``family.call``, short timeout) and returns one line for the notification. It never raises and
never makes an alert wait for long: the answer (or the failure) is cached for ten minutes per category, so a Ledger that is
not running costs one quick failed call per category and period, not one per alert.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Optional

from .mail.parse import fold
from .notify.labels import format_price

CACHE_S = 600.0
TIMEOUT_S = 2.5
Call = Callable[..., dict[str, Any]]


def default_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .hoard_link import family
    return family.call(*args, **kwargs)


def unwrap(answer: Any) -> Optional[dict[str, Any]]:
    """The tool result of a hub proxy answer (plain dict, JSON string or MCP content), or None."""
    if not isinstance(answer, dict) or not answer.get("ok"):
        return None
    result = answer.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        for part in result["content"]:
            if isinstance(part, dict) and part.get("type") == "text":
                try:
                    return json.loads(part["text"])
                except ValueError:
                    continue
        return None
    return result if isinstance(result, dict) else None


class BudgetNote:
    def __init__(self, call: Optional[Call] = None, clock: Callable[[], float] = time.time, cache_s: float = CACHE_S, timeout: float = TIMEOUT_S):
        self._call = call or default_call
        self.clock = clock
        self.cache_s = cache_s
        self.timeout = timeout
        self._cache: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def left(self, category: str) -> Optional[dict[str, Any]]:
        """``{category, budget, spent, left}`` for the category, or None (unknown category, Ledger away, any error)."""
        key = fold(category).strip()
        if not key:
            return None
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_s:
                return hit[1]
        row: Optional[dict[str, Any]] = None
        try:
            result = unwrap(self._call("ledger", "budget_status", {"category": category}, timeout=self.timeout))
            for item in (result or {}).get("categories") or []:
                if isinstance(item, dict) and fold(item.get("category")).strip() == key and item.get("left") is not None:
                    float(item["left"])
                    row = item
                    break
        except Exception:  # noqa: BLE001 - never block an alert on Ledger
            row = None
        with self._lock:
            self._cache[key] = (self.clock(), row)          # a failure is cached too: Ledger away must not slow every alert
        return row

    def note(self, category: Any, lang: str = "es") -> str:
        """``"quedan 120 € en Ocio"`` / ``"120 € left in Ocio"``, or '' when there is nothing to say."""
        category = str(category or "").strip()
        if not category:
            return ""
        row = self.left(category)
        if row is None:
            return ""
        amount = format_price(row["left"], row.get("currency") or "EUR", lang)
        name = str(row.get("category") or category)
        return f"quedan {amount} en {name}" if lang == "es" else f"{amount} left in {name}"
