from __future__ import annotations

import base64
from pathlib import Path

import httpx

from tantalus_hoard.model import FetchResult, SearchHit
from tantalus_hoard.hoard_link.web import safety
from tantalus_hoard.search import WebSearch, normalize_url, parse_bing_html, parse_ddg_html, rrf_merge, url_key

FIX = Path(__file__).parent / "fixtures" / "search"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeFetcher:
    """Answers by URL substring; records every call."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[dict] = []

    def _answer(self, url: str):
        for key, value in self.routes.items():
            if key in url:
                return value
        return FetchResult(url=url, error="no route")

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        value = self._answer(url)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            return FetchResult(url=url, status=200, text=value, ok=True, content_type="text/html")
        return value

    def get_json(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        value = self._answer(url)
        if isinstance(value, dict):
            return FetchResult(url=url, status=200, ok=True, text="{}"), value
        return value, None


# ------------------------------------------------------------------ parsers on real result pages
def test_parse_ddg_real_pages_decode_uddg():
    hits = parse_ddg_html(load("ddg_pokemon.html"))
    assert len(hits) == 8
    assert hits[0].url == "https://tcgradar.net/aniversario/"
    assert "30 Aniversario" in hits[0].title and "Comparador" in hits[0].snippet
    assert all(h.url.startswith("https://") and "duckduckgo.com" not in h.url for h in hits)
    assert [h.rank for h in hits] == list(range(1, 9))
    rtx = parse_ddg_html(load("ddg_rtx.html"))
    assert len(rtx) == 10 and rtx[0].url == "https://www.nvidia.com/es-es/products/rtx-spark/"


def test_parse_bing_real_pages_decode_redirect():
    hits = parse_bing_html(load("bing_rtx.html"))
    assert len(hits) == 10
    assert hits[0].url == "https://www.nvidia.com/es-la/geforce/rtx/"
    assert all("bing.com/ck" not in h.url for h in hits)
    assert hits[0].snippet
    assert len(parse_bing_html(load("bing_pokemon.html"))) == 10


def test_bing_direct_link_is_kept():
    html = '<li class="b_algo"><h2><a href="https://example.com/x">T</a></h2><div class="b_caption"><p>S</p></div></li>'
    (hit,) = parse_bing_html(html)
    assert (hit.url, hit.title, hit.snippet) == ("https://example.com/x", "T", "S")


def test_ddg_ads_and_internal_links_skipped():
    html = ('<div class="result result--ad"><a class="result__a" href="https://ads.example/x">ad</a></div>'
            '<div class="result"><a class="result__a" href="//duckduckgo.com/y.js?ad=1">tracker</a></div>'
            '<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fok.example%2Fp">ok</a></div>')
    assert [h.url for h in parse_ddg_html(html)] == ["https://ok.example/p"]


# ------------------------------------------------------------------ url helpers
def _safe(url):
    """A result URL a person may be sent to: judged by the commons' policy (name only, no DNS)."""
    return safety.check_url(url, safety.PUBLIC, lambda h, p: ["93.184.216.34"]) is None


def test_safe_url_rules():
    assert _safe("https://www.game.es/producto/x")
    for bad in ("ftp://example.com/a", "javascript:alert(1)", "http://127.0.0.1/x", "http://10.0.0.5/", "http://192.168.1.1/",
                "http://169.254.169.254/latest", "http://[::1]/", "http://localhost:8080/", "http://2130706433/", "http://printer.local/",
                "https://user:pw@example.com/", "not a url", "", "http://0x7f.0.0.1/", "http://100.64.0.1/"):
        assert not _safe(bad), bad
    assert _safe("http://93.184.216.34/")


def test_search_results_pointing_at_private_hosts_are_dropped():
    page = ('<li class="b_algo"><h2><a href="http://192.168.1.1/admin">router</a></h2></li>'
            '<li class="b_algo"><h2><a href="https://ok.example/p">ok</a></h2></li>')
    hits, _ = WebSearch(FakeFetcher({"bing.com": page, "duckduckgo": "<html></html>"})).search("q")
    assert [h.url for h in hits] == ["https://ok.example/p"]


def test_normalize_and_key():
    a = "HTTPS://WWW.Game.ES/producto/etb/?utm_source=x&fbclid=abc&color=red#frag"
    assert normalize_url(a) == "https://www.game.es/producto/etb?color=red"
    assert url_key(a) == url_key("http://game.es/producto/etb?color=red")
    assert normalize_url("https://a.com/") == "https://a.com"


# ------------------------------------------------------------------ fusion
def test_rrf_merges_same_url_across_engines():
    a = [SearchHit("https://a.com/x?utm_source=1", "A", "", "ddg", 1), SearchHit("https://b.com/", "B", "snip", "ddg", 2)]
    b = [SearchHit("https://www.b.com", "B2", "", "bing", 1), SearchHit("https://c.com/", "C", "", "bing", 2)]
    merged = rrf_merge([a, b])
    assert [h.url for h in merged][0] == "https://b.com/"  # in both lists: 1/62 + 1/61 beats 1/61
    b_hit = merged[0]
    assert b_hit.engine == "ddg+bing" and b_hit.snippet == "snip" and b_hit.rank == 1
    assert len(merged) == 3


# ------------------------------------------------------------------ WebSearch
def test_search_partial_failure_and_merge():
    fetcher = FakeFetcher({"duckduckgo": load("ddg_rtx.html"), "bing.com": FetchResult(url="x", status=429, blocked=True, block_reason="http_429")})
    hits, errors = WebSearch(fetcher).search('"RTX Spark" 128GB Europe', limit=5)
    assert len(hits) == 5 and hits[0].engine == "ddg"
    assert errors == {"bing": "blocked: http_429"}
    ddg_call = fetcher.calls[0]
    assert ddg_call["params"]["kl"] == "es-es" and ddg_call["respect_robots"] is False and ddg_call["tier"] == "http"


def test_search_both_engines_rrf_and_unsafe_dropped():
    ddg = ('<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=http%3A%2F%2F127.0.0.1%2Fadmin">bad</a></div>'
           '<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example%2Fp%2F1">good</a></div>')
    u = base64.urlsafe_b64encode(b"https://shop.example/p/1").decode().rstrip("=")
    bing = f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1{u}">Good</a></h2><div class="b_caption"><p>desc</p></div></li>'
    hits, errors = WebSearch(FakeFetcher({"duckduckgo": ddg, "bing.com": bing})).search("q")
    assert errors == {} and [h.url for h in hits] == ["https://shop.example/p/1"]
    assert hits[0].engine == "ddg+bing" and hits[0].snippet == "desc"


def test_search_freshness_params():
    fetcher = FakeFetcher({"duckduckgo": load("ddg_rtx.html"), "bing.com": load("bing_rtx.html")})
    WebSearch(fetcher).search("q", freshness_days=5)
    params = {("ddg" if "duck" in c["url"] else "bing"): c["params"] for c in fetcher.calls}
    assert params["ddg"]["df"] == "w"
    assert params["bing"]["filters"] == 'ex1:"ez2"'
    fetcher.calls.clear()
    WebSearch(fetcher).search("q")
    assert all("df" not in c["params"] and "filters" not in c["params"] for c in fetcher.calls)


def test_search_blocked_page_is_reported_not_bypassed():
    page = '<html><body><div class="anomaly-modal__modal">Unfortunately, bots use DuckDuckGo too</div></body></html>'
    hits, errors = WebSearch(FakeFetcher({"duckduckgo": page, "bing.com": "<html></html>"})).search("q")
    assert hits == [] and errors["ddg"] == "blocked: bot check" and errors["bing"] == "no results"


def test_search_engine_exception_is_contained():
    hits, errors = WebSearch(FakeFetcher({"duckduckgo": RuntimeError("boom"), "bing.com": load("bing_rtx.html")})).search("q")
    assert len(hits) == 10 and "RuntimeError" in errors["ddg"]


def test_brave_only_with_key_and_uses_header():
    payload = {"web": {"results": [{"url": "https://brave.example/a", "title": "<b>A</b>", "description": "d", "page_age": "2026-09-01"}]}}
    fetcher = FakeFetcher({"api.search.brave.com": payload, "duckduckgo": "<html></html>", "bing.com": "<html></html>"})
    ws = WebSearch(fetcher)
    assert "brave" not in ws.available_engines()
    hits, _ = WebSearch(fetcher, brave_key="k123").search("q", freshness_days=1)
    assert [h.url for h in hits] == ["https://brave.example/a"] and hits[0].title == "A" and hits[0].published == "2026-09-01"
    call = next(c for c in fetcher.calls if "brave" in c["url"])
    assert call["headers"]["X-Subscription-Token"] == "k123" and call["params"]["freshness"] == "pd"


def test_brave_key_from_config():
    class Cfg:
        def secret(self, name):
            return {"BRAVE_KEY": "from-config"}.get(name, "")

    assert "brave" in WebSearch(FakeFetcher({}), config=Cfg()).available_engines()


def test_searxng_local_instance_goes_through_the_fetcher_with_the_operator_profile():
    data = {"results": [{"url": "https://sx.example/1", "title": "One", "content": "c", "publishedDate": "2026-09-02"}]}
    fetcher = FakeFetcher({"localhost:8080": data})
    hits, errors = WebSearch(fetcher, searxng_url="http://localhost:8080").search("q", engines=["searxng"], freshness_days=20)
    assert errors == {} and hits[0].url == "https://sx.example/1" and hits[0].published == "2026-09-02"
    call = fetcher.calls[0]
    assert call["url"] == "http://localhost:8080/search" and call["params"]["format"] == "json" and call["params"]["time_range"] == "month"
    assert call["profile"] == "operator_local"      # the user's own instance may be on a private address; nothing else may


def test_searxng_down_is_an_error_not_a_crash():
    down = FetchResult(url="x", error="connection refused", error_kind="refused")
    ws = WebSearch(FakeFetcher({"localhost:8080": down, "duckduckgo": load("ddg_rtx.html")}), searxng_url="http://localhost:8080")
    hits, errors = ws.search("q", engines=["searxng", "ddg"])
    assert errors["searxng"] == "connection refused" and len(hits) == 10


def test_empty_query():
    assert WebSearch(FakeFetcher({})).search("  ") == ([], {"query": "empty query"})


def test_search_http_202_is_reported_as_bot_check():
    fr = FetchResult(url="x", status=202, ok=True, text="<html><body>please wait</body></html>")
    hits, errors = WebSearch(FakeFetcher({"duckduckgo": fr, "bing.com": load("bing_rtx.html")})).search("q")
    assert errors == {"ddg": "blocked: bot check (http 202)"} and len(hits) == 10


GNEWS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>
<item><title>ASUS ProArt P16 llegará el 8 de octubre con RTX Spark N1X - Xataka</title>
<link>https://news.google.com/rss/articles/CBMiABC?oc=5</link><pubDate>Mon, 28 Sep 2026 07:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;ASUS ProArt P16 llegará el 8 de octubre con RTX Spark N1X&lt;/a&gt;</description>
<source url="https://www.xataka.com">Xataka</source></item>
<item><title>MSI EdgeMesa N AI+ con RTX Spark N1X, 128 GB</title>
<link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;tid=1&amp;url=https%3a%2f%2fwww.msi.com%2fnews%2fedgemesa&amp;c=1</link>
<pubDate>Tue, 29 Sep 2026 07:00:00 GMT</pubDate><description>Mini PC with 128 GB of unified memory</description></item>
</channel></rss>"""


def test_parse_news_rss_keeps_publisher_and_decodes_bing_links():
    from tantalus_hoard.search import parse_news_rss
    g = parse_news_rss(GNEWS, "gnews")
    assert len(g) == 2 and g[0].snippet.endswith("[https://www.xataka.com]") and g[0].published == "2026-09-28T07:00:00Z"   # ISO UTC now (was the raw RFC 2822 text)
    b = parse_news_rss(GNEWS, "bingnews")
    assert b[1].url == "https://www.msi.com/news/edgemesa" and b[1].snippet == "Mini PC with 128 GB of unified memory"
