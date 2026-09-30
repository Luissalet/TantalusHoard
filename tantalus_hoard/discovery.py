"""Discovery: watcher queries -> search hits -> classified, scored candidate URLs.

A candidate is only a proposal; the integrator stores it in ``candidates`` and the user (or the watcher's
auto-accept rule) turns it into a target. Nothing here fetches the candidate pages themselves.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qsl, urlsplit

from .model import SELLER_ANY_BELOW, SELLER_RETAIL_ONLY, SELLER_RETAIL_PLUS_MARKETPLACE, SearchHit
from .search import WebSearch, host_of, url_key

log = logging.getLogger("tantalus.discovery")

# ----------------------------------------------------------------------------- host table
# kind -> {registrable host: display name}
RETAILERS: dict[str, str] = {
    "game.es": "GAME", "elcorteingles.es": "El Corte Ingles", "carrefour.es": "Carrefour", "amazon.es": "Amazon",
    "pccomponentes.com": "PcComponentes", "mediamarkt.es": "MediaMarkt", "fnac.es": "Fnac", "xtralife.com": "Xtralife",
    "toysrus.es": "Toys R Us", "juguettos.com": "Juguettos", "marketplace.nvidia.com": "NVIDIA Marketplace",
    "store.nvidia.com": "NVIDIA Store", "coolmod.com": "Coolmod", "neobyte.es": "Neobyte", "ldlc.com": "LDLC",
    "alternate.es": "Alternate", "miravia.es": "Miravia", "pokemoncenter.com": "Pokemon Center", "eneba.com": "Eneba",
    "shop.lenovo.com": "Lenovo Store", "store.hp.com": "HP Store",
}
OFFICIAL: dict[str, str] = {
    "nvidia.com": "NVIDIA", "asus.com": "ASUS", "lenovo.com": "Lenovo", "dell.com": "Dell", "hp.com": "HP", "msi.com": "MSI",
    "gigabyte.com": "Gigabyte", "pokemon.com": "Pokemon", "tcg.pokemon.com": "Pokemon TCG", "acer.com": "Acer",
    "nintendo.com": "Nintendo", "sony.com": "Sony", "playstation.com": "PlayStation", "xbox.com": "Xbox",
    "samsung.com": "Samsung", "apple.com": "Apple", "amd.com": "AMD", "intel.com": "Intel",
}
COMMUNITY: dict[str, str] = {
    "reddit.com": "Reddit", "x.com": "X", "twitter.com": "X", "forocoches.com": "Forocoches", "mediavida.com": "Mediavida",
    "facebook.com": "Facebook", "youtube.com": "YouTube", "tiktok.com": "TikTok", "instagram.com": "Instagram",
    "t.me": "Telegram", "discord.com": "Discord", "hardzone.es": "Hardzone",
}
MARKETPLACES: dict[str, str] = {
    "wallapop.com": "Wallapop", "ebay.es": "eBay", "ebay.com": "eBay", "cardmarket.com": "Cardmarket",
    "todocoleccion.net": "Todocoleccion", "milanuncios.com": "Milanuncios", "vinted.es": "Vinted", "amazon.com": "Amazon US",
}
AGGREGATORS: dict[str, str] = {
    "idealo.es": "Idealo", "idealo.com": "Idealo", "kelkoo.es": "Kelkoo", "kelkoo.com": "Kelkoo", "pricespy.com": "PriceSpy",
    "shopping.google.com": "Google Shopping", "pricerunner.com": "PriceRunner", "tcgradar.net": "TCG Radar",
    "comparaiso.es": "Comparaiso", "shopmania.es": "Shopmania", "pcpartpicker.com": "PCPartPicker",
}
_TABLES = (("retailer", RETAILERS), ("official", OFFICIAL), ("community", COMMUNITY), ("marketplace", MARKETPLACES), ("aggregator", AGGREGATORS))
_KIND_LEVEL = {"retailer": 1, "official": 3, "community": 4, "marketplace": 5, "aggregator": 5, "other": 5}


def _match(host: str, table: dict[str, str]) -> Optional[str]:
    """Longest registrable-suffix match of ``host`` in ``table`` (subdomains included)."""
    best = ""
    for domain in table:
        if (host == domain or host.endswith("." + domain)) and len(domain) > len(best):
            best = domain
    return best or None


def _extract_helpers() -> Optional[tuple[Callable[[str], Any], Callable[[str], Any]]]:
    """The extractor team's host helpers, when that module exists. Imported lazily so this file never depends on it."""
    try:
        from .extract import sites  # type: ignore

        return sites.retailer_for_host, sites.source_level_for_host
    except Exception:  # noqa: BLE001 — optional override
        return None


def classify_host(host: str) -> tuple[int, str, str]:
    """``(source_level, retailer, kind)``. kind: retailer | official | community | marketplace | aggregator | other.

    Retailer hosts are level 1 (discovery downgrades listing / search URLs to 2), official brand pages 3,
    community 4, everything else 5. ``retailer`` is a display name ('' when unknown).
    """
    host = (host or "").lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    level, retailer, kind = 5, "", "other"
    for table_kind, table in _TABLES:
        domain = _match(host, table)
        if domain:
            kind, retailer, level = table_kind, table[domain], _KIND_LEVEL[table_kind]
            break
    if kind == "official" and re.match(r"^[a-z]{2}-store\.|^(store|shop|tienda|buy)\.|-store\.|\bshop\b", host):
        kind, level, retailer = "retailer", 1, retailer + " Store"  # a brand's own web shop sells: treat as retailer
    helpers = _extract_helpers()
    if helpers is not None:
        try:
            name = helpers[0](host)
            if name:
                retailer = str(name)
                if kind in ("other", "aggregator", "community"):
                    kind, level = "retailer", 1
            lvl = helpers[1](host)
            if isinstance(lvl, int) and 1 <= lvl <= 5:
                level = lvl
        except Exception:  # noqa: BLE001
            pass
    return level, retailer, kind


# ----------------------------------------------------------------------------- url shape
_PRODUCT_PATH = re.compile(
    r"/(producto|productos|product|products|p|dp|gp/product|ip|pd|item|itm|articulo|art|detail|detalle|ficha|sku)/"
    r"|-p-?\d{4,}|/\d{5,}(?:[/.\-]|$)|[-_/][a-z]{0,4}\d{6,}[a-z0-9]*\.html?$|[-_]\d{5,}\.html?$", re.I)
_LISTING_PATH = re.compile(
    r"/(search|buscar|busqueda|catalogsearch|resultados|results|categoria|categorias|category|categories|c|cat|collections?|coleccion(?:es)?|"
    r"shop|tienda|listado|browse|s|marca|brand|brands)(/|$)", re.I)
_SEARCH_QUERY_KEYS = {"q", "s", "query", "search", "keyword", "keywords", "text", "k", "term", "searchterm"}


def url_shape(url: str) -> str:
    """``product`` | ``listing`` | ``page``: a cheap guess from the URL alone."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "page"
    path = parts.path or "/"
    keys = {k.lower() for k, _ in parse_qsl(parts.query)}
    if keys & _SEARCH_QUERY_KEYS:
        return "listing"
    if _PRODUCT_PATH.search(path) or keys & {"sku", "pid", "productid", "product_id", "itemid"}:
        return "product"
    if _LISTING_PATH.search(path):
        return "listing"
    if path in ("", "/"):
        return "listing"  # a home page is never a product
    return "page"


# ----------------------------------------------------------------------------- watcher config
def watcher_config(watcher: dict[str, Any]) -> dict[str, Any]:
    """The watcher's JSON config as a dict (accepts a DB row with a JSON string, a dict, or an already flat dict)."""
    raw = watcher.get("config") if isinstance(watcher, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {k: v for k, v in (watcher or {}).items() if k in ("discovery", "seller_policy", "info", "product_terms")}
    return raw


def fold(text: str) -> str:
    """Lowercase without accents, for tolerant keyword matching."""
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


_STOP = {"de", "del", "la", "el", "los", "las", "en", "y", "o", "a", "the", "and", "or", "of", "for", "in", "on", "to", "un", "una", "con", "por", "para"}


def query_terms(text: str) -> list[str]:
    text = re.sub(r"\bsite:\S+", " ", text or "", flags=re.I)
    text = re.sub(r"(^|\s)-\S+", " ", text)
    out: list[str] = []
    for token in re.findall(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*", fold(text)):
        if token in _STOP or token in ("or", "and", "site", "inurl", "intitle"):
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        if token not in out:
            out.append(token)
    return out


def product_terms(watcher: dict[str, Any]) -> list[str]:
    cfg = watcher_config(watcher)
    disc = cfg.get("discovery") if isinstance(cfg.get("discovery"), dict) else {}
    explicit = disc.get("terms") or cfg.get("product_terms")
    if isinstance(explicit, str):
        explicit = [explicit]
    if explicit:
        terms: list[str] = []
        for item in explicit:
            terms.extend(t for t in query_terms(str(item)) if t not in terms)
        return terms
    terms = query_terms(str(watcher.get("name", "")))
    for query in disc.get("queries") or []:
        terms.extend(t for t in query_terms(str(query)) if t not in terms)
    return terms


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value or [] if str(v).strip()]


# ----------------------------------------------------------------------------- scoring
def score_hit(hit: SearchHit, *, terms: list[str], include: list[str], exclude: list[str], allow: list[str],
              seller_policy: str) -> Optional[dict[str, Any]]:
    """Candidate dict for one hit, or ``None`` when it is dropped (excluded, wrong retailer, resale, off-topic)."""
    host = host_of(hit.url)
    bare = host[4:] if host.startswith("www.") else host
    level, retailer, kind = classify_host(host)
    shape = url_shape(hit.url)
    haystack = fold(" ".join((hit.title, hit.snippet, hit.url)))

    if any(fold(word) in haystack for word in exclude):
        return None
    if allow:
        wanted = [fold(a).strip() for a in allow]
        if not any(bare == w or bare.endswith("." + w) or fold(retailer) == w for w in wanted):
            return None
    if kind == "marketplace" and seller_policy == SELLER_RETAIL_ONLY:
        return None

    matched = [t for t in terms if re.search(r"(?<![a-z0-9])" + re.escape(t), haystack)]
    if terms and len(matched) < max(1, -(-len(terms) // 3)):  # at least a third of the product terms must appear
        return None

    reasons: list[str] = []
    base = {"retailer": {"product": 60, "page": 48, "listing": 42}[shape], "official": 35, "community": 25, "aggregator": 15,
            "other": 10, "marketplace": 8}[kind]
    if kind == "retailer":
        reasons.append("retailer product page" if shape == "product" else "retailer search/listing" if shape == "listing" else "retailer page")
        if shape == "listing":
            level = 2
    elif kind == "official":
        reasons.append("official brand page")
        if shape == "product":
            base += 5
    else:
        reasons.append({"community": "community source", "aggregator": "price aggregator", "marketplace": "marketplace / resale",
                        "other": "other site"}[kind])
    score = float(base)
    if kind not in ("retailer", "official") and shape == "product":
        score += 4
    if terms:
        ratio = len(matched) / len(terms)
        score += round(30 * ratio, 1)
        reasons.append(f"matches {len(matched)}/{len(terms)} terms")
    bonus = [w for w in include if fold(w) in haystack]
    if bonus:
        score += min(12, 4 * len(bonus))
        reasons.append("include: " + ", ".join(bonus[:3]))
    if kind == "marketplace":
        score -= 10 if seller_policy == SELLER_RETAIL_PLUS_MARKETPLACE else 5 if seller_policy == SELLER_ANY_BELOW else 10
    if "+" in hit.engine:
        score += 5
        reasons.append("several engines")
    score += max(0, 5 - hit.rank) * 1.0 if hit.rank else 0
    return {"url": hit.url, "host": bare, "title": hit.title, "snippet": hit.snippet, "source_level": level, "retailer": retailer,
            "engine": hit.engine, "query": "", "score": round(max(0.0, min(100.0, score)), 1), "reason": "; ".join(reasons)}


def _llm_refine(candidates: list[dict[str, Any]], watcher: dict[str, Any], llm: Any, budget: int = 8) -> None:
    """Optional: ask the model to confirm middle-band candidates. Silent fallback to the rule score."""
    from .llm import UNTRUSTED, page_block

    system = ("You judge whether a search result is a page where the user's tracked product can be bought or tracked. "
              + UNTRUSTED + ' Reply with JSON {"relevant": true|false, "reason": "short"}.')
    used = 0
    for cand in candidates:
        if used >= budget:
            break
        if not 35 <= cand["score"] <= 65:
            continue
        used += 1
        user = (f"Tracked product: {watcher.get('name', '')}\n" + page_block(f"{cand['title']}\n{cand['snippet']}\n{cand['url']}", 1200))
        try:
            data = llm.json(system, user, required=("relevant",), max_tokens=120)
        except Exception:  # noqa: BLE001
            data = None
        if not data:
            continue
        if data.get("relevant") is False:
            cand["score"] = round(max(0.0, cand["score"] - 25), 1)
            cand["reason"] += "; model: not relevant"
        elif data.get("relevant") is True:
            cand["score"] = round(min(100.0, cand["score"] + 10), 1)
            cand["reason"] += "; model: relevant"


# ----------------------------------------------------------------------------- entry point
def discover(watcher: dict[str, Any], search: WebSearch, *, existing_urls: Iterable[str] = (), llm: Any = None
             ) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Run the watcher's discovery queries. Returns ``(candidates sorted by score, {engine|query: error})``."""
    cfg = watcher_config(watcher)
    disc = cfg.get("discovery") if isinstance(cfg.get("discovery"), dict) else {}
    queries = _as_list(disc.get("queries"))
    errors: dict[str, str] = {}
    if not queries:
        return [], {"discovery": "no queries configured"}
    include, exclude, allow = _as_list(disc.get("include")), _as_list(disc.get("exclude")), _as_list(disc.get("retailers"))
    seller_policy = str(cfg.get("seller_policy") or disc.get("seller_policy") or SELLER_RETAIL_ONLY)
    terms = product_terms(watcher)
    try:
        limit = max(1, min(30, int(disc.get("limit_per_query", 10))))
    except (TypeError, ValueError):
        limit = 10
    try:
        freshness = int(disc["freshness_days"]) if disc.get("freshness_days") else None
    except (TypeError, ValueError):
        freshness = None
    try:
        min_score = float(disc.get("min_score", 25))
    except (TypeError, ValueError):
        min_score = 25.0
    try:
        max_candidates = max(1, int(disc.get("max_candidates", 30)))
    except (TypeError, ValueError):
        max_candidates = 30

    known = {url_key(u) for u in existing_urls}
    best: dict[str, dict[str, Any]] = {}
    for query in queries:
        try:
            hits, engine_errors = search.search(query, limit, freshness_days=freshness)
        except Exception as exc:  # noqa: BLE001 — a search object may be user-supplied
            errors[f"query:{query[:60]}"] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        for engine, message in engine_errors.items():
            errors.setdefault(engine, message)
        for hit in hits:
            key = url_key(hit.url)
            if key in known:
                continue
            cand = score_hit(hit, terms=terms, include=include, exclude=exclude, allow=allow, seller_policy=seller_policy)
            if cand is None or cand["score"] < min_score:
                continue
            cand["query"] = query
            previous = best.get(key)
            if previous is None or cand["score"] > previous["score"]:
                best[key] = cand
    ranked = sorted(best.values(), key=lambda c: (-c["score"], c["url"]))[:max_candidates]
    if llm is not None and ranked:
        _llm_refine(ranked, watcher, llm)
        ranked.sort(key=lambda c: (-c["score"], c["url"]))
    return ranked, errors


__all__ = ["discover", "classify_host", "url_shape", "score_hit", "product_terms", "watcher_config"]
