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
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from bs4 import BeautifulSoup

from .discovery import classify_host, fold, watcher_config
from .model import InfoFinding
from .search import WebSearch, host_of, url_key

log = logging.getLogger("tantalus.info")

MIN_WORDS = 40                 # below this a page is treated as empty / blocked / nav-only
MAX_ADDED_LINES = 8
MAX_LINE_CHARS = 200
MAX_STORED_TEXT = 20_000
MAX_SEEN = 400
MAX_FEED_FINDINGS = 20
MAX_FIRST_SEARCH_FINDINGS = 10
MIDDLE_BAND = (30.0, 60.0)     # rule scores that may be refined by the model
LLM_BUDGET = 6
_DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "template", "dialog")
_NOISE_ATTR = re.compile(r"cookie|consent|gdpr|onetrust", re.I)
_BLOCK_PHRASES = ("enable javascript", "captcha", "access denied", "verify you are human", "just a moment", "acceso denegado",
                  "activa javascript", "not a robot")


# ----------------------------------------------------------------------------- text helpers
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def readable_text(html: str) -> tuple[str, str]:
    """``(title, text)``: visible text without navigation, headers, footers, scripts and cookie banners."""
    soup = BeautifulSoup(html or "", "html.parser")
    title = _clip(soup.title.get_text(" ") if soup.title else "", 200)
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()
    for tag in soup.find_all(True):
        attrs = tag.attrs if isinstance(tag.attrs, dict) else {}
        blob = " ".join([str(attrs.get("id", "")), " ".join(attrs.get("class") or []) if isinstance(attrs.get("class"), list) else str(attrs.get("class", ""))])
        if attrs.get("role") in ("navigation", "banner", "contentinfo") or _NOISE_ATTR.search(blob):
            tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    if len(root.get_text(" ", strip=True).split()) < MIN_WORDS:
        root = soup.body or soup
    lines: list[str] = []
    for raw in root.get_text("\n").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return title, "\n".join(lines)


def quality_gate(text: str) -> str:
    """'' when the text is a usable document, otherwise the reason it is not (too short, blocked, mostly navigation)."""
    words = text.split()
    low = text[:800].lower()
    if len(words) < 150 and any(p in low for p in _BLOCK_PHRASES):
        return "blocked page"
    if len(words) < MIN_WORDS:
        return "too short"
    lines = [l for l in text.splitlines() if l.strip()]
    short = sum(1 for l in lines if len(l.split()) <= 3)
    if lines and short / len(lines) > 0.85 and len(words) < 400:
        return "mostly navigation"
    return ""


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", fold(line)).strip()


def _volatile(line: str) -> bool:
    """Lines that change on every load (clocks, counters) and must not count as a change."""
    n = _norm_line(line)
    return bool(re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?( ?(am|pm))?", n)) or n.startswith(("hoy es ", "actualizado hace", "copyright", "©"))


def normalised_hash(text: str) -> str:
    lines = [_norm_line(l) for l in text.splitlines() if l.strip() and not _volatile(l)]
    return _sha("\n".join(lines))


def diff_lines(old: str, new: str) -> tuple[list[str], list[str]]:
    """``(added, removed)`` lines, compared on normalised text, in document order."""
    old_lines = [l for l in old.splitlines() if l.strip() and not _volatile(l)]
    new_lines = [l for l in new.splitlines() if l.strip() and not _volatile(l)]
    old_set, new_set = {_norm_line(l) for l in old_lines}, {_norm_line(l) for l in new_lines}
    return ([l for l in new_lines if _norm_line(l) not in old_set], [l for l in old_lines if _norm_line(l) not in new_set])


def diff_summary(added: list[str], removed: list[str]) -> str:
    out = [f"+ {_clip(l, MAX_LINE_CHARS)}" for l in added[:MAX_ADDED_LINES]]
    if len(added) > MAX_ADDED_LINES:
        out.append(f"+ … {len(added) - MAX_ADDED_LINES} more lines")
    if removed:
        out.append(f"- {len(removed)} line(s) removed" + (f": {_clip(removed[0], 120)}" if removed else ""))
    return "\n".join(out)


# ----------------------------------------------------------------------------- feeds
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child_text(el: ET.Element, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def parse_feed(xml_text: str) -> list[dict[str, str]]:
    """RSS 2.0 / RSS 1.0 (RDF) / Atom items as ``{id, title, link, published, summary}``. Empty list when it is not a feed."""
    if not xml_text or "<!ENTITY" in xml_text:  # never expand entities from remote XML
        return []
    try:
        root = ET.fromstring(xml_text.lstrip("﻿").encode("utf-8"))
    except (ET.ParseError, ValueError):
        return []
    items: list[dict[str, str]] = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        link = ""
        if name == "entry":
            for child in el:
                if _local(child.tag) == "link":
                    href = child.attrib.get("href", "")
                    if href and child.attrib.get("rel", "alternate") == "alternate":
                        link = href
                        break
                    link = link or href
        else:
            link = _child_text(el, "link")
        title = _child_text(el, "title")
        guid = _child_text(el, "guid", "id") or link or title
        summary = _child_text(el, "encoded", "content", "description", "summary")
        summary = BeautifulSoup(summary, "html.parser").get_text(" ") if summary else ""
        published = _child_text(el, "pubdate", "published", "updated", "date")
        if guid or link:
            items.append({"id": guid, "title": _clip(title, 200), "link": link, "published": published, "summary": _clip(summary, 400)})
    return items


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
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac"}


def registrable_domain(host: str) -> str:
    labels = [l for l in (host or "").lower().split(".") if l]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


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
        host = host_of(f.url)
        bare = host[4:] if host.startswith("www.") else host
        if official:
            is_official = any(bare == d or bare.endswith("." + d) for d in official)
        else:
            is_official = classify_host(host)[2] == "official"
        prepared.append({"f": f, "text": text, "folded": folded, "domain": registrable_domain(host), "official": is_official,
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
        if fr.not_modified:
            return []
        title, text = readable_text(fr.text)
        problem = quality_gate(text)
        if problem:
            update["last_error"] = f"low quality page ({problem}); no comparison made"
            return []
        new_hash = normalised_hash(text)
        stored = text[:MAX_STORED_TEXT]
        old_hash, old_text = str(row.get("last_hash") or ""), str(row.get("last_text") or "")
        update.update(last_hash=new_hash, last_text=stored)
        if not old_hash or new_hash == old_hash:
            return []  # baseline, or nothing changed
        added, removed = diff_lines(old_text, stored)
        if not added and not removed:
            return []  # same lines in another order
        url = fr.final_url or fr.url or str(row.get("value", ""))
        return [InfoFinding(url=url, title=title or str(row.get("label") or url), snippet=_clip(" ".join(added[:3]) or "content removed", 300),
                            kind="page_change", content_hash=_sha(url + "\n" + "\n".join(_norm_line(l) for l in added or removed))[:32],
                            diff=diff_summary(added, removed), source_level=int(row.get("source_level") or 3))]

    # -- feed
    def _feed(self, row: dict[str, Any], update: dict[str, Any]) -> list[InfoFinding]:
        fr = self._fetch(row, update)
        if fr is None or fr.not_modified:
            return []
        items = parse_feed(fr.text)
        if not items:
            update["last_error"] = "not a feed or empty"
            return []
        seen = [l for l in str(row.get("last_text") or "").splitlines() if l]
        seen_set = set(seen)
        keys = [it["id"] or it["link"] for it in items]
        merged = (keys + [k for k in seen if k not in set(keys)])[:MAX_SEEN]
        update.update(last_text="\n".join(merged), last_hash="feed:" + _sha("\n".join(keys[:50]))[:16])
        if not row.get("last_hash"):
            return []  # first check is the baseline
        level = int(row.get("source_level") or 3)
        out = []
        for it, key in zip(items, keys):
            if key in seen_set:
                continue
            url = it["link"] or str(row.get("value", ""))
            out.append(InfoFinding(url=url, title=it["title"] or url, snippet=it["summary"], kind="feed_item",
                                   published=it["published"] or None, content_hash=_sha(f"{row.get('value', '')}\n{key}")[:32], source_level=level))
        return out[:MAX_FEED_FINDINGS]

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
        hits, errors = self.search.search(str(row.get("value", "")), 10, freshness_days=days)
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
            level = classify_host(host_of(hit.url))[0]
            out.append(InfoFinding(url=hit.url, title=hit.title or hit.url, snippet=_clip(hit.snippet, 300), kind="search_hit",
                                   published=hit.published, content_hash=_sha(key)[:32], source_level=level))
        return out[:MAX_FIRST_SEARCH_FINDINGS] if first else out


__all__ = ["InfoSentry", "judge", "judge_rules", "readable_text", "quality_gate", "parse_feed", "diff_lines", "diff_summary"]
