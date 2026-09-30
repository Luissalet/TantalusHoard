"""Background scheduler: one worker thread that runs what is due, one job at a time (politeness first).

Order of each tick: pending revalidations (they are time-sensitive) -> due product targets -> due second-hand
and information watchers -> due discovery passes. A job that raises is logged and never stops the loop.
Manual "check now" requests from the UI or the assistant go through the same queue, so two checks of the same
host never overlap.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .model import MODE_AVAILABILITY, MODE_INFORMATION, MODE_SECONDHAND

log = logging.getLogger("tantalus.scheduler")

TICK_S = 15.0
MIN_WATCHER_INTERVAL_MIN = 10  # floor inherited from Radar de Libros: never hammer a marketplace


@dataclass
class Job:
    kind: str                 # check | revalidate | secondhand | information | discovery
    ref: str
    reason: str = "schedule"
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str = ""


class Scheduler:
    def __init__(self, engine: Any, store: Any, *, clock: Callable[[], float] = time.time, enabled: bool = True,
                 paused: Callable[[], bool] = lambda: False):
        self.engine = engine
        self.store = store
        self.clock = clock
        self.enabled = enabled
        self.paused = paused
        self._queue: "queue.Queue[Job]" = queue.Queue()
        self._pending: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.current: Optional[Job] = None
        self.last_tick_ts: Optional[float] = None
        self.jobs_done = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tantalus-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def status(self) -> dict[str, Any]:
        cur = self.current
        return {"enabled": self.enabled, "running": bool(self._thread and self._thread.is_alive()), "paused": bool(self.paused()),
                "queue": self._queue.qsize(), "current": {"kind": cur.kind, "ref": cur.ref, "reason": cur.reason} if cur else None,
                "last_tick_ts": self.last_tick_ts, "jobs_done": self.jobs_done}

    # ------------------------------------------------------------------ queue
    def submit(self, kind: str, ref: str, reason: str = "manual") -> Optional[Job]:
        key = (kind, ref)
        with self._lock:
            if key in self._pending:
                return None
            self._pending.add(key)
        job = Job(kind, ref, reason)
        self._queue.put(job)
        return job

    def run_now(self, kind: str, ref: str, timeout: float = 240.0) -> Any:
        """Queue a job and wait for it (used by 'check now'). Without a running loop, run inline."""
        if not (self._thread and self._thread.is_alive()):
            return self._execute(Job(kind, ref, "inline"))
        job = self.submit(kind, ref)
        if job is None:
            return {"queued": True, "note": "already queued"}
        if not job.done.wait(timeout):
            return {"queued": True, "note": "still running; the result will appear in the history"}
        if job.error:
            raise RuntimeError(job.error)
        return job.result

    def _execute(self, job: Job) -> Any:
        eng = self.engine
        if job.kind == "check":
            return eng.check_target(job.ref)
        if job.kind == "revalidate":
            return eng.revalidate(job.ref)
        if job.kind == "secondhand":
            return eng.run_secondhand(job.ref)
        if job.kind == "information":
            return eng.run_information(job.ref)
        if job.kind == "discovery":
            return eng.run_discovery(job.ref)
        raise ValueError(f"unknown job kind {job.kind}")

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                job = None
            if job is not None:
                self._run(job)
                continue
            now = self.clock()
            if self.last_tick_ts is None or now - self.last_tick_ts >= TICK_S:
                self.last_tick_ts = now
                if self.enabled and not self.paused():
                    try:
                        self.enqueue_due(now)
                    except Exception:  # noqa: BLE001
                        log.exception("scheduler tick failed")

    def _run(self, job: Job) -> None:
        self.current = job
        try:
            job.result = self._execute(job)
        except Exception as error:  # noqa: BLE001
            job.error = f"{type(error).__name__}: {error}"
            log.warning("job %s %s failed: %s", job.kind, job.ref, job.error)
        finally:
            with self._lock:
                self._pending.discard((job.kind, job.ref))
            self.current = None
            self.jobs_done += 1
            job.done.set()

    def enqueue_due(self, now: float) -> int:
        n = 0
        for ev in self.store.pending_revalidations(now):
            n += bool(self.submit("revalidate", ev["id"], "schedule"))
        for t in self.store.due_targets(now, limit=30):
            n += bool(self.submit("check", t["id"], "schedule"))
        for w in self.store.watchers(enabled=True):
            if w["mode"] in (MODE_SECONDHAND, MODE_INFORMATION):
                due = w.get("next_run_ts") is None or w["next_run_ts"] <= now
                last = w.get("last_run_ts")
                if due and (last is None or now - last >= MIN_WATCHER_INTERVAL_MIN * 60):
                    n += bool(self.submit(w["mode"], w["id"], "schedule"))
            if w["mode"] == MODE_AVAILABILITY:
                disc = (w.get("config") or {}).get("discovery") or {}
                if disc.get("queries") and disc.get("enabled", True):
                    hours = float(w.get("discovery_interval_h") or 24)
                    last = w.get("last_discovery_ts")
                    if hours > 0 and (last is None or now - last >= hours * 3600):
                        n += bool(self.submit("discovery", w["id"], "schedule"))
        return n
