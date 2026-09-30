"""Common contract of a second-hand source."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from ..model import RawListing


@runtime_checkable
class SecondhandSource(Protocol):
    """A source only returns observations; scoring and decisions live elsewhere.

    ``search`` never raises for expected failures (blocked, offline, changed markup, login needed): it returns
    what it could read plus an error string (empty when everything went well). A non-empty error together with
    a non-empty list means a partial result.
    """

    name: str

    def search(self, query: str, *, latitude: Optional[float] = None, longitude: Optional[float] = None,
               location_text: Optional[str] = None, radius_km: float = 30, max_price: Optional[float] = None,
               min_price: Optional[float] = None, limit: int = 40, order_by: str = "newest"
               ) -> tuple[list[RawListing], str]:
        ...
