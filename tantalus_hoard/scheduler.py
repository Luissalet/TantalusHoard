"""Background scheduler: two worker lanes, each running one job at a time.

- ``checks`` lane: revalidations and product-target checks (short, time-sensitive).
- ``sweeps`` lane: second-hand sweeps, information sweeps, discovery and the extra kinds (long: many queries, polite pauses).

Extra kinds are registered by the caller as ``{kind: (due(now) -> bool, run(ref) -> Any)}``; the mailbox scan is one of them.

A long Wallapop sweep therefore never delays a restock check. Per-host politeness lives in the fetcher (one
request at a time per host, minimum interval), so the two lanes can run side by side safely. A job that raises is
logged and never stops a lane. Manual "check now" requests go through the same queues.
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
    kind: str                 # check | revalidate | secondhand | information | discovery | (an extra kind such as mail_deals)
    ref: str
    reason: str = "schedule"
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str = ""


LANES = ("checks", "sweeps")


def lane_of(kind: str) -> str:
    return "checks" if kind in ("check", "revalidate") else "sweeps"


class Scheduler:
    def __init__(self, engine: Any, store: Any, *, clock: Callable[[], float] = time.time, enabled: bool = True,
                 paused: Callable[[], bool] = lambda: False,
                 extra: Optional[dict[str, tuple[Callable[[float], bool], Callable[[str], Any]]]] = None):
        self.extra = dict(extra or {})
        self.engine = engine
        self.store = store
        self.clock = clock
        self.enabled = enabled
        self.paused = paused
        self._queues: dict[str, "queue.Queue[Job]"] = {lane: queue.Queue() for lane in LANES}
        self._pending: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._current: dict[str, Optional[Job]] = {lane: None for lane in LANES}
        self.last_tick_ts: Optional[float] = None
        self.jobs_done = 0

    @property
    def current(self) -> Optional[Job]:
        return self._current["checks"] or self._current["sweeps"]

    def _alive(self) -> bool:
        return any(t.is_alive() for t in self._threads.values())

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._alive():
            return
        self._stop.clear()
        for lane in LANES:
            thread = threading.Thread(target=self._loop, args=(lane,), name=f"tantalus-{lane}", daemon=True)
            self._threads[lane] = thread
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for thread in self._threads.values():
            thread.join(timeout)

    def status(self) -> dict[str, Any]:
        def view(job: Optional[Job]) -> Optional[dict[str, str]]:
            return {"kind": job.kind, "ref": job.ref, "reason": job.reason} if job else None
        cur = self.current
        return {"enabled": self.enabled, "running": self._alive(), "paused": bool(self.paused()),
                "queue": sum(q.qsize() for q in self._queues.values()), "current": view(cur),
                "lanes": {lane: {"queue": self._queues[lane].qsize(), "current": view(self._current[lane])} for lane in LANES},
                "last_tick_ts": self.last_tick_ts, "jobs_done": self.jobs_done}

    # ------------------------------------------------------------------ queue
    def submit(self, kind: str, ref: str, reason: str = "manual") -> Optional[Job]:
        key = (kind, ref)
        with self._lock:
            if key in self._pending:
                return None
            self._pending.add(key)
        job = Job(kind, ref, reason)
        self._queues[lane_of(kind)].put(job)
        return job

    def run_now(self, kind: str, ref: str, timeout: float = 240.0) -> Any:
        """Queue a job and wait for it (used by 'check now'). Without a running loop, run inline."""
        if not self._alive():
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
        if job.kind in self.extra:
            return self.extra[job.kind][1](job.ref)
        raise ValueError(f"unknown job kind {job.kind}")

    # ------------------------------------------------------------------ loop
    def _loop(self, lane: str) -> None:
        while not self._stop.is_set():
            try:
                job = self._queues[lane].get(timeout=1.0)
            except queue.Empty:
                job = None
            if job is not None:
                self._run(job, lane)
                continue
            if lane != "checks":
                continue  # one lane owns the tick
            now = self.clock()
            if self.last_tick_ts is None or now - self.last_tick_ts >= TICK_S:
                self.last_tick_ts = now
                if self.enabled and not self.paused():
                    try:
                        self.enqueue_due(now)
                    except Exception:  # noqa: BLE001
                        log.exception("scheduler tick failed")

    def _run(self, job: Job, lane: str) -> None:
        self._current[lane] = job
        try:
            job.result = self._execute(job)
        except Exception as error:  # noqa: BLE001
            job.error = f"{type(error).__name__}: {error}"
            log.warning("job %s %s failed: %s", job.kind, job.ref, job.error)
        finally:
            with self._lock:
                self._pending.discard((job.kind, job.ref))
            self._current[lane] = None
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
        for kind, (due_fn, _run) in self.extra.items():
            try:
                if due_fn(now):
                    n += bool(self.submit(kind, "all", "schedule"))
            except Exception:  # noqa: BLE001 - a broken extra job must not stop the rest of the tick
                log.exception("due check of %s failed", kind)
        return n
