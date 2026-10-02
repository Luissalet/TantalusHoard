"""Match a purchase (title, merchant, URL) against the watchers that were waiting for it.

The family hub calls ``watchers_match_purchase`` when a payment shows up: a watcher that scores 0.8 or more is marked bought
and stops checking. The score is explainable and deliberately cautious, because a wrong "bought" silences a watcher:

* **identifiers** (EAN/GTIN, ASIN, or the same product URL) found in the purchase and in a target of the watcher -> 0.95-1.0;
* otherwise **token overlap**: the share of the watcher's product words (its ``product.terms`` and ``must``, or its name)
  found in the purchase title and URL. A watcher with fewer than two product words never goes above 0.7 (too generic to
  trust); a missing ``must`` word halves the score; any ``exclude`` word in the purchase zeroes it;
* a purchase from a shop the watcher already follows adds 0.08.

Pure functions over plain dicts: no network, no I/O.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import unquote, urlsplit

from .mail.parse import fold

STOPWORDS = frozenset(
    "a al con de del el en la las los un una unos unas y o u para por su sus que se es the of and or for with in on at to an by from "
    "pack set edicion edition new nuevo nueva envio gratis oferta".split())
MIN_SCORE = 0.3
GENERIC_CAP = 0.7           # a watcher with fewer than two product words cannot be told apart from a lookalike
ASIN_RE = re.compile(r"(?<![A-Z0-9])(B0[A-Z0-9]{8})(?![A-Z0-9])")
EAN_RE = re.compile(r"(?<!\d)(\d{13}|\d{12}|\d{8})(?!\d)")
URL_ID_RE = re.compile(r"(?:/dp/|/gp/product/|/p/|/product/|/producto/|/ip/|sku=|pid=|id=)([A-Za-z0-9_-]{5,})", re.I)


def _stem(word: str) -> str:
    """Fold simple plurals (boxes -> box, trainers -> trainer, aniversarios -> aniversario)."""
    if len(word) > 4 and word.endswith(("xes", "ches", "shes", "zes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: Any) -> set[str]:
    """Significant folded words: letters and digits, stop-words and one-letter words dropped, plurals folded."""
    out = set()
    for word in re.split(r"[^a-z0-9]+", fold(unquote(str(text or "")))):
        if len(word) >= 2 and word not in STOPWORDS or word.isdigit():
            out.add(_stem(word))
    return out


def host_of(url: Any) -> str:
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def identifiers(*texts: Any) -> set[str]:
    """EAN/GTIN digits, ASINs and URL product ids found in the texts (upper case)."""
    out = set()
    for text in texts:
        raw = unquote(str(text or ""))
        out.update(m.group(1).upper() for m in ASIN_RE.finditer(raw.upper()))
        out.update(m.group(1) for m in EAN_RE.finditer(raw))
        out.update(m.group(1).upper() for m in URL_ID_RE.finditer(raw) if any(c.isdigit() for c in m.group(1)))
    return out


def _norm_url(url: Any) -> str:
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return f"{host}{parts.path.rstrip('/')}".lower() if host else ""


def _watcher_words(watcher: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    cfg = watcher.get("config") if isinstance(watcher.get("config"), dict) else {}
    product = cfg.get("product") if isinstance(cfg.get("product"), dict) else {}
    terms = tokens(" ".join(str(x) for x in (product.get("terms") or []))) | tokens(" ".join(str(x) for x in (product.get("must") or [])))
    if not terms:
        terms = tokens(watcher.get("name"))
    must = tokens(" ".join(str(x) for x in (product.get("must") or [])))
    exclude = tokens(" ".join(str(x) for x in (product.get("exclude") or [])))
    return terms, must, exclude


def score_watcher(watcher: dict[str, Any], targets: Iterable[dict[str, Any]], *, title: str, merchant: str = "", url: str = "") -> tuple[float, list[str]]:
    """``(score 0..1, reasons)`` for one watcher against one purchase."""
    reasons: list[str] = []
    targets = list(targets)
    purchase_words = tokens(title) | tokens(unquote(urlsplit(url).path) if url else "")
    purchase_ids = identifiers(title, url)
    purchase_url = _norm_url(url)
    # --- identifiers: the strongest evidence
    for target in targets:
        if purchase_url and _norm_url(target.get("url")) == purchase_url:
            return 1.0, ["same product page"]
        wanted = {str(target.get(k)).upper() for k in ("ean", "sku") if target.get(k)} | identifiers(target.get("url"))
        shared = purchase_ids & {w for w in wanted if len(w) >= 5}
        if shared:
            ident = sorted(shared)[0]
            return (1.0 if ident.isdigit() else 0.97), [f"same product id {ident}"]
    # --- token overlap
    words, must, exclude = _watcher_words(watcher)
    if not words:
        return 0.0, ["the watcher has no product words"]
    if exclude & purchase_words:
        return 0.0, [f"excluded word: {sorted(exclude & purchase_words)[0]}"]
    hit = words & purchase_words
    score = len(hit) / len(words)
    reasons.append(f"{len(hit)}/{len(words)} product words")
    if len(words) < 2:
        score = min(score, GENERIC_CAP)
        reasons.append("too few product words to be sure")
    if must and not must <= purchase_words:
        score *= 0.5
        reasons.append("a required word is missing")
    if score > 0.4:
        shops = {fold(t.get("retailer")) for t in targets if t.get("retailer")} | {host_of(t.get("url")) for t in targets if t.get("url")}
        shops.discard("")
        merchant_words = fold(merchant).strip()
        merchant_host = host_of(url)
        if (merchant_words and any(merchant_words in s or s in merchant_words for s in shops if len(s) >= 3)) or (merchant_host and merchant_host in shops):
            score += 0.08
            reasons.append("a shop the watcher follows")
    return round(min(1.0, score), 3), reasons


def match_purchase(watchers: list[dict[str, Any]], targets_by_watcher: dict[str, list[dict[str, Any]]], *, title: str, merchant: str = "",
                   url: str = "", min_score: float = MIN_SCORE, limit: int = 10) -> list[dict[str, Any]]:
    """Active watchers that look like this purchase, best first: ``[{watcher_id, title, score, reasons}]``."""
    found = []
    for watcher in watchers:
        cfg = watcher.get("config") if isinstance(watcher.get("config"), dict) else {}
        if cfg.get("status") == "bought":
            continue
        score, reasons = score_watcher(watcher, targets_by_watcher.get(watcher["id"], []), title=title, merchant=merchant, url=url)
        if score >= min_score:
            found.append({"watcher_id": watcher["id"], "title": watcher["name"], "score": score, "reasons": reasons})
    found.sort(key=lambda m: (-m["score"], m["title"]))
    return found[:limit]


__all__ = ["tokens", "identifiers", "score_watcher", "match_purchase", "MIN_SCORE"]
