"""The two scheduler lanes: a slow sweep never blocks a product check."""

from __future__ import annotations

import threading
import time

from tantalus_hoard.scheduler import Scheduler, lane_of


class SlowEngine:
    def __init__(self):
        self.release = threading.Event()
        self.done: list[str] = []

    def run_secondhand(self, ref):
        self.release.wait(5)
        self.done.append(f"sweep:{ref}")
        return {"ok": True}

    def check_target(self, ref):
        self.done.append(f"check:{ref}")
        return {"state": "IN_STOCK"}


class NoStore:
    def pending_revalidations(self, now):
        return []

    def due_targets(self, now, limit=30):
        return []

    def watchers(self, enabled=True):
        return []


def test_lanes():
    assert lane_of("check") == lane_of("revalidate") == "checks"
    assert lane_of("secondhand") == lane_of("information") == lane_of("discovery") == "sweeps"


def test_check_runs_while_a_sweep_is_busy():
    engine = SlowEngine()
    sched = Scheduler(engine, NoStore())
    sched.start()
    try:
        sched.submit("secondhand", "w1")
        time.sleep(0.2)
        assert sched.status()["lanes"]["sweeps"]["current"]["kind"] == "secondhand"
        result = sched.run_now("check", "t1", timeout=5)
        assert result == {"state": "IN_STOCK"} and engine.done == ["check:t1"]
        engine.release.set()
        for _ in range(50):
            if "sweep:w1" in engine.done:
                break
            time.sleep(0.05)
        assert engine.done == ["check:t1", "sweep:w1"]
    finally:
        engine.release.set()
        sched.stop()


def test_duplicate_submissions_are_ignored():
    sched = Scheduler(SlowEngine(), NoStore())
    assert sched.submit("check", "t1") is not None
    assert sched.submit("check", "t1") is None
