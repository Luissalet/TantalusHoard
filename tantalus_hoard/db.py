"""SQLite connection (WAL) and ordered schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1: settings, watchers, targets, observations, events, notifications
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE watchers (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      mode TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      config TEXT NOT NULL DEFAULT '{}',
      interval_min INTEGER NOT NULL DEFAULT 30,
      discovery_interval_h INTEGER NOT NULL DEFAULT 24,
      notes TEXT NOT NULL DEFAULT '',
      created_ts REAL NOT NULL,
      updated_ts REAL NOT NULL,
      last_run_ts REAL,
      next_run_ts REAL,
      last_discovery_ts REAL,
      last_error TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE targets (
      id TEXT PRIMARY KEY,
      watcher_id TEXT NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
      label TEXT NOT NULL DEFAULT '',
      url TEXT NOT NULL,
      host TEXT NOT NULL DEFAULT '',
      adapter TEXT NOT NULL DEFAULT 'auto',
      retailer TEXT NOT NULL DEFAULT '',
      sku TEXT NOT NULL DEFAULT '',
      ean TEXT NOT NULL DEFAULT '',
      product_type TEXT NOT NULL DEFAULT '',
      store_ids TEXT NOT NULL DEFAULT '[]',
      source_level INTEGER NOT NULL DEFAULT 1,
      msrp REAL,
      price_ceiling REAL,
      price_threshold REAL,
      seller_policy TEXT NOT NULL DEFAULT 'retail_only',
      fetch_tier TEXT NOT NULL DEFAULT 'auto',
      interval_min INTEGER,
      status TEXT NOT NULL DEFAULT 'active',
      created_ts REAL NOT NULL,
      last_check_ts REAL,
      next_check_ts REAL,
      last_state TEXT NOT NULL DEFAULT 'UNKNOWN',
      last_price REAL,
      last_currency TEXT NOT NULL DEFAULT '',
      last_confidence INTEGER NOT NULL DEFAULT 0,
      last_title TEXT NOT NULL DEFAULT '',
      last_image TEXT NOT NULL DEFAULT '',
      last_error TEXT NOT NULL DEFAULT '',
      fail_count INTEGER NOT NULL DEFAULT 0,
      min_price REAL,
      extra TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX targets_watcher ON targets(watcher_id);
    CREATE INDEX targets_due ON targets(status, next_check_ts);
    CREATE TABLE observations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
      checked_at REAL NOT NULL,
      source_url TEXT NOT NULL DEFAULT '',
      tier TEXT NOT NULL DEFAULT '',
      http_status INTEGER NOT NULL DEFAULT 0,
      availability TEXT NOT NULL DEFAULT 'UNKNOWN',
      price REAL,
      currency TEXT NOT NULL DEFAULT '',
      seller TEXT NOT NULL DEFAULT '',
      seller_is_retailer INTEGER,
      buy_button INTEGER,
      store_availability TEXT NOT NULL DEFAULT '{}',
      preorder_date TEXT NOT NULL DEFAULT '',
      restock_date TEXT NOT NULL DEFAULT '',
      evidence TEXT NOT NULL DEFAULT '[]',
      confidence INTEGER NOT NULL DEFAULT 0,
      factors TEXT NOT NULL DEFAULT '[]',
      method TEXT NOT NULL DEFAULT '',
      raw_hash TEXT NOT NULL DEFAULT '',
      blocked_reason TEXT NOT NULL DEFAULT '',
      error TEXT NOT NULL DEFAULT '',
      is_revalidation INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX observations_target ON observations(target_id, checked_at);
    CREATE TABLE events (
      id TEXT PRIMARY KEY,
      watcher_id TEXT NOT NULL DEFAULT '',
      target_id TEXT NOT NULL DEFAULT '',
      listing_id TEXT NOT NULL DEFAULT '',
      info_id TEXT NOT NULL DEFAULT '',
      candidate_id TEXT NOT NULL DEFAULT '',
      type TEXT NOT NULL,
      severity TEXT NOT NULL DEFAULT 'medium',
      status TEXT NOT NULL DEFAULT 'confirmed',
      old_state TEXT NOT NULL DEFAULT '',
      new_state TEXT NOT NULL DEFAULT '',
      price REAL,
      old_price REAL,
      currency TEXT NOT NULL DEFAULT '',
      confidence INTEGER NOT NULL DEFAULT 0,
      dedupe_key TEXT NOT NULL,
      url TEXT NOT NULL DEFAULT '',
      title TEXT NOT NULL DEFAULT '',
      summary TEXT NOT NULL DEFAULT '',
      data TEXT NOT NULL DEFAULT '{}',
      detected_at REAL NOT NULL,
      revalidate_at REAL,
      seen INTEGER NOT NULL DEFAULT 0,
      notified INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX events_detected ON events(detected_at);
    CREATE INDEX events_dedupe ON events(dedupe_key, detected_at);
    CREATE INDEX events_status ON events(status, revalidate_at);
    CREATE TABLE notifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL,
      channel TEXT NOT NULL,
      sent_at REAL NOT NULL,
      ok INTEGER NOT NULL,
      error TEXT NOT NULL DEFAULT '',
      UNIQUE(event_id, channel)
    );
    CREATE TABLE host_state (
      host TEXT PRIMARY KEY,
      last_fetch_ts REAL,
      min_interval_s REAL NOT NULL DEFAULT 20,
      blocked_until_ts REAL,
      block_reason TEXT NOT NULL DEFAULT '',
      preferred_tier TEXT NOT NULL DEFAULT '',
      robots_txt TEXT,
      robots_fetched_ts REAL,
      ok_count INTEGER NOT NULL DEFAULT 0,
      fail_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      kind TEXT NOT NULL,
      watcher_id TEXT NOT NULL DEFAULT '',
      target_id TEXT NOT NULL DEFAULT '',
      started_ts REAL NOT NULL,
      finished_ts REAL,
      ok INTEGER NOT NULL DEFAULT 0,
      summary TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX runs_started ON runs(started_ts);
    """,
    # 2: second-hand listings, information sentries, discovery candidates
    """
    CREATE TABLE listings (
      id TEXT PRIMARY KEY,
      watcher_id TEXT NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
      source TEXT NOT NULL,
      external_id TEXT NOT NULL DEFAULT '',
      url TEXT NOT NULL,
      title TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '',
      price REAL,
      currency TEXT NOT NULL DEFAULT 'EUR',
      price_raw TEXT NOT NULL DEFAULT '',
      location_text TEXT NOT NULL DEFAULT '',
      distance_km REAL,
      image_url TEXT NOT NULL DEFAULT '',
      seller TEXT NOT NULL DEFAULT '',
      shipping INTEGER,
      reserved INTEGER,
      score REAL NOT NULL DEFAULT 0,
      relevant INTEGER NOT NULL DEFAULT 0,
      signals TEXT NOT NULL DEFAULT '[]',
      reason TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT '',
      method TEXT NOT NULL DEFAULT 'rules',
      quantity_min INTEGER,
      quantity_max INTEGER,
      matched_query TEXT NOT NULL DEFAULT '',
      first_seen_ts REAL NOT NULL,
      last_seen_ts REAL NOT NULL,
      gone_ts REAL,
      status TEXT NOT NULL DEFAULT 'new',
      extra TEXT NOT NULL DEFAULT '{}'
    );
    CREATE UNIQUE INDEX listings_source_ext ON listings(watcher_id, source, external_id);
    CREATE INDEX listings_score ON listings(watcher_id, relevant, score);
    CREATE TABLE listing_prices (
      listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
      price REAL,
      recorded_ts REAL NOT NULL
    );
    CREATE INDEX listing_prices_listing ON listing_prices(listing_id, recorded_ts);
    CREATE TABLE info_sources (
      id TEXT PRIMARY KEY,
      watcher_id TEXT NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      value TEXT NOT NULL,
      label TEXT NOT NULL DEFAULT '',
      source_level INTEGER NOT NULL DEFAULT 3,
      last_hash TEXT NOT NULL DEFAULT '',
      last_text TEXT NOT NULL DEFAULT '',
      etag TEXT NOT NULL DEFAULT '',
      last_modified TEXT NOT NULL DEFAULT '',
      last_check_ts REAL,
      last_error TEXT NOT NULL DEFAULT '',
      created_ts REAL NOT NULL
    );
    CREATE TABLE info_items (
      id TEXT PRIMARY KEY,
      watcher_id TEXT NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
      source_id TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL,
      url TEXT NOT NULL,
      title TEXT NOT NULL DEFAULT '',
      snippet TEXT NOT NULL DEFAULT '',
      diff TEXT NOT NULL DEFAULT '',
      content_hash TEXT NOT NULL,
      published TEXT NOT NULL DEFAULT '',
      verdict TEXT NOT NULL DEFAULT 'unknown',
      material INTEGER NOT NULL DEFAULT 0,
      score REAL NOT NULL DEFAULT 0,
      reason TEXT NOT NULL DEFAULT '',
      method TEXT NOT NULL DEFAULT 'rules',
      source_level INTEGER NOT NULL DEFAULT 5,
      first_seen_ts REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'new'
    );
    CREATE UNIQUE INDEX info_items_hash ON info_items(watcher_id, content_hash);
    CREATE TABLE candidates (
      id TEXT PRIMARY KEY,
      watcher_id TEXT NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
      url TEXT NOT NULL,
      host TEXT NOT NULL DEFAULT '',
      title TEXT NOT NULL DEFAULT '',
      snippet TEXT NOT NULL DEFAULT '',
      source_level INTEGER NOT NULL DEFAULT 5,
      retailer TEXT NOT NULL DEFAULT '',
      engine TEXT NOT NULL DEFAULT '',
      query TEXT NOT NULL DEFAULT '',
      score REAL NOT NULL DEFAULT 0,
      reason TEXT NOT NULL DEFAULT '',
      found_ts REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'proposed',
      target_id TEXT NOT NULL DEFAULT ''
    );
    CREATE UNIQUE INDEX candidates_url ON candidates(watcher_id, url);
    """,
    # 3: sale mails read from the mailbox (deals) and the ids of the mails already looked at
    """
    CREATE TABLE mail_deals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      message_id TEXT NOT NULL,
      item_key TEXT NOT NULL,
      store_id TEXT NOT NULL DEFAULT '',
      store TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT 'campaign',
      title TEXT NOT NULL DEFAULT '',
      titles TEXT NOT NULL DEFAULT '[]',
      discount_pct INTEGER,
      up_to INTEGER NOT NULL DEFAULT 0,
      price REAL,
      old_price REAL,
      currency TEXT NOT NULL DEFAULT '',
      ends_ts REAL,
      ends_known INTEGER NOT NULL DEFAULT 0,
      expires_ts REAL,
      url TEXT NOT NULL DEFAULT '',
      subject TEXT NOT NULL DEFAULT '',
      sender_domain TEXT NOT NULL DEFAULT '',
      mail_ts REAL,
      first_seen_ts REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      wishlist INTEGER NOT NULL DEFAULT 0,
      matched INTEGER NOT NULL DEFAULT 0,
      match_source TEXT NOT NULL DEFAULT '',
      match_label TEXT NOT NULL DEFAULT '',
      owned INTEGER NOT NULL DEFAULT 0,
      quiet INTEGER NOT NULL DEFAULT 0,
      notified INTEGER NOT NULL DEFAULT 0,
      event_id TEXT NOT NULL DEFAULT '',
      UNIQUE(message_id, item_key)
    );
    CREATE INDEX mail_deals_status ON mail_deals(status, expires_ts);
    CREATE INDEX mail_deals_matched ON mail_deals(matched, mail_ts);
    CREATE TABLE mail_seen (
      message_id TEXT PRIMARY KEY,
      seen_ts REAL NOT NULL,
      deals INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 4: the aggregator radar (shop offers seen on stock aggregators, release calendar, page fetch times)
    """
CREATE TABLE radar_offers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  watcher_id TEXT NOT NULL,
  okey TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  store TEXT NOT NULL DEFAULT '',
  store_slug TEXT NOT NULL DEFAULT '',
  chain INTEGER NOT NULL DEFAULT 0,
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  product_key TEXT NOT NULL DEFAULT '',
  release TEXT NOT NULL DEFAULT '',
  set_name TEXT NOT NULL DEFAULT '',
  fmt TEXT NOT NULL DEFAULT '',
  lang TEXT NOT NULL DEFAULT '',
  image TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'UNKNOWN',
  price REAL,
  currency TEXT NOT NULL DEFAULT 'EUR',
  first_seen_ts REAL NOT NULL,
  last_seen_ts REAL NOT NULL,
  last_change_ts REAL NOT NULL,
  last_buyable_ts REAL,
  extra TEXT NOT NULL DEFAULT '{}',
  UNIQUE(watcher_id, okey)
);
CREATE INDEX radar_offers_watcher ON radar_offers(watcher_id, state, last_change_ts);
CREATE TABLE radar_releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  watcher_id TEXT NOT NULL,
  rkey TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  slug TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '{}',
  first_seen_ts REAL NOT NULL,
  last_seen_ts REAL NOT NULL,
  detail_ts REAL,
  alerted TEXT NOT NULL DEFAULT '[]',
  UNIQUE(watcher_id, rkey)
);
CREATE TABLE radar_pages (
  url TEXT PRIMARY KEY,
  last_fetch_ts REAL NOT NULL,
  ok INTEGER NOT NULL DEFAULT 1,
  error TEXT NOT NULL DEFAULT ''
);
    """,
]


class Database:
    """One connection shared by every thread, guarded by a re-entrant lock.

    The app is the only writer; the MCP bridge never opens this file.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for index, sql in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                script = f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({index});\nCOMMIT;"
                try:
                    self.conn.executescript(script)
                except Exception:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise

    def version(self) -> int:
        row = self.one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"] or 0)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def transaction(self):
        """`with db.transaction():` — BEGIN IMMEDIATE / COMMIT (ROLLBACK on error) under the lock."""
        return _Transaction(self)

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.conn.close()


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.conn.execute("COMMIT")
            else:
                self.db.conn.execute("ROLLBACK")
        finally:
            self.db.lock.release()
        return False
