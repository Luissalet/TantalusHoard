"""The family hub side: notifications through the hub, mail through the hub, the agenda, purchase matching and the budget line."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import T0, FakeNotifier, tool
from tantalus_hoard.budget import BudgetNote, unwrap
from tantalus_hoard.errors import TantalusError
from tantalus_hoard.mail.source import MailSource
from tantalus_hoard.notify import Notifier
from tantalus_hoard.purchase_match import identifiers, match_purchase, score_watcher, tokens

HOUR = 3600
EVENT = {"id": "e1", "type": "RESTOCK", "severity": "high", "title": "Elite Trainer Box", "summary": "En stock en GAME",
         "url": "https://www.game.es/etb", "price": 49.99, "currency": "EUR", "confidence": 90, "watcher_name": "Pokémon", "image": "",
         "dedupe_key": "k-etb"}


class Cfg:
    secrets: dict = {}

    def secret(self, name):
        return ""


class FakeHubNotify:
    """Stands in for hoard_link.fam_notify."""

    def __init__(self, up=True, answer=None):
        self.up, self.calls = up, []
        self.answer = answer if answer is not None else {"ok": True, "id": 7, "held": ""}

    def hub_available(self, timeout=1.0):
        return self.up

    def notify(self, title, body="", **kw):
        self.calls.append({"title": title, "body": body, **kw})
        return dict(self.answer)


def notifier(sett=None, hub=None, **kw):
    values = {"notify__email__faustus_dir": "/nonexistent/faustus", **(sett or {})}
    shown = []
    n = Notifier(Cfg(), lambda key, default=None: values.get(key.replace(".", "__"), default), clock=lambda: 42.0, platform="win32",
                 toast_backend=lambda t, b, u, h: shown.append((t, b)), hub_notify=hub, **kw)
    n.shown = shown
    return n


# ============================================================================ 1. notifications through the hub
def test_auto_sends_one_hub_notification_instead_of_the_push_channels():
    hub = FakeHubNotify()
    n = notifier(hub=hub)
    results = n.send(EVENT, ["toast", "hub", "ntfy", "telegram", "email"])
    assert len(hub.calls) == 1 and n.shown == []                         # the own toast did not run
    call = hub.calls[0]
    assert call["priority"] == "high" and call["group"] == "restock" and call["dedupe_key"] == "k-etb"
    assert call["url"] == "https://www.game.es/etb" and call["title"].startswith("Restock:") and "49,99" in call["body"]
    by = {r["channel"]: r for r in results}
    assert [by[c]["via"] for c in ("toast", "ntfy", "telegram", "email")] == ["hub"] * 4 and all(by[c]["ok"] for c in ("toast", "ntfy"))
    assert "via" not in by["hub"]                                        # the bus event is a separate channel, still its own


def test_severity_maps_to_hub_priority():
    hub = FakeHubNotify()
    n = notifier(hub=hub)
    for severity, expected in (("low", "low"), ("medium", "normal"), ("high", "high")):
        n.send({**EVENT, "severity": severity, "dedupe_key": f"k-{severity}"}, ["toast"])
        assert hub.calls[-1]["priority"] == expected


def test_a_held_notification_is_handled_not_failed():
    hub = FakeHubNotify(answer={"ok": True, "id": 9, "held": "quiet"})
    (res,) = notifier(hub=hub).send(EVENT, ["toast"])
    assert res["ok"] and res["via"] == "hub" and "quiet" in res["error"]


def test_auto_falls_back_to_the_own_channels_when_the_hub_does_not_answer():
    for hub in (FakeHubNotify(up=False), FakeHubNotify(answer={"ok": False, "error": "hub unreachable"})):
        n = notifier(hub=hub)
        (res,) = n.send(EVENT, ["toast"])
        assert res["ok"] and "via" not in res and len(n.shown) == 1


def test_hub_mode_never_falls_back_and_own_mode_never_calls_the_hub():
    down = FakeHubNotify(answer={"ok": False, "error": "hub unreachable"})
    n = notifier({"notify__via": "hub"}, hub=down)
    (res,) = n.send(EVENT, ["toast"])
    assert not res["ok"] and res["via"] == "hub" and "unreachable" in res["error"] and n.shown == []
    hub = FakeHubNotify()
    own = notifier({"notify__via": "own"}, hub=hub)
    (res,) = own.send(EVENT, ["toast"])
    assert res["ok"] and hub.calls == [] and len(own.shown) == 1


def test_only_the_bus_channel_means_no_hub_call():
    hub = FakeHubNotify()
    notifier(hub=hub).send(EVENT, ["hub"])
    assert hub.calls == []


def test_via_status_and_test_hub():
    hub = FakeHubNotify()
    n = notifier(hub=hub)
    assert n.via_status() == {"setting": "auto", "effective": "hub", "hub_available": True}
    hub.up = False
    assert n.via_status()["effective"] == "own"
    assert notifier({"notify__via": "own"}, hub=hub).via_status() == {"setting": "own", "effective": "own", "hub_available": False}
    hub.up = True
    res = n.test_hub()
    assert res["ok"] and res["via"] == "hub" and hub.calls[-1]["priority"] == "normal"


def test_settings_accept_the_new_keys(svc):
    out = svc.set_settings({"notify.via": "hub", "mail.source": "faustus"})
    assert out["notify.via"] == "hub" and out["mail.source"] == "faustus"
    with pytest.raises(TantalusError):
        svc.set_settings({"notify.via": "carrier-pigeon"})
    assert svc.settings()["notify.via"] == "hub"


def test_notify_tools_report_the_route(svc):
    out = tool(svc, "notify_status")
    assert "via" in out or out["channels"]                # FakeNotifier has no via_status: the key is simply empty
    svc.notifier = Notifier(Cfg(), svc.db.get_setting, hub_notify=FakeHubNotify(), platform="linux")
    assert tool(svc, "notify_status")["via"]["effective"] == "hub"
    assert tool(svc, "notify_test", via="hub")["ok"]


# ============================================================================ 5. the budget line
def ledger(answer, calls):
    def call(app, name, args, timeout=0):
        calls.append((app, name, args, timeout))
        return answer
    return call


OK = {"ok": True, "result": {"ok": True, "categories": [{"category": "Ocio", "budget": 100, "spent": 40, "left": 60.5},
                                                          {"category": "Casa", "budget": 50, "spent": 70, "left": -20}]}}


def test_budget_note_text_cache_and_failures():
    calls: list = []
    now = [1000.0]
    b = BudgetNote(call=ledger(OK, calls), clock=lambda: now[0])
    assert b.note("ocio") == "quedan 60,50 € en Ocio" and b.note("Ocio", "en") == "€60.50 left in Ocio"
    assert len(calls) == 1 and calls[0][:3] == ("ledger", "budget_status", {"category": "ocio"}) and calls[0][3] <= 3     # cached, short timeout
    now[0] += 601
    b.note("Ocio")
    assert len(calls) == 2                                              # ten minutes later it asks again
    assert b.note("Viajes") == "" and b.note("") == ""                  # unknown or empty category: no line

    calls2: list = []
    away = BudgetNote(call=ledger({"ok": False, "error": "app not running"}, calls2), clock=lambda: now[0])
    assert away.note("Ocio") == "" and away.note("Ocio") == "" and len(calls2) == 1       # the failure is cached too
    boom = BudgetNote(call=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hub")), clock=lambda: now[0])
    assert boom.note("Ocio") == ""


def test_unwrap_handles_json_text_and_mcp_content():
    assert unwrap({"ok": True, "result": '{"a": 1}'}) == {"a": 1}
    assert unwrap({"ok": True, "result": {"content": [{"type": "text", "text": '{"b": 2}'}]}}) == {"b": 2}
    assert unwrap({"ok": False}) is None and unwrap("x") is None


def make_watcher(svc, name="Pokémon 30 aniversario", *, terms=None, must=None, category=None, **cfg):
    config = {"product": {"terms": terms or ["pokemon", "30", "aniversario"], "must": must if must is not None else ["pokemon"]}, **cfg}
    if category:
        config["budget_category"] = category
    w = svc.store.create_watcher(name=name, mode="availability", config=config)
    return w


def dispatch(svc, watcher, **event):
    ev = svc.store.add_event({"watcher_id": watcher["id"], "type": "PRICE_DROP", "status": "confirmed", "severity": "high", "price": 25.0,
                              "currency": "EUR", "dedupe_key": "d1", "url": "https://x.example/p", "title": "ETB", "summary": "Baja", **event})
    svc.engine.dispatch(ev, watcher)
    return svc.notifier.sent[-1][0]


def test_alert_with_a_price_gets_the_budget_line_and_never_waits_for_ledger(svc):
    calls: list = []
    svc.engine.budget = BudgetNote(call=ledger(OK, calls), clock=lambda: T0)
    payload = dispatch(svc, make_watcher(svc, category="Ocio"))
    assert payload["budget_note"] == "quedan 60,50 € en Ocio" and payload["dedupe_key"] == "d1"
    from tantalus_hoard.notify import compose
    assert "quedan 60,50 € en Ocio" in compose(payload, "es")[1]
    # no category, or no price: Ledger is not even asked
    before = len(calls)
    assert "budget_note" not in dispatch(svc, make_watcher(svc, "Otro"), dedupe_key="d2")
    assert "budget_note" not in dispatch(svc, make_watcher(svc, "Más", category="Ocio"), dedupe_key="d3", price=None)
    assert len(calls) == before
    # Ledger away: the alert still goes out, without the line
    svc.engine.budget = BudgetNote(call=ledger({"ok": False, "error": "not reachable"}, []), clock=lambda: T0)
    assert "budget_note" not in dispatch(svc, make_watcher(svc, "Y otro", category="Ocio"), dedupe_key="d4")


def test_budget_category_is_a_watcher_field(svc):
    w = tool(svc, "watcher_create", name="Kettle", mode="availability", budget_category="Casa")
    assert w["budget_category"] == "Casa" and w["config"]["budget_category"] == "Casa" and w["status"] == "active"
    upd = tool(svc, "watcher_update", watcher_id=w["id"], budget_category="Ocio")
    assert upd["budget_category"] == "Ocio"
    assert tool(svc, "watcher_update", watcher_id=w["id"], budget_category="")["budget_category"] == ""


# ============================================================================ 4. purchase matching, mark bought, watcher_add
def test_tokens_and_identifiers():
    assert tokens("Pokémon JCC 30.º Aniversario Elite Trainer Boxes") >= {"pokemon", "jcc", "30", "aniversario", "elite", "trainer", "box"}
    assert "de" not in tokens("Caja de cartas") and "ab" in tokens("AB 12")
    assert identifiers("https://www.amazon.es/dp/B0C1234567/ref=x", "EAN 8435407612345") == {"B0C1234567", "8435407612345"}


def test_match_scores_by_overlap_ids_and_shop(svc):
    poke = make_watcher(svc, terms=["pokemon", "30", "aniversario"])
    svc.store.create_target(poke["id"], "https://www.game.es/buscar/pokemon", retailer="GAME")
    spark = make_watcher(svc, "DGX Spark", terms=["dgx", "spark"], must=[])
    svc.store.create_target(spark["id"], "https://marketplace.example.com/dp/B0CSPARK99", retailer="Example")
    kettle = make_watcher(svc, "Kettle", terms=["kettle"], must=[])
    by_id = {w["id"]: svc.store.targets(watcher_id=w["id"]) for w in svc.store.watchers()}
    ws = svc.store.watchers()

    out = tool(svc, "watchers_match_purchase", title="Pokémon JCC 30 Aniversario Elite Trainer Box", merchant="GAME")
    assert out["ok"] and out["matches"][0]["watcher_id"] == poke["id"] and out["matches"][0]["score"] >= 0.9
    assert out["matches"][0]["title"] == "Pokémon 30 aniversario" and out["matches"][0]["reasons"]
    assert all(m["watcher_id"] != spark["id"] for m in out["matches"])

    ids = tool(svc, "watchers_match_purchase", title="Mini PC", url="https://marketplace.example.com/dp/B0CSPARK99?tag=x")
    assert ids["matches"][0]["watcher_id"] == spark["id"] and ids["matches"][0]["score"] == 1.0 and "same product" in ids["matches"][0]["reasons"][0]
    one_word = tool(svc, "watchers_match_purchase", title="Electric kettle 1.7L")
    assert one_word["matches"] and one_word["matches"][0]["score"] <= 0.7             # too generic to be marked bought automatically
    assert tool(svc, "watchers_match_purchase", title="Garden hose 20 m")["matches"] == []


def test_exclude_and_must_words():
    watcher = {"id": "w1", "name": "ETB", "config": {"product": {"terms": ["pokemon", "etb"], "must": ["pokemon"], "exclude": ["funda"]}}}
    assert score_watcher(watcher, [], title="Funda para Pokemon ETB")[0] == 0.0
    assert score_watcher(watcher, [], title="Magic ETB")[0] < 0.5
    assert score_watcher(watcher, [], title="Pokemon ETB")[0] == 1.0
    bought = {**watcher, "config": {**watcher["config"], "status": "bought"}}
    assert match_purchase([bought], {}, title="Pokemon ETB") == []


def test_mark_bought_stops_checks_keeps_history_and_emits(svc, monkeypatch):
    emitted, linked = [], []
    monkeypatch.setattr(svc, "_emit", lambda t, d: emitted.append((t, d)))
    monkeypatch.setattr(svc, "refs_link", lambda *a, **k: linked.append((a, k)))
    w = make_watcher(svc)
    t = svc.store.create_target(w["id"], "https://www.game.es/etb")
    svc.store.add_observation(t["id"], {"availability": "IN_STOCK", "price": 49.9})
    out = tool(svc, "watcher_mark_bought", watcher_id=w["id"], purchase_ref="hoard://hub/purchase/3")
    assert out["ok"] and out["status"] == "bought" and not out["already"]
    after = svc.store.watcher(w["id"])
    assert after["enabled"] is False and after["config"]["status"] == "bought" and after["config"]["bought"]["purchase_ref"] == "hoard://hub/purchase/3"
    assert svc.store.observations(t["id"]) and svc.store.due_targets(T0 + 10 * 86400) == []          # history kept, no more checks
    assert emitted == [("tantalus.watcher.bought", {"watcher_id": w["id"], "purchase_ref": "hoard://hub/purchase/3", "name": w["name"]})]
    svc.drain_background()
    assert linked and linked[0][0][:3] == (f"hoard://tantalus/watcher/{w['id']}", "hoard://hub/purchase/3", "purchase")
    again = tool(svc, "watcher_mark_bought", watcher_id=w["id"], purchase_ref="hoard://hub/purchase/3")
    assert again["already"] and len(emitted) == 1                                              # once only
    assert tool(svc, "watcher_get", watcher_id=w["id"])["status"] == "bought"
    assert tool(svc, "watchers_match_purchase", title="Pokemon 30 aniversario")["matches"] == []   # a bought watcher is not matched again
    back = tool(svc, "watcher_update", watcher_id=w["id"], enabled=True)
    assert back["status"] == "active" and "bought" not in back["config"]
    with pytest.raises(TantalusError):
        tool(svc, "watcher_mark_bought", watcher_id="w_nope")


def test_mark_bought_accepts_a_numeric_id(svc):
    w = make_watcher(svc)
    assert tool(svc, "watcher_mark_bought", watcher_id=w["id"])["ok"]
    with pytest.raises(TantalusError):
        tool(svc, "watcher_mark_bought", watcher_id=5)             # unknown numeric id: a clean not_found


def test_watcher_add_for_a_gift_idea(svc, monkeypatch):
    linked = []
    monkeypatch.setattr(svc, "refs_link", lambda *a, **k: linked.append(a))
    out = tool(svc, "watcher_add", title="Nintendo Switch 2 Mario Kart bundle", url="https://www.game.es/switch2", max_price=449.0,
               source_ref="hoard://people/gift/12", budget_category="Regalos")
    assert out["ok"] and not out["existing"] and out["target_id"] and out["price_threshold"] == 449.0 and "nintendo" in [t.lower() for t in out["terms"]]
    w = svc.store.watcher(out["watcher_id"])
    assert w["mode"] == "availability" and w["config"]["source_ref"] == "hoard://people/gift/12" and w["config"]["budget_category"] == "Regalos"
    assert w["config"]["policies"]["price_threshold"] == 449.0 and w["config"]["discovery"]["queries"]
    (target,) = svc.store.targets(watcher_id=w["id"])
    assert target["price_threshold"] == 449.0 and target["url"] == "https://www.game.es/switch2"
    svc.drain_background()
    assert linked and linked[0][1] == "hoard://people/gift/12"
    again = tool(svc, "watcher_add", name="whatever else", source_ref="hoard://people/gift/12")
    assert again["existing"] and again["watcher_id"] == w["id"] and len(svc.store.watchers()) == 1       # same idea, same watcher
    no_url = tool(svc, "watcher_add", name="Kettle Xiaomi")
    assert not no_url["target_id"] and "discovery" in no_url["note"]
    with pytest.raises(TantalusError):
        tool(svc, "watcher_add")


# ============================================================================ 3. the agenda
def agenda(client, **q):
    return client.get("/api/family/agenda", params=q, headers=client.bearer)


def test_agenda_needs_the_app_token(client):
    assert client.get("/api/family/agenda").status_code == 401
    assert client.get("/api/family/agenda", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_agenda_lists_radar_releases_and_dated_targets(client):
    svc = client.svc
    today = datetime.fromtimestamp(T0, timezone.utc).date()
    soon, later = today + timedelta(days=1), today + timedelta(days=20)
    w = svc.store.create_watcher(name="Pokémon", mode="availability", config={"radar": {"enabled": True}})
    for key, title, day in (("a", "Wave 2 ETB", soon), ("b", "Booster Bundle restock", later), ("c", "Dateless", None)):
        svc.db.execute("INSERT INTO radar_releases(watcher_id, rkey, source, slug, title, date, kind, url, data, first_seen_ts, last_seen_ts) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (w["id"], key, "stocktcg.net", key, title, day.isoformat() if day else "", "release",
                                                          f"https://stocktcg.example/{key}", json.dumps({"stores_total": 10, "stores_buyable": 3}), T0, T0))
    t = svc.store.create_target(w["id"], "https://www.game.es/coming", retailer="GAME", label="Booster Display")
    svc.store.update_target(t["id"], last_state="COMING_SOON", last_title="Booster Display ES")
    svc.store.add_observation(t["id"], {"availability": "COMING_SOON", "preorder_date": (today + timedelta(days=5)).isoformat()})
    nodate = svc.store.create_target(w["id"], "https://www.game.es/nodate")
    svc.store.update_target(nodate["id"], last_state="COMING_SOON")
    svc.store.add_observation(nodate["id"], {"availability": "COMING_SOON"})

    res = agenda(client, **{"from": today.isoformat(), "to": (today + timedelta(days=60)).isoformat()}).json()
    assert res["ok"]
    titles = {i["title"]: i for i in res["items"]}
    assert set(titles) == {"Wave 2 ETB", "Booster Bundle restock", "Booster Display ES"}                # no dateless items, no target without a date
    wave = titles["Wave 2 ETB"]
    assert wave["kind"] == "release" and wave["start"] == soon.isoformat() and wave["all_day"] and wave["priority"] == "high"
    assert wave["url"] == "https://stocktcg.example/a" and "3/10" in wave["detail"] and wave["id"].startswith("tantalus:release:")
    assert titles["Booster Bundle restock"]["priority"] == "normal"
    assert titles["Booster Display ES"]["start"] == (today + timedelta(days=5)).isoformat() and "coming soon" in titles["Booster Display ES"]["detail"]
    assert [i["start"] for i in res["items"]] == sorted(i["start"] for i in res["items"])
    narrow = agenda(client, **{"from": (today + timedelta(days=10)).isoformat(), "to": (today + timedelta(days=30)).isoformat()}).json()
    assert [i["title"] for i in narrow["items"]] == ["Booster Bundle restock"]


def test_manifest_declares_the_agenda():
    from pathlib import Path
    manifest = json.loads((Path(__file__).resolve().parent.parent / "faustus-plugin.json").read_text(encoding="utf-8"))
    assert manifest["x-family"] == {"agenda": True}


# ============================================================================ 2. mail through the hub
class FakeHubMail:
    """Stands in for hoard_link.fam_mail: a gateway with a few stored sale mails."""

    def __init__(self):
        self.up, self.messages_stored, self.interests, self.claims, self.requests = True, [], [], [], []
        self.fail = ""

    def available(self, timeout=1.0):
        return self.up

    def register_interest(self, spec, sphere=None):
        self.interests.append(spec)
        return {"ok": True}

    def messages(self, since_id=0, limit=100, full=True, interest=True):
        self.requests.append({"since_id": since_id, "limit": limit, "interest": interest})
        if self.fail:
            return {"ok": False, "error": self.fail, "messages": [], "last_id": since_id}
        rows = [m for m in self.messages_stored if m["id"] > since_id][:limit]
        return {"ok": True, "messages": rows, "last_id": rows[-1]["id"] if rows else since_id}

    def claim(self, ids, kind, ref):
        self.claims.append((list(ids), kind, ref))
        return {"ok": True}


def hub_steam(hub_id, mid, title, *, age_s=HOUR, pct=40, app=111):
    from test_mail_deals import steam
    m = steam(mid, title, pct, age_s=age_s, app=app)
    m = {**m, "id": hub_id, "from_addr": m["from_address"], "source": "main"}
    m.pop("images", None)
    return m


@pytest.fixture
def hubmail(svc):
    fake = FakeHubMail()
    svc.mail.source = MailSource(svc.notifier, svc.db.get_setting, settings_set=svc.db.set_setting, hub_mail=fake,
                                 clock=lambda: T0, runner=lambda request, timeout: {"ok": False, "error": "own helper must not run"})
    return fake


def test_auto_reads_from_the_hub_registers_the_interest_and_claims(svc, hubmail):
    hubmail.messages_stored = [hub_steam(1, "<a>", "Hades II", app=1145350), hub_steam(2, "<b>", "Celeste", app=504230)]
    first = svc.mail.run()
    assert first["ok"] and first["quiet"] and first["deals_new"] == 2 and first["notified"] == 0
    assert hubmail.interests == [{"from_domains": sorted({d for s in svc.mail.stores() for d in s["domains"]})}]
    assert hubmail.requests[0]["since_id"] == 0 and hubmail.requests[0]["interest"] is True
    assert svc.db.get_setting("mail.hub.since_id") == "2" and svc.db.get_setting("mail.deals.first_scan_done") == "1"
    claimed = {ids[0]: (kind, ref) for ids, kind, ref in hubmail.claims}
    assert set(claimed) == {1, 2} and all(k == "deal" and r.startswith("hoard://tantalus/deal/") for k, r in claimed.values())

    hubmail.messages_stored.append(hub_steam(3, "<c>", "Hollow Knight", pct=60, app=367520))
    second = svc.mail.run()
    assert second["scanned"] == 1 and second["notified"] == 1 and hubmail.requests[1]["since_id"] == 2     # resumes from the watermark
    assert len(hubmail.interests) == 1                                                                      # registered once
    assert svc.store.events(types=["MAIL_DEAL"], limit=5)[0]["title"] == "Hollow Knight"
    third = svc.mail.run()
    assert third["scanned"] == 0 and svc.db.get_setting("mail.hub.since_id") == "3"


def test_hub_mail_older_than_the_window_or_already_seen_is_skipped(svc, hubmail):
    svc.db.set_setting("mail.deals.history_days", "7")
    hubmail.messages_stored = [hub_steam(1, "<old>", "Ancient", age_s=20 * 86400), hub_steam(2, "<new>", "Fresh")]
    out = svc.mail.run()
    assert out["scanned"] == 1 and out["deals_new"] == 1
    svc.mail.repo.mark_seen("<seen>", 1)
    hubmail.messages_stored.append(hub_steam(3, "<seen>", "Known"))
    assert svc.mail.run()["scanned"] == 0


def test_a_hub_failure_falls_back_to_faustus_in_auto_and_is_reported_in_hub_mode(svc, hubmail):
    hubmail.fail = "hub unreachable"
    box_calls = []

    def runner(request, timeout):
        box_calls.append(request["action"])
        return {"ok": True, "error": "", "accounts": [], "messages": []}
    svc.mail.source = MailSource(svc.notifier, svc.db.get_setting, settings_set=svc.db.set_setting, hub_mail=hubmail, runner=runner)
    assert svc.mail.run()["ok"] and box_calls == ["scan"]                      # auto: the own helper took over
    svc.set_settings({"mail.source": "hub"})
    out = svc.mail.run()
    assert not out["ok"] and "unreachable" in out["errors"][0] and box_calls == ["scan"]     # hub only: no fallback
    svc.set_settings({"mail.source": "faustus"})
    hubmail.requests.clear()
    svc.mail.run()
    assert box_calls == ["scan", "scan"] and hubmail.requests == []            # faustus only: the hub is not asked


def test_a_hub_that_is_not_ready_means_faustus_in_auto(svc, hubmail):
    hubmail.up = False
    calls = []
    svc.mail.source = MailSource(svc.notifier, svc.db.get_setting, settings_set=svc.db.set_setting, hub_mail=hubmail,
                                 runner=lambda r, t: calls.append(r["action"]) or {"ok": True, "error": "", "accounts": [], "messages": []})
    svc.mail.run()
    assert calls == ["scan"] and hubmail.requests == []
    assert svc.mail.source.source_status()["effective"] == "faustus"


def test_the_noise_report_always_uses_the_own_helper(svc, hubmail):
    seen = []
    svc.mail.source = MailSource(svc.notifier, svc.db.get_setting, hub_mail=hubmail,
                                 runner=lambda r, t: seen.append(r["action"]) or {"ok": True, "error": "", "accounts": [], "messages": []})
    svc.mail.noise(refresh=True)
    assert seen == ["headers"] and hubmail.requests == []
    assert svc.mail.status()["source"]["noise_report"] == "faustus"


def test_changing_the_shops_registers_the_interest_again(svc, hubmail, monkeypatch):
    svc.mail.refresh_interest()
    svc.mail.refresh_interest(force=False)
    assert len(hubmail.interests) == 1
    svc.set_settings({"mail.deals.stores": "steam"})
    svc.mail.refresh_interest(force=False)
    assert len(hubmail.interests) == 2 and hubmail.interests[1]["from_domains"] == ["steampowered.com"]
    svc.set_settings({"mail.source": "faustus"})
    assert svc.mail.refresh_interest() == {"ok": False, "skipped": "mail.source is faustus"}


def test_full_pages_do_not_end_the_quiet_first_scan(svc, hubmail):
    hubmail.messages_stored = [hub_steam(i, f"<m{i}>", f"Game {i}", app=100 + i) for i in range(1, 4)]
    out = svc.mail.run(limit=3)                       # the page is full: more mail may be waiting
    assert out["quiet"] and svc.db.get_setting("mail.deals.first_scan_done") != "1"
    out2 = svc.mail.run(limit=3)
    assert out2["quiet"] and out2["scanned"] == 0 and svc.db.get_setting("mail.deals.first_scan_done") == "1"
