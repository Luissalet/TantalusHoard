"""Listing de-duplication (pure functions over a list of existing rows).

One listing can show up in several searches and in several runs. ``DedupeIndex`` decides whether an incoming
listing is already known, in this order:

    1. source-native id (``external_id``)                  -> reason "external_id"
    2. normalised URL                                       -> reason "url"
    3. exact normalised title + compatible location + price -> reason "title_location_price"
    4. title similarity >= 0.88 with the same price         -> reason "similar_title"

"Existing" rows may be dicts, sqlite3.Row objects (``listings`` table columns) or RawListing dataclasses: only
``source``, ``external_id``, ``url``, ``title``, ``location_text`` and ``price`` are read. The functions never
touch the database, so the integrator can call them with rows it has already loaded.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

from .textutil import get_field, normalize_text, normalize_url

TITLE_SIMILARITY_THRESHOLD = 0.88
_PRICE_EPSILON = 0.005


def _price_key(price: Any) -> Optional[int]:
    """Price in cents, or None when unknown."""
    try:
        return None if price is None else int(round(float(price) * 100))
    except (TypeError, ValueError):
        return None


def _same_price(a: Any, b: Any) -> bool:
    ka, kb = _price_key(a), _price_key(b)
    if ka is None or kb is None:
        return ka is None and kb is None
    return abs(ka - kb) <= _PRICE_EPSILON * 100


def _locations_compatible(a: str, b: str) -> bool:
    """Same normalised location, or at least one side unknown."""
    return not (a and b and a != b)


class _Entry:
    __slots__ = ("row", "source", "external_id", "url", "title", "location", "price")

    def __init__(self, row: Any) -> None:
        self.row = row
        self.source = str(get_field(row, "source", "") or "")
        self.external_id = str(get_field(row, "external_id", "") or "")
        self.url = normalize_url(get_field(row, "url", ""))
        self.title = normalize_text(get_field(row, "title", ""))
        self.location = normalize_text(get_field(row, "location_text", ""))
        self.price = get_field(row, "price", None)


class DedupeIndex:
    """Index of known listings. Build once per run, call :meth:`find` per incoming listing and :meth:`add` for
    the ones that turn out to be new (so duplicates inside the same batch are caught as well)."""

    def __init__(self, existing: Iterable[Any] = (), *, cross_source: bool = False,
                 threshold: float = TITLE_SIMILARITY_THRESHOLD) -> None:
        self.cross_source = cross_source
        self.threshold = threshold
        self._by_ext: dict[tuple[str, str], _Entry] = {}
        self._by_url: dict[str, _Entry] = {}
        self._by_price: dict[Optional[int], list[_Entry]] = {}
        for row in existing:
            self.add(row)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_price.values())

    def add(self, row: Any) -> None:
        entry = _Entry(row)
        if entry.external_id:
            self._by_ext.setdefault((entry.source, entry.external_id), entry)
        if entry.url:
            self._by_url.setdefault(entry.url, entry)
        self._by_price.setdefault(_price_key(entry.price), []).append(entry)

    def _candidates(self, price: Any) -> list[_Entry]:
        key = _price_key(price)
        if key is None:
            return list(self._by_price.get(None, []))
        found: list[_Entry] = []
        for cents in (key - 1, key, key + 1):  # tolerate rounding
            found.extend(self._by_price.get(cents, []))
        return found

    def find(self, incoming: Any) -> tuple[Optional[Any], str]:
        """Return ``(existing_row, reason)`` or ``(None, "")``."""
        probe = _Entry(incoming)
        if probe.external_id:
            hit = self._by_ext.get((probe.source, probe.external_id))
            if hit is not None:
                return hit.row, "external_id"
        if probe.url:
            hit = self._by_url.get(probe.url)
            if hit is not None and (self.cross_source or not hit.source or not probe.source or hit.source == probe.source):
                return hit.row, "url"

        candidates = [c for c in self._candidates(probe.price)
                      if self.cross_source or not c.source or not probe.source or c.source == probe.source]
        if not candidates or not probe.title:
            return None, ""

        for cand in candidates:
            if cand.title == probe.title and _locations_compatible(probe.location, cand.location):
                return cand.row, "title_location_price"

        best: Optional[_Entry] = None
        best_ratio = 0.0
        for cand in candidates:
            if not cand.title or not _locations_compatible(probe.location, cand.location):
                continue
            ratio = SequenceMatcher(None, probe.title, cand.title).ratio()
            if ratio > best_ratio:
                best, best_ratio = cand, ratio
        if best is not None and best_ratio >= self.threshold:
            return best.row, "similar_title"
        return None, ""


def find_duplicate(incoming: Any, existing: Iterable[Any], *, cross_source: bool = False,
                   threshold: float = TITLE_SIMILARITY_THRESHOLD) -> tuple[Optional[Any], str]:
    """One-shot lookup: the existing row ``incoming`` duplicates and why, or ``(None, "")``."""
    return DedupeIndex(existing, cross_source=cross_source, threshold=threshold).find(incoming)


def dedupe_batch(listings: Iterable[Any], existing: Iterable[Any] = (), *, cross_source: bool = False
                 ) -> tuple[list[Any], list[tuple[Any, Any, str]]]:
    """Split a batch into ``(new_listings, duplicates)``.

    ``duplicates`` is a list of ``(listing, matched_row, reason)``; a listing that repeats an earlier one of the
    same batch matches that earlier listing. Order is preserved.
    """
    index = DedupeIndex(existing, cross_source=cross_source)
    fresh: list[Any] = []
    dups: list[tuple[Any, Any, str]] = []
    for item in listings:
        hit, reason = index.find(item)
        if hit is None:
            fresh.append(item)
            index.add(item)
        else:
            dups.append((item, hit, reason))
    return fresh, dups
