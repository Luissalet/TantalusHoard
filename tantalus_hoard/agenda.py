"""The family agenda: release days Tantalus knows, answered to the hub as ``GET /api/family/agenda``.

Two sources, both real dated things and nothing else:

* the aggregator radar's release calendar (StockTCG releases of the products the watchers follow);
* targets whose latest check says "coming soon", "restock scheduled" or "pre-order" WITH a date the page stated.

Items are ``kind: release``. ``provider(...)`` is what ``fam_agenda.install_fastapi`` calls; it never raises.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from .model import COMING_SOON, MODE_AVAILABILITY, PREORDER, RESTOCK_SCHEDULED
from .radar.core import local_today

DATED_STATES = (COMING_SOON, RESTOCK_SCHEDULED, PREORDER)
STATE_LABEL = {COMING_SOON: "coming soon", RESTOCK_SCHEDULED: "restock scheduled", PREORDER: "pre-order"}


def _iso_day(value: Any) -> str:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _where(data: dict[str, Any]) -> str:
    """One short line: how many shops have it and the chains."""
    bits = []
    if data.get("stores_total"):
        bits.append(f"{data.get('stores_buyable') or 0}/{data['stores_total']} shops with stock")
    chains = [f"{name}: {state}" for name, state in list((data.get("where") or {}).items())[:4]] if isinstance(data.get("where"), dict) else []
    return "; ".join(bits + chains)[:240]


def build_items(svc: Any, date_from: date, date_to: date, *, base_url: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_days: set[tuple[str, str]] = set()
    today = local_today(svc.clock())
    # --- the radar's release calendar
    try:
        rows = svc.radar.releases(upcoming_days=max(0, (date_to - today).days), past_days=max(0, (today - date_from).days))
    except Exception:  # noqa: BLE001 - the radar is optional
        rows = []
    for row in rows:
        day = _iso_day(row.get("date"))
        if not day or not date_from <= date.fromisoformat(day) <= date_to:
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        seen_days.add((title.lower(), day))
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        soon = row.get("days") is not None and -1 <= int(row["days"]) <= 1
        items.append({"id": f"tantalus:release:r{row['id']}", "title": title, "start": day, "all_day": True, "kind": "release",
                      "priority": "high" if soon else "normal", "url": row.get("url") or base_url, "detail": _where(data)})
    # --- targets with a stated date
    try:
        watchers = {w["id"]: w for w in svc.store.watchers(enabled=True, mode=MODE_AVAILABILITY)}
        targets = [t for t in svc.store.targets() if t["watcher_id"] in watchers and t.get("last_state") in DATED_STATES
                   and t.get("status") != "paused"]
    except Exception:  # noqa: BLE001
        targets = []
    for target in targets:
        try:
            latest = svc.store.observations(target["id"], limit=1)
        except Exception:  # noqa: BLE001
            latest = []
        if not latest:
            continue
        day = _iso_day(latest[0].get("restock_date") or latest[0].get("preorder_date"))
        if not day or not date_from <= date.fromisoformat(day) <= date_to:
            continue
        title = str(target.get("last_title") or target.get("label") or watchers[target["watcher_id"]]["name"]).strip()
        if (title.lower(), day) in seen_days:
            continue
        shop = target.get("retailer") or target.get("host") or ""
        items.append({"id": f"tantalus:release:{target['id']}", "title": title, "start": day, "all_day": True, "kind": "release",
                      "priority": "normal", "url": target.get("url") or base_url,
                      "detail": f"{STATE_LABEL.get(target['last_state'], '')}{' · ' + shop if shop else ''}"[:240]})
    items.sort(key=lambda i: (i["start"], i["title"]))
    return items


def make_provider(get_services: Callable[[], Any], base_url: Callable[[], str]) -> Callable[[date, date, str], list[dict[str, Any]]]:
    """``provider(date_from, date_to, sphere)`` for ``fam_agenda.install_fastapi``; ``sphere`` is ignored (Tantalus has no spheres)."""
    def provider(date_from: date, date_to: date, sphere: str) -> list[dict[str, Any]]:
        svc = get_services()
        if svc is None:
            return []
        return build_items(svc, date_from, date_to, base_url=base_url())
    return provider
