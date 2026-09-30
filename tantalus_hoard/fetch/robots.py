"""robots.txt cache, persisted per host in the ``host_state`` table (robots_txt, robots_fetched_ts, 24 h TTL).

Semantics (the usual crawler conventions):
* 200 -> parsed and stored;
* 404 / other 4xx -> "no robots" -> allow everything (stored as an empty text so it is not re-fetched for 24 h);
* 5xx / network error -> allow, but report a note; only remembered in memory for a few minutes.
"""

from __future__ import annotations

import threading
import time
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

ROBOTS_TTL_S = 24 * 3600
UNREACHABLE_RETRY_S = 10 * 60
PRODUCT_TOKEN = "TantalusHoard"

# fetch_text(url) -> (status, text, error). ``status`` 0 with an ``error`` means unreachable.
RobotsFetcher = Callable[[str], tuple[int, str, str]]


class RobotsCache:
    def __init__(self, db, fetch_text: RobotsFetcher, clock: Callable[[], float] = time.time):
        self.db = db
        self.fetch_text = fetch_text
        self.clock = clock
        self._parsers: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._unreachable_until: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def check(self, url: str) -> tuple[bool, str]:
        """(allowed, note). ``note`` is non-empty when robots.txt could not be read (the fetch is allowed)."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            return True, ""
        scheme = parts.scheme or "https"
        parser, note = self._parser_for(host, scheme)
        if parser is None:
            return True, note
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        allowed = parser.can_fetch(PRODUCT_TOKEN, f"{scheme}://{host}{path}")
        return allowed, ""

    def raw(self, host: str) -> str | None:
        """The cached robots.txt text (``None`` when never fetched)."""
        row = self.db.one("SELECT robots_txt FROM host_state WHERE host = ?", (host,))
        return row["robots_txt"] if row else None

    # ------------------------------------------------------------------ internals
    def _parser_for(self, host: str, scheme: str) -> tuple[RobotFileParser | None, str]:
        now = self.clock()
        with self._lock:
            cached = self._parsers.get(host)
            if cached and now - cached[0] < ROBOTS_TTL_S:
                return cached[1], ""
            if self._unreachable_until.get(host, 0) > now:
                return None, "robots.txt unreachable (assumed allowed)"
        row = self.db.one("SELECT robots_txt, robots_fetched_ts FROM host_state WHERE host = ?", (host,))
        if row and row["robots_fetched_ts"] is not None and row["robots_txt"] is not None \
                and now - float(row["robots_fetched_ts"]) < ROBOTS_TTL_S:
            parser = self._parse(row["robots_txt"])
            with self._lock:
                self._parsers[host] = (float(row["robots_fetched_ts"]), parser)
            return parser, ""
        return self._refresh(host, scheme, now)

    def _refresh(self, host: str, scheme: str, now: float) -> tuple[RobotFileParser | None, str]:
        status, text, error = self.fetch_text(f"{scheme}://{host}/robots.txt")
        if status == 200 and text is not None:
            self._store(host, text, now)
            parser = self._parse(text)
            with self._lock:
                self._parsers[host] = (now, parser)
            return parser, ""
        if 400 <= status < 500:  # no robots.txt (or not for us) -> everything allowed
            self._store(host, "", now)
            with self._lock:
                self._parsers[host] = (now, None)
            return None, ""
        # 5xx / network error: allow this time, retry later
        with self._lock:
            self._unreachable_until[host] = now + UNREACHABLE_RETRY_S
        return None, f"robots.txt unreachable ({error or 'HTTP ' + str(status)}); assumed allowed"

    def _store(self, host: str, text: str, now: float) -> None:
        self.db.execute("INSERT INTO host_state(host) VALUES (?) ON CONFLICT(host) DO NOTHING", (host,))
        self.db.execute("UPDATE host_state SET robots_txt = ?, robots_fetched_ts = ? WHERE host = ?", (text, now, host))

    @staticmethod
    def _parse(text: str) -> RobotFileParser | None:
        if not text.strip():
            return None
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return parser
