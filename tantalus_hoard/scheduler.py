"""Background scheduler: two worker lanes, each running one job at a time.

- ``checks`` lane: revalidations and product-target checks (short, time-sensitive).
- ``sweeps`` lane: second-hand sweeps, information sweeps, discovery and the extra kinds (long: many queries, polite pauses).

The lanes, the de-duplication, ``run_now`` and the worker threads are the commons' ``hoard_link.lanes.LaneScheduler``; what stays
here is what is Tantalus's own: which targets and watchers are due (read from the store on every tick) and how a job kind runs
in the engine. Extra kinds are registered by the caller as ``{kind: (due(now) -> bool, run(ref) -> Any)}``; the mailbox scan is one
of them.

A long Wallapop sweep therefore never delays a restock check. Per-host politeness lives in the fetcher (one
request at a time per host, minimum interval), so the two lanes can run side by side safely. A job that raises is
logged and never stops a lane. Manual "check now" requests go through the same queues.
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import Any, Callable, Optional

from .hoard_link.lanes import Job as _LaneJob, LaneScheduler
from .model import MODE_AVAILABILITY, MODE_INFORMATION, MODE_SECONDHAND

log = logging.getLogger("tantalus.scheduler")

TICK_S = 15.0
MIN_WATCHER_INTERVAL_MIN = 10  # floor inherited from Radar de Libros: never hammer a marketplace


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
        self.lanes = LaneScheduler({lane: 1 for lane in LANES}, enabled=enabled, paused=paused, clock=clock, tick_s=TICK_S, name="tantalus")
        # the tick: one registered job that asks the store what is due (the due logic is Tantalus's own)
        self.lanes.register(_LaneJob("tick", key="tick", fn=lambda: self.enqueue_due(self.clock()), lane="checks", every_s=1.0))

    @property
    def last_tick_ts(self) -> Optional[float]:
        return self.lanes.last_tick_ts

    @property
    def jobs_done(self) -> int:
        return self.lanes.jobs_done - int(self.lanes._stats.get("tick", {}).get("runs", 0))

    # ------------------------------------------------------------------ lifecycle
    def _alive(self) -> bool:
        return self.lanes._alive()

    def start(self) -> None:
        self.lanes.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.lanes.stop(timeout)

    def status(self) -> dict[str, Any]:
        raw = self.lanes.status()

        def view(job: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
            return {"kind": job["kind"], "ref": job["key"][len(job["kind"]) + 1:], "reason": job["reason"]} if job else None

        def current(lane: str) -> Optional[dict[str, str]]:
            running = [j for j in raw["lanes"][lane]["current"] if j["kind"] != "tick"]
            return view(running[0]) if running else None

        lanes = {lane: {"queue": raw["lanes"][lane]["queue"], "current": current(lane)} for lane in LANES}
        return {"enabled": self.enabled, "running": raw["running"], "paused": bool(self.paused()),
                "queue": sum(v["queue"] for v in lanes.values()), "current": lanes["checks"]["current"] or lanes["sweeps"]["current"],
                "lanes": lanes, "last_tick_ts": raw["last_tick_ts"], "jobs_done": self.jobs_done}

    # ------------------------------------------------------------------ queue
    def _job(self, kind: str, ref: str, reason: str) -> _LaneJob:
        return _LaneJob(kind, key=f"{kind}:{ref}", fn=partial(self._execute, kind, ref), lane=lane_of(kind), reason=reason)

    def submit(self, kind: str, ref: str, reason: str = "manual") -> Optional[_LaneJob]:
        return self.lanes.submit(self._job(kind, ref, reason))

    def run_now(self, kind: str, ref: str, timeout: float = 240.0) -> Any:
        """Queue a job and wait for it (used by 'check now'). Without a running loop, run inline."""
        return self.lanes.run_now(self._job(kind, ref, "manual"), timeout)

    def _execute(self, kind: str, ref: str) -> Any:
        eng = self.engine
        if kind == "check":
            return eng.check_target(ref)
        if kind == "revalidate":
            return eng.revalidate(ref)
        if kind == "secondhand":
            return eng.run_secondhand(ref)
        if kind == "information":
            return eng.run_information(ref)
        if kind == "discovery":
            return eng.run_discovery(ref)
        if kind in self.extra:
            return self.extra[kind][1](ref)
        raise ValueError(f"unknown job kind {kind}")

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
