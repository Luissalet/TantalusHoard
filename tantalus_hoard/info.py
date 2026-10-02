"""Information sentry: pages, feeds and searches that report *changes*, plus a rule-based materiality judge.

``InfoSentry.check_source(row, watcher)`` fetches one source and returns ``(findings, source_update)``:
``source_update`` holds the columns the integrator writes back to ``info_sources`` (hash, seen-list,
ETag / Last-Modified, last_check_ts, last_error). The first check of a page or feed is a baseline (no
findings); a search source reports every result on its first check (capped) and only new URLs afterwards.
Remote text is data only: it is never executed and only reaches the optional model inside ``<page>`` tags.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Callable, Optional

from .discovery import classify_host, fold, watcher_config
from .hoard_link.web import watch
from .hoard_link.web.feeds import parse_feed
from .hoard_link.web.urls import registrable_domain
from .model import InfoFinding
from .search import WebSearch, host_of, url_key

log = logging.getLogger("tantalus.info")

MAX_SEEN = watch.MAX_SEEN
MAX_FEED_FINDINGS = watch.MAX_FEED_FINDINGS
MAX_FIRST_SEARCH_FINDINGS = 10
MIDDLE_BAND = (30.0, 60.0)     # rule scores that may be refined by the model
LLM_BUDGET = 6


# ----------------------------------------------------------------------------- text helpers
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _item_key(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("link") or item.get("title") or "")


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ----------------------------------------------------------------------------- materiality rules
_PRICE = re.compile(r"(?:€|\$|£|\beur\b|\busd\b)\s?\d[\d.,]*|\d[\d.,]*\s?(?:€|\$|£|\beur\b|\beuros\b|\busd\b|\bd[oó]lares\b)", re.I)
_MONTHS = (r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|"
           r"january|february|march|april|may|june|july|august|september|october|november|december")
_MONTHS_ABBR = r"ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic|jan|apr|aug|dec"
_DATE = re.compile(
    r"\b\d{1,2}[/.\-]\d{1,2}[/.\-](?:\d{4}|\d{2})\b|\b\d{4}-\d{2}-\d{2}\b"
    rf"|\b\d{{1,2}}\s+(?:de\s+)?(?:{_MONTHS})(?:\s+(?:de\s+)?\d{{4}})?\b|\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b"
    rf"|\b(?:{_MONTHS})\s+(?:de\s+)?\d{{4}}\b|\b\d{{1,2}}\s+(?:{_MONTHS_ABBR})\b\.?"
    r"|\bq[1-4]\s*(?:de\s+|of\s+)?(?:\d{4}|\d{2})\b|\b(?:primer|segundo|tercer|cuarto)\s+trimestre(?:\s+de\s+\d{4})?\b", re.I)
_ACTION = re.compile(r"\b(pre-?order|preventa|reservas?|reservar|disponible|disponibles|available|availability|launch|lanzamiento|"
                     r"precio|precios|price|pricing|a la venta|on sale|restock|reposicion)\b", re.I)
_LEAK = re.compile(r"\b(rumou?rs?|leaks?|leaked|filtraci[oó]n|filtrad[oa]s?|seg[uú]n fuentes|supuestamente|allegedly|reportedly|"
                   r"according to sources|could|podr[ií]a|podr[ií]an)\b", re.I)
_ESTIMATE = re.compile(r"\b(estimates?|estimated|estimaci[oó]n|estimad[oa]|expected|se espera|se estima|anticipated)\b", re.I)


def _info_cfg(watcher: dict[str, Any]) -> dict[str, Any]:
    cfg = watcher_config(watcher)
    info = cfg.get("info")
    return info if isinstance(info, dict) else {}


def _terms(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value or [] if str(v).strip()]


def _has(term: str, folded: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(fold(term)), folded) is not None


def _facts(text: str) -> set[str]:
    facts = {re.sub(r"[^\d,.]", "", m.group(0)).strip(",.") + "€" for m in _PRICE.finditer(text)}
    facts |= {re.sub(r"\s+", " ", m.group(0).lower()) for m in _DATE.finditer(text)}
    facts.discard("€")
    return facts


def _finding_text(f: InfoFinding) -> str:
    return " ".join(x for x in (f.title, f.snippet, f.diff) if x)


def judge_rules(findings: list[InfoFinding], watcher: dict[str, Any]) -> list[InfoFinding]:
    """Score every finding with explainable rules and set ``verdict`` / ``material`` / ``score`` / ``reason``."""
    info = _info_cfg(watcher)
    must, boost, exclude = _terms(info.get("must_terms")), _terms(info.get("boost_terms")), _terms(info.get("exclude_terms"))
    official = [d.lower().lstrip(".") for d in _terms(info.get("official_domains"))]
    try:
        threshold = float(info.get("material_threshold", 40))
    except (TypeError, ValueError):
        threshold = 40.0

    prepared: list[dict[str, Any]] = []
    for f in findings:
        text = _finding_text(f)
        folded = fold(text)
        host = host_of(f.publisher or f.url)
        bare = host[4:] if host.startswith("www.") else host
        if official:
            is_official = any(bare == d or bare.endswith("." + d) for d in official)
        else:
            is_official = classify_host(host)[2] == "official"
        prepared.append({"f": f, "text": text, "folded": folded, "domain": registrable_domain(host) or host, "official": is_official,
                         "facts": _facts(text)})

    for p in prepared:
        f: InfoFinding = p["f"]
        text, folded = p["text"], p["folded"]
        reasons: list[str] = []
        f.method = "rules"
        if any(_has(t, folded) for t in exclude):
            f.verdict, f.material, f.score, f.reason = "irrelevant", False, 0.0, "matches an exclude term"
            continue
        score = 5.0 if f.kind == "page_change" else 0.0
        substantive = False
        hit_must = [t for t in must if _has(t, folded)]
        if must and not hit_must:
            f.verdict, f.material, f.score, f.reason = "irrelevant", False, min(10.0, score), "none of the must terms"
            continue
        if must:
            score += 25
            reasons.append("term: " + ", ".join(hit_must[:3]))
        else:
            score += 10
        if p["official"]:
            score += 25
            substantive = True
            reasons.append("official domain")
        if _PRICE.search(text):
            score += 15
            substantive = True
            reasons.append("price")
        if _DATE.search(text):
            score += 10
            substantive = True
            reasons.append("date")
        actions = {m.group(0).lower() for m in _ACTION.finditer(text)}
        if actions:
            score += 8 if len(actions) == 1 else 16
            substantive = True
            reasons.append("keywords: " + ", ".join(sorted(actions)[:3]))
        boosted = [t for t in boost if _has(t, folded)]
        if boosted:
            score += min(18, 6 * len(boosted))
            substantive = True
            reasons.append("boost: " + ", ".join(boosted[:3]))

        others = [q for q in prepared if q is not p and q["domain"] != p["domain"]]
        agree = [q for q in others if p["facts"] & q["facts"]]  # the same price or date reported elsewhere
        if _LEAK.search(text):
            f.verdict = "leak"
            score -= 5
            reasons.append("rumour wording")
        elif _ESTIMATE.search(text):
            f.verdict = "estimate"
            reasons.append("estimate wording")
        elif p["official"]:
            f.verdict = "confirmed"
        elif agree:
            f.verdict = "confirmed"
            reasons.append(f"agrees with {len({q['domain'] for q in agree})} other domain(s)")
            score += 5
        else:
            f.verdict = "unknown"
        bar = threshold + (10 if f.verdict in ("leak", "estimate") else 0)
        f.score = round(max(0.0, min(100.0, score)), 1)
        f.material = substantive and f.score >= bar
        f.reason = "; ".join(reasons) or "no signal"
    return findings


def _llm_refine(findings: list[InfoFinding], watcher: dict[str, Any], llm: Any) -> None:
    from .llm import UNTRUSTED, page_block

    system = ("You classify one piece of news for a product watcher. " + UNTRUSTED +
              ' Reply with JSON {"verdict": "confirmed|leak|estimate|irrelevant", "material": true|false, "reason": "short"}. '
              "material means the user should be told now: a confirmed price, date, availability or launch change.")
    used = 0
    for f in findings:
        if used >= LLM_BUDGET:
            break
        if not (MIDDLE_BAND[0] <= f.score <= MIDDLE_BAND[1]) or f.verdict == "irrelevant":
            continue
        used += 1
        user = (f"Watching: {watcher.get('name', '')}\nSource: {host_of(f.url)}\n" + page_block(_finding_text(f), 2500))
        try:
            data = llm.json(system, user, required=("verdict", "material"), max_tokens=200)
        except Exception:  # noqa: BLE001 — the model is optional
            data = None
        if not data:
            continue
        verdict = str(data.get("verdict", "")).lower()
        if verdict not in ("confirmed", "leak", "estimate", "irrelevant"):
            continue
        f.verdict = verdict
        f.material = bool(data.get("material")) and verdict != "irrelevant"
        f.method = "rules+llm"
        note = _clip(str(data.get("reason", "")), 160)
        f.reason = f"{f.reason}; model: {note}" if note else f.reason


def judge(findings: list[InfoFinding], watcher: dict[str, Any], llm: Any = None) -> list[InfoFinding]:
    """Rules first; the optional model only refines findings whose rule score sits in the middle band."""
    judge_rules(findings, watcher)
    if llm is not None and findings:
        try:
            _llm_refine(findings, watcher, llm)
        except Exception as exc:  # noqa: BLE001
            log.info("model judge skipped: %s", exc)
    return findings


# ----------------------------------------------------------------------------- the sentry
class InfoSentry:
    def __init__(self, fetcher: Any, search: Optional[WebSearch] = None, llm: Any = None, *, clock: Callable[[], float] = time.time):
        self.fetcher = fetcher
        self.search = search
        self.llm = llm
        self.clock = clock

    def judge(self, findings: list[InfoFinding], watcher: dict[str, Any]) -> list[InfoFinding]:
        return judge(findings, watcher, self.llm)

    def check_source(self, source_row: dict[str, Any], watcher: dict[str, Any]) -> tuple[list[InfoFinding], dict[str, Any]]:
        kind = str(source_row.get("kind", ""))
        update: dict[str, Any] = {"last_check_ts": self.clock(), "last_error": ""}
        try:
            if kind == "page":
                findings = self._page(source_row, update)
            elif kind == "feed":
                findings = self._feed(source_row, update)
            elif kind == "search":
                findings = self._search(source_row, watcher, update)
            else:
                findings, update["last_error"] = [], f"unknown source kind: {kind or '?'}"
        except Exception as exc:  # noqa: BLE001 — a source must never raise out of the scheduler
            log.info("info source %s failed: %s", source_row.get("id"), exc)
            findings, update["last_error"] = [], f"{type(exc).__name__}: {exc}"[:200]
        return findings, update

    # -- shared
    def _fetch(self, row: dict[str, Any], update: dict[str, Any]) -> Any:
        fr = self.fetcher.get(str(row.get("value", "")), tier="auto", accept="html", etag=row.get("etag") or "",
                              last_modified=row.get("last_modified") or "", respect_robots=True)
        if fr is not None and (fr.etag or fr.last_modified):
            update["etag"], update["last_modified"] = fr.etag or "", fr.last_modified or ""
        if fr is None or not fr.ok:
            reason = (fr.block_reason or fr.error or (f"http {fr.status}" if fr.status else "no answer")) if fr is not None else "no answer"
            update["last_error"] = ("blocked: " if fr is not None and fr.blocked else "") + reason
            return None
        return fr

    # -- page
    def _page(self, row: dict[str, Any], update: dict[str, Any]) -> list[InfoFinding]:
        fr = self._fetch(row, update)
        if fr is None:
            return []
        prev = {"hash": row.get("last_hash") or "", "text": row.get("last_text") or ""}
        found, state = watch.check_page(fr, prev)       # a blocked or low-quality page never replaces the stored baseline
        if state["error"]:
            update["last_error"] = state["error"]
            return []
        if fr.not_modified:
            return []
        update.update(last_hash=state["hash"], last_text=state["text"])
        if found is None:
            return []                                   # baseline, nothing changed, or the same lines in another order
        url = found["url"] or str(row.get("value", ""))
        return [InfoFinding(url=url, title=found["title"] if found["title"] != found["url"] else str(row.get("label") or url),
                            snippet=found["snippet"], kind="page_change", content_hash=found["content_hash"], diff=found["summary"],
                            source_level=int(row.get("source_level") or 3))]

    # -- feed
    def _feed(self, row: dict[str, Any], update: dict[str, Any]) -> list[InfoFinding]:
        fr = self._fetch(row, update)
        if fr is None or fr.not_modified:
            return []
        feed = parse_feed(fr.text, fr.final_url or fr.url)
        if not feed or not feed["items"]:
            update["last_error"] = "not a feed or empty"
            return []
        seen = [l for l in str(row.get("last_text") or "").splitlines() if l]
        baseline = not row.get("last_hash")            # the first check is the baseline
        items, merged = watch.check_feed(feed, seen, baseline)
        keys = [_item_key(it) for it in feed["items"]]
        update.update(last_text="\n".join(merged), last_hash="feed:" + _sha("\n".join(keys[:50]))[:16])
        level = int(row.get("source_level") or 3)
        out = []
        for it in items:
            url = it.get("link") or str(row.get("value", ""))
            out.append(InfoFinding(url=url, title=it.get("title") or url, snippet=_clip(it.get("summary") or "", 400), kind="feed_item",
                                   published=it.get("published") or it.get("updated") or None, content_hash=_sha(f"{row.get('value', '')}\n{it['key']}")[:32],
                                   source_level=level))
        return out

    # -- search
    def _search(self, row: dict[str, Any], watcher: dict[str, Any], update: dict[str, Any]) -> list[InfoFinding]:
        if self.search is None:
            update["last_error"] = "no search available"
            return []
        info = _info_cfg(watcher)
        try:
            days = int(info["freshness_days"]) if info.get("freshness_days") else 30
        except (TypeError, ValueError):
            days = 30
        engines = info.get("engines") or ["gnews", "bingnews", "bing"]
        hits, errors = self.search.search(str(row.get("value", "")), 20, freshness_days=days, engines=list(engines))
        if not hits:
            update["last_error"] = "; ".join(f"{k}: {v}" for k, v in errors.items())[:300] or "no results"
            return []
        seen = [l for l in str(row.get("last_text") or "").splitlines() if l]
        seen_set = set(seen)
        keys = [url_key(h.url) for h in hits]
        merged = (keys + [k for k in seen if k not in set(keys)])[:MAX_SEEN]
        update.update(last_text="\n".join(merged), last_hash="search:" + _sha("\n".join(sorted(keys)))[:16])
        first = not row.get("last_hash")
        out = []
        for hit, key in zip(hits, keys):
            if key in seen_set:
                continue
            publisher = ""
            m = re.search(r"\[(https?://[^\]\s]+)\]\s*$", hit.snippet or "")
            snippet = hit.snippet or ""
            if m:
                publisher, snippet = m.group(1), snippet[: m.start()].strip()
            level = classify_host(host_of(publisher or hit.url))[0]
            out.append(InfoFinding(url=hit.url, title=hit.title or hit.url, snippet=_clip(snippet, 300), kind="search_hit",
                                   published=hit.published, content_hash=_sha(key)[:32], source_level=level, publisher=publisher))
        return out[:MAX_FIRST_SEARCH_FINDINGS] if first else out


__all__ = ["InfoSentry", "judge", "judge_rules"]
