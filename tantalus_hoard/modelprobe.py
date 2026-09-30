"""Cached, time-boxed model resolution: one worker thread, a 60 s cache, never a long block on the request path."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional


class ModelProbe:
    def __init__(self, probe: Callable[[], Any], ttl: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._probe, self.ttl, self._clock = probe, ttl, clock
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tantalus-model-probe")
        self._lock = threading.Lock()
        self._future: Optional[Future] = None
        self._value: Any = None
        self._at: Optional[float] = None
        self.probes = 0

    def _run(self) -> Any:
        try:
            value = self._probe()
        except Exception as error:  # noqa: BLE001 — an unreachable model must never break status
            value = {"error": str(error)}
        with self._lock:
            self._value, self._at = value, self._clock()
        return value

    def _kick(self) -> Future:
        with self._lock:
            if self._future is None or self._future.done():
                self.probes += 1
                self._future = self._pool.submit(self._run)
            return self._future

    def get(self, wait: Optional[float] = 0.8) -> Any:
        """The cached result; a fresh probe runs in the background when it is older than the TTL.

        Waits at most ``wait`` seconds (``None`` = until done) and answers ``{"state": "probing"}`` if nothing is known yet."""
        with self._lock:
            fresh = self._at is not None and self._clock() - self._at < self.ttl
            known = self._at is not None
            value = self._value
        if fresh:
            return value
        future = self._kick()
        if known:
            return value  # stale-while-revalidate: the refresh lands in the cache for the next call
        try:
            return future.result(timeout=wait)
        except TimeoutError:
            return {"state": "probing"}
        except Exception as error:  # noqa: BLE001
            return {"error": str(error)}

    def llm_block(self) -> str:
        """Why the language model is known to be unavailable right now, or '' (available, or not known)."""
        data = self.get(wait=None)
        llm = data.get("llm") if isinstance(data, dict) else None
        if isinstance(llm, dict) and llm.get("state") and llm.get("state") != "resolved":
            return str(llm.get("reason") or f"model state: {llm.get('state')}")
        return ""

    def wait_idle(self, timeout: float = 10.0) -> None:
        future = self._future
        if future is not None:
            try:
                future.result(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
