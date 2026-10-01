"""Headless-browser rung of the fetch ladder (Playwright, sync API, optional dependency).

* Playwright is imported lazily; when the package is missing the rung reports itself unavailable with a
  clear message and everything else keeps working.
* One persistent profile (``config.browser_profile_dir``) is shared by every use, so cookies and logins
  survive between runs. Chromium-family browsers are tried in this order: Edge on Windows (always
  installed there, so nothing has to be downloaded), then Chrome, then Playwright's bundled Chromium.
* The Playwright sync API is bound to the thread that started it. ``fetch`` therefore runs on one small
  dedicated worker thread fed by a queue, so any thread may call it. A gate lock guarantees a single
  browser user at a time (a profile cannot be opened twice).
* Nothing here tries to defeat a challenge. A challenge page is reported (``blocked`` / ``block_reason``);
  the only "waiting" is passive: some interstitials refresh themselves after a few seconds in a real browser,
  and we give them a bounded chance to do so. ``open_for_human`` opens a visible window on the same
  profile so the person can solve a CAPTCHA or log in.
"""

from __future__ import annotations

import importlib.util
import logging
import queue
import sys
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from ..errors import TantalusError
from ..model import FetchResult
from .blocks import AKAMAI, CLOUDFLARE, apply_block, detect_block

log = logging.getLogger("tantalus.browser")

SETTLE_BUDGET_S = 8.0          # bounded wait for network idle after DOMContentLoaded
PASSIVE_RECHECKS = 2           # times we re-read a self-refreshing interstitial ...
PASSIVE_RECHECK_WAIT_S = 3.0   # ... this many seconds apart
WORKER_IDLE_S = 90.0           # the browser closes itself after this long without use
INSTALL_HINT = "Install it with: python -m pip install playwright  (Edge or Chrome must be installed, or run: python -m playwright install chromium)"


class BrowserUnavailable(RuntimeError):
    """Playwright missing or no browser could be launched."""


def channel_order(platform: str | None = None) -> list[str | None]:
    """Browser channels to try, ``None`` = Playwright's bundled Chromium."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return ["msedge", "chrome", None]
    if platform == "darwin":
        return ["chrome", "msedge", None]
    return ["chrome", None, "msedge"]


def playwright_installed() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def _start_playwright() -> Any:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 — lazy on purpose
    return sync_playwright().start()


def launch_context(pw: Any, profile_dir: Any, *, headless: bool, channels: list[str | None] | None = None,
                   args: list[str] | None = None) -> tuple[Any, str]:
    """Open the persistent context on the first browser that starts. Returns ``(context, channel_name)``."""
    errors: list[str] = []
    for channel in (channels if channels is not None else channel_order()):
        kwargs: dict[str, Any] = dict(user_data_dir=str(profile_dir), headless=headless, locale="es-ES")
        if args:
            kwargs["args"] = list(args)
        if headless:
            kwargs["viewport"] = {"width": 1366, "height": 850}
        else:
            kwargs["no_viewport"] = True  # a visible window uses its real size
        if channel:
            kwargs["channel"] = channel
        try:
            return pw.chromium.launch_persistent_context(**kwargs), channel or "chromium"
        except Exception as error:  # noqa: BLE001 — try the next browser
            first_line = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
            errors.append(f"{channel or 'chromium'}: {first_line[:160]}")
            log.info("browser launch failed (%s): %s", channel or "chromium", first_line)
    raise BrowserUnavailable("no browser could be started (" + "; ".join(errors) + ")")


class _Worker:
    """A single daemon thread that runs callables in order; used so Playwright stays on one thread."""

    def __init__(self, idle_s: float, on_idle: Callable[[], None]):
        self.idle_s = idle_s
        self.on_idle = on_idle
        self._queue: "queue.Queue[tuple[Callable[[], Any], Future] | None]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def call(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        self._ensure_thread()
        future: Future = Future()
        self._queue.put((fn, future))
        return future.result(timeout=timeout)

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="tantalus-browser", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=self.idle_s)
            except queue.Empty:
                try:
                    self.on_idle()
                except Exception:  # noqa: BLE001
                    log.debug("idle cleanup failed", exc_info=True)
                continue
            if item is None:
                return
            fn, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as error:  # noqa: BLE001 — hand every failure back to the caller
                future.set_exception(error)

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._queue.put(None)
            thread.join(timeout=10)


class BrowserRung:
    def __init__(self, config: Any, *, playwright_factory: Callable[[], Any] | None = None,
                 channels: list[str | None] | None = None, idle_s: float = WORKER_IDLE_S,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self._factory = playwright_factory or _start_playwright
        self._channels = channels
        self._sleep = sleep
        self._gate = threading.Lock()          # one browser user at a time
        self._worker = _Worker(idle_s, self._release_in_worker)
        # worker-thread state (touched only from the worker thread)
        self._pw: Any = None
        self._ctx: Any = None
        self.channel: str = ""

    # ------------------------------------------------------------------ availability
    def available(self) -> tuple[bool, str]:
        if not getattr(self.config, "browser", True):
            return False, "the browser rung is disabled (TANTALUS_BROWSER=0)"
        if self._factory is _start_playwright and not playwright_installed():
            return False, "Playwright is not installed. " + INSTALL_HINT
        return True, ""

    # ------------------------------------------------------------------ fetch (worker thread)
    def fetch(self, url: str, *, timeout: float | None = None) -> FetchResult:
        started = time.monotonic()
        fr = FetchResult(url=url, tier="browser", fetched_at=time.time())
        ok, why = self.available()
        if not ok:
            fr.error = why
            return fr
        timeout_s = float(timeout or getattr(self.config, "http_timeout_s", 25.0))
        with self._gate:
            try:
                data = self._worker.call(lambda: self._fetch_in_worker(url, timeout_s), timeout=timeout_s + SETTLE_BUDGET_S + 30)
            except BrowserUnavailable as error:
                fr.error = str(error)
                return fr
            except Exception as error:  # noqa: BLE001 — navigation errors, timeouts, crashed browser
                self._reset_after_error()
                fr.error = f"browser error: {str(error).strip().splitlines()[0][:200] if str(error).strip() else type(error).__name__}"
                return fr
        fr.final_url = data["final_url"]
        fr.status = data["status"]
        fr.text = data["text"]
        fr.content_type = data["content_type"]
        fr.headers = data["headers"]
        fr.elapsed_ms = int((time.monotonic() - started) * 1000)
        return apply_block(fr, detect_block(fr.status, fr.text, fr.headers, fr.final_url or url))

    def _fetch_in_worker(self, url: str, timeout_s: float) -> dict[str, Any]:
        ctx = self._ensure_context()
        page = ctx.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
            self._settle(page)
            html = page.content()
            status = response.status if response is not None else 0
            headers = {str(k).lower(): str(v) for k, v in (response.headers if response is not None else {}).items()}
            verdict = detect_block(status, html, headers, page.url)
            # Some interstitials refresh themselves once the browser has run their script. Look again a
            # couple of times; never interact with the page.
            rechecks = 0
            while verdict in (CLOUDFLARE, AKAMAI) and rechecks < PASSIVE_RECHECKS:
                rechecks += 1
                page.wait_for_timeout(int(PASSIVE_RECHECK_WAIT_S * 1000))
                self._settle(page, budget=2.0)
                html = page.content()
                verdict = detect_block(200, html, {}, page.url)
                if not verdict:
                    status = 200
            return {"final_url": page.url, "status": status, "text": html,
                    "content_type": headers.get("content-type", "text/html"), "headers": headers}
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _settle(page: Any, budget: float = SETTLE_BUDGET_S) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=int(budget * 1000))
        except Exception:  # noqa: BLE001 — never idle (chatty pages) is fine
            pass

    def _ensure_context(self) -> Any:
        if self._ctx is not None:
            try:
                _ = self._ctx.pages  # raises when the browser was closed underneath us
                return self._ctx
            except Exception:  # noqa: BLE001
                self._release_in_worker()
        self.config.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = self._factory()
        try:
            self._ctx, self.channel = launch_context(self._pw, self.config.browser_profile_dir, headless=True, channels=self._channels)
        except Exception:
            self._release_in_worker()
            raise
        return self._ctx

    def _release_in_worker(self) -> None:
        """Close the worker's browser (worker thread only)."""
        ctx, pw = self._ctx, self._pw
        self._ctx = self._pw = None
        for closer in (getattr(ctx, "close", None), getattr(pw, "stop", None)):
            if closer:
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass

    def _reset_after_error(self) -> None:
        try:
            self._worker.call(self._release_in_worker, timeout=20)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ sessions (caller thread)
    @contextmanager
    def browser_session(self, *, headless: bool = True, args: list[str] | None = None) -> Iterator[Any]:
        """Yield a Playwright ``BrowserContext`` on the persistent profile.

        The context belongs to the *calling* thread (a private Playwright instance is started here), so use
        it on that thread only. The shared worker browser is closed for the duration, because a profile can
        be opened by one browser at a time.
        """
        ok, why = self.available()
        if not ok:
            raise TantalusError("fetch_failed", why, "Use a source that does not need the browser, or install Playwright.")
        with self._gate:
            if self._worker.alive():
                self._worker.call(self._release_in_worker, timeout=30)
            self.config.browser_profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                pw = self._factory()
            except Exception as error:  # noqa: BLE001
                raise TantalusError("fetch_failed", f"cannot start Playwright: {error}", INSTALL_HINT) from error
            try:
                ctx, self.channel = launch_context(pw, self.config.browser_profile_dir, headless=headless, channels=self._channels,
                                                   args=args)
            except BrowserUnavailable as error:
                _quiet(getattr(pw, "stop", None))
                raise TantalusError("fetch_failed", str(error), "Close any other browser window that uses the Tantalus profile.") from error
            try:
                yield ctx
            finally:
                _quiet(getattr(ctx, "close", None))
                _quiet(getattr(pw, "stop", None))

    def fetch_window(self, url: str, *, timeout: float | None = None) -> FetchResult:
        """Read a page in an ordinary visible browser window (started minimised) on the shared profile.

        For shops whose anti-bot layer turns away headless browsers but serves a normal browser (Carrefour): the
        browser is unmodified, nothing is spoofed or solved, the window opens and closes for one page, and the
        profile is the one the person uses with «Resolver», so a check they passed there counts here too. A
        challenge that still shows after the passive waits is reported as blocked (needs a human).
        """
        started = time.monotonic()
        fr = FetchResult(url=url, tier="window", fetched_at=time.time())
        ok, why = self.available()
        if not ok:
            fr.error = why
            return fr
        timeout_s = float(timeout or getattr(self.config, "http_timeout_s", 25.0)) + 20
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                with self.browser_session(headless=False, args=["--start-minimized"]) as ctx:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
                    self._settle(page)
                    html = page.content()
                    status = response.status if response is not None else 0
                    headers = {str(k).lower(): str(v) for k, v in (response.headers if response is not None else {}).items()}
                    verdict = detect_block(status, html, headers, page.url)
                    rechecks = 0
                    while verdict in (CLOUDFLARE, AKAMAI) and rechecks < PASSIVE_RECHECKS:
                        rechecks += 1
                        page.wait_for_timeout(int(PASSIVE_RECHECK_WAIT_S * 1000))
                        self._settle(page, budget=2.0)
                        html = page.content()
                        verdict = detect_block(200, html, {}, page.url)
                        if not verdict:
                            status = 200
                    outcome.update({"final_url": page.url, "status": status, "text": html, "headers": headers,
                                    "content_type": headers.get("content-type", "text/html")})
            except BaseException as error:  # noqa: BLE001
                outcome["error"] = error

        thread = threading.Thread(target=run, name="tantalus-browser-window", daemon=True)
        thread.start()
        thread.join(timeout=timeout_s + SETTLE_BUDGET_S + 60)
        error = outcome.get("error")
        if error is not None or "text" not in outcome:
            text = str(error or "the window did not answer in time").strip().splitlines()
            fr.error = f"browser window error: {text[0][:200] if text else type(error).__name__}"
            return fr
        fr.final_url = outcome["final_url"]
        fr.status = outcome["status"]
        fr.text = outcome["text"]
        fr.content_type = outcome["content_type"]
        fr.headers = outcome["headers"]
        fr.elapsed_ms = int((time.monotonic() - started) * 1000)
        return apply_block(fr, detect_block(fr.status, fr.text, fr.headers, fr.final_url or url))

    def open_for_human(self, url: str, *, timeout_s: float = 900.0) -> dict[str, Any]:
        """Open a visible window on the shared profile and return when the person closes it.

        Used by the "resolve" button: the human solves a CAPTCHA or logs in, the cookies stay in the profile
        and later headless fetches benefit. Runs on a private thread so the caller's thread (which may host
        an event loop) is never used for Playwright.
        """
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                with self.browser_session(headless=False) as ctx:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    except Exception as error:  # noqa: BLE001 — the person can navigate by hand
                        outcome["goto_error"] = str(error)[:200]
                    try:
                        ctx.wait_for_event("close", timeout=int(timeout_s * 1000))
                        outcome["closed_by_user"] = True
                    except Exception:  # noqa: BLE001 — timeout: we close it ourselves
                        outcome["closed_by_user"] = False
                    try:
                        outcome["final_url"] = page.url
                    except Exception:  # noqa: BLE001
                        pass
            except BaseException as error:  # noqa: BLE001
                outcome["error"] = error

        thread = threading.Thread(target=run, name="tantalus-browser-human", daemon=True)
        thread.start()
        thread.join(timeout=timeout_s + 60)
        error = outcome.get("error")
        if isinstance(error, TantalusError):
            raise error
        if error is not None:
            raise TantalusError("fetch_failed", f"browser error: {error}")
        return {"url": url, "channel": self.channel, **{k: v for k, v in outcome.items() if k != "error"}}

    # ------------------------------------------------------------------ shutdown
    def close(self) -> None:
        try:
            with self._gate:
                if self._worker.alive():
                    self._worker.call(self._release_in_worker, timeout=30)
        except Exception:  # noqa: BLE001
            pass
        self._worker.stop()


def _quiet(fn: Callable[[], Any] | None) -> None:
    if fn:
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
