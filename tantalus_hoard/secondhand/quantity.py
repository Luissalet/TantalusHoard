"""Rough estimate of how many books a listing offers.

Never invents an exact figure that is not in the text: when the text gives no number a heuristic range is
returned (or nothing) and ``approximate`` says it is an estimate, not a literal reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .textutil import normalize_text

# Books per moving box: a standard box holds 12-20 mixed paperbacks / hardcovers (a range, not an exact value).
BOOKS_PER_BOX_MIN = 12
BOOKS_PER_BOX_MAX = 20

_WORD_QUANTITIES: dict[str, tuple[int, int]] = {
    "un par de": (2, 2),
    "unas pocas": (2, 4),
    "unos pocos": (2, 4),
    "varias": (3, 5),
    "varios": (3, 5),
}

_QUALITATIVE_PHRASES: list[tuple[str, tuple[int, int]]] = [
    ("biblioteca entera", (80, 150)),
    ("biblioteca completa", (80, 150)),
    ("toda la biblioteca", (80, 150)),
    ("coleccion completa", (20, 80)),
    ("muchisimos libros", (30, 80)),
    ("muchisimos", (30, 80)),
    ("muchos libros", (15, 40)),
]

_EXACT_NUMBER_BOOKS = re.compile(r"\b(\d{1,4})\s*libros?\b")
_APPROX_PREFIX = re.compile(r"\b(unos|unas|aprox\.?|aproximadamente)\s+\d")
_NUMBER_BOXES = re.compile(r"\b(\d{1,3})\s*cajas?\b")
_WORD_BOXES = re.compile(r"\b(" + "|".join(re.escape(w) for w in _WORD_QUANTITIES) + r")\s*(?:de\s+)?cajas?\b")


@dataclass
class QuantityEstimate:
    minimum: Optional[int]
    maximum: Optional[int]
    approximate: bool
    matched_text: Optional[str]

    @property
    def has_estimate(self) -> bool:
        return self.minimum is not None or self.maximum is not None

    @property
    def representative(self) -> Optional[int]:
        """A single midpoint number for sorting and comparing."""
        if self.minimum is not None and self.maximum is not None:
            return round((self.minimum + self.maximum) / 2)
        return self.minimum or self.maximum

    def display(self) -> Optional[str]:
        if self.minimum is None and self.maximum is None:
            return None
        if self.minimum == self.maximum:
            return f"≈ {self.minimum} libros" if self.approximate else f"{self.minimum} libros"
        return f"≈ {self.minimum}-{self.maximum} libros"


_NO_ESTIMATE = QuantityEstimate(None, None, False, None)


def estimate_quantity(text: str) -> QuantityEstimate:
    """Estimate the book count from title + description. Priority: explicit number of books > number of
    boxes > word quantity + boxes > qualitative phrases without a number."""
    if not text:
        return _NO_ESTIMATE
    normalized = normalize_text(text)

    match = _EXACT_NUMBER_BOOKS.search(normalized)
    if match:
        value = int(match.group(1))
        if _APPROX_PREFIX.search(normalized[: match.end()][-30:]):
            spread = max(1, round(value * 0.15))
            return QuantityEstimate(value - spread, value + spread, True, match.group(0))
        return QuantityEstimate(value, value, False, match.group(0))

    match = _NUMBER_BOXES.search(normalized)
    if match:
        boxes = int(match.group(1))
        return QuantityEstimate(boxes * BOOKS_PER_BOX_MIN, boxes * BOOKS_PER_BOX_MAX, True, match.group(0))

    match = _WORD_BOXES.search(normalized)
    if match:
        low, high = _WORD_QUANTITIES[match.group(1)]
        return QuantityEstimate(low * BOOKS_PER_BOX_MIN, high * BOOKS_PER_BOX_MAX, True, match.group(0))

    for phrase, (low, high) in _QUALITATIVE_PHRASES:
        if phrase in normalized:
            return QuantityEstimate(low, high, True, phrase)
    return _NO_ESTIMATE
