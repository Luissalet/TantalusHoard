"""Published monitoring metadata; no queries, URLs, notes or log bodies leave this app."""
import re


def _label(value):
    text = " ".join(str(value or "").split())
    text = re.sub(r"(?i)\b(password|passwd|contraseña|api[_ -]?key|access[_ -]?token)\s*[:=]\s*\S+", r"\1 ***", text)
    text = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}", "***", text)
    return text[:120]


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
    items = []
    for watcher in svc.store.watchers():
        record = outcomes.get(watcher["id"], {})
        items.append({"id": "tantalus:watcher:" + str(watcher["id"]), "owner": "tantalus", "kind": "watcher",
            "label": _label(watcher["name"]), "created_ts": watcher["created_ts"],
            "every": f"{max(5, int(watcher.get('interval_min') or 30))}m",
            "enabled": bool(watcher["enabled"]), "paused": bool(scheduler["paused"] or not scheduler["enabled"]),
            "last_success_ts": record.get("last_success_ts"), "last_error_ts": record.get("last_error_ts"),
            "last_status": "ok" if latest.get(watcher["id"]) is True else "error" if latest.get(watcher["id"]) is False else None,
            "needs_user": bool(blocked.get(watcher["id"])), "user_reason": "other" if blocked.get(watcher["id"]) else None,
            "partial": bool(watcher.get("last_error")),
            "error_code": "watcher_run_failed" if latest.get(watcher["id"]) is False else None})
    return {"ok": True, "owner": "tantalus", "items": items,
        "coverage": {"outcomes": "retained_finished_runs", "retention": 5000,
            "success_basis": "app_confirmed_run_result; not evidence of stock", "targets_needing_user": "current_status"},
        "scheduler": {k: scheduler[k] for k in ("enabled", "running", "paused", "last_tick_ts")}}
