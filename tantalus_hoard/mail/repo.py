"""Storage of the mail deals (``mail_deals``) and of the mails already looked at (``mail_seen``)."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from ..db import Database

STATUSES = ("active", "expired", "dismissed")
COLUMNS = ("message_id", "item_key", "store_id", "store", "kind", "title", "titles", "discount_pct", "up_to", "price", "old_price",
           "currency", "ends_ts", "ends_known", "expires_ts", "url", "subject", "sender_domain", "mail_ts", "first_seen_ts", "status",
           "wishlist", "matched", "match_source", "match_label", "owned", "quiet", "notified", "event_id")
UPDATABLE = {"status", "wishlist", "matched", "match_source", "match_label", "owned", "quiet", "notified", "event_id", "expires_ts"}
BOOLS = ("up_to", "ends_known", "wishlist", "matched", "owned", "quiet", "notified")


def _row(row: Any) -> dict[str, Any]:
    d = dict(row)
    try:
        d["titles"] = json.loads(d.get("titles") or "[]")
    except ValueError:
        d["titles"] = []
    for key in BOOLS:
        d[key] = bool(d.get(key))
    return d


class MailRepo:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db = db
        self.clock = clock

    # ------------------------------------------------------------------ mails already looked at
    def seen_ids(self, since: float = 0.0, limit: int = 20000) -> list[str]:
        return [r["message_id"] for r in self.db.query("SELECT message_id FROM mail_seen WHERE seen_ts >= ? ORDER BY seen_ts DESC LIMIT ?", (since, limit))]

    def mark_seen(self, message_id: str, deals: int = 0) -> None:
        if message_id:
            self.db.execute("INSERT INTO mail_seen(message_id, seen_ts, deals) VALUES (?,?,?) ON CONFLICT(message_id) DO UPDATE SET deals = excluded.deals",
                            (message_id, self.clock(), int(deals)))

    def seen_count(self) -> int:
        return int(self.db.one("SELECT COUNT(*) FROM mail_seen")[0])

    # ------------------------------------------------------------------ deals
    def add(self, deal: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Insert a deal; a deal of the same mail and item that already exists is returned untouched (``created`` False)."""
        values = {c: deal.get(c) for c in COLUMNS}
        values["titles"] = json.dumps(deal.get("titles") or [], ensure_ascii=False)
        values["first_seen_ts"] = deal.get("first_seen_ts") or self.clock()
        values["status"] = deal.get("status") or "active"
        for key in BOOLS:
            values[key] = 1 if deal.get(key) else 0
        for key in ("store_id", "store", "kind", "title", "currency", "url", "subject", "sender_domain", "match_source", "match_label", "event_id"):
            values[key] = values[key] or ""
        marks = ",".join("?" * len(COLUMNS))
        cur = self.db.execute(f"INSERT OR IGNORE INTO mail_deals({','.join(COLUMNS)}) VALUES ({marks})", [values[c] for c in COLUMNS])
        row = self.db.one("SELECT * FROM mail_deals WHERE message_id = ? AND item_key = ?", (values["message_id"], values["item_key"]))
        return _row(row), cur.rowcount > 0

    def get(self, deal_id: int) -> Optional[dict[str, Any]]:
        row = self.db.one("SELECT * FROM mail_deals WHERE id = ?", (int(deal_id),))
        return _row(row) if row else None

    def update(self, deal_id: int, **fields: Any) -> Optional[dict[str, Any]]:
        sets, params = [], []
        for key, value in fields.items():
            if key not in UPDATABLE:
                continue
            sets.append(f"{key} = ?")
            params.append((1 if value else 0) if key in BOOLS else value)
        if sets:
            self.db.execute(f"UPDATE mail_deals SET {', '.join(sets)} WHERE id = ?", [*params, int(deal_id)])
        return self.get(deal_id)

    def clear(self) -> dict[str, int]:
        """Forget every stored deal and every seen mail id (used to read the mailbox again from scratch)."""
        deals = self.db.execute("DELETE FROM mail_deals").rowcount
        seen = self.db.execute("DELETE FROM mail_seen").rowcount
        return {"deals": deals, "mails_seen": seen}

    def expire(self, now: Optional[float] = None) -> int:
        cur = self.db.execute("UPDATE mail_deals SET status = 'expired' WHERE status = 'active' AND expires_ts IS NOT NULL AND expires_ts < ?",
                              (now if now is not None else self.clock(),))
        return cur.rowcount

    def find(self, *, status: str = "", matched: Optional[bool] = None, store_id: str = "", kind: str = "", query: str = "",
             limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM mail_deals WHERE 1=1", []
        if status in STATUSES:
            sql += " AND status = ?"
            params.append(status)
        if matched is not None:
            sql += " AND matched = ?"
            params.append(1 if matched else 0)
        if store_id:
            sql += " AND store_id = ?"
            params.append(store_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if query.strip():
            sql += " AND (title LIKE ? OR titles LIKE ? OR subject LIKE ?)"
            like = f"%{query.strip()}%"
            params += [like, like, like]
        sql += " ORDER BY matched DESC, COALESCE(mail_ts, first_seen_ts) DESC, id DESC LIMIT ? OFFSET ?"
        params += [max(1, min(int(limit), 500)), max(0, int(offset))]
        return [_row(r) for r in self.db.query(sql, params)]

    def all_open(self) -> list[dict[str, Any]]:
        return [_row(r) for r in self.db.query("SELECT * FROM mail_deals WHERE status = 'active' ORDER BY id")]

    def counts(self) -> dict[str, int]:
        def n(sql: str) -> int:
            return int(self.db.one(sql)[0])
        return {"deals": n("SELECT COUNT(*) FROM mail_deals"), "active": n("SELECT COUNT(*) FROM mail_deals WHERE status = 'active'"),
                "matched": n("SELECT COUNT(*) FROM mail_deals WHERE status = 'active' AND matched = 1"),
                "mails_seen": n("SELECT COUNT(*) FROM mail_seen")}
