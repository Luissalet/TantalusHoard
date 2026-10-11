"""Published monitoring metadata; no queries, URLs, notes or log bodies leave this app."""
import re

from .model import MODE_AVAILABILITY, MODE_INFORMATION, MODE_SECONDHAND

CHECKABLE = ("active", "needs_human", "error")   # the target states the scheduler still visits


def _label(value):
    text = " ".join(str(value or "").split())
    text = re.sub(r"(?i)\b(password|passwd|contraseña|api[_ -]?key|access[_ -]?token)\s*[:=]\s*\S+", r"\1 ***", text)
    text = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}", "***", text)
    return text[:120]


def effective_every_min(watcher, targets):
    """How often something of this watcher really runs, in minutes; None when nothing is expected to run.

    ``watcher.interval_min`` is only the default: product targets carry their own interval, a target that needs
    the person is revisited every 3 hours at most often, a failing one backs off, and the second-hand and
    information sweeps never run more often than the scheduler's floor. Publishing the nominal value would make
    a quiet but healthy watcher look silently stopped. Any run of any target counts as the watcher running, so
    the shortest effective period of its live targets is the one to expect.
    """
    from .engine import MAX_BACKOFF_MIN, NEEDS_HUMAN_RETRY_MIN
    from .scheduler import MIN_WATCHER_INTERVAL_MIN
    base = float(watcher.get("interval_min") or 30)
    if watcher.get("mode") in (MODE_SECONDHAND, MODE_INFORMATION):
        return max(float(MIN_WATCHER_INTERVAL_MIN), base)
    periods = []
    for target in targets:
        every = float(target.get("interval_min") or base)
        if target.get("status") == "needs_human":
            every = max(every, float(NEEDS_HUMAN_RETRY_MIN))
        elif target.get("status") == "error":
            every = min(float(MAX_BACKOFF_MIN), every * 2 ** min(int(target.get("fail_count") or 0), 5))
        periods.append(every)
    discovery = (watcher.get("config") or {}).get("discovery") or {}
    if watcher.get("mode") == MODE_AVAILABILITY and discovery.get("queries") and discovery.get("enabled", True):
        hours = float(watcher.get("discovery_interval_h") or 24)
        if hours > 0:
            periods.append(hours * 60)
    return max(5.0, min(periods)) if periods else None


def fleet_status(svc):
    scheduler = svc.scheduler.status()
    outcomes = {row["watcher_id"]: dict(row) for row in svc.db.query(
        "SELECT watcher_id, MAX(CASE WHEN ok=1 THEN finished_ts END) AS last_success_ts, "
        "MAX(CASE WHEN ok=0 THEN finished_ts END) AS last_error_ts, MAX(id) AS last_id "
        "FROM runs WHERE finished_ts IS NOT NULL GROUP BY watcher_id")}
    latest = {row["watcher_id"]: bool(row["ok"]) for row in svc.db.query(
        "SELECT watcher_id,ok FROM runs WHERE id IN (SELECT MAX(id) FROM runs WHERE finished_ts IS NOT NULL GROUP BY watcher_id)")}
    blocked = {row["watcher_id"]: row["n"] for row in svc.db.query(
        "SELECT watcher_id,COUNT(*) AS n FROM targets WHERE status='needs_human' GROUP BY watcher_id")}
    live = {}
    for row in svc.db.query("SELECT watcher_id, status, interval_min, fail_count FROM targets WHERE status IN ('active','needs_human','error')"):
        live.setdefault(row["watcher_id"], []).append(dict(row))
    items = []
    for watcher in svc.store.watchers():
        record = outcomes.get(watcher["id"], {})
        every = effective_every_min(watcher, live.get(watcher["id"], []))
        if every is None and watcher["enabled"]:
            continue   # nothing to check yet (no targets, no discovery): nothing is expected, so nothing can go silent
        every = every or max(5.0, float(watcher.get("interval_min") or 30))   # a paused watcher keeps its nominal cadence
        items.append({"id": "tantalus:watcher:" + str(watcher["id"]), "owner": "tantalus", "kind": "watcher",
            "label": _label(watcher["name"]), "created_ts": watcher["created_ts"],
            "every": f"{int(round(every))}m",
            "enabled": bool(watcher["enabled"]), "paused": bool(scheduler["paused"] or not scheduler["enabled"]),
            "last_success_ts": record.get("last_success_ts"), "last_error_ts": record.get("last_error_ts"),
            "last_status": "ok" if latest.get(watcher["id"]) is True else "error" if latest.get(watcher["id"]) is False else None,
            "needs_user": bool(blocked.get(watcher["id"])), "user_reason": "other" if blocked.get(watcher["id"]) else None,
            "partial": bool(watcher.get("last_error")),
            "error_code": "watcher_run_failed" if latest.get(watcher["id"]) is False else None})
    return {"ok": True, "owner": "tantalus", "items": items,
        "coverage": {"outcomes": "retained_finished_runs", "retention": 5000,
            "success_basis": "app_confirmed_run_result; not evidence of stock", "targets_needing_user": "current_status",
            "cadence": "effective: shortest live target interval (needs_human >= 180m, error backoff), sweeps >= 10m; "
                       "watchers with nothing to check are not published"},
        "scheduler": {k: scheduler[k] for k in ("enabled", "running", "paused", "last_tick_ts")}}
