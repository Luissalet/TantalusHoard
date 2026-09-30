"""Small text helpers shared by the second-hand modules (pure, no I/O)."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional


def normalize_text(text: Optional[str]) -> str:
    """Lower case, accents stripped, whitespace collapsed. Used for matching and comparisons."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


def normalize_url(url: Optional[str]) -> str:
    """URL without fragment, query string and trailing slash, lower case (the listing id lives in the path)."""
    if not url:
        return ""
    url = str(url).strip().split("#", 1)[0].split("?", 1)[0]
    if url.endswith("/"):
        url = url[:-1]
    return url.lower()


def get_field(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dict, sqlite3.Row, dataclass or plain object (used for DB rows and RawListing alike)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    try:  # sqlite3.Row supports mapping access by column name but has no .get
        if hasattr(obj, "keys") and name in obj.keys():
            return obj[name]
    except Exception:  # noqa: BLE001
        pass
    return getattr(obj, name, default)


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (``Z`` accepted) as an aware UTC datetime, or None."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def epoch_to_iso(value: Any) -> Optional[str]:
    """Epoch seconds or milliseconds to an ISO-8601 UTC string, or None when not a plausible epoch."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 1e11:  # milliseconds
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None
