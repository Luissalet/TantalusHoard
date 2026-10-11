"""The Hub's browser lanes (Agora #265): a browser read of a site takes the site's lane first, so two apps never automate
the same shop at once. A stub Hub (a tiny HTTP server speaking the lane endpoints) stands in for the real one; the real
client library does the talking."""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest
from conftest import T0, tool

from tantalus_hoard.config import Config
from tantalus_hoard.db import Database
from tantalus_hoard.engine import BUSY_RETRY_S
from tantalus_hoard.errors import TantalusError
from tantalus_hoard.fetch import Fetcher, busy_text
from tantalus_hoard.hoard_link import fam_web
from tantalus_hoard.model import FetchResult

PUBLIC = ["93.184.216.34"]
URL = "https://shop.example.co.uk/p/1"
HTML = "<html><head><title>Ficha</title></head><body><h1>Ficha</h1></body></html>"


class Clock:
    def __init__(self, start: float = 1_790_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeBrowser:
    """Duck-typed browser rung; logs every call into the shared ``events`` list (next to the Hub requests)."""

    def __init__(self, events: list):
        self.events = events
        self.calls: list[str] = []

    def available(self):
        return True

    def unavailable_reason(self):
        return ""

    def fetch(self, url, settle_s=None, *, timeout_s=None):
        self.calls.append(url)
        self.events.append(("browser.fetch", url))
        return FetchResult(url=url, tier="browser", status=200, text=HTML, ok=True)

    def fetch_window(self, url, settle_s=None, *, timeout_s=None):
        self.calls.append(url)
        self.events.append(("browser.window", url))
        return FetchResult(url=url, tier="window", status=200, text=HTML, ok=True)

    @contextmanager
    def browser_session(self, headless=True):
        self.events.append(("browser.session", headless))
        yield SimpleNamespace(cookies=lambda *a: [])

    def open_for_human(self, url, **kw):
        self.events.append(("browser.human", url))
        return {"closed": True}

    def close(self):
        pass


@pytest.fixture()
def hub(monkeypatch):
    """A stub Hub with lane endpoints; ``busy`` makes ``acquire`` answer that another app holds the lane."""
    import tantalus_hoard.fetch as fetch_mod
    fetch_mod._hub_down_until[0] = 0.0                      # a Hub that was down in another test is not remembered here
    state = SimpleNamespace(events=[], busy=None, auth=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, payload, status=200):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._reply({"ok": False}, 404)

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            state.events.append((self.path, body))
            state.auth.append(self.headers.get("Authorization"))
            if self.path == "/api/lane/acquire":
                if state.busy:
                    return self._reply({"ok": True, "granted": False, "busy": True, "queue_seq": 7, "waiting": 0, **state.busy})
                return self._reply({"ok": True, "granted": True, "busy": False, "lease_id": "L1", "domain": body["domain"]})
            return self._reply({"ok": True, "released": True})

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setenv("HOARD_HUB_URL", state.url)
    monkeypatch.setenv("HOARD_HUB_AUTOSTART", "0")
    monkeypatch.setattr(fam_web, "available", lambda *a, **k: False)
    state.requests = lambda path: [b for p, b in state.events if p == path]
    yield state
    srv.shutdown()
    fetch_mod._hub_down_until[0] = 0.0


@pytest.fixture()
def env(tmp_path, hub):
    db = Database(tmp_path / "t.db")
    config = Config(data_dir=tmp_path, browser=True, lane_wait_s=0.3)
    clock = Clock()
    browser = FakeBrowser(hub.events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    def make(handler=handler, **kw) -> Fetcher:
        return Fetcher(config, db, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep,
                       resolver=lambda host, port: PUBLIC, browser=browser, **kw)

    yield SimpleNamespace(db=db, config=config, hub=hub, browser=browser, make=make)
    db.close()


def test_a_free_lane_wraps_the_browser_read(env):
    fetcher = env.make(use_hub=True)
    fr = fetcher.get(URL, tier="browser")
    assert fr.ok and fr.tier == "browser"
    kinds = [e[0] for e in env.hub.events]
    assert kinds[0] == "/api/lane/acquire" and "browser.fetch" in kinds and kinds[-1] == "/api/lane/release"
    assert kinds.index("browser.fetch") < kinds.index("/api/lane/release")           # held for the whole read
    [acquire] = env.hub.requests("/api/lane/acquire")
    assert acquire["domain"] == "example.co.uk" and acquire["holder"] == "tantalus"       # the registrable root, not the shop host
    assert acquire["profile"].replace("\\", "/").lower().endswith("browser-profile")      # and the persistent Edge profile
    assert env.hub.requests("/api/lane/release")[0]["lease_id"] == "L1"


def test_a_visible_window_read_takes_the_lane_too(env):
    fetcher = env.make(use_hub=True)
    fr = fetcher.get(URL, tier="window")
    assert fr.ok and fr.tier == "window"
    assert [e[0] for e in env.hub.events] == ["/api/lane/acquire", "browser.window", "/api/lane/release"]


def test_busy_lane_answers_ocupado_por_x_and_never_opens_the_browser(env):
    env.hub.busy = {"held_by": "faustus", "since": T0, "purpose": "reading the shop", "domain": "example.co.uk"}
    fetcher = env.make(use_hub=True)
    fr = fetcher.get(URL, tier="browser")
    assert fr.ok is False and fr.error_kind == "busy" and fr.tier == "browser"
    assert "ocupado por faustus" in fr.error and "example.co.uk" in fr.error
    assert fr.blocked is False and fr.block_reason == ""                                   # not a block
    assert env.browser.calls == []                                                       # no browser was opened
    assert [e[0] for e in env.hub.events if e[0].startswith("/api")] == ["/api/lane/acquire"] * len(env.hub.requests("/api/lane/acquire"))
    row = fetcher.state.get("shop.example.co.uk")
    assert not row.get("fail_count") and not row.get("blocked_until_ts") and not row.get("block_reason")   # no failure, no cooldown


def test_busy_window_read_says_who_too(env):
    env.hub.busy = {"held_by": "argus", "since": T0}
    fr = env.make(use_hub=True).get(URL, tier="window")
    assert fr.error_kind == "busy" and fr.tier == "window" and "ocupado por argus" in fr.error
    assert env.browser.calls == []


def test_it_works_without_the_hub(env, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    monkeypatch.setenv("HOARD_HUB_URL", f"http://127.0.0.1:{dead}")
    fr = env.make(use_hub=True).get(URL, tier="browser")
    assert fr.ok and env.browser.calls == [URL]                                           # Hub down: proceed
    assert env.hub.events == [("browser.fetch", URL)]                                     # and nothing reached the stub


def test_no_lane_on_a_test_transport_or_when_switched_off(env):
    fr = env.make().get(URL, tier="browser")                                              # use_hub follows the transport: off
    assert fr.ok and env.hub.requests("/api/lane/acquire") == []
    env.config.lanes = False
    fr = env.make(use_hub=True).get(URL, tier="browser")
    assert fr.ok and env.hub.requests("/api/lane/acquire") == []


def test_browser_session_holds_the_lane_and_reports_busy(env):
    fetcher = env.make(use_hub=True)
    with fetcher.browser_session(url="https://www.facebook.com/") as context:
        assert context is not None
        assert [e[0] for e in env.hub.events] == ["/api/lane/acquire", "browser.session"]
    assert env.hub.events[-1][0] == "/api/lane/release"
    assert env.hub.requests("/api/lane/acquire")[0]["domain"] == "facebook.com"
    env.hub.busy = {"held_by": "faustus", "since": T0}
    with pytest.raises(TantalusError) as exc:
        with fetcher.browser_session(url="https://www.facebook.com/"):
            pytest.fail("must not open")
    assert exc.value.code == "busy" and "ocupado por faustus" in exc.value.message and exc.value.status == 409


def test_open_for_human_reports_busy_quickly(env, monkeypatch):
    import tantalus_hoard.fetch as fetch_mod
    assert 0 < fetch_mod.LANE_HUMAN_WAIT_S <= 20                       # a person is waiting: a short wait for the lane
    monkeypatch.setattr(fetch_mod, "LANE_HUMAN_WAIT_S", 0.2)
    env.hub.busy = {"held_by": "faustus", "since": T0}
    fetcher = env.make(use_hub=True)
    with pytest.raises(TantalusError) as exc:
        fetcher.open_for_human(URL)
    assert exc.value.code == "busy" and ("browser.human", URL) not in env.hub.events
    env.hub.busy = None
    assert fetcher.open_for_human(URL) == {"closed": True}


def test_busy_text():
    assert busy_text("example.com", "faustus", T0).startswith("ocupado por faustus desde las ")
    assert busy_text("", "", None) == "ocupado por otra app"
    assert busy_text("example.com", "tantalus", T0).startswith("ocupado por otra lectura de Tantalus desde las ")


def _challenge(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403, text="<html><title>Just a moment...</title></html>",
                          headers={"content-type": "text/html", "cf-mitigated": "challenge"})


def test_a_busy_lane_in_the_auto_ladder_is_not_recorded_as_a_block(env):
    """P2 of the review: HTTP 403 + Cloudflare challenge escalates to the browser; with the lane held by another app the
    answer is *busy, retry later* and the site is not marked blocked (no failure, no cooldown)."""
    env.hub.busy = {"held_by": "faustus", "since": T0}
    fetcher = env.make(handler=_challenge, use_hub=True)
    fr = fetcher.get(URL)                                                    # tier "auto": http first, then the browser
    assert fr.error_kind == "busy" and fr.ok is False and fr.blocked is False and fr.block_reason == ""
    assert "ocupado por faustus" in fr.error and "browser fallback failed" not in fr.error
    assert env.browser.calls == []
    row = fetcher.state.get("shop.example.co.uk")
    assert not row.get("fail_count") and not row.get("blocked_until_ts") and not row.get("block_reason")
    # the lane frees up: the same ladder goes through the browser and gets the page
    env.hub.busy = None
    fr = fetcher.get(URL)
    assert fr.ok and fr.tier == "browser" and env.browser.calls == [URL]
    assert not fetcher.state.get("shop.example.co.uk").get("blocked_until_ts")


def test_the_auto_ladder_still_records_a_real_block(env):
    """Without the Hub's lanes in the way, the cascade is what it always was: the block is remembered."""
    class Failing(FakeBrowser):
        def fetch(self, url, settle_s=None, *, timeout_s=None):
            return FetchResult(url=url, tier="browser", status=403, ok=False, blocked=True, block_reason="cloudflare",
                               error="blocked", error_kind="blocked")

    env.browser.__class__ = Failing
    fetcher = env.make(handler=_challenge, use_hub=False)
    fr = fetcher.get(URL)
    assert fr.blocked and fr.block_reason == "cloudflare"
    row = fetcher.state.get("shop.example.co.uk")
    assert row.get("fail_count") == 2 and row.get("blocked_until_ts")


def test_waiting_on_its_own_profile_says_so(env):
    env.hub.busy = {"held_by": "tantalus", "since": T0, "purpose": "reading a page in a headless browser"}
    fr = env.make(use_hub=True).get(URL, tier="browser")
    assert fr.error_kind == "busy" and fr.error.startswith("ocupado por otra lectura de Tantalus desde las ")


def test_a_hub_that_did_not_answer_is_not_asked_again_for_a_minute(env, monkeypatch):
    import tantalus_hoard.fetch as fetch_mod
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    monkeypatch.setenv("HOARD_HUB_URL", f"http://127.0.0.1:{dead}")
    calls = []
    real = fetch_mod.browser_lane.lane
    monkeypatch.setattr(fetch_mod.browser_lane, "lane", lambda *a, **k: calls.append(1) or real(*a, **k))
    fetcher = env.make(use_hub=True)
    assert fetcher.get(URL, tier="browser").ok and len(calls) == 1
    assert fetch_mod._hub_down_until[0] > 0
    assert fetcher.get(URL, tier="browser").ok and len(calls) == 1               # no second attempt to reach the Hub
    fetch_mod._hub_down_until[0] = 0.0                                             # a minute later it asks again
    assert fetcher.get(URL, tier="browser").ok and len(calls) == 2


def test_the_lane_is_taken_with_the_apps_token_and_a_short_renewed_ttl(env, tmp_path):
    (tmp_path / "mcp-token").write_text("tok-tantalus")
    fetcher = env.make(use_hub=True)
    fetcher.get(URL, tier="browser")
    with fetcher.browser_session(url="https://www.facebook.com/"):
        pass
    assert set(env.hub.auth) == {"Bearer tok-tantalus"}
    assert [b["ttl_s"] for b in env.hub.requests("/api/lane/acquire")] == [300.0, 300.0]    # no 900 s / 1020 s leases


def test_engine_retries_soon_without_a_failure_or_an_observation(svc):
    class BusyFetcher:
        def get(self, url, **_kw):
            return FetchResult(url=url, ok=False, tier="browser", error_kind="busy",
                               error="ocupado por faustus desde las 10:00:00: game.es ya se está leyendo con un navegador (carril del Hub)")

        def get_json(self, url, **kw):
            return self.get(url, **kw), None

        def clear_block(self, host):
            pass

        def host_status(self):
            return []

        def close(self):
            pass

    svc.engine.fetcher = BusyFetcher()
    w = tool(svc, "watcher_create", name="Switch 2", mode="availability", config={"product": {"terms": ["switch"]}})
    t = svc.store.create_target(w["id"], "https://www.game.es/consolas/switch-2", label="Switch 2")
    before = svc.store.target(t["id"])
    result = svc.engine.check_target(t["id"])
    assert result["busy"] is True and result["ok"] is False and result["blocked"] is False and "ocupado por faustus" in result["error"]
    after = svc.store.target(t["id"])
    assert after["status"] == before["status"] and int(after.get("fail_count") or 0) == 0
    assert after["next_check_ts"] == T0 + BUSY_RETRY_S and "ocupado por faustus" in after["last_error"]
    assert tool(svc, "target_get", target_id=t["id"])["observations"] == []
