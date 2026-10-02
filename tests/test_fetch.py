"""Fetch ladder: SSRF, robots, politeness, conditional GET, redirects, block detection, browser fallback.

Everything runs against ``httpx.MockTransport`` and fakes (clock, sleep, resolver, browser, Playwright): no network.
"""

from __future__ import annotations

import threading
from pathlib import Path

import httpx
import pytest

from tantalus_hoard.config import Config
from tantalus_hoard.db import Database
from tantalus_hoard.errors import TantalusError
from tantalus_hoard.fetch import Fetcher
from tantalus_hoard.hoard_link.web import safety
from tantalus_hoard.model import FetchResult

PAGES = Path(__file__).parent / "fixtures" / "pages"
PUBLIC = ["93.184.216.34"]
HTML = "<html><head><title>Ficha</title></head><body><h1>Pokémon</h1><p>Añadir al carrito</p></body></html>"


class Clock:
    def __init__(self, start: float = 1_790_000_000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeBrowser:
    """Duck-typed browser rung: returns scripted FetchResults."""

    def __init__(self, result: FetchResult | None = None, available: tuple[bool, str] = (True, "")):
        self.result = result
        self._available = available
        self.calls: list[str] = []
        self.closed = False

    def available(self):
        return self._available[0]

    def unavailable_reason(self):
        return self._available[1]

    def fetch(self, url, settle_s=None, *, timeout_s=None):
        self.calls.append(url)
        fr = self.result or FetchResult(url=url, tier="browser", status=200, text=HTML, ok=True)
        return FetchResult(**{**fr.__dict__, "url": url})

    def close(self):
        self.closed = True


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    config = Config(data_dir=tmp_path, browser=True)
    clock = Clock()
    requests: list[httpx.Request] = []
    routes: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        key = str(request.url)
        for pattern, response in routes.items():
            if key == pattern or key.startswith(pattern):
                return response(request) if callable(response) else response
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html; charset=utf-8"})

    def make(browser=None, **kw) -> Fetcher:
        return Fetcher(config, db, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep,
                       resolver=lambda host, port: PUBLIC, browser=browser if browser is not None else False, **kw)

    class Env:
        pass

    e = Env()
    e.db, e.config, e.clock, e.requests, e.routes, e.make = db, config, clock, requests, routes, make
    e.pages = lambda: [r for r in requests if r.url.path != "/robots.txt"]
    yield e
    db.close()


# ------------------------------------------------------------------------------------------------ safety
@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "http://127.0.0.1/admin", "http://localhost:8080/",
    "http://[::1]/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://user:pw@example.com/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/", "http://printer.local/", "", "http:///nohost",
])
def test_unsafe_urls_are_rejected(url):
    assert safety.check_url(url, resolver=lambda h, p: PUBLIC)


def test_public_urls_pass_and_resolution_is_checked():
    assert safety.check_url("https://www.game.es/x", resolver=lambda h, p: PUBLIC) is None
    assert safety.check_url("https://8.8.8.8/x") is None
    assert "10.1.2.3" in safety.check_url("https://evil.example/x", resolver=lambda h, p: ["93.184.216.34", "10.1.2.3"])   # every answer is judged
    assert "::1" in safety.check_url("https://evil.example/x", resolver=lambda h, p: ["::1"])
    bad = safety.check_url("https://nope.invalid/x", resolver=lambda h, p: (_ for _ in ()).throw(OSError("no dns")))
    assert bad.startswith(safety.UNRESOLVABLE_PREFIX)


def test_get_refuses_unsafe_and_offline(env):
    fetcher = env.make()
    fr = fetcher.get("http://127.0.0.1/secret")
    assert not fr.ok and fr.block_reason == "unsafe_url" and not env.requests
    env.config.offline = True
    fr = fetcher.get("https://www.game.es/x")
    assert not fr.ok and fr.block_reason == "offline" and not env.requests
    env.config.offline = False


def test_dns_failure_is_a_plain_error_not_unsafe(env):
    fetcher = Fetcher(env.config, env.db, transport=httpx.MockTransport(lambda r: httpx.Response(200)), clock=env.clock,
                      sleep=env.clock.sleep, browser=False,
                      resolver=lambda h, p: (_ for _ in ()).throw(OSError("Name or service not known")))
    fr = fetcher.get("https://nonexistent.example/x")
    assert not fr.ok and fr.block_reason == "" and "unresolvable" in fr.error


def test_redirect_to_private_address_is_refused(env):
    env.routes["https://www.game.es/redir"] = httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    fr = env.make().get("https://www.game.es/redir", respect_robots=False)
    assert not fr.ok and fr.block_reason == "unsafe_url" and "redirect refused" in fr.error
    assert all(r.url.host != "169.254.169.254" for r in env.requests)


def test_redirects_are_followed_and_capped(env):
    env.routes["https://www.game.es/a"] = httpx.Response(301, headers={"location": "/b"})
    env.routes["https://www.game.es/b"] = httpx.Response(200, text=HTML)
    fr = env.make().get("https://www.game.es/a", respect_robots=False)
    assert fr.ok and fr.final_url == "https://www.game.es/b" and fr.url == "https://www.game.es/a"
    env.routes["https://www.game.es/loop"] = httpx.Response(302, headers={"location": "/loop"})
    fr = env.make().get("https://www.game.es/loop", respect_robots=False, min_interval_s=0)
    assert not fr.ok and "too many redirects" in fr.error


# ------------------------------------------------------------------------------------------------ basics
def test_ok_fetch_sends_a_desktop_browser_identity(env):
    fr = env.make().get("https://www.game.es/x", params={"a": 1})
    assert fr.ok and fr.status == 200 and fr.tier == "http" and "Pokémon" in fr.text
    assert fr.url == "https://www.game.es/x?a=1"
    request = env.pages()[0]
    assert "Chrome/" in request.headers["user-agent"] and "Windows NT" in request.headers["user-agent"]
    assert request.headers["accept-language"].startswith("es-ES")
    assert "br" not in request.headers["accept-encoding"]
    assert "text/html" in request.headers["accept"]


def test_charset_is_honoured_and_body_is_capped(env):
    env.routes["https://www.game.es/latin"] = httpx.Response(200, content="<p>camión</p>".encode("latin-1"), headers={"content-type": "text/html; charset=iso-8859-1"})
    env.routes["https://www.game.es/big"] = httpx.Response(200, content=b"a" * (4 * 1024 * 1024), headers={"content-type": "text/html"})
    fetcher = env.make()
    assert "camión" in fetcher.get("https://www.game.es/latin", min_interval_s=0).text
    assert len(fetcher.get("https://www.game.es/big", min_interval_s=0).text) <= 3 * 1024 * 1024 + 65536


def test_network_error_is_not_a_block(env):
    def boom(request):
        raise httpx.ConnectError("refused")

    env.routes["https://www.game.es/x"] = boom
    fr = env.make().get("https://www.game.es/x", respect_robots=False)
    assert not fr.ok and not fr.blocked and fr.error_kind == "network" and "refused" in fr.error and fr.status == 0
    row = env.db.one("SELECT * FROM host_state WHERE host='www.game.es'")
    assert row["fail_count"] == 1 and not row["blocked_until_ts"]


def test_conditional_get(env):
    env.routes["https://www.game.es/c"] = lambda r: (httpx.Response(304) if r.headers.get("if-none-match") == '"v1"'
                                                     else httpx.Response(200, text=HTML, headers={"etag": '"v1"', "last-modified": "Tue, 29 Sep 2026 10:00:00 GMT"}))
    fetcher = env.make()
    first = fetcher.get("https://www.game.es/c", respect_robots=False, min_interval_s=0)
    assert first.ok and first.etag == '"v1"' and first.last_modified.startswith("Tue")
    second = fetcher.get("https://www.game.es/c", etag=first.etag, last_modified=first.last_modified, respect_robots=False, min_interval_s=0)
    assert second.ok and second.not_modified and second.status == 304
    assert env.pages()[-1].headers["if-modified-since"].startswith("Tue")


def test_get_json(env):
    env.routes["https://api.example.com/x"] = httpx.Response(200, json={"a": [1, 2]})
    env.routes["https://api.example.com/bad"] = httpx.Response(200, text="{nope")
    fetcher = env.make()
    fr, data = fetcher.get_json("https://api.example.com/x", min_interval_s=0)
    assert fr.ok and data == {"a": [1, 2]}
    assert env.requests[-1].headers["accept"].startswith("application/json")
    assert not [r for r in env.requests if r.url.path == "/robots.txt"]  # JSON APIs skip robots
    fr, data = fetcher.get_json("https://api.example.com/bad", min_interval_s=0)
    assert not fr.ok and data is None and "invalid JSON" in fr.error


# ------------------------------------------------------------------------------------------------ robots
def test_robots_disallow_blocks_and_is_cached(env):
    calls = []

    def robots(request):
        calls.append(1)
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")

    env.routes["https://www.game.es/robots.txt"] = robots
    fetcher = env.make()
    denied = fetcher.get("https://www.game.es/private/page", min_interval_s=0)
    assert not denied.ok and denied.block_reason == "robots" and not denied.blocked
    allowed = fetcher.get("https://www.game.es/public/page", min_interval_s=0)
    assert allowed.ok
    assert len(calls) == 1                      # cached (memory + host_state)
    row = env.db.one("SELECT robots_txt, robots_fetched_ts FROM host_state WHERE host='www.game.es'")
    assert "Disallow: /private" in row["robots_txt"] and row["robots_fetched_ts"]
    # a brand-new fetcher reads the DB copy instead of the network
    env.make().get("https://www.game.es/public/other", min_interval_s=0)
    assert len(calls) == 1
    # ... until the 24 h TTL expires
    env.clock.now += 25 * 3600
    env.make().get("https://www.game.es/public/other", min_interval_s=0)
    assert len(calls) == 2


def test_robots_missing_or_unreachable_allows(env):
    fetcher = env.make()
    assert fetcher.get("https://www.game.es/x", min_interval_s=0).ok        # 404 robots
    env.routes["https://shop.example/robots.txt"] = httpx.Response(503)
    fr = fetcher.get("https://shop.example/x", min_interval_s=0)
    assert fr.ok and "robots.txt unreachable" in fr.note
    fr = fetcher.get("https://shop.example/y", min_interval_s=0)             # not re-fetched right away
    assert fr.ok
    assert len([r for r in env.requests if str(r.url) == "https://shop.example/robots.txt"]) == 1


def test_respect_robots_false_and_non_html_skip_robots(env):
    env.routes["https://www.game.es/robots.txt"] = httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    fetcher = env.make()
    assert fetcher.get("https://www.game.es/x", respect_robots=False, min_interval_s=0).ok
    assert fetcher.get("https://www.game.es/api", accept="json", min_interval_s=0).ok


# ------------------------------------------------------------------------------------------------ politeness
def test_per_host_politeness_uses_injected_sleep(env):
    fetcher = env.make()
    fetcher.get("https://www.game.es/a", respect_robots=False)
    assert env.clock.sleeps == []                       # first hit: nothing to wait for
    env.clock.now += 5
    fetcher.get("https://www.game.es/b", respect_robots=False)
    assert env.clock.sleeps == [15.0]                   # default 20 s interval, 5 s already elapsed
    fetcher.get("https://www.game.es/c", respect_robots=False, min_interval_s=0)
    assert env.clock.sleeps == [15.0]                   # caller override: no wait
    fetcher.get("https://other.example/a", respect_robots=False)
    assert env.clock.sleeps == [15.0]                   # a different host is independent


def test_wait_is_capped_at_30_seconds_and_interval_is_per_host_setting(env):
    fetcher = env.make()
    fetcher.set_min_interval("www.game.es", 120)
    fetcher.get("https://www.game.es/a", respect_robots=False)
    fetcher.get("https://www.game.es/b", respect_robots=False)
    assert env.clock.sleeps == [30.0]


# ------------------------------------------------------------------------------------------------ blocks
def test_http_block_sets_cooldown_and_is_not_retried_over_http(env):
    env.routes["https://www.carrefour.es/"] = httpx.Response(403, text=(PAGES / "carrefour_cloudflare.html").read_text(encoding="utf-8"))
    fetcher = env.make()
    fr = fetcher.get("https://www.carrefour.es/p", respect_robots=False)
    assert fr.blocked and fr.block_reason == "cloudflare" and not fr.ok and fr.status == 403
    state = env.db.one("SELECT * FROM host_state WHERE host='www.carrefour.es'")
    assert state["block_reason"] == "cloudflare" and state["blocked_until_ts"] == pytest.approx(env.clock.now + 1800, abs=1)
    n = len(env.requests)
    again = fetcher.get("https://www.carrefour.es/p2", respect_robots=False)
    assert again.blocked and again.block_reason == "cloudflare" and "not retrying" in again.error
    assert len(env.requests) == n                       # cooldown: no request at all
    env.clock.now += 1801                               # cooldown over: http is tried again
    fetcher.get("https://www.carrefour.es/p3", respect_robots=False)
    assert len(env.requests) == n + 1
    status = {r["host"]: r for r in fetcher.host_status()}["www.carrefour.es"]
    assert status["fail_count"] == 2 and status["block_reason"] == "cloudflare"


def test_auto_falls_back_to_browser_and_remembers_it(env):
    env.routes["https://www.amazon.es/dp/"] = httpx.Response(200, text=(PAGES / "amazon_interstitial_akamai.html").read_text(encoding="utf-8"))
    browser = FakeBrowser()
    fetcher = env.make(browser=browser)
    fr = fetcher.get("https://www.amazon.es/dp/B0F18B4CVX", respect_robots=False)
    assert fr.ok and fr.tier == "browser" and browser.calls == ["https://www.amazon.es/dp/B0F18B4CVX"]
    assert len(env.pages()) == 1                        # one plain-http attempt first
    state = env.db.one("SELECT * FROM host_state WHERE host='www.amazon.es'")
    assert state["preferred_tier"] == "browser" and not state["blocked_until_ts"] and state["ok_count"] == 1
    fr = fetcher.get("https://www.amazon.es/dp/B0D6WLV3QH", respect_robots=False)
    assert fr.ok and fr.tier == "browser" and len(env.pages()) == 1       # straight to the browser, no http
    fr = fetcher.get("https://www.amazon.es/dp/X", tier="http", respect_robots=False)
    assert len(env.pages()) == 2 and fr.tier == "http"                     # tier=http never uses the browser
    assert len(browser.calls) == 2


def test_known_js_shell_hosts_go_straight_to_the_browser(env):
    browser = FakeBrowser()
    fetcher = env.make(browser=browser)
    fr = fetcher.get("https://www.game.es/buscar/pokemon", respect_robots=False)
    assert fr.ok and fr.tier == "browser" and env.pages() == []                # the profile says plain http is a shell
    fetcher.reset_host("www.game.es")                                            # forget the remembered tier
    fr = fetcher.get("https://www.game.es/producto/12345", respect_robots=False)   # only /buscar is a shell there
    assert fr.tier == "http" and len(env.pages()) == 1
    fr = fetcher.get("https://www.xtralife.com/p/abc", respect_robots=False)
    assert fr.tier == "browser" and len(browser.calls) == 2


def test_known_shell_host_without_the_browser_falls_back_to_http(env):
    fetcher = env.make()                                                        # no browser rung
    fr = fetcher.get("https://www.xtralife.com/p/abc", respect_robots=False)
    assert fr.tier == "http" and len(env.pages()) == 1


def test_browser_blocked_too_is_reported_and_left_alone(env):
    env.routes["https://www.pccomponentes.com/"] = httpx.Response(403, text=(PAGES / "pccomponentes_cloudflare.html").read_text(encoding="utf-8"))
    blocked = FetchResult(url="", tier="browser", status=403, text=(PAGES / "pccomponentes_cloudflare.html").read_text(encoding="utf-8"),
                          blocked=True, block_reason="cloudflare", error="blocked: Cloudflare challenge page")
    browser = FakeBrowser(blocked)
    fetcher = env.make(browser=browser)
    fr = fetcher.get("https://www.pccomponentes.com/x", respect_robots=False)
    assert fr.blocked and fr.tier == "browser" and fr.block_reason == "cloudflare"
    calls = len(browser.calls)
    again = fetcher.get("https://www.pccomponentes.com/y", respect_robots=False)
    assert again.blocked and "not retrying" in again.error and len(browser.calls) == calls
    fetcher.clear_block("www.pccomponentes.com")        # e.g. after the human solved it
    fetcher.get("https://www.pccomponentes.com/z", respect_robots=False, min_interval_s=0)
    assert len(browser.calls) == calls + 1


def test_tier_browser_respects_the_cooldown_until_it_is_cleared_and_needs_the_rung(env):
    env.routes["https://www.fnac.es/"] = httpx.Response(403, text="<html>x</html>")
    fetcher = env.make(browser=FakeBrowser())
    fetcher.get("https://www.fnac.es/a", tier="http", respect_robots=False)     # cooldown now
    fr = fetcher.get("https://www.fnac.es/b", tier="browser", respect_robots=False, min_interval_s=0)
    assert fr.blocked and "not retrying" in fr.error                             # the family cooldown applies to every tier
    fetcher.clear_block("www.fnac.es")                                           # e.g. after the human solved it
    fr = fetcher.get("https://www.fnac.es/b", tier="browser", respect_robots=False, min_interval_s=0)
    assert fr.ok and fr.tier == "browser"
    off = env.make()                                                             # rung disabled
    fr = off.get("https://www.fnac.es/c", tier="browser", respect_robots=False)
    assert not fr.ok and "disabled" in fr.error


def test_block_without_browser_stays_blocked(env):
    env.routes["https://www.fnac.es/"] = httpx.Response(403, text="<html>x</html>")
    fr = env.make().get("https://www.fnac.es/a", respect_robots=False)
    assert fr.blocked and fr.block_reason == "http_403" and not fr.ok


def test_rate_limit_uses_retry_after_and_never_the_browser(env):
    env.routes["https://shop.example/"] = httpx.Response(429, text="slow down", headers={"retry-after": "120"})
    browser = FakeBrowser()
    fetcher = env.make(browser=browser)
    fr = fetcher.get("https://shop.example/a", respect_robots=False)
    assert fr.blocked and fr.block_reason == "http_429" and not browser.calls
    state = env.db.one("SELECT blocked_until_ts FROM host_state WHERE host='shop.example'")
    assert state["blocked_until_ts"] == pytest.approx(env.clock.now + 120, abs=1)


def test_server_error_is_not_a_block(env):
    env.routes["https://shop.example/"] = httpx.Response(503, text="down")
    fr = env.make(browser=FakeBrowser()).get("https://shop.example/a", respect_robots=False)
    assert not fr.ok and not fr.blocked and fr.block_reason == "http_5xx"
    assert not env.db.one("SELECT blocked_until_ts FROM host_state WHERE host='shop.example'")["blocked_until_ts"]


def test_host_status_lists_rows_and_reset(env):
    fetcher = env.make()
    fetcher.get("https://www.game.es/x", respect_robots=False)
    rows = fetcher.host_status()
    assert rows[0]["host"] == "www.game.es" and rows[0]["ok_count"] == 1 and rows[0]["blocked_now"] is False
    fetcher.reset_host("www.game.es")
    assert fetcher.host_status()[0]["preferred_tier"] == ""


def test_browser_session_requires_the_rung(env):
    with pytest.raises(TantalusError) as info:
        with env.make().browser_session():
            pass
    assert info.value.code == "fetch_failed"


class HumanBrowser(FakeBrowser):
    def open_for_human(self, url, *, timeout_s=900.0):
        self.calls.append(("human", url))
        return {"url": url, "closed_by_user": True}


def test_fetcher_open_for_human_clears_the_block(env):
    env.routes["https://www.fnac.es/"] = httpx.Response(403, text="<html>x</html>")
    fetcher = env.make(browser=HumanBrowser())
    fetcher.get("https://www.fnac.es/a", tier="http", respect_robots=False)
    assert fetcher.host_status()[0]["blocked_now"] is True
    assert fetcher.open_for_human("https://www.fnac.es/a", timeout_s=2)["closed_by_user"] is True
    assert fetcher.host_status()[0]["blocked_now"] is False


def test_open_for_human_without_a_browser_asks_the_hub_or_says_why(env, monkeypatch):
    from tantalus_hoard.fetch import fam_web
    fetcher = env.make()                                  # no rung
    fetcher.use_hub = True
    monkeypatch.setattr(fam_web, "available", lambda *a, **k: False)
    with pytest.raises(TantalusError) as info:
        fetcher.open_for_human("https://www.fnac.es/a")
    assert info.value.code == "fetch_failed"
    monkeypatch.setattr(fam_web, "available", lambda *a, **k: True)
    monkeypatch.setattr(fam_web, "open_for_human", lambda url: {"ok": True, "started": True, "via": "family_profile"})
    assert fetcher.open_for_human("https://www.fnac.es/a")["via"] == "hub"


# ------------------------------------------------------------------------------------------------ visible window (Carrefour)
class FakeWindowBrowser(FakeBrowser):
    def __init__(self):
        super().__init__()
        self.window_calls: list[str] = []

    def fetch_window(self, url, *, timeout_s=None):
        self.window_calls.append(url)
        return FetchResult(url=url, final_url=url, tier="window", status=200, text=HTML, ok=True)


def test_carrefour_product_pages_go_through_a_visible_window_and_never_plain_http(env):
    from tantalus_hoard.extract.sites import window_ok
    product = "https://www.carrefour.es/pokemon-mini-lata/VC4A-34066236/p"
    assert window_ok(product) and not window_ok("https://www.carrefour.es/?q=pokemon") and not window_ok("https://www.game.es/x/1")
    browser = FakeWindowBrowser()
    fetcher = env.make(browser=browser)
    fr = fetcher.get(product, respect_robots=False)
    assert fr.ok and fr.tier == "window" and browser.window_calls == [product] and not browser.calls
    assert not [r for r in env.pages() if r.url.host == "www.carrefour.es"]
    # the setting turns it off: back to the headless browser rung
    env.db.set_setting("browser.window", "0")
    fr = fetcher.get(product, respect_robots=False, min_interval_s=0)
    assert fr.tier == "browser" and browser.calls == [product]


def test_a_refused_window_leaves_the_shop_alone_for_a_while(env):
    product = "https://www.carrefour.es/pokemon-ultra-premium/VC4A-34535253/p"

    class Refused(FakeWindowBrowser):
        def fetch_window(self, url, *, timeout_s=None):
            self.window_calls.append(url)
            return FetchResult(url=url, final_url=url, tier="window", status=403, blocked=True, block_reason="cloudflare",
                               error="blocked: Cloudflare challenge page", text="<html><title>Attention Required! | Cloudflare</title></html>")
    browser = Refused()
    fetcher = env.make(browser=browser)
    first = fetcher.get(product, respect_robots=False)
    assert first.blocked and len(browser.window_calls) == 1
    second = fetcher.get(product, respect_robots=False, min_interval_s=0)
    assert second.blocked and "not retrying" in second.error and len(browser.window_calls) == 1


# ------------------------------------------------------------------------------------------------ the family web service
@pytest.fixture()
def hub(monkeypatch):
    """A fake hub web service: records the calls and answers what the test scripts."""
    from tantalus_hoard.fetch import fam_web

    class Hub:
        up = True
        calls: list = []
        answer: dict = {}

    h = Hub()
    h.calls = []
    h.answer = {"ok": True, "status": 200, "tier": "http", "text": HTML, "content_type": "text/html", "final_url": "https://www.game.es/x"}
    monkeypatch.setattr(fam_web, "available", lambda *a, **k: h.up)

    def fetch(url, **kw):
        h.calls.append((url, kw))
        return dict(h.answer) if h.up else {"ok": False, "error": "hub unreachable"}

    monkeypatch.setattr(fam_web, "fetch", fetch)
    return h


def test_plain_page_fetches_go_through_the_family_service_and_keep_the_local_state(env, hub):
    fetcher = env.make(use_hub=True)
    fr = fetcher.get("https://www.game.es/x", min_interval_s=0)
    assert fr.ok and fr.text == HTML and "family web service" in fr.note and fr.tier == "http"
    url, kw = hub.calls[0]
    assert url == "https://www.game.es/x" and kw["tier"] == "http" and kw["respect_robots"] is True and kw["fresh"] is True
    assert env.requests == []                                    # no traffic of our own, not even robots.txt
    assert env.db.one("SELECT ok_count FROM host_state WHERE host='www.game.es'")["ok_count"] == 1


def test_the_politeness_interval_of_the_app_still_applies_in_front_of_the_hub(env, hub):
    fetcher = env.make(use_hub=True)
    fetcher.get("https://www.game.es/a")
    env.clock.now += 5
    fetcher.get("https://www.game.es/b")
    assert env.clock.sleeps == [15.0] and len(hub.calls) == 2


def test_a_block_reported_by_the_hub_starts_the_local_cooldown_too(env, hub):
    hub.answer = {"ok": False, "status": 403, "tier": "http", "blocked": True, "block_reason": "cloudflare", "error": "blocked: Cloudflare"}
    fetcher = env.make(use_hub=True)
    fr = fetcher.get("https://www.carrefour.es/p", min_interval_s=0)
    assert fr.blocked and fr.block_reason == "cloudflare"
    again = fetcher.get("https://www.carrefour.es/p2", min_interval_s=0)
    assert again.blocked and "not retrying" in again.error and len(hub.calls) == 1


def test_robots_refusal_from_the_hub_is_a_soft_failure(env, hub):
    hub.answer = {"ok": False, "status": 0, "tier": "http", "error": "robots.txt of www.game.es disallows this path", "error_kind": "robots"}
    fr = env.make(use_hub=True).get("https://www.game.es/private", min_interval_s=0)
    assert not fr.ok and fr.block_reason == "robots" and not fr.blocked


def test_without_the_hub_the_local_ladder_runs_and_checks_robots_itself(env, hub):
    hub.up = False
    env.routes["https://www.game.es/robots.txt"] = httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    fetcher = env.make(use_hub=True)
    assert not fetcher.get("https://www.game.es/private/x", min_interval_s=0).ok
    assert fetcher.get("https://www.game.es/public", min_interval_s=0).ok
    assert [r.url.path for r in env.requests].count("/robots.txt") == 1


def test_custom_headers_and_other_tiers_stay_local(env, hub):
    fetcher = env.make(use_hub=True, browser=FakeBrowser())
    assert fetcher.get("https://www.game.es/x", headers={"X-Test": "1"}, respect_robots=False, min_interval_s=0).ok
    assert fetcher.get("https://www.fnac.es/y", tier="browser", respect_robots=False, min_interval_s=0).tier == "browser"
    assert hub.calls == []


def test_a_hub_level_refusal_falls_back_to_the_local_fetcher(env, hub):
    hub.answer = {"ok": False, "status": 403, "error": "this profile is for the hub page"}      # no "tier": the hub did not fetch
    fr = env.make(use_hub=True).get("https://www.game.es/x", respect_robots=False, min_interval_s=0)
    assert fr.ok and env.pages()
