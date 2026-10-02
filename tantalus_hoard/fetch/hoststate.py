"""The app's ``host_state`` table behind the commons' ``HostStateStore`` protocol, plus the robots.txt store.

The table (and what is in it) did not change: ``last_fetch_ts``, ``min_interval_s``, ``blocked_until_ts``, ``block_reason``,
``preferred_tier``, ``ok_count``, ``fail_count`` for the politeness state and ``robots_txt`` / ``robots_fetched_ts`` for the
cached robots.txt, one row per host.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Iterator
from urllib.parse import urlsplit

DEFAULT_MIN_INTERVAL_S = 20.0

_WRITABLE = ("last_fetch_ts", "min_interval_s", "blocked_until_ts", "block_reason", "preferred_tier", "ok_count", "fail_count")
_EMPTY: dict[str, Any] = {"last_fetch_ts": 0.0, "min_interval_s": None, "blocked_until_ts": 0.0, "block_reason": "",
                          "preferred_tier": "", "ok_count": 0, "fail_count": 0}


class SqlHostState:
    """``HostStateStore`` over ``host_state`` (a host without a row reads as the defaults)."""

    def __init__(self, db: Any):
        self.db = db

    def get(self, host: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM host_state WHERE host = ?", (host,))
        return self._shape(row) if row is not None else dict(_EMPTY)

    def update(self, host: str, **cols: Any) -> None:
        values: dict[str, Any] = {}
        for name, value in cols.items():
            if name not in _WRITABLE:
                continue
            if name == "blocked_until_ts" and not value:
                value = None                          # the column stores NULL for "not blocked"
            elif name == "min_interval_s" and value is None:
                value = DEFAULT_MIN_INTERVAL_S
            values[name] = value
        self.db.execute("INSERT INTO host_state(host) VALUES (?) ON CONFLICT(host) DO NOTHING", (host,))
        if values:
            assignments = ", ".join(f"{name} = ?" for name in values)
            self.db.execute(f"UPDATE host_state SET {assignments} WHERE host = ?", (*values.values(), host))

    def items(self) -> list[tuple[str, dict[str, Any]]]:
        return [(row["host"], self._shape(row)) for row in self.db.query("SELECT * FROM host_state ORDER BY host")]

    @staticmethod
    def _shape(row: Any) -> dict[str, Any]:
        shaped = {name: row[name] for name in _WRITABLE}
        shaped["last_fetch_ts"] = float(shaped["last_fetch_ts"] or 0.0)
        shaped["blocked_until_ts"] = float(shaped["blocked_until_ts"] or 0.0)
        # what the Settings page shows about robots.txt
        shaped["robots_fetched_ts"] = row["robots_fetched_ts"]
        shaped["has_robots"] = bool(row["robots_txt"])
        return shaped


class SqlRobotsStore(MutableMapping):
    """``RobotsCache`` store: ``"scheme://host"`` -> ``{"text", "ts"}`` kept in ``host_state.robots_txt`` / ``robots_fetched_ts``."""

    def __init__(self, db: Any):
        self.db = db

    @staticmethod
    def _host(key: str) -> str:
        return (urlsplit(key).hostname or key).lower()

    def __getitem__(self, key: str) -> dict[str, Any]:
        row = self.db.one("SELECT robots_txt, robots_fetched_ts FROM host_state WHERE host = ?", (self._host(key),))
        if row is None or row["robots_fetched_ts"] is None or row["robots_txt"] is None:
            raise KeyError(key)
        return {"text": row["robots_txt"], "ts": float(row["robots_fetched_ts"])}

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        host = self._host(key)
        self.db.execute("INSERT INTO host_state(host) VALUES (?) ON CONFLICT(host) DO NOTHING", (host,))
        self.db.execute("UPDATE host_state SET robots_txt = ?, robots_fetched_ts = ? WHERE host = ?",
                        (value.get("text", ""), value.get("ts"), host))

    def __delitem__(self, key: str) -> None:
        self.db.execute("UPDATE host_state SET robots_txt = NULL, robots_fetched_ts = NULL WHERE host = ?", (self._host(key),))

    def __iter__(self) -> Iterator[str]:
        for row in self.db.query("SELECT host FROM host_state WHERE robots_fetched_ts IS NOT NULL"):
            yield f"https://{row['host']}"

    def __len__(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM host_state WHERE robots_fetched_ts IS NOT NULL")
        return int(row["n"] if row else 0)
