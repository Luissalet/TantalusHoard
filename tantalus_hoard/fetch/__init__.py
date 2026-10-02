"""The fetch ladder: polite, SSRF-safe HTTP first, a real browser only when the answer is blocked.

The work is done by the commons' :class:`hoard_link.web.fetch.Fetcher` (policy check on every hop with a pinned connection,
robots.txt with wildcards, per-host spacing, block cooldown, conditional GET, retries, the Playwright rung). This module adds
what only Tantalus knows:

* the politeness state lives in the app's ``host_state`` table (:mod:`.hoststate`), so cooldowns survive restarts;
* the per-retailer site profiles (``extract.sites``) choose the tier: a JS shell goes straight to the browser, a shop that serves
  a normal window and refuses headless goes through a visible window (``tier="window"``) at its own slow interval;
* when the hub answers, plain page fetches go through the **family web service** (one throttle, one block cooldown and one robots
  cache for every app on the machine); the local ladder is the fallback and the way to the browser, windows and custom headers.

Public surface (see CONTRACT.md):

    fetcher = Fetcher(config, db)
    fetcher.get(url, *, tier="auto", headers=None, params=None, accept="html", etag="", last_modified="",
                respect_robots=True, min_interval_s=None, timeout=None) -> FetchResult
    fetcher.get_json(url, **same) -> (FetchResult, parsed_json | None)
    with fetcher.browser_session() as context: ...     # Playwright BrowserContext on the persistent profile
    fetcher.host_status() -> list[dict]                 # host_state rows, for the UI
    fetcher.close()

Extras used by the app: ``open_for_human(url)``, ``clear_block(host)``, ``set_min_interval(host, s)``, ``reset_host(host)``.
Expected failures never raise from ``get``: they come back as ``ok=False`` with ``blocked`` / ``block_reason`` / ``error``
(and the commons' ``error_kind``) filled in. ``block_reason`` is also ``robots``, ``offline`` or ``unsafe_url`` when the request
was refused before any traffic, as the engine and the extractors have always read it.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, Optional
from urllib.parse import urlsplit

from ..errors import TantalusError
from ..hoard_link import fam_web
from ..hoard_link.web import safety
from ..hoard_link.web.blocks import BLOCK_REASONS, detect_block
from ..hoard_link.web.browser import BrowserRung, playwright_installed
from ..hoard_link.web.fetch import DEFAULT_USER_AGENT, FetchResult, Fetcher as _CommonsFetcher
from .hoststate import DEFAULT_MIN_INTERVAL_S, SqlHostState, SqlRobotsStore

check_url = safety.check_url
__all__ = ["Fetcher", "FetchResult", "BLOCK_REASONS", "detect_block", "check_url", "BrowserRung", "playwright_installed"]

ACCEPT_LANGUAGE = "es-ES,es;q=0.9,en;q=0.7"
ROBOTS_AGENT = "TantalusHoard"
# What the engine, the extractors and the search code read from ``block_reason`` when nothing was even requested.
_REFUSED = {"policy": "unsafe_url", "robots": "robots", "offline": "offline"}
_TIERS = ("auto", "http", "browser", "window")


class Fetcher(_CommonsFetcher):
    def __init__(self, config: Any, db: Any, *, transport: Any = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, browser: Any = None, resolver: Any = None,
                 use_hub: Optional[bool] = None):
        self.config = config
        self.db = db
        self._own_rung = False
        if not getattr(config, "browser", True):
            rung: Any = False                                   # TANTALUS_BROWSER=0: no browser of any kind
        elif browser is None:
            rung, self._own_rung = BrowserRung(config.browser_profile_dir, locale="es-ES"), True
        else:
            rung = browser
        super().__init__(user_agent=DEFAULT_USER_AGENT, state=SqlHostState(db), transport=transport, clock=clock, sleep=sleep,
                         resolver=resolver, browser=rung, offline=bool(config.offline), min_interval_s=DEFAULT_MIN_INTERVAL_S,
                         timeout_s=config.http_timeout_s, accept_language=ACCEPT_LANGUAGE, robots_agent=ROBOTS_AGENT,
                         robots_store=SqlRobotsStore(db))
        # The family web service is for real traffic: a test transport (or an explicit False) keeps everything local.
        self.use_hub = (transport is None) if use_hub is None else bool(use_hub)
        self._hub_local = threading.local()

    # ================================================================== public API
    def get(self, url: str, *, tier: str = "auto", headers: Optional[Mapping[str, str]] = None,
            params: Optional[Mapping[str, Any]] = None, accept: str = "html", etag: str = "", last_modified: str = "",
            respect_robots: bool = True, min_interval_s: Optional[float] = None, timeout: Optional[float] = None,
            **extra: Any) -> FetchResult:
        self.offline = bool(self.config.offline)                  # the setting is live (tests flip it)
        tier = tier if tier in _TIERS else "auto"
        if tier == "auto" and accept == "html" and not self.offline and self._rung_available(self.profile):
            tier, min_interval_s, refused = self._profile_tier(url, params, min_interval_s)
            if refused is not None:
                return refused
        hub_robots = self._hub_ready(headers, accept, extra) and tier in ("auto", "http")
        self._hub_local.robots = bool(respect_robots) if hub_robots else None
        try:
            result = super().get(url, tier=tier, headers=headers, params=params, accept=accept, etag=etag, last_modified=last_modified,
                                 respect_robots=respect_robots, min_interval_s=min_interval_s, timeout=timeout, **extra)
        finally:
            self._hub_local.robots = None
        if result.error_kind in _REFUSED and not result.block_reason:
            result.block_reason = _REFUSED[result.error_kind]
        return result

    @contextmanager
    def browser_session(self, *, headless: bool = True) -> Iterator[Any]:
        """Playwright ``BrowserContext`` on the persistent profile (bound to the calling thread)."""
        rung = self._rung()
        if rung is None:
            raise TantalusError("fetch_failed", "the browser rung is disabled", "Enable it with TANTALUS_BROWSER=1.")
        with rung.browser_session(headless=headless) as context:
            yield context

    def open_for_human(self, url: str, *, timeout_s: float = 900.0, **kw: Any) -> dict[str, Any]:
        """Visible browser on the shared profile so a person can solve a CAPTCHA / log in; returns on close. Without a local
        browser the hub opens the page for the person (the family profile or the default browser) and answers at once."""
        rung = self._rung()
        if rung is None or not self._rung_available(self.profile):
            if self.use_hub and fam_web.available():
                answer = fam_web.open_for_human(url)
                if answer.get("ok"):
                    return {**{k: v for k, v in answer.items() if k not in ("ok", "via")}, "url": url, "via": "hub", "hub_via": answer.get("via", "")}
            raise TantalusError("fetch_failed", self._rung_unavailable_text(self.profile), "Enable it with TANTALUS_BROWSER=1.")
        return super().open_for_human(url, timeout_s=timeout_s, **kw)

    def close(self) -> None:
        super().close()
        if self._own_rung and self._browser not in (None, False):
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass

    # ================================================================== site profiles
    def _profile_tier(self, url: str, params: Optional[Mapping[str, Any]], min_interval_s: Optional[float]) -> tuple[str, Optional[float], Any]:
        """The tier the retailer's profile asks for (``extract.sites``): a visible window, the headless browser, or plain
        ``auto``. Returns ``(tier, min_interval_s, refused_result_or_None)``."""
        try:
            from ..extract.sites import needs_browser, profile_for_host, window_ok
        except Exception:  # noqa: BLE001 - a profile lookup must never break a fetch
            return "auto", min_interval_s, None
        host = (urlsplit(url).hostname or "").lower()
        try:
            window = bool(window_ok(url)) and (self.db.get_setting("browser.window", "1") or "1") == "1"
            if window:
                row = self.state.get(host)
                if float(row.get("blocked_until_ts") or 0) > self.clock():
                    fr = FetchResult(url=url, fetched_at=self.clock())
                    return "window", min_interval_s, self._cooldown_result(fr, host, row, "window")   # refused recently: leave it alone
                profile = profile_for_host(url)
                interval = float(profile.min_interval_s) if profile else 60.0
                return "window", max(float(min_interval_s or 0), interval), None
            if needs_browser(url):
                return "browser", min_interval_s, None             # the profile says plain http only gets a JS shell / interstitial
        except Exception:  # noqa: BLE001
            pass
        return "auto", min_interval_s, None

    # ================================================================== the family web service
    def _hub_ready(self, headers: Optional[Mapping[str, str]], accept: str, extra: Mapping[str, Any]) -> bool:
        """Can this request go through the hub? Only plain public fetches: no custom headers, no MIME filter, no other profile."""
        if not self.use_hub or self.offline or headers or extra.get("allowed_mime") or extra.get("profile") not in (None, safety.PUBLIC):
            return False
        return accept in ("html", "json", "any") and fam_web.available()

    def _get_locked(self, url, host, tier, headers, accept, etag, last_modified, respect_robots, min_interval_s, timeout_s, cap,
                    retries, profile, allowed_mime, fr, memo, stale_entry=None):
        if getattr(self._hub_local, "robots", None) is not None and tier in ("auto", "http"):
            respect_robots = False        # the hub reads (and caches) robots.txt for the whole family; see _attempt_http
        return super()._get_locked(url, host, tier, headers, accept, etag, last_modified, respect_robots, min_interval_s, timeout_s,
                                   cap, retries, profile, allowed_mime, fr, memo, stale_entry=stale_entry)

    def _attempt_http(self, url, headers, accept, etag, last_modified, timeout_s, cap, retries, profile, allowed_mime, fr, memo):
        robots = getattr(self._hub_local, "robots", None)
        if robots is not None and not headers:
            answer = fam_web.fetch(url, tier="http", accept=accept, etag=etag, last_modified=last_modified, respect_robots=robots,
                                   max_bytes=cap, timeout=timeout_s, fresh=True)     # a watcher needs the page as it is now
            if _served_by_hub(answer):
                return self._from_hub(answer, fr)
            if robots and accept == "html":        # the hub is gone: the robots.txt check we skipped happens here
                allowed, note = self.robots.check(url)
                if not allowed:
                    fr.error = f"robots.txt of {(urlsplit(url).hostname or '').lower()} disallows this path"
                    fr.error_kind = "robots"
                    return fr
                fr.note = note
        return super()._attempt_http(url, headers, accept, etag, last_modified, timeout_s, cap, retries, profile, allowed_mime, fr, memo)

    @staticmethod
    def _from_hub(answer: Mapping[str, Any], fr: FetchResult) -> FetchResult:
        fr.final_url = answer.get("final_url") or answer.get("url") or fr.url
        fr.status = int(answer.get("status") or 0)
        fr.headers = {str(k).lower(): str(v) for k, v in (answer.get("headers") or {}).items()}
        fr.content_type = answer.get("content_type") or fr.headers.get("content-type", "")
        fr.text = answer.get("text") or ""
        fr.body = answer.get("body")
        fr.etag = answer.get("etag") or ""
        fr.last_modified = answer.get("last_modified") or ""
        fr.not_modified = bool(answer.get("not_modified"))
        fr.truncated = bool(answer.get("truncated"))
        fr.from_cache = bool(answer.get("from_cache"))
        fr.ok = bool(answer.get("ok"))
        fr.blocked = bool(answer.get("blocked"))
        fr.block_reason = answer.get("block_reason") or ""
        fr.error = answer.get("error") or ""
        fr.error_kind = answer.get("error_kind") or ""
        fr.elapsed_ms = int(answer.get("elapsed_ms") or answer.get("ms") or 0)
        fr.tier = "http" if answer.get("tier") in (None, "", "cache") else str(answer["tier"])
        fr.note = "served by the family web service"
        return fr

    # ================================================================== messages
    def _rung_unavailable_text(self, profile: str) -> str:
        rung = self._rung()
        if rung is None:
            return "the browser rung is disabled (TANTALUS_BROWSER=0)" if not getattr(self.config, "browser", True) \
                else "the browser rung is disabled"
        return super()._rung_unavailable_text(profile)


def _served_by_hub(answer: Mapping[str, Any]) -> bool:
    """True when the hub really fetched (or refused) the page, False when the hub itself could not serve the call."""
    if answer.get("error") == "hub unreachable":
        return False
    return "tier" in answer or bool(answer.get("ok"))

