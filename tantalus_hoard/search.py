"""Web search over several engines, merged with Reciprocal Rank Fusion.

Engines: SearXNG (JSON, only when configured), DuckDuckGo HTML, Bing HTML and the Brave Search API
(only with ``TANTALUS_BRAVE_KEY``). Every engine may fail on its own; failures are reported per engine
and never raised. Result pages are parsed with BeautifulSoup's ``html.parser`` (no lxml needed).
Nothing here solves CAPTCHAs: a blocked answer is reported as ``blocked``.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .model import FetchResult, SearchHit

log = logging.getLogger("tantalus.search")

RRF_K = 60
DEFAULT_ENGINES = ("searxng", "ddg", "bing", "brave", "gnews", "bingnews")
NEWS_ENGINES = ("gnews", "bingnews")
SEARCH_MIN_INTERVAL_S = {"ddg": 8.0, "bing": 3.0, "gnews": 3.0, "bingnews": 3.0}  # DuckDuckGo answers 202 (bot check) to bursts

_TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid", "_ga", "ref_src", "spm"}
_BLOCKED_HOST_SUFFIXES = (".local", ".localdomain", ".internal", ".lan", ".home.arpa")
# hosts that only exist to redirect / advertise inside search result pages
_SEARCH_NOISE_HOSTS = ("duckduckgo.com", "bing.com", "r.bing.com", "googleadservices.com", "doubleclick.net")


# ----------------------------------------------------------------------------- URL helpers
def is_private_host(host: str) -> bool:
    """True for loopback / private / link-local / reserved IP literals and obviously internal names."""
    host = (host or "").strip().strip("[]").lower().rstrip(".")
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost") or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # dotted / decimal / hex forms that resolvers accept ("2130706433", "0x7f.1") are IP tricks too
        if re.fullmatch(r"(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}", host):
            return True
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
            or ip.is_unspecified or getattr(ip, "is_site_local", False))


def is_safe_url(url: str) -> bool:
    """http(s) only, with a public host name (IP literals in private ranges are rejected)."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    return not is_private_host(parts.hostname)


def normalize_url(url: str) -> str:
    """Canonical form used as identity: lowercase scheme/host, no fragment, no tracking params, no trailing slash."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip()
    host = (parts.hostname or "").lower()
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS)
    path = parts.path or ""
    if len(path) > 1:
        path = path.rstrip("/")
    elif path == "/":
        path = ""
    scheme = (parts.scheme or "https").lower()
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def url_key(url: str) -> str:
    """Identity for merging and de-duplicating: like :func:`normalize_url` but ignoring scheme and ``www.``."""
    norm = normalize_url(url)
    parts = urlsplit(norm)
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    return host + parts.path + ("?" + parts.query if parts.query else "")


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ----------------------------------------------------------------------------- HTML parsers
def _ddg_target(href: str) -> str:
    href = (href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if (parts.hostname or "").endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        for key, value in parse_qsl(parts.query):
            if key == "uddg" and value:
                return unquote(value)
        return ""
    return href


def parse_ddg_html(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html or "", "html.parser")
    hits: list[SearchHit] = []
    for block in soup.select("div.result"):
        classes = block.get("class") or []
        if any("result--ad" in c or c == "result--ad" for c in classes):
            continue
        link = block.select_one("a.result__a")
        if link is None:
            continue
        url = _ddg_target(link.get("href", ""))
        if not url or host_of(url).endswith("duckduckgo.com"):
            continue  # ads (y.js) and internal links
        snippet = block.select_one(".result__snippet")
        hits.append(SearchHit(url=url, title=_clean(link.get_text(" ")), snippet=_clean(snippet.get_text(" ")) if snippet else "",
                              engine="ddg", rank=len(hits) + 1))
    return hits


def _bing_target(href: str) -> str:
    href = (href or "").strip()
    parts = urlsplit(href)
    if (parts.hostname or "").endswith("bing.com") and parts.path.startswith("/ck/"):
        for key, value in parse_qsl(parts.query):
            if key == "u" and value.startswith("a1"):
                raw = value[2:]
                try:
                    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
                except ValueError:
                    return ""
        return ""
    return href


def parse_bing_html(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html or "", "html.parser")
    hits: list[SearchHit] = []
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a")
        if link is None:
            continue
        url = _bing_target(link.get("href", ""))
        if not url or host_of(url).endswith("bing.com"):
            continue
        snippet = block.select_one(".b_caption p") or block.select_one("p.b_lineclamp2") or block.select_one(".b_caption")
        hits.append(SearchHit(url=url, title=_clean(link.get_text(" ")), snippet=_clean(snippet.get_text(" ")) if snippet else "",
                              engine="bing", rank=len(hits) + 1))
    return hits


def is_blocked_page(html: str, hits: list[SearchHit]) -> bool:
    """A results page without results that looks like a bot check (reported, never bypassed)."""
    if hits:
        return False
    low = (html or "").lower()
    return any(token in low for token in ("anomaly-modal", "captcha", "unusual traffic", "are you a human", "/sorry/", "challenge"))


# ----------------------------------------------------------------------------- freshness mapping
def _ddg_df(days: Optional[int]) -> str:
    if not days:
        return ""
    return "d" if days <= 1 else "w" if days <= 7 else "m" if days <= 31 else "y"


def _bing_filter(days: Optional[int]) -> str:
    if not days:
        return ""
    if days <= 1:
        return 'ex1:"ez1"'
    if days <= 7:
        return 'ex1:"ez2"'
    if days <= 31:
        return 'ex1:"ez3"'
    return ""


def _brave_freshness(days: Optional[int]) -> str:
    if not days:
        return ""
    return "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py"


def _searxng_range(days: Optional[int]) -> str:
    if not days:
        return ""
    return "day" if days <= 1 else "week" if days <= 7 else "month" if days <= 31 else "year"


# ----------------------------------------------------------------------------- fusion
def _rss_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def _bing_news_target(href: str) -> str:
    """Bing News RSS links are apiclick.aspx redirects; the article is in the ``url`` parameter."""
    try:
        parts = urlsplit(href)
        if "bing.com" in (parts.hostname or "") and "apiclick" in parts.path:
            target = parse_qs(parts.query).get("url", [""])[0]
            if target.startswith("http"):
                return target
    except ValueError:
        pass
    return href


def parse_news_rss(xml_text: str, engine: str) -> list[SearchHit]:
    """Items of a news RSS feed (Google News, Bing News). Google News titles end with " - <publisher>"; its
    <source url=...> element names the publisher's site (the article link itself is a news.google.com redirect)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    hits: list[SearchHit] = []
    for rank, item in enumerate(root.iter("item"), start=1):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        if engine == "bingnews":
            link = _bing_news_target(link)
        source = item.find("source")
        publisher = (source.text or "").strip() if source is not None and source.text else ""
        publisher_url = source.get("url", "") if source is not None else ""
        desc = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)
        snippet = desc if desc and desc != title else ""
        if publisher and not snippet:
            snippet = publisher
        hit = SearchHit(url=link, title=title, snippet=snippet[:400], engine=engine, rank=rank, published=item.findtext("pubDate"))
        if publisher_url:
            hit.snippet = (hit.snippet + f" [{publisher_url}]").strip()
        hits.append(hit)
    return hits


def rrf_merge(lists: list[list[SearchHit]], *, k: int = RRF_K) -> list[SearchHit]:
    """Reciprocal Rank Fusion over normalised URLs. The best-ranked copy supplies title/snippet; engines are joined."""
    scores: dict[str, float] = {}
    best: dict[str, SearchHit] = {}
    engines: dict[str, list[str]] = {}
    order: dict[str, int] = {}
    for hits in lists:
        for position, hit in enumerate(hits, start=1):
            key = url_key(hit.url)
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + (hit.rank or position))
            order.setdefault(key, len(order))
            current = best.get(key)
            if current is None:
                best[key] = SearchHit(url=hit.url, title=hit.title, snippet=hit.snippet, engine=hit.engine, rank=hit.rank,
                                      published=hit.published)
            else:  # later engines only fill gaps
                current.snippet = current.snippet or hit.snippet
                current.title = current.title or hit.title
                current.published = current.published or hit.published
            names = engines.setdefault(key, [])
            if hit.engine and hit.engine not in names:
                names.append(hit.engine)
    ranked = sorted(scores, key=lambda key: (-scores[key], order[key]))
    merged: list[SearchHit] = []
    for position, key in enumerate(ranked, start=1):
        hit = best[key]
        merged.append(SearchHit(url=hit.url, title=hit.title, snippet=hit.snippet, engine="+".join(engines.get(key, [hit.engine])),
                                rank=position, published=hit.published))
    return merged


# ----------------------------------------------------------------------------- the search object
class WebSearch:
    """``search(query, limit, freshness_days=..., engines=...) -> (hits, {engine: error})``."""

    def __init__(self, fetcher: Any, *, searxng_url: Optional[str] = None, brave_key: Optional[str] = None,
                 config: Any = None, transport: Any = None):
        self.fetcher = fetcher
        self.searxng_url = (searxng_url or (config.secret("SEARXNG_URL") if config is not None else "") or "").strip().rstrip("/")
        self.brave_key = (brave_key or (config.secret("BRAVE_KEY") if config is not None else "") or "").strip()
        self.transport = transport  # httpx transport, only used for a local SearXNG instance

    # -- public
    def available_engines(self) -> list[str]:
        engines = []
        if self.searxng_url:
            engines.append("searxng")
        engines += ["ddg", "bing"]
        if self.brave_key:
            engines.append("brave")
        return engines

    def search(self, query: str, limit: int = 10, *, freshness_days: Optional[int] = None,
               engines: Optional[list[str]] = None) -> tuple[list[SearchHit], dict[str, str]]:
        query = _clean(query)
        errors: dict[str, str] = {}
        if not query:
            return [], {"query": "empty query"}
        chosen = [e for e in (engines or self.available_engines()) if e in DEFAULT_ENGINES]
        per_engine: list[list[SearchHit]] = []
        for engine in chosen:
            runner: Callable[..., tuple[list[SearchHit], str]] = getattr(self, "_" + engine)
            try:
                hits, error = runner(query, limit, freshness_days)
            except Exception as exc:  # noqa: BLE001 — one engine must never take the others down
                log.info("search engine %s failed: %s", engine, exc)
                hits, error = [], f"{type(exc).__name__}: {exc}"[:200]
            if error:
                errors[engine] = error
            safe = [h for h in hits if is_safe_url(h.url)]
            if safe:
                per_engine.append(safe)
        merged = rrf_merge(per_engine)
        return merged[: max(1, int(limit))], errors

    # -- helpers
    def _page(self, fr: FetchResult, parser: Callable[[str], list[SearchHit]]) -> tuple[list[SearchHit], str]:
        if fr is None:
            return [], "no answer"
        if not fr.ok:
            reason = fr.block_reason or fr.error or (f"http {fr.status}" if fr.status else "no answer")
            return [], ("blocked: " if fr.blocked else "") + reason
        hits = parser(fr.text)
        if not hits and fr.status == 202:  # DuckDuckGo answers 202 with a bot-check page when it is queried too fast
            return [], "blocked: bot check (http 202)"
        if not hits:
            return [], "blocked: bot check" if is_blocked_page(fr.text, hits) else "no results"
        return hits, ""

    def _get(self, engine: str, url: str, params: dict[str, Any], **kwargs: Any) -> FetchResult:
        return self.fetcher.get(url, tier="http", params=params, accept="html", respect_robots=False,
                                min_interval_s=SEARCH_MIN_INTERVAL_S.get(engine, 3.0), **kwargs)

    # -- engines
    def _news_rss(self, url: str, params: dict[str, Any], engine: str, limit: int,
                  freshness_days: Optional[int]) -> tuple[list[SearchHit], str]:
        fr = self._get(engine, url, params)
        if fr is None or not fr.ok:
            return [], (fr.block_reason or fr.error or f"http {fr.status}") if fr is not None else "no answer"
        hits = parse_news_rss(fr.text, engine)
        if freshness_days:
            cutoff = time.time() - freshness_days * 86400
            hits = [h for h in hits if h.published is None or _rss_ts(h.published) is None or _rss_ts(h.published) >= cutoff]
        return hits[:limit], "" if hits else "no results"

    def _gnews(self, query: str, limit: int, freshness_days: Optional[int]) -> tuple[list[SearchHit], str]:
        """Google News RSS: keyless, stable, and relevant for change intelligence (launches, prices, dates)."""
        q = query + (f" when:{freshness_days}d" if freshness_days else "")
        return self._news_rss("https://news.google.com/rss/search", {"q": q, "hl": "es", "gl": "ES", "ceid": "ES:es"},
                              "gnews", limit, freshness_days)

    def _bingnews(self, query: str, limit: int, freshness_days: Optional[int]) -> tuple[list[SearchHit], str]:
        return self._news_rss("https://www.bing.com/news/search", {"q": query, "format": "rss", "setlang": "es"}, "bingnews",
                              limit, freshness_days)

    def _ddg(self, query: str, limit: int, freshness_days: Optional[int]) -> tuple[list[SearchHit], str]:
        params = {"q": query, "kl": "es-es"}
        if _ddg_df(freshness_days):
            params["df"] = _ddg_df(freshness_days)
        return self._page(self._get("ddg", "https://html.duckduckgo.com/html/", params), parse_ddg_html)

    def _bing(self, query: str, limit: int, freshness_days: Optional[int]) -> tuple[list[SearchHit], str]:
        params = {"q": query, "setlang": "es", "cc": "ES"}
        if _bing_filter(freshness_days):
            params["filters"] = _bing_filter(freshness_days)
        return self._page(self._get("bing", "https://www.bing.com/search", params), parse_bing_html)

    def _brave(self, query: str, limit: int, freshness_days: Optional[int]) -> tuple[list[SearchHit], str]:
        if not self.brave_key:
            return [], "no API key"
        params: dict[str, Any] = {"q": query, "count": min(20, max(1, limit)), "country": "ES", "search_lang": "es"}
        if _brave_freshness(freshness_days):
            params["freshness"] = _brave_freshness(freshness_days)
        fr, data = self.fetcher.get_json("https://api.search.brave.com/res/v1/web/search", tier="http", params=params,
                                         headers={"X-Subscription-Token": self.brave_key, "Accept": "application/json"},
                                         accept="json", respect_robots=False, min_interval_s=1.0)
        if fr is None or not fr.ok or not isinstance(data, dict):
            reason = (fr.block_reason or fr.error or f"http {fr.status}") if fr is not None else "no answer"
            return [], reason or "no answer"
        hits = []
        for item in (data.get("web") or {}).get("results") or []:
            if isinstance(item, dict) and item.get("url"):
                hits.append(SearchHit(url=str(item["url"]), title=_clean(re.sub(r"<[^>]+>", "", str(item.get("title", "")))),
                                      snippet=_clean(re.sub(r"<[^>]+>", "", str(item.get("description", "")))), engine="brave",
                                      rank=len(hits) + 1, published=item.get("page_age") or None))
        return hits, "" if hits else "no results"

    def _searxng(self, query: str, limit: int, freshness_days: Optional[int]) -> tuple[list[SearchHit], str]:
        if not self.searxng_url:
            return [], "not configured"
        params: dict[str, Any] = {"q": query, "format": "json", "language": "es"}
        if _searxng_range(freshness_days):
            params["time_range"] = _searxng_range(freshness_days)
        endpoint = self.searxng_url + "/search"
        data: Any = None
        if is_private_host(host_of(endpoint)):
            data, error = self._local_json(endpoint, params)  # the user's own instance: the public-URL guard does not apply
            if error:
                return [], error
        else:
            fr, data = self.fetcher.get_json(endpoint, tier="http", params=params, accept="json", respect_robots=False, min_interval_s=1.0)
            if fr is None or not fr.ok or not isinstance(data, dict):
                return [], (fr.block_reason or fr.error or f"http {fr.status}") if fr is not None else "no answer"
        hits = []
        for item in (data or {}).get("results") or []:
            if isinstance(item, dict) and item.get("url"):
                hits.append(SearchHit(url=str(item["url"]), title=_clean(str(item.get("title", ""))), snippet=_clean(str(item.get("content", ""))),
                                      engine="searxng", rank=len(hits) + 1, published=item.get("publishedDate") or None))
        return hits, "" if hits else "no results"

    def _local_json(self, url: str, params: dict[str, Any]) -> tuple[Any, str]:
        import httpx

        try:
            with httpx.Client(transport=self.transport, timeout=6.0, follow_redirects=False) as client:
                response = client.get(url, params=params, headers={"Accept": "application/json"})
            if response.status_code != 200:
                return None, f"http {response.status_code}"
            return response.json(), ""
        except (httpx.HTTPError, ValueError) as exc:
            return None, f"unreachable: {type(exc).__name__}"


__all__ = ["WebSearch", "parse_ddg_html", "parse_bing_html", "rrf_merge", "is_safe_url", "is_private_host", "normalize_url", "url_key", "host_of"]
