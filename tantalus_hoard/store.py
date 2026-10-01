"""Repository: every read and write of the SQLite store, returning plain dicts (JSON columns decoded)."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from .db import Database
from .errors import TantalusError
from .model import MODES, SELLER_POLICIES, TARGET_STATUSES, UNKNOWN

JSON_COLUMNS = {
    "watchers": ("config",),
    "targets": ("store_ids", "extra"),
    "observations": ("store_availability", "evidence", "factors"),
    "events": ("data",),
    "listings": ("signals", "extra"),
}
BOOL_COLUMNS = {"watchers": ("enabled",), "events": ("seen", "notified"), "listings": ("relevant", "shipping", "reserved"),
                "observations": ("seller_is_retailer", "buy_button", "is_revalidation"), "info_items": ("material",)}


def new_id(prefix: str) -> str:
    """Sortable, readable ids: prefix + base36 milliseconds + 6 random hex chars."""
    ms = int(time.time() * 1000)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while ms:
        ms, r = divmod(ms, 36)
        out = digits[r] + out
    return f"{prefix}_{out}{secrets.token_hex(3)}"


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def row_dict(table: str, row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    for col in JSON_COLUMNS.get(table, ()):
        if col in d and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except ValueError:
                d[col] = {} if col in ("config", "extra", "data", "store_availability") else []
    for col in BOOL_COLUMNS.get(table, ()):
        if col in d and d[col] is not None:
            d[col] = bool(d[col])
    return d


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


class Store:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db = db
        self.clock = clock

    # =========================================================================== watchers
    def watchers(self, *, mode: Optional[str] = None, enabled: Optional[bool] = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM watchers WHERE 1=1", []
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        if enabled is not None:
            sql += " AND enabled = ?"
            params.append(1 if enabled else 0)
        return [row_dict("watchers", r) for r in self.db.query(sql + " ORDER BY created_ts", params)]

    def watcher(self, watcher_id: str) -> dict[str, Any]:
        row = row_dict("watchers", self.db.one("SELECT * FROM watchers WHERE id = ?", (watcher_id,)))
        if row is None:
            raise TantalusError("not_found", f"Watcher {watcher_id} does not exist.", "List them with watcher_list.")
        return row

    def create_watcher(self, *, name: str, mode: str, config: dict[str, Any], interval_min: int = 30,
                       discovery_interval_h: int = 24, enabled: bool = True, notes: str = "", watcher_id: str = "") -> dict[str, Any]:
        if mode not in MODES:
            raise TantalusError("invalid", f"Unknown mode {mode!r}.", f"Use one of: {', '.join(MODES)}.")
        now = self.clock()
        wid = watcher_id or new_id("w")
        self.db.execute(
            "INSERT INTO watchers(id, name, mode, enabled, config, interval_min, discovery_interval_h, notes, created_ts, updated_ts, next_run_ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (wid, name.strip(), mode, 1 if enabled else 0, _dump(config), int(interval_min), int(discovery_interval_h), notes, now, now, now))
        return self.watcher(wid)

    def update_watcher(self, watcher_id: str, **fields: Any) -> dict[str, Any]:
        self.watcher(watcher_id)
        allowed = {"name", "enabled", "config", "interval_min", "discovery_interval_h", "notes", "last_run_ts", "next_run_ts",
                   "last_discovery_ts", "last_error"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "config":
                value = _dump(value)
            elif key == "enabled":
                value = 1 if value else 0
            sets.append(f"{key} = ?")
            params.append(value)
        if sets:
            sets.append("updated_ts = ?")
            params.append(self.clock())
            self.db.execute(f"UPDATE watchers SET {', '.join(sets)} WHERE id = ?", params + [watcher_id])
        return self.watcher(watcher_id)

    def delete_watcher(self, watcher_id: str) -> None:
        self.watcher(watcher_id)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM events WHERE watcher_id = ?", (watcher_id,))
            conn.execute("DELETE FROM watchers WHERE id = ?", (watcher_id,))

    # =========================================================================== targets
    def targets(self, *, watcher_id: Optional[str] = None, status: Optional[str] = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM targets WHERE 1=1", []
        if watcher_id:
            sql += " AND watcher_id = ?"
            params.append(watcher_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        return [row_dict("targets", r) for r in self.db.query(sql + " ORDER BY created_ts", params)]

    def target(self, target_id: str) -> dict[str, Any]:
        row = row_dict("targets", self.db.one("SELECT * FROM targets WHERE id = ?", (target_id,)))
        if row is None:
            raise TantalusError("not_found", f"Target {target_id} does not exist.", "List them with target_list.")
        return row

    def target_by_url(self, watcher_id: str, url: str) -> Optional[dict[str, Any]]:
        return row_dict("targets", self.db.one("SELECT * FROM targets WHERE watcher_id = ? AND url = ?", (watcher_id, url)))

    def create_target(self, watcher_id: str, url: str, **fields: Any) -> dict[str, Any]:
        self.watcher(watcher_id)
        url = url.strip()
        existing = self.target_by_url(watcher_id, url)
        if existing:
            return existing
        seller_policy = fields.get("seller_policy") or "retail_only"
        if seller_policy not in SELLER_POLICIES:
            raise TantalusError("invalid", f"Unknown seller policy {seller_policy!r}.", f"Use one of: {', '.join(SELLER_POLICIES)}.")
        now = self.clock()
        tid = new_id("t")
        self.db.execute(
            "INSERT INTO targets(id, watcher_id, label, url, host, adapter, retailer, sku, ean, product_type, store_ids, source_level,"
            " msrp, price_ceiling, price_threshold, seller_policy, fetch_tier, interval_min, status, created_ts, next_check_ts, extra)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, watcher_id, fields.get("label") or "", url, fields.get("host") or host_of(url), fields.get("adapter") or "auto",
             fields.get("retailer") or "", fields.get("sku") or "", fields.get("ean") or "", fields.get("product_type") or "",
             _dump(fields.get("store_ids") or []), int(fields.get("source_level") or 1), fields.get("msrp"),
             fields.get("price_ceiling"), fields.get("price_threshold"), seller_policy, fields.get("fetch_tier") or "auto",
             fields.get("interval_min"), "active", now, now, _dump(fields.get("extra") or {})))
        return self.target(tid)

    def update_target(self, target_id: str, **fields: Any) -> dict[str, Any]:
        self.target(target_id)
        allowed = {"label", "url", "adapter", "retailer", "sku", "ean", "product_type", "store_ids", "source_level", "msrp",
                   "price_ceiling", "price_threshold", "seller_policy", "fetch_tier", "interval_min", "status", "last_check_ts",
                   "next_check_ts", "last_state", "last_price", "last_currency", "last_confidence", "last_title", "last_image",
                   "last_error", "fail_count", "min_price", "extra", "host"}
        if "status" in fields and fields["status"] not in TARGET_STATUSES:
            raise TantalusError("invalid", f"Unknown target status {fields['status']!r}.", f"Use one of: {', '.join(TARGET_STATUSES)}.")
        if "seller_policy" in fields and fields["seller_policy"] not in SELLER_POLICIES:
            raise TantalusError("invalid", f"Unknown seller policy {fields['seller_policy']!r}.")
        if "url" in fields and "host" not in fields:
            fields["host"] = host_of(fields["url"])
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("store_ids", "extra"):
                value = _dump(value)
            sets.append(f"{key} = ?")
            params.append(value)
        if sets:
            self.db.execute(f"UPDATE targets SET {', '.join(sets)} WHERE id = ?", params + [target_id])
        return self.target(target_id)

    def delete_target(self, target_id: str) -> None:
        self.target(target_id)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM events WHERE target_id = ?", (target_id,))
            conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))

    def due_targets(self, now: float, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT t.* FROM targets t JOIN watchers w ON w.id = t.watcher_id"
            " WHERE w.enabled = 1 AND t.status IN ('active', 'needs_human', 'error') AND (t.next_check_ts IS NULL OR t.next_check_ts <= ?)"
            " ORDER BY t.next_check_ts LIMIT ?", (now, limit))
        return [row_dict("targets", r) for r in rows]

    # =========================================================================== observations
    def add_observation(self, target_id: str, obs: dict[str, Any]) -> dict[str, Any]:
        cur = self.db.execute(
            "INSERT INTO observations(target_id, checked_at, source_url, tier, http_status, availability, price, currency, seller,"
            " seller_is_retailer, buy_button, store_availability, preorder_date, restock_date, evidence, confidence, factors, method,"
            " raw_hash, blocked_reason, error, is_revalidation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (target_id, obs.get("checked_at") or self.clock(), obs.get("source_url") or "", obs.get("tier") or "",
             int(obs.get("http_status") or 0), obs.get("availability") or UNKNOWN, obs.get("price"), obs.get("currency") or "",
             obs.get("seller") or "", _b(obs.get("seller_is_retailer")), _b(obs.get("buy_button")),
             _dump(obs.get("store_availability") or {}), obs.get("preorder_date") or "", obs.get("restock_date") or "",
             _dump(obs.get("evidence") or []), int(obs.get("confidence") or 0), _dump(obs.get("factors") or []),
             obs.get("method") or "", obs.get("raw_hash") or "", obs.get("blocked_reason") or "", obs.get("error") or "",
             1 if obs.get("is_revalidation") else 0))
        return row_dict("observations", self.db.one("SELECT * FROM observations WHERE id = ?", (cur.lastrowid,)))

    def observations(self, target_id: str, *, limit: int = 50, since: Optional[float] = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM observations WHERE target_id = ?", [target_id]
        if since is not None:
            sql += " AND checked_at >= ?"
            params.append(since)
        rows = self.db.query(sql + " ORDER BY checked_at DESC, id DESC LIMIT ?", params + [limit])
        return [row_dict("observations", r) for r in rows]

    def price_history(self, target_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT checked_at, price, currency, availability FROM observations WHERE target_id = ? AND price IS NOT NULL"
                             " ORDER BY checked_at DESC LIMIT ?", (target_id, limit))
        return [dict(r) for r in reversed(rows)]

    # =========================================================================== events
    def recent_event(self, dedupe_key: str, since: float) -> Optional[dict[str, Any]]:
        return row_dict("events", self.db.one("SELECT * FROM events WHERE dedupe_key = ? AND detected_at >= ? AND status != 'dismissed'"
                                              " ORDER BY detected_at DESC LIMIT 1", (dedupe_key, since)))

    def last_event_of(self, target_id: str, types: Iterable[str], since: float) -> Optional[dict[str, Any]]:
        types = list(types)
        marks = ",".join("?" * len(types))
        return row_dict("events", self.db.one(
            f"SELECT * FROM events WHERE target_id = ? AND type IN ({marks}) AND detected_at >= ? AND status != 'dismissed'"
            " ORDER BY detected_at DESC LIMIT 1", [target_id, *types, since]))

    def add_event(self, ev: dict[str, Any]) -> dict[str, Any]:
        eid = new_id("e")
        self.db.execute(
            "INSERT INTO events(id, watcher_id, target_id, listing_id, info_id, candidate_id, type, severity, status, old_state, new_state,"
            " price, old_price, currency, confidence, dedupe_key, url, title, summary, data, detected_at, revalidate_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, ev.get("watcher_id") or "", ev.get("target_id") or "", ev.get("listing_id") or "", ev.get("info_id") or "",
             ev.get("candidate_id") or "", ev["type"], ev.get("severity") or "medium", ev.get("status") or "confirmed",
             ev.get("old_state") or "", ev.get("new_state") or "", ev.get("price"), ev.get("old_price"), ev.get("currency") or "",
             int(ev.get("confidence") or 0), ev.get("dedupe_key") or eid, ev.get("url") or "", ev.get("title") or "",
             ev.get("summary") or "", _dump(ev.get("data") or {}), ev.get("detected_at") or self.clock(), ev.get("revalidate_at")))
        return self.event(eid)

    def event(self, event_id: str) -> dict[str, Any]:
        row = row_dict("events", self.db.one("SELECT * FROM events WHERE id = ?", (event_id,)))
        if row is None:
            raise TantalusError("not_found", f"Event {event_id} does not exist.")
        return row

    def update_event(self, event_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "confidence", "summary", "seen", "notified", "revalidate_at", "data", "severity"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "data":
                value = _dump(value)
            elif key in ("seen", "notified"):
                value = 1 if value else 0
            sets.append(f"{key} = ?")
            params.append(value)
        if sets:
            self.db.execute(f"UPDATE events SET {', '.join(sets)} WHERE id = ?", params + [event_id])
        return self.event(event_id)

    def events(self, *, watcher_id: Optional[str] = None, target_id: Optional[str] = None, types: Optional[list[str]] = None,
               statuses: Optional[list[str]] = None, since: Optional[float] = None, unseen: Optional[bool] = None,
               limit: int = 50, before: Optional[float] = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM events WHERE 1=1", []
        for col, value in (("watcher_id", watcher_id), ("target_id", target_id)):
            if value:
                sql += f" AND {col} = ?"
                params.append(value)
        for col, values in (("type", types), ("status", statuses)):
            if values:
                sql += f" AND {col} IN ({','.join('?' * len(values))})"
                params.extend(values)
        if since is not None:
            sql += " AND detected_at > ?"
            params.append(since)
        if before is not None:
            sql += " AND detected_at < ?"
            params.append(before)
        if unseen is not None:
            sql += " AND seen = ?"
            params.append(0 if unseen else 1)
        rows = self.db.query(sql + " ORDER BY detected_at DESC LIMIT ?", params + [limit])
        return [row_dict("events", r) for r in rows]

    def pending_revalidations(self, now: float) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM events WHERE status = 'pending' AND revalidate_at IS NOT NULL AND revalidate_at <= ?"
                             " ORDER BY revalidate_at LIMIT 20", (now,))
        return [row_dict("events", r) for r in rows]

    def mark_seen(self, *, before: Optional[float] = None, event_ids: Optional[list[str]] = None) -> int:
        if event_ids:
            marks = ",".join("?" * len(event_ids))
            cur = self.db.execute(f"UPDATE events SET seen = 1 WHERE id IN ({marks})", event_ids)
        else:
            cur = self.db.execute("UPDATE events SET seen = 1 WHERE seen = 0 AND detected_at <= ?", (before or self.clock(),))
        return cur.rowcount

    def record_notification(self, event_id: str, channel: str, ok: bool, error: str = "") -> bool:
        """False when this event already went out on this channel (UNIQUE)."""
        try:
            self.db.execute("INSERT INTO notifications(event_id, channel, sent_at, ok, error) VALUES (?,?,?,?,?)",
                            (event_id, channel, self.clock(), 1 if ok else 0, error[:300]))
            return True
        except Exception:  # noqa: BLE001 — unique violation
            return False

    def notified_channels(self, event_id: str) -> set[str]:
        return {r["channel"] for r in self.db.query("SELECT channel FROM notifications WHERE event_id = ? AND ok = 1", (event_id,))}

    def notifications(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM notifications ORDER BY sent_at DESC LIMIT ?", (limit,))]

    # =========================================================================== listings
    def listings(self, *, watcher_id: Optional[str] = None, relevant: Optional[bool] = None, statuses: Optional[list[str]] = None,
                 min_score: Optional[float] = None, since: Optional[float] = None, limit: int = 60, order: str = "recent") -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM listings WHERE 1=1", []
        if watcher_id:
            sql += " AND watcher_id = ?"
            params.append(watcher_id)
        if relevant is not None:
            sql += " AND relevant = ?"
            params.append(1 if relevant else 0)
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        if min_score is not None:
            sql += " AND score >= ?"
            params.append(min_score)
        if since is not None:
            sql += " AND first_seen_ts > ?"
            params.append(since)
        sql += " ORDER BY score DESC, first_seen_ts DESC" if order == "score" else " ORDER BY first_seen_ts DESC"
        return [row_dict("listings", r) for r in self.db.query(sql + " LIMIT ?", params + [limit])]

    def listing(self, listing_id: str) -> dict[str, Any]:
        row = row_dict("listings", self.db.one("SELECT * FROM listings WHERE id = ?", (listing_id,)))
        if row is None:
            raise TantalusError("not_found", f"Listing {listing_id} does not exist.")
        return row

    def listing_rows_for_dedupe(self, watcher_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT id, source, external_id, url, title, location_text, price FROM listings WHERE watcher_id = ?", (watcher_id,))]

    def insert_listing(self, watcher_id: str, data: dict[str, Any]) -> dict[str, Any]:
        lid = new_id("l")
        now = self.clock()
        cols = ["id", "watcher_id", "source", "external_id", "url", "title", "description", "price", "currency", "price_raw",
                "location_text", "distance_km", "image_url", "seller", "shipping", "reserved", "score", "relevant", "signals",
                "reason", "category", "method", "quantity_min", "quantity_max", "matched_query", "first_seen_ts", "last_seen_ts",
                "status", "extra"]
        values = [lid, watcher_id, data["source"], data.get("external_id") or "", data["url"], data.get("title") or "",
                  data.get("description") or "", data.get("price"), data.get("currency") or "EUR", data.get("price_raw") or "",
                  data.get("location_text") or "", data.get("distance_km"), data.get("image_url") or "", data.get("seller") or "",
                  _b(data.get("shipping")), _b(data.get("reserved")), float(data.get("score") or 0), 1 if data.get("relevant") else 0,
                  _dump(data.get("signals") or []), data.get("reason") or "", data.get("category") or "", data.get("method") or "rules",
                  data.get("quantity_min"), data.get("quantity_max"), data.get("matched_query") or "", now, now,
                  data.get("status") or "new", _dump(data.get("extra") or {})]
        self.db.execute(f"INSERT INTO listings({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", values)
        if data.get("price") is not None:
            self.db.execute("INSERT INTO listing_prices(listing_id, price, recorded_ts) VALUES (?,?,?)", (lid, data.get("price"), now))
        return self.listing(lid)

    def touch_listing(self, listing_id: str, *, price: Optional[float], reserved: Optional[bool] = None) -> dict[str, Any]:
        now = self.clock()
        old = self.listing(listing_id)
        self.db.execute("UPDATE listings SET last_seen_ts = ?, gone_ts = NULL, reserved = COALESCE(?, reserved) WHERE id = ?",
                        (now, _b(reserved), listing_id))
        if price is not None and old.get("price") != price:
            self.db.execute("UPDATE listings SET price = ? WHERE id = ?", (price, listing_id))
            self.db.execute("INSERT INTO listing_prices(listing_id, price, recorded_ts) VALUES (?,?,?)", (listing_id, price, now))
        return self.listing(listing_id)

    def set_listing_status(self, listing_id: str, status: str) -> dict[str, Any]:
        if status not in ("new", "seen", "saved", "dismissed", "gone"):
            raise TantalusError("invalid", f"Unknown listing status {status!r}.", "Use new, seen, saved, dismissed or gone.")
        self.listing(listing_id)
        self.db.execute("UPDATE listings SET status = ? WHERE id = ?", (status, listing_id))
        return self.listing(listing_id)

    def listing_prices(self, listing_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT price, recorded_ts FROM listing_prices WHERE listing_id = ? ORDER BY recorded_ts",
                                               (listing_id,))]

    # =========================================================================== information
    def info_sources(self, watcher_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM info_sources WHERE watcher_id = ? ORDER BY created_ts", (watcher_id,))]

    def sync_info_sources(self, watcher_id: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Make info_sources match the watcher config (kind+value), keeping the state of those that stay."""
        wanted = {(s.get("kind"), str(s.get("value") or "").strip()): s for s in sources if s.get("kind") in ("page", "feed", "search")
                  and str(s.get("value") or "").strip()}
        existing = {(r["kind"], r["value"]): r for r in self.info_sources(watcher_id)}
        with self.db.transaction() as conn:
            for key, row in existing.items():
                if key not in wanted:
                    conn.execute("DELETE FROM info_sources WHERE id = ?", (row["id"],))
            for key, spec in wanted.items():
                if key in existing:
                    conn.execute("UPDATE info_sources SET label = ?, source_level = ? WHERE id = ?",
                                 (spec.get("label") or "", int(spec.get("source_level") or 3), existing[key]["id"]))
                else:
                    conn.execute("INSERT INTO info_sources(id, watcher_id, kind, value, label, source_level, created_ts) VALUES (?,?,?,?,?,?,?)",
                                 (new_id("s"), watcher_id, key[0], key[1], spec.get("label") or "", int(spec.get("source_level") or 3), self.clock()))
        return self.info_sources(watcher_id)

    def update_info_source(self, source_id: str, update: dict[str, Any]) -> None:
        allowed = {"last_hash", "last_text", "etag", "last_modified", "last_check_ts", "last_error"}
        sets = [(k, v) for k, v in update.items() if k in allowed]
        if sets:
            self.db.execute(f"UPDATE info_sources SET {', '.join(k + ' = ?' for k, _ in sets)} WHERE id = ?",
                            [v for _, v in sets] + [source_id])

    def insert_info_item(self, watcher_id: str, source_id: str, f: Any) -> Optional[dict[str, Any]]:
        """None when the same content was already recorded for this watcher."""
        iid = new_id("i")
        cur = self.db.execute(
            "INSERT OR IGNORE INTO info_items(id, watcher_id, source_id, kind, url, title, snippet, diff, content_hash, published, verdict,"
            " material, score, reason, method, source_level, first_seen_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, watcher_id, source_id, f.kind, f.url, f.title or "", f.snippet or "", f.diff or "", f.content_hash, f.published or "",
             f.verdict, 1 if f.material else 0, float(f.score or 0), f.reason or "", f.method or "rules", int(f.source_level or 5),
             self.clock()))
        if cur.rowcount == 0:
            return None
        return row_dict("info_items", self.db.one("SELECT * FROM info_items WHERE id = ?", (iid,)))

    def info_items(self, *, watcher_id: Optional[str] = None, material: Optional[bool] = None, since: Optional[float] = None,
                   limit: int = 60) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM info_items WHERE 1=1", []
        if watcher_id:
            sql += " AND watcher_id = ?"
            params.append(watcher_id)
        if material is not None:
            sql += " AND material = ?"
            params.append(1 if material else 0)
        if since is not None:
            sql += " AND first_seen_ts > ?"
            params.append(since)
        return [row_dict("info_items", r) for r in self.db.query(sql + " ORDER BY first_seen_ts DESC LIMIT ?", params + [limit])]

    def set_info_status(self, item_id: str, status: str) -> None:
        self.db.execute("UPDATE info_items SET status = ? WHERE id = ?", (status, item_id))

    # =========================================================================== candidates
    def candidates(self, *, watcher_id: Optional[str] = None, status: Optional[str] = "proposed", limit: int = 100) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM candidates WHERE 1=1", []
        if watcher_id:
            sql += " AND watcher_id = ?"
            params.append(watcher_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        return [dict(r) for r in self.db.query(sql + " ORDER BY score DESC, found_ts DESC LIMIT ?", params + [limit])]

    def candidate(self, candidate_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
        if row is None:
            raise TantalusError("not_found", f"Candidate {candidate_id} does not exist.")
        return dict(row)

    def known_urls(self, watcher_id: str) -> set[str]:
        urls = {r["url"] for r in self.db.query("SELECT url FROM candidates WHERE watcher_id = ?", (watcher_id,))}
        urls |= {r["url"] for r in self.db.query("SELECT url FROM targets WHERE watcher_id = ?", (watcher_id,))}
        return urls

    def insert_candidate(self, watcher_id: str, c: dict[str, Any]) -> Optional[dict[str, Any]]:
        cid = new_id("c")
        cur = self.db.execute(
            "INSERT OR IGNORE INTO candidates(id, watcher_id, url, host, title, snippet, source_level, retailer, engine, query, score, reason, found_ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, watcher_id, c["url"], c.get("host") or host_of(c["url"]), c.get("title") or "", c.get("snippet") or "",
             int(c.get("source_level") or 5), c.get("retailer") or "", c.get("engine") or "", c.get("query") or "",
             float(c.get("score") or 0), c.get("reason") or "", self.clock()))
        return self.candidate(cid) if cur.rowcount else None

    def set_candidate(self, candidate_id: str, status: str, target_id: str = "") -> dict[str, Any]:
        self.db.execute("UPDATE candidates SET status = ?, target_id = ? WHERE id = ?", (status, target_id, candidate_id))
        return self.candidate(candidate_id)

    # =========================================================================== runs
    def start_run(self, kind: str, *, watcher_id: str = "", target_id: str = "") -> int:
        cur = self.db.execute("INSERT INTO runs(kind, watcher_id, target_id, started_ts) VALUES (?,?,?,?)",
                              (kind, watcher_id, target_id, self.clock()))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, ok: bool, summary: dict[str, Any]) -> None:
        self.db.execute("UPDATE runs SET finished_ts = ?, ok = ?, summary = ? WHERE id = ?",
                        (self.clock(), 1 if ok else 0, _dump(summary), run_id))
        # keep the table bounded
        self.db.execute("DELETE FROM runs WHERE id <= (SELECT MAX(id) FROM runs) - 5000")

    def runs(self, *, limit: int = 50, watcher_id: str = "") -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM runs", []
        if watcher_id:
            sql += " WHERE watcher_id = ?"
            params.append(watcher_id)
        rows = self.db.query(sql + " ORDER BY id DESC LIMIT ?", params + [limit])
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["summary"] = json.loads(d["summary"])
            except ValueError:
                pass
            out.append(d)
        return out

    def counts(self) -> dict[str, int]:
        def n(sql: str) -> int:
            return int(self.db.one(sql)[0])
        return {
            "watchers": n("SELECT COUNT(*) FROM watchers"),
            "targets": n("SELECT COUNT(*) FROM targets"),
            "observations": n("SELECT COUNT(*) FROM observations"),
            "events": n("SELECT COUNT(*) FROM events"),
            "unseen": n("SELECT COUNT(*) FROM events WHERE seen = 0 AND status = 'confirmed'"),
            "listings": n("SELECT COUNT(*) FROM listings"),
            "info_items": n("SELECT COUNT(*) FROM info_items"),
            "candidates": n("SELECT COUNT(*) FROM candidates WHERE status = 'proposed'"),
            "needs_human": n("SELECT COUNT(*) FROM targets WHERE status = 'needs_human'"),
            "mail_deals": n("SELECT COUNT(*) FROM mail_deals WHERE status = 'active'"),
        }


def _b(value: Any) -> Optional[int]:
    return None if value is None else (1 if value else 0)
