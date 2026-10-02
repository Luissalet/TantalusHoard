"""Wiring: database, fetch ladder, model, notifiers, search, sentries, engine and scheduler behind one object that
the API routers and the agent tools share."""

from __future__ import annotations

import json
import logging
import secrets as _secrets
import threading
import time
from typing import Any, Callable, Optional

import httpx

from . import SERVICE, __version__
from .config import Config
from .db import Database
from .engine import Engine
from .errors import TantalusError
from .fetch import Fetcher
from .info import InfoSentry
from .llm import LLM
from .mail.deals import DEFAULTS as MAIL_DEFAULTS, NUMERIC as MAIL_NUMERIC, MailDeals
from .mail.repo import MailRepo
from .mail.source import SOURCE_MODES, MailSource
from .model import BUYABLE, MODE_AVAILABILITY, MODE_INFORMATION, MODE_SECONDHAND, MODES
from .modelprobe import ModelProbe
from .budget import BudgetNote
from .notify import CHANNELS, EMAIL_BACKENDS, VIA_MODES, Notifier
from .presets import PRESETS, get_preset
from .radar.core import Radar
from .scheduler import Scheduler
from .search import WebSearch
from .store import Store

log = logging.getLogger("tantalus")

SECRET_NAMES = ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "NTFY_TOPIC", "NTFY_TOKEN", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
                "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO", "BRAVE_KEY", "SEARXNG_URL")
UI_SETTINGS = {
    "notify.language": ("es", "en"),
    "llm.enabled": ("1", "0"),
    "scheduler.paused": ("0", "1"),
    "notify.ntfy.server": None,
    "notify.via": VIA_MODES,
    "mail.source": SOURCE_MODES,
    "notify.email.backend": EMAIL_BACKENDS,
    "notify.email.faustus_dir": None,
    "notify.email.faustus_owner": None,
    **{f"notify.{c}.enabled": ("1", "0") for c in CHANNELS},
    **{f"notify.{c}.min_severity": ("low", "medium", "high") for c in CHANNELS},
    "mail.deals.enabled": ("1", "0"),
    **{key: None for key in MAIL_DEFAULTS if key != "mail.deals.enabled"},
}


def write_token(config: Config) -> str:
    """The MCP token is persistent: created once, reused on every later start."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        existing = config.token_path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if len(existing) >= 32:
        return existing
    token = _secrets.token_hex(32)
    config.token_path.write_text(token, encoding="utf-8")
    try:
        config.token_path.chmod(0o600)
    except OSError:
        pass
    return token


def write_url(config: Config) -> None:
    try:
        config.url_path.write_text(f"http://127.0.0.1:{config.port}", encoding="utf-8")
    except OSError:
        pass


class Services:
    def __init__(self, config: Config, *, link: Any = None, http_transport: Optional[httpx.BaseTransport] = None,
                 clock_fn: Callable[[], float] = time.time, sleep_fn: Callable[[float], None] = time.sleep,
                 notifier: Any = None, websearch: Any = None, browser: Any = None, install_presets: Optional[bool] = None):
        self.config = config
        self.clock = clock_fn
        self.started_at = time.time()
        for d in (config.data_dir, config.cache_dir, config.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.token = write_token(config)
        write_url(config)
        self.db = Database(config.db_path)
        self.store = Store(self.db, clock_fn)
        self._load_secrets()
        # model (optional)
        if link is not None:
            self.link_sync, self._link = link, None
        else:
            from .hoard_link.config import LinkConfig
            from .hoard_link.link import Link
            link_config = LinkConfig.load(config.backend_json_path if config.backend_json_path.is_file() else None, app="tantalus")
            self._link = Link(link_config)
            self.link_sync = self._link.sync
        self.model_probe = ModelProbe(lambda: self.link_sync.status())
        self.llm = LLM(self.link_sync, gate=self.model_probe.llm_block, enabled=lambda: self.db.get_setting("llm.enabled", "1") == "1")
        # network
        self.fetcher = Fetcher(config, self.db, transport=http_transport, clock=clock_fn, sleep=sleep_fn, browser=browser)
        self.websearch = websearch or WebSearch(self.fetcher, config=config)
        self.info = InfoSentry(self.fetcher, self.websearch, self.llm, clock=clock_fn)
        self.notifier = notifier or Notifier(config, self.db.get_setting, clock=clock_fn)
        self.budget = BudgetNote(clock=clock_fn)
        self.engine = Engine(self.store, self.fetcher, llm=self.llm, notifier=self.notifier, websearch=self.websearch,
                             info_sentry=self.info, settings_get=self.db.get_setting, emit=self._emit, clock=clock_fn, budget=self.budget)
        self.mail = MailDeals(self.store, self.engine, MailRepo(self.db, clock_fn),
                              MailSource(self.notifier, self.db.get_setting, settings_set=self.db.set_setting, clock=clock_fn),
                              self.db.get_setting, self.db.set_setting, clock=clock_fn)
        self.radar = Radar(self.db, self.store, self.engine, self.fetcher, self.db.get_setting, self.db.set_setting, clock=clock_fn)
        self.scheduler = Scheduler(self.engine, self.store, clock=clock_fn, enabled=config.scheduler,
                                   paused=lambda: self.db.get_setting("scheduler.paused", "0") == "1",
                                   extra={"mail_deals": (self.mail.due, self.mail.run_job), "radar": (self.radar.due, self.radar.run_job)})
        should_install = install_presets if install_presets is not None else (config.scheduler and not config.offline)
        if should_install and self.db.get_setting("presets.installed") is None and not self.store.watchers():
            self.install_presets()
        if self.db.get_setting("presets.installed") is None:
            self.db.set_setting("presets.installed", "0")

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.config.scheduler:
            self.scheduler.start()
            if not self.config.offline:
                self._refresh_mail_interest()

    def stop(self) -> None:
        self.scheduler.stop()
        self.model_probe.close()
        try:
            self.fetcher.close()
        except Exception:  # noqa: BLE001
            pass
        if self._link is not None:
            try:
                self.link_sync.close()
            except Exception:  # noqa: BLE001
                pass
        self.db.close()

    def background(self, fn: Callable[[], Any]) -> None:
        """Run a fire-and-forget hint (a reference link for the hub) on its own thread; errors are ignored."""
        def run() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001 - hints to the hub, the database is the truth
                pass
        thread = threading.Thread(target=run, name="tantalus-background", daemon=True)
        self._background = [t for t in getattr(self, "_background", []) if t.is_alive()] + [thread]
        thread.start()

    def drain_background(self, timeout: float = 5.0) -> None:
        for thread in list(getattr(self, "_background", [])):
            thread.join(timeout)

    def refs_link(self, from_uri: str, to_uri: str, rel: str, *, from_label: str = "") -> dict[str, Any]:
        """Tell the hub two records are the same thing (``hoard://`` references); the hub may be away."""
        from .hoard_link import fam_refs
        return fam_refs.link(from_uri, to_uri, rel, from_label=from_label)

    def _refresh_mail_interest(self) -> None:
        """Tell the hub which sale mails Tantalus wants (in the background: the hub may be slow or away)."""
        def run() -> None:
            try:
                self.mail.refresh_interest()
            except Exception:  # noqa: BLE001 - a hint to the hub; the next scan registers again
                pass
        threading.Thread(target=run, name="tantalus-mail-interest", daemon=True).start()

    def _emit(self, type_: str, data: dict[str, Any]) -> None:
        try:
            from .hoard_link import family
            family.emit(type_, data)
        except Exception:  # noqa: BLE001 — events are hints; the database is the truth
            pass

    # ------------------------------------------------------------------ secrets (write-only)
    def _load_secrets(self) -> None:
        for name in SECRET_NAMES:
            value = self.db.get_setting(f"secret.{name}")
            key = f"TANTALUS_{name}"
            if value and key not in self.config.secrets:
                self.config.secrets[key] = value

    def set_secret(self, name: str, value: str) -> dict[str, Any]:
        name = name.upper().removeprefix("TANTALUS_")
        if name not in SECRET_NAMES:
            raise TantalusError("invalid", f"Unknown secret {name}.", f"Known: {', '.join(SECRET_NAMES)}.")
        key = f"TANTALUS_{name}"
        value = (value or "").strip()
        if value:
            self.db.set_setting(f"secret.{name}", value)
            self.config.secrets[key] = value
        else:
            self.db.execute("DELETE FROM settings WHERE key = ?", (f"secret.{name}",))
            self.config.secrets.pop(key, None)
        if name in ("SEARXNG_URL", "BRAVE_KEY") and isinstance(self.websearch, WebSearch):
            self.websearch.searxng_url = self.config.secret("SEARXNG_URL").rstrip("/")
            self.websearch.brave_key = self.config.secret("BRAVE_KEY")
        return self.secrets_status()[name]

    def secrets_status(self) -> dict[str, dict[str, Any]]:
        import os
        out = {}
        for name in SECRET_NAMES:
            key = f"TANTALUS_{name}"
            value = self.config.secret(name)
            env = bool(os.environ.get(key))
            db = self.db.get_setting(f"secret.{name}") is not None
            source = "env" if env else ("settings" if db else (".env" if value else ""))
            shown = value if name in ("SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_TO", "SEARXNG_URL", "TELEGRAM_CHAT_ID") else (
                ("…" + value[-4:]) if len(value) >= 8 else ("****" if value else ""))
            out[name] = {"configured": bool(value), "source": source, "value": shown}
        return out

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        values = {}
        for key, allowed in UI_SETTINGS.items():
            default = allowed[0] if allowed else ("https://ntfy.sh" if key == "notify.ntfy.server" else "")
            if key.endswith(".enabled") and key.startswith("notify."):
                channel = key.split(".")[1]
                from .notify import DEFAULT_ENABLED
                default = "1" if DEFAULT_ENABLED.get(channel) else "0"
            if key.endswith(".min_severity"):
                default = "low"
            if key in MAIL_DEFAULTS:
                default = MAIL_DEFAULTS[key]
            values[key] = self.db.get_setting(key, default)
        return values

    def set_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        for key, value in values.items():
            if key not in UI_SETTINGS:
                raise TantalusError("invalid", f"Unknown setting {key}.", f"Known: {', '.join(UI_SETTINGS)}.")
            allowed = UI_SETTINGS[key]
            value = str(value).strip() if not isinstance(value, bool) else ("1" if value else "0")
            if allowed and value not in allowed:
                raise TantalusError("invalid", f"{key} must be one of {', '.join(allowed)}.")
            if key in MAIL_NUMERIC:
                low, high = MAIL_NUMERIC[key]
                if not value.isdigit() or not low <= int(value) <= high:
                    raise TantalusError("invalid", f"{key} must be a whole number between {low} and {high}.")
            if key == "mail.deals.stores":
                from .mail.stores import STORE_BY_ID
                bad = [s for s in value.replace(";", ",").split(",") if s.strip() and s.strip().lower() not in STORE_BY_ID]
                if bad:
                    raise TantalusError("invalid", f"Unknown store id(s): {', '.join(bad)}.", f"Known: {', '.join(STORE_BY_ID)}.")
            if key == "mail.deals.domains":
                from .mail.stores import clean_domains
                value = ", ".join(clean_domains(value))
            self.db.set_setting(key, value)
        if any(k in values for k in ("mail.deals.stores", "mail.deals.domains", "mail.source")):
            self.mail.source.forget_interest()
            if not self.config.offline and self.config.scheduler:
                self._refresh_mail_interest()
        return self.settings()

    # ------------------------------------------------------------------ presets
    def install_presets(self, ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
        created = []
        existing = {w["name"] for w in self.store.watchers()}
        for preset in PRESETS:
            if ids and preset["id"] not in ids:
                continue
            p = get_preset(preset["id"])
            if p["name"] in existing:
                continue
            w = self.store.create_watcher(name=p["name"], mode=p["mode"], config=p["config"], interval_min=p.get("interval_min", 30),
                                          discovery_interval_h=p.get("discovery_interval_h", 24), enabled=p.get("enabled", True),
                                          notes=p.get("notes", ""))
            for t in p.get("targets") or []:
                t = dict(t)
                url = t.pop("url")
                if "seller_policy" not in t:
                    t["seller_policy"] = (p["config"].get("policies") or {}).get("seller") or "retail_only"
                self.store.create_target(w["id"], url, **t)
            if p["mode"] == MODE_INFORMATION:
                self.store.sync_info_sources(w["id"], p["config"].get("sources") or [])
            created.append(w)
        self.db.set_setting("presets.installed", "1")
        return created

    # ------------------------------------------------------------------ dashboard
    def dashboard(self) -> dict[str, Any]:
        now = self.clock()
        last_visit = float(self.db.get_setting("dashboard.last_visit_ts", "0") or 0)
        news = self.store.events(statuses=["confirmed"], unseen=True, limit=80)
        watchers = []
        targets = self.store.targets()
        by_watcher: dict[str, list[dict[str, Any]]] = {}
        for t in targets:
            by_watcher.setdefault(t["watcher_id"], []).append(t)
        for w in self.store.watchers():
            ts = by_watcher.get(w["id"], [])
            row = {"id": w["id"], "name": w["name"], "mode": w["mode"], "enabled": w["enabled"], "interval_min": w["interval_min"],
                   "status": "bought" if (w.get("config") or {}).get("status") == "bought" else ("active" if w["enabled"] else "paused"),
                   "last_run_ts": w.get("last_run_ts"), "next_run_ts": w.get("next_run_ts"), "last_error": w.get("last_error") or "",
                   "targets": len(ts), "buyable": sum(1 for t in ts if t["last_state"] in BUYABLE),
                   "needs_human": sum(1 for t in ts if t["status"] == "needs_human"),
                   "last_check_ts": max((t.get("last_check_ts") or 0 for t in ts), default=None) or w.get("last_run_ts"),
                   "unseen": sum(1 for e in news if e["watcher_id"] == w["id"])}
            if w["mode"] == MODE_AVAILABILITY:
                pending = [t["next_check_ts"] for t in ts if t.get("next_check_ts") and t["status"] != "paused"]
                row["next_run_ts"] = min(pending) if pending else None
            if not w["enabled"]:
                row["next_run_ts"] = None
            if w["mode"] == MODE_SECONDHAND:
                row["listings_new"] = len(self.store.listings(watcher_id=w["id"], relevant=True, statuses=["new"], limit=500))
            if w["mode"] == MODE_INFORMATION:
                row["info_material"] = len(self.store.info_items(watcher_id=w["id"], material=True, limit=500))
            watchers.append(row)
        buyable = [self._target_card(t) for t in targets if t["last_state"] in BUYABLE]
        return {
            "now": now, "last_visit_ts": last_visit or None, "news": news, "news_count": len(news),
            "buyable": buyable, "needs_human": [self._target_card(t) for t in targets if t["status"] == "needs_human"],
            "watchers": watchers,
            "listings": self.store.listings(relevant=True, statuses=["new"], order="score", limit=12),
            "info": [i for i in self.store.info_items(material=True, limit=30) if i.get("status") == "new"][:8],
            "candidates": self.store.candidates(status="proposed", limit=8),
            "recent": self.store.events(statuses=["confirmed", "logged", "pending"], limit=15),
            "scheduler": self.scheduler.status(), "counts": self.store.counts(),
            "releases": [r for r in self.radar.releases(upcoming_days=45, past_days=3)],
            "chains": self.radar.chains_view(),
            "radar": self.radar.status(),
        }

    def mark_visit(self, *, mark_seen: bool = True) -> dict[str, Any]:
        now = self.clock()
        n = self.store.mark_seen(before=now) if mark_seen else 0
        self.db.set_setting("dashboard.last_visit_ts", str(now))
        return {"marked_seen": n, "last_visit_ts": now}

    def _target_card(self, t: dict[str, Any]) -> dict[str, Any]:
        return {k: t.get(k) for k in ("id", "watcher_id", "label", "url", "host", "retailer", "status", "last_state", "last_price",
                                      "last_currency", "last_confidence", "last_title", "last_image", "last_check_ts", "last_error",
                                      "min_price", "price_threshold", "msrp", "price_ceiling", "seller_policy",
                                      "fetch_tier", "adapter", "interval_min", "sku", "ean", "source_level", "next_check_ts")}

    # ------------------------------------------------------------------ status
    def counts(self) -> dict[str, int]:
        return self.store.counts()

    def status(self) -> dict[str, Any]:
        from .fetch.browser import playwright_installed
        ok, reason = self.llm.available()
        return {"service": SERVICE, "version": __version__, "data_dir": str(self.config.data_dir), "uptime_s": int(time.time() - self.started_at),
                "counts": self.counts(), "scheduler": self.scheduler.status(), "channels": self.notifier.channels_status(),
                "notify_via": self.notifier.via_status() if hasattr(self.notifier, "via_status") else {},
                "mail": {k: v for k, v in self.mail.status().items() if k in ("enabled", "first_scan_done", "last_run_ts", "next_run_ts", "last_error", "counts", "source")},
                "radar": self.radar.status(),
                "llm": {"available": ok, "reason": reason, "calls": self.llm.calls, "failures": self.llm.failures, "skipped_budget": self.llm.skipped,
                        "budget": f"{self.llm.max_calls} per {int(self.llm.window_s // 60)} min"},
                "search_engines": self.websearch.available_engines() if hasattr(self.websearch, "available_engines") else [],
                "browser": {"enabled": self.config.browser, "playwright": playwright_installed()},
                "offline": self.config.offline}

    # ------------------------------------------------------------------ YAML-ish export / import (JSON-compatible)
    def export_config(self) -> dict[str, Any]:
        out = []
        for w in self.store.watchers():
            item = {"name": w["name"], "mode": w["mode"], "enabled": w["enabled"], "interval_min": w["interval_min"],
                    "discovery_interval_h": w["discovery_interval_h"], "notes": w["notes"], "config": w["config"]}
            if w["mode"] == MODE_AVAILABILITY:
                item["targets"] = [{k: t[k] for k in ("url", "label", "retailer", "sku", "ean", "product_type", "store_ids", "source_level",
                                                      "msrp", "price_ceiling", "price_threshold", "seller_policy", "fetch_tier",
                                                      "interval_min", "adapter") if t.get(k) not in (None, "", [])}
                                   for t in self.store.targets(watcher_id=w["id"])]
            out.append(item)
        return {"tantalus": 1, "watchers": out}

    def import_config(self, data: dict[str, Any]) -> dict[str, Any]:
        watchers = data.get("watchers") if isinstance(data, dict) else None
        if not isinstance(watchers, list):
            raise TantalusError("invalid", "Expected {watchers: [...]}.", "Export first to see the format.")
        created, updated = [], []
        by_name = {w["name"]: w for w in self.store.watchers()}
        for item in watchers:
            mode = item.get("mode")
            if mode not in MODES:
                raise TantalusError("invalid", f"Watcher {item.get('name')!r}: unknown mode {mode!r}.")
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                raise TantalusError("invalid", "Every watcher needs a name.")
            config = item.get("config") or {k: v for k, v in item.items() if k not in ("name", "id", "mode", "enabled", "interval_min",
                                                                                          "discovery_interval_h", "notes", "targets")}
            if name in by_name:
                w = self.store.update_watcher(by_name[name]["id"], config=config, interval_min=int(item.get("interval_min") or by_name[name]["interval_min"]),
                                              enabled=item.get("enabled", True), notes=item.get("notes", by_name[name]["notes"]))
                updated.append(w["id"])
            else:
                w = self.store.create_watcher(name=name, mode=mode, config=config, interval_min=int(item.get("interval_min") or 30),
                                              discovery_interval_h=int(item.get("discovery_interval_h") or 24),
                                              enabled=item.get("enabled", True), notes=item.get("notes", ""))
                created.append(w["id"])
            for t in item.get("targets") or []:
                t = dict(t)
                url = t.pop("url", None)
                if url:
                    self.store.create_target(w["id"], url, **t)
            if mode == MODE_INFORMATION:
                self.store.sync_info_sources(w["id"], config.get("sources") or [])
        return {"created": created, "updated": updated}
