"""The fetch ladder: polite, SSRF-safe HTTP first, a real browser only when the answer is blocked.

Public surface (see CONTRACT.md):

    fetcher = Fetcher(config, db)
    fetcher.get(url, *, tier="auto", headers=None, params=None, accept="html", etag="", last_modified="",
                respect_robots=True, min_interval_s=None, timeout=None) -> FetchResult
    fetcher.get_json(url, **same) -> (FetchResult, parsed_json | None)
    with fetcher.browser_session() as context: ...     # Playwright BrowserContext on the persistent profile
    fetcher.host_status() -> list[dict]                 # host_state rows, for the UI
    fetcher.close()

Extras used by the app: ``open_for_human(url)``, ``clear_block(host)``, ``set_min_interval(host, s)``,
``reset_host(host)``. Expected failures never raise from ``get``: they come back as ``ok=False`` with
``blocked`` / ``block_reason`` / ``error`` filled in.
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ..errors import TantalusError
from ..model import FetchResult
from .blocks import BLOCK_REASONS, BROWSER_RETRY_REASONS, HTTP_429, apply_block, block_hint, detect_block
from .browser import BrowserRung
from .robots import RobotsCache
from .safety import UNRESOLVABLE_PREFIX, Resolver, check_url

__all__ = ["Fetcher", "BLOCK_REASONS", "detect_block", "check_url", "BrowserRung"]

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")
ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
ACCEPT_JSON = "application/json, text/plain, */*"
ACCEPT_LANGUAGE = "es-ES,es;q=0.9,en;q=0.7"

MAX_BODY_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 6
DEFAULT_MIN_INTERVAL_S = 20.0
MAX_WAIT_S = 30.0             # a single politeness sleep never exceeds this
BLOCK_COOLDOWN_S = 30 * 60    # after a block the host is left alone (over http) for this long
MAX_COOLDOWN_S = 3600
ROBOTS_TIMEOUT_S = 10.0
ROBOTS_MAX_BYTES = 512 * 1024
_REDIRECTS = (301, 302, 303, 307, 308)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.I)
_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.I | re.S)


class Fetcher:
    def __init__(self, config: Any, db: Any, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 browser: Any = None, resolver: Resolver | None = None):
        self.config = config
        self.db = db
        self.clock = clock
        self.sleep = sleep
        self.resolver = resolver
        self._browser = browser                   # None = created lazily; False = disabled
        self._browser_owned = browser is None
        self._browser_lock = threading.Lock()
        self._browser_cooldown: dict[str, float] = {}   # host -> until (a browser block is remembered in memory)
        self._host_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._client = httpx.Client(transport=transport, follow_redirects=False, timeout=config.http_timeout_s,
                                    headers={"User-Agent": USER_AGENT, "Accept-Language": ACCEPT_LANGUAGE,
                                             "Accept-Encoding": "gzip, deflate"})
        self._robots = RobotsCache(db, self._fetch_robots, clock)

    # ================================================================== public API
    def get(self, url: str, *, tier: str = "auto", headers: Mapping[str, str] | None = None,
            params: Mapping[str, Any] | None = None, accept: str = "html", etag: str = "",
            last_modified: str = "", respect_robots: bool = True, min_interval_s: float | None = None,
            timeout: float | None = None) -> FetchResult:
        started = self.clock()
        full_url = _with_params(url, params)
        fr = FetchResult(url=full_url, fetched_at=started)
        if self.config.offline:
            fr.block_reason = "offline"
            fr.error = "offline mode: the network is disabled"
            return fr
        problem = check_url(full_url, self.resolver)
        if problem:
            fr.error = problem
            if not problem.startswith(UNRESOLVABLE_PREFIX):
                fr.block_reason = "unsafe_url"
            return fr
        host = (urlsplit(full_url).hostname or "").lower()
        tier = tier if tier in ("auto", "http", "browser") else "auto"
        timeout_s = float(timeout or self.config.http_timeout_s)
        with self._host_lock(host):
            result = self._get_locked(full_url, host, tier, dict(headers or {}), accept, etag, last_modified,
                                      respect_robots, min_interval_s, timeout_s, fr)
        result.elapsed_ms = result.elapsed_ms or int((self.clock() - started) * 1000)
        return result

    def get_json(self, url: str, **kwargs: Any) -> tuple[FetchResult, Any | None]:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Accept", ACCEPT_JSON)
        kwargs.setdefault("accept", "json")
        fr = self.get(url, headers=headers, **kwargs)
        if not fr.ok or fr.not_modified:
            return fr, None
        text = fr.text
        if fr.tier == "browser":  # a browser wraps a raw JSON document in <pre>
            match = _PRE.search(text)
            if match:
                import html as htmllib  # noqa: PLC0415
                text = htmllib.unescape(match.group(1))
        try:
            return fr, json.loads(text)
        except ValueError as error:
            fr.ok = False
            fr.error = f"invalid JSON: {error}"
            return fr, None

    @contextmanager
    def browser_session(self, *, headless: bool = True) -> Iterator[Any]:
        """Playwright ``BrowserContext`` on the persistent profile (bound to the calling thread)."""
        rung = self._rung()
        if rung is None:
            raise TantalusError("fetch_failed", "the browser rung is disabled", "Enable it with TANTALUS_BROWSER=1.")
        with rung.browser_session(headless=headless) as context:
            yield context

    def open_for_human(self, url: str, *, timeout_s: float = 900.0) -> dict[str, Any]:
        """Visible browser on the shared profile so a person can solve a CAPTCHA / log in; returns on close."""
        rung = self._rung()
        if rung is None:
            raise TantalusError("fetch_failed", "the browser rung is disabled", "Enable it with TANTALUS_BROWSER=1.")
        result = rung.open_for_human(url, timeout_s=timeout_s)
        host = (urlsplit(url).hostname or "").lower()
        if host:
            self.clear_block(host)  # the person may have solved it: let the next fetch try again
        return result

    def host_status(self) -> list[dict[str, Any]]:
        now = self.clock()
        rows = self.db.query("SELECT * FROM host_state ORDER BY host")
        out = []
        for row in rows:
            blocked_until = row["blocked_until_ts"]
            out.append({
                "host": row["host"],
                "last_fetch_ts": row["last_fetch_ts"],
                "min_interval_s": row["min_interval_s"],
                "blocked_until_ts": blocked_until,
                "blocked_now": bool(blocked_until and blocked_until > now),
                "block_reason": row["block_reason"],
                "preferred_tier": row["preferred_tier"],
                "ok_count": row["ok_count"],
                "fail_count": row["fail_count"],
                "robots_fetched_ts": row["robots_fetched_ts"],
                "has_robots": bool(row["robots_txt"]),
            })
        return out

    def clear_block(self, host: str) -> None:
        self._ensure_row(host)
        self.db.execute("UPDATE host_state SET blocked_until_ts = NULL, block_reason = '' WHERE host = ?", (host,))
        self._browser_cooldown.pop(host, None)

    def reset_host(self, host: str) -> None:
        """Forget the block cooldown and the remembered tier (e.g. after the site changed)."""
        self.clear_block(host)
        self.db.execute("UPDATE host_state SET preferred_tier = '' WHERE host = ?", (host,))

    def set_min_interval(self, host: str, seconds: float) -> None:
        self._ensure_row(host)
        self.db.execute("UPDATE host_state SET min_interval_s = ? WHERE host = ?", (max(0.0, float(seconds)), host))

    def close(self) -> None:
        try:
            self._client.close()
        finally:
            if self._browser_owned and self._browser:
                try:
                    self._browser.close()
                except Exception:  # noqa: BLE001
                    pass

    # ================================================================== orchestration
    def _get_locked(self, url: str, host: str, tier: str, headers: dict[str, str], accept: str, etag: str,
                    last_modified: str, respect_robots: bool, min_interval_s: float | None, timeout_s: float,
                    fr: FetchResult) -> FetchResult:
        note = ""
        if respect_robots and accept == "html":
            allowed, note = self._robots.check(url)
            if not allowed:
                fr.block_reason = "robots"
                fr.error = f"robots.txt of {host} disallows this path"
                return fr
        result = self._route(url, host, tier, headers, accept, etag, last_modified, min_interval_s, timeout_s, fr)
        if note:
            result.headers.setdefault("x-tantalus-note", note)
        return result

    @staticmethod
    def _known_shell_host(url: str) -> bool:
        """Does the site profile say plain http is useless here? (Lazy import: extract imports fetch.blocks.)"""
        try:
            from ..extract.sites import needs_browser
            return bool(needs_browser(url))
        except Exception:  # noqa: BLE001 - a profile lookup must never break a fetch
            return False

    def _route(self, url: str, host: str, tier: str, headers: dict[str, str], accept: str, etag: str,
               last_modified: str, min_interval_s: float | None, timeout_s: float, fr: FetchResult) -> FetchResult:
        state = self._state(host)
        now = self.clock()
        cooling = bool(state["blocked_until_ts"] and state["blocked_until_ts"] > now)
        rung_ok = self._rung_available()
        prefers_browser = state["preferred_tier"] == "browser"

        if tier == "http":
            if cooling:
                return self._cooldown_result(fr, state, "http")
            order = ["http"]
        elif tier == "browser":
            if not rung_ok:
                fr.tier = "browser"
                fr.error = self._rung_unavailable_text()
                return fr
            order = ["browser"]
        else:  # auto
            if cooling and (prefers_browser or self._browser_cooldown.get(host, 0) > now):
                return self._cooldown_result(fr, state, "browser")   # the browser was blocked here too: leave it
            if prefers_browser or cooling:
                if rung_ok:
                    order = ["browser"]          # this host is known to refuse plain http
                elif cooling:
                    return self._cooldown_result(fr, state, "http")
                else:
                    order = ["http"]
            elif rung_ok and self._known_shell_host(url):
                order = ["browser"]              # the site profile says plain http only gets a JS shell / interstitial
            else:
                order = ["http", "browser"]

        self._wait_turn(host, state, min_interval_s)
        first = order[0]
        result = (self._attempt_browser(url, timeout_s, fr) if first == "browser"
                  else self._attempt_http(url, headers, accept, etag, last_modified, timeout_s, fr))
        self._record(host, result)

        if (first == "http" and len(order) > 1 and result.blocked and result.block_reason in BROWSER_RETRY_REASONS
                and rung_ok):
            second = FetchResult(url=url, fetched_at=self.clock())
            browser_result = self._attempt_browser(url, timeout_s, second)
            self._record(host, browser_result)
            if browser_result.ok or browser_result.blocked:
                return browser_result
            # the browser itself failed (crashed / cannot start): keep the http verdict and say why
            result.error = f"{result.error}; browser fallback failed: {browser_result.error}"
        return result

    # ------------------------------------------------------------------ rungs
    def _attempt_http(self, url: str, headers: dict[str, str], accept: str, etag: str, last_modified: str,
                      timeout_s: float, fr: FetchResult) -> FetchResult:
        request_headers = {"Accept": ACCEPT_HTML if accept == "html" else ACCEPT_JSON}
        request_headers.update(headers)
        if etag:
            request_headers["If-None-Match"] = etag
        if last_modified:
            request_headers["If-Modified-Since"] = last_modified
        began = self.clock()
        self._fetch_raw(url, request_headers, timeout_s, MAX_BODY_BYTES, fr)
        fr.elapsed_ms = int((self.clock() - began) * 1000)
        if fr.error and not fr.status:      # network-level failure
            return fr
        if fr.status == 304:
            fr.not_modified = True
            fr.ok = True
            fr.error = ""
            return fr
        return apply_block(fr, detect_block(fr.status, fr.text, fr.headers, fr.final_url or fr.url))

    def _attempt_browser(self, url: str, timeout_s: float, fr: FetchResult) -> FetchResult:
        rung = self._rung()
        if rung is None or not self._rung_available():
            fr.tier = "browser"
            fr.error = self._rung_unavailable_text()
            return fr
        result = rung.fetch(url, timeout=timeout_s)
        result.tier = "browser"
        return result

    def _fetch_raw(self, url: str, headers: dict[str, str], timeout_s: float, cap: int, fr: FetchResult) -> FetchResult:
        """One logical GET with manual, SSRF-checked redirects and a body cap. Fills ``fr``; never raises."""
        current = url
        try:
            for hop in range(MAX_REDIRECTS + 1):
                with self._client.stream("GET", current, headers=headers, timeout=timeout_s) as response:
                    if response.status_code in _REDIRECTS and response.headers.get("location"):
                        target = urljoin(current, response.headers["location"])
                        problem = check_url(target, self.resolver)
                        if problem:
                            fr.error = f"redirect refused: {problem}"
                            fr.block_reason = "unsafe_url"
                            fr.final_url = target
                            return fr
                        current = target
                        continue
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) >= cap:
                            break
                    fr.final_url = str(response.url)
                    fr.status = response.status_code
                    fr.headers = {k.lower(): v for k, v in response.headers.items()}
                    fr.content_type = fr.headers.get("content-type", "")
                    fr.etag = fr.headers.get("etag", "")
                    fr.last_modified = fr.headers.get("last-modified", "")
                    fr.text = _decode(bytes(body[:cap]), fr.content_type)
                    return fr
            fr.error = f"too many redirects (> {MAX_REDIRECTS})"
        except httpx.TimeoutException:
            fr.error = f"timeout after {timeout_s:.0f}s"
        except httpx.HTTPError as error:
            fr.error = f"network error: {type(error).__name__}: {error}"[:300]
        except (OSError, ValueError) as error:  # noqa: BLE001 — bad URL, socket failure
            fr.error = f"network error: {error}"[:300]
        return fr

    def _fetch_robots(self, url: str) -> tuple[int, str, str]:
        probe = FetchResult(url=url)
        self._fetch_raw(url, {"Accept": "text/plain,*/*;q=0.5"}, ROBOTS_TIMEOUT_S, ROBOTS_MAX_BYTES, probe)
        if probe.error and not probe.status:
            return 0, "", probe.error
        return probe.status, probe.text, ""

    # ------------------------------------------------------------------ browser plumbing
    def _rung(self) -> Any:
        if self._browser is False:
            return None
        if self._browser is None:
            with self._browser_lock:
                if self._browser is None:
                    self._browser = BrowserRung(self.config) if getattr(self.config, "browser", True) else False
        return self._browser or None

    def _rung_available(self) -> bool:
        rung = self._rung()
        if rung is None or not getattr(self.config, "browser", True):
            return False
        try:
            return bool(rung.available()[0])
        except Exception:  # noqa: BLE001
            return False

    def _rung_unavailable_text(self) -> str:
        rung = self._rung()
        if rung is None or not getattr(self.config, "browser", True):
            return "the browser rung is disabled (TANTALUS_BROWSER=0)"
        try:
            return rung.available()[1] or "the browser rung is unavailable"
        except Exception as error:  # noqa: BLE001
            return f"the browser rung is unavailable: {error}"

    # ------------------------------------------------------------------ host_state bookkeeping
    def _host_lock(self, host: str) -> threading.Lock:
        with self._locks_guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _ensure_row(self, host: str) -> None:
        self.db.execute("INSERT INTO host_state(host) VALUES (?) ON CONFLICT(host) DO NOTHING", (host,))

    def _state(self, host: str) -> Any:
        self._ensure_row(host)
        return self.db.one("SELECT * FROM host_state WHERE host = ?", (host,))

    def _wait_turn(self, host: str, state: Any, override: float | None) -> None:
        interval = float(override) if override is not None else float(state["min_interval_s"] or DEFAULT_MIN_INTERVAL_S)
        last = state["last_fetch_ts"]
        if last and interval > 0:
            wait = float(last) + interval - self.clock()
            if wait > 0:
                self.sleep(min(wait, MAX_WAIT_S))
        self.db.execute("UPDATE host_state SET last_fetch_ts = ? WHERE host = ?", (self.clock(), host))

    def _record(self, host: str, fr: FetchResult) -> None:
        """Counters, block cooldown and the remembered tier after one attempt."""
        now = self.clock()
        self.db.execute("UPDATE host_state SET last_fetch_ts = ? WHERE host = ?", (now, host))
        if fr.ok:
            self.db.execute("UPDATE host_state SET ok_count = ok_count + 1 WHERE host = ?", (host,))
            if fr.tier == "browser":
                # The browser worked (possibly after an http block): go straight to it next time.
                self.db.execute("UPDATE host_state SET preferred_tier = 'browser', blocked_until_ts = NULL, block_reason = '' WHERE host = ?", (host,))
            else:
                self.db.execute("UPDATE host_state SET blocked_until_ts = NULL, block_reason = '' WHERE host = ?", (host,))
            self._browser_cooldown.pop(host, None)
            return
        self.db.execute("UPDATE host_state SET fail_count = fail_count + 1 WHERE host = ?", (host,))
        if fr.blocked:
            cooldown = BLOCK_COOLDOWN_S
            if fr.block_reason == HTTP_429:
                retry_after = fr.headers.get("retry-after", "")
                if retry_after.isdigit():
                    cooldown = min(max(int(retry_after), 60), MAX_COOLDOWN_S)
            self.db.execute("UPDATE host_state SET blocked_until_ts = ?, block_reason = ? WHERE host = ?",
                            (now + cooldown, fr.block_reason, host))
            if fr.tier == "browser":
                self._browser_cooldown[host] = now + cooldown

    def _cooldown_result(self, fr: FetchResult, state: Any, rung: str) -> FetchResult:
        until = float(state["blocked_until_ts"] or self._browser_cooldown.get(state["host"], 0))
        remaining = max(0, int(until - self.clock()))
        reason = state["block_reason"] or "http_403"
        fr.tier = rung
        fr.blocked = True
        fr.block_reason = reason
        fr.error = (f"blocked: {block_hint(reason)}; not retrying {rung} for {remaining // 60} more minute(s) "
                    "(open the site in the browser to resolve it, or wait)")
        return fr


# ====================================================================== helpers
def _with_params(url: str, params: Mapping[str, Any] | None) -> str:
    if not params:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(k, str(v)) for k, v in params.items() if v is not None]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _decode(body: bytes, content_type: str) -> str:
    """Decode with the declared charset, then a <meta> sniff, then UTF-8 (never raises)."""
    charset = ""
    match = re.search(r"charset\s*=\s*['\"]?([\w\-:.]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    elif "html" in (content_type or "").lower() or not content_type:
        sniff = _META_CHARSET.search(body[:4096])
        if sniff:
            charset = sniff.group(1).decode("ascii", "ignore")
    for name in (charset, "utf-8"):
        if not name:
            continue
        try:
            return body.decode(name, errors="replace" if name == "utf-8" else "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")
