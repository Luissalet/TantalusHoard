"""Web search for the app: the commons' ``WebSearch`` (engines, parsers, rank fusion) returning ``SearchHit`` objects.

Engines: SearXNG (only when configured), DuckDuckGo HTML, Bing HTML, the Brave Search API (only with ``TANTALUS_BRAVE_KEY``) and
Google / Bing News RSS. Every engine may fail on its own; failures are reported per engine and never raised. Nothing here solves
CAPTCHAs: a blocked answer is reported as ``blocked``. Every request goes through the app's fetcher, so the SSRF policy, the
per-host spacing and the block cooldown (the hub's, when it answers) are shared with the rest of the family.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .hoard_link.web import search as _commons
from .hoard_link.web.feeds import parse_news_rss as _parse_news_rss
from .hoard_link.web.urls import host_of, normalize_url, url_key
from .model import SearchHit

RRF_K = _commons.RRF_K
DEFAULT_ENGINES = _commons.DEFAULT_ENGINES
NEWS_ENGINES = _commons.NEWS_ENGINES
SEARCH_MIN_INTERVAL_S = _commons.SEARCH_MIN_INTERVAL_S


def _hit(raw: dict[str, Any]) -> SearchHit:
    return SearchHit(url=raw["url"], title=raw.get("title", ""), snippet=raw.get("snippet", ""), engine=raw.get("engine", ""),
                     rank=int(raw.get("rank") or 0), published=raw.get("published"))


def _raw(hit: SearchHit) -> dict[str, Any]:
    return {"url": hit.url, "title": hit.title, "snippet": hit.snippet, "engine": hit.engine, "rank": hit.rank, "published": hit.published}


def parse_ddg_html(html: str) -> list[SearchHit]:
    return [_hit(h) for h in _commons.parse_ddg_html(html)]


def parse_bing_html(html: str) -> list[SearchHit]:
    return [_hit(h) for h in _commons.parse_bing_html(html)]


def parse_news_rss(xml_text: str, engine: str) -> list[SearchHit]:
    return [_hit(h) for h in _parse_news_rss(xml_text, engine)]


def rrf_merge(lists: list[list[SearchHit]], *, k: int = RRF_K) -> list[SearchHit]:
    """Reciprocal Rank Fusion over normalised URLs (the commons' fusion over ``SearchHit`` lists)."""
    return [_hit(h) for h in _commons.rrf_merge([[_raw(h) for h in hits] for hits in lists], k=k)]


class WebSearch(_commons.WebSearch):
    """``search(query, limit, freshness_days=..., engines=...) -> (hits, {engine: error})`` with ``SearchHit`` hits."""

    def __init__(self, fetcher: Any, *, searxng_url: Optional[str] = None, brave_key: Optional[str] = None, config: Any = None):
        super().__init__(fetcher, lang="es", region="ES",
                         searxng_url=searxng_url or (config.secret("SEARXNG_URL") if config is not None else ""),
                         brave_key=brave_key or (config.secret("BRAVE_KEY") if config is not None else ""))

    def search(self, query: str, limit: int = 10, *, freshness_days: Optional[int] = None,  # type: ignore[override]
               engines: Optional[Iterable[str]] = None, news: bool = False) -> tuple[list[SearchHit], dict[str, str]]:
        hits, errors = super().search(query, limit, freshness_days=freshness_days, engines=engines, news=news)
        return [_hit(h) for h in hits], errors


__all__ = ["WebSearch", "parse_ddg_html", "parse_bing_html", "parse_news_rss", "rrf_merge", "normalize_url", "url_key", "host_of"]
