"""The mail noise report: which sender domains fill the mailbox with promotional mail nobody reads.

A pure aggregator over header records (sender, date, ``List-Unsubscribe``, Gmail category) as the Faustus mail helper returns
them. It never touches the mailbox and never follows a link: the unsubscribe links it reports are text for the user to act on.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from ..hoard_link.web.urls import registrable_domain
from .stores import CONSUMER_TANTALUS, consumers_for, domain_of

PROMO_CATEGORIES = {"promotions", "social", "forums"}
ANGLE = re.compile(r"<([^<>]+)>")
MIN_NOISE_MAILS = 3


def registrable(domain: str) -> str:
    """``news.mail.example.com`` -> ``example.com``; ``shop.example.co.uk`` -> ``example.co.uk`` (the commons' suffix rules)."""
    return registrable_domain(domain) or domain.lower().strip(".")


def unsubscribe_links(value: str) -> dict[str, str]:
    """The ``http(s)`` and ``mailto`` entries of a List-Unsubscribe header (read as text, never opened)."""
    out = {"http": "", "mailto": ""}
    for item in ANGLE.findall(value or ""):
        item = item.strip()
        low = item.lower()
        if low.startswith(("http://", "https://")) and not out["http"]:
            out["http"] = item[:400]
        elif low.startswith("mailto:") and not out["mailto"]:
            out["mailto"] = item[:300]
    return out


def is_promotional(record: dict[str, Any]) -> bool:
    category = str(record.get("category") or "").lower()
    if category in PROMO_CATEGORIES:
        return True
    return bool(record.get("list_unsubscribe")) and category != "updates"


def build_report(records: Iterable[dict[str, Any]], *, stores: Iterable[dict[str, Any]] = (), days: int = 30, top: int = 25) -> dict[str, Any]:
    """Aggregate header records into the noise report. Own mail (``from_self``) is left out of every count."""
    stores = list(stores)
    total = own = promo = 0
    per: dict[str, dict[str, Any]] = {}
    for rec in records:
        total += 1
        if rec.get("from_self"):
            own += 1
            continue
        address = str(rec.get("from_address") or "").lower()
        domain = registrable(domain_of(address))
        if not domain:
            continue
        slot = per.setdefault(domain, {"domain": domain, "count": 0, "promotional": 0, "categories": Counter(), "unsubscribe_mails": 0,
                                       "one_click": 0, "last_ts": 0, "http": "", "mailto": "", "locals": set(), "senders": Counter()})
        slot["count"] += 1
        slot["categories"][str(rec.get("category") or "none").lower() or "none"] += 1
        slot["locals"].add(address.partition("@")[0])
        slot["senders"][address] += 1
        if is_promotional(rec):
            slot["promotional"] += 1
            promo += 1
        header = str(rec.get("list_unsubscribe") or "")
        if header:
            slot["unsubscribe_mails"] += 1
            slot["one_click"] += 1 if rec.get("one_click") else 0
        ts = int(rec.get("ts") or 0)
        if ts >= slot["last_ts"]:
            links = unsubscribe_links(header)
            slot["last_ts"] = ts
            if links["http"] or links["mailto"]:
                slot["http"], slot["mailto"] = links["http"], links["mailto"]
    inbound = total - own
    rows = []
    for slot in per.values():
        consumers = consumers_for(slot["domain"], sorted(slot["locals"])[:20], stores)
        mostly_promo = slot["promotional"] * 2 >= slot["count"]
        rows.append({
            "domain": slot["domain"], "count": slot["count"], "share": round(slot["count"] / inbound, 4) if inbound else 0.0,
            "promotional": slot["promotional"], "mostly_promotional": mostly_promo,
            "categories": dict(slot["categories"].most_common()),
            "unsubscribe": {"available": bool(slot["http"] or slot["mailto"]), "http": slot["http"], "mailto": slot["mailto"],
                            "one_click": slot["one_click"] > 0, "mails_with_header": slot["unsubscribe_mails"]},
            "last_ts": slot["last_ts"], "last_date": datetime.fromtimestamp(slot["last_ts"], timezone.utc).strftime("%Y-%m-%d") if slot["last_ts"] else "",
            "consumers": consumers, "read_by_a_hoard": bool(consumers), "promotions_read": CONSUMER_TANTALUS in consumers,
            "senders": [a for a, _ in slot["senders"].most_common(3)],
        })
    rows.sort(key=lambda r: (-r["count"], r["domain"]))
    # Noise: a sender with a real share of promotional mail that no Hoard reads as promotions. A Hoard that reads a sender's receipts or
    # parcel mails (Ledger, Phileas, Kafka, JobHunter) does not read its promotions; only Tantalus does, and only for the stores it follows.
    noise = [r for r in rows if r["promotional"] >= MIN_NOISE_MAILS and r["promotional"] * 3 >= r["count"] and CONSUMER_TANTALUS not in r["consumers"]]
    noise.sort(key=lambda r: (-r["promotional"], r["domain"]))
    return {"window_days": int(days), "total": total, "own": own, "inbound": inbound, "promotional": promo,
            "promotional_share": round(promo / inbound, 4) if inbound else 0.0, "domains_seen": len(rows),
            "noise_count": sum(r["promotional"] for r in noise), "noise_domains": len(noise),
            "top": rows[:max(1, int(top))], "noise": noise[:max(1, int(top))]}
