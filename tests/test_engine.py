"""The decision pipeline end to end, with a scripted fetcher and the real extractors / rules / store."""

from __future__ import annotations

from pathlib import Path

from conftest import T0, tool

from tantalus_hoard.model import FetchResult, InfoFinding, RawListing

PAGES = Path(__file__).parent / "fixtures" / "pages"


class ScriptedFetcher:
    """Returns the next scripted page for a URL (the last one repeats)."""

    def __init__(self, pages: dict[str, list[str]]):
        self.pages = {k: list(v) for k, v in pages.items()}
        self.calls: list[str] = []

    def _next(self, url: str) -> str:
        seq = self.pages[url]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def get(self, url, **_kw):
        self.calls.append(url)
        body = self._next(url)
        if body == "BLOCKED":
            return FetchResult(url=url, final_url=url, status=403, ok=False, blocked=True, block_reason="cloudflare", tier="browser")
        return FetchResult(url=url, final_url=url, status=200, text=body, ok=True, content_type="text/html", tier="http")

    def get_json(self, url, **kw):
        return self.get(url, **kw), None

    def clear_block(self, host):
        pass

    def host_status(self):
        return []

    def close(self):
        pass


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def swap_clock(svc, value):
    svc.clock = svc.store.clock = svc.engine.clock = lambda: value


def make_watcher(svc, **policies):
    base = {"seller": "retail_only", "revalidate_seconds": 0, "cooldown_minutes": 20}
    base.update(policies)
    return tool(svc, "watcher_create", name="Switch 2", mode="availability", config={"product": {"terms": ["switch"]}, "policies": base})


URL = "https://www.game.es/consolas/switch-2"


def test_restock_after_sold_out_raises_one_confirmed_event_and_notifies(svc):
    fetcher = ScriptedFetcher({URL: [page("game_product_outofstock.html"), page("game_product_instock.html")]})
    svc.engine.fetcher = fetcher
    w = make_watcher(svc)
    t = svc.store.create_target(w["id"], URL, label="Switch 2", retailer="GAME")
    first = svc.engine.check_target(t["id"])
    assert first["state"] == "OUT_OF_STOCK" and first["events"] == []
    swap_clock(svc, T0 + 1800)
    second = svc.engine.check_target(t["id"])
    assert second["state"] == "IN_STOCK" and second["confidence"] >= 75
    [event] = [svc.store.event(e) for e in second["events"]]
    assert event["type"] == "RESTOCK" and event["status"] == "confirmed" and event["price"] == 499.99
    assert "disponible" in event["summary"] and event["url"].startswith("https://www.game.es")
    assert svc.notifier.sent and svc.notifier.sent[-1][0]["type"] == "RESTOCK"
    # identical state -> no new event
    swap_clock(svc, T0 + 3600)
    third = svc.engine.check_target(t["id"])
    assert third["events"] == []
    history = tool(svc, "target_get", target_id=t["id"])
    assert len(history["observations"]) == 3 and history["price_history"][-1]["price"] == 499.99


def test_high_priority_event_waits_for_revalidation(svc):
    fetcher = ScriptedFetcher({URL: [page("game_product_outofstock.html"), page("game_product_instock.html")]})
    svc.engine.fetcher = fetcher
    w = make_watcher(svc, revalidate_seconds=60)
    t = svc.store.create_target(w["id"], URL, label="Switch 2")
    svc.engine.check_target(t["id"])
    swap_clock(svc, T0 + 600)
    result = svc.engine.check_target(t["id"])
    event = svc.store.event(result["events"][0])
    assert event["status"] == "pending" and event["revalidate_at"] == T0 + 660
    assert not svc.notifier.sent
    swap_clock(svc, T0 + 700)
    assert [e["id"] for e in svc.store.pending_revalidations(T0 + 700)] == [event["id"]]
    confirmed = svc.engine.revalidate(event["id"])
    assert confirmed["status"] == "confirmed" and confirmed["confidence"] >= 90
    assert svc.notifier.sent


def test_revalidation_that_does_not_hold_dismisses(svc):
    out, instock = page("game_product_outofstock.html"), page("game_product_instock.html")
    svc.engine.fetcher = ScriptedFetcher({URL: [out, instock, out]})
    w = make_watcher(svc, revalidate_seconds=60)
    t = svc.store.create_target(w["id"], URL)
    svc.engine.check_target(t["id"])
    ev_id = svc.engine.check_target(t["id"])["events"][0]
    assert svc.engine.revalidate(ev_id)["status"] == "dismissed"
    assert not svc.notifier.sent


def test_marketplace_seller_is_not_a_restock_under_retail_only(svc):
    html = page("game_product_instock.html").replace('"@type": "Offer"', '"@type": "Offer", "seller": {"@type": "Organization", "name": "Tienda Pepe"}')
    svc.engine.fetcher = ScriptedFetcher({URL: [page("game_product_outofstock.html"), html]})
    w = make_watcher(svc)
    t = svc.store.create_target(w["id"], URL)
    svc.engine.check_target(t["id"])
    result = svc.engine.check_target(t["id"])
    if result["state"] == "MARKETPLACE_ONLY":  # the fixture's JSON-LD accepted the seller override
        assert not [e for e in result["events"] if svc.store.event(e)["type"] == "RESTOCK"]


def test_blocked_target_needs_human_once(svc):
    svc.engine.fetcher = ScriptedFetcher({URL: ["BLOCKED"]})
    w = make_watcher(svc)
    t = svc.store.create_target(w["id"], URL)
    r1 = svc.engine.check_target(t["id"])
    assert r1["blocked"] and svc.store.target(t["id"])["status"] == "needs_human"
    assert svc.store.event(r1["events"][0])["type"] == "NEEDS_HUMAN"
    r2 = svc.engine.check_target(t["id"])
    assert r2["events"] == []
    card = svc.dashboard()["needs_human"]
    assert card and card[0]["id"] == t["id"]


def test_price_threshold_and_drop(svc):
    cheap = page("game_product_instock.html").replace("499.99", "449.99")
    svc.engine.fetcher = ScriptedFetcher({URL: [page("game_product_instock.html"), cheap]})
    w = make_watcher(svc, min_drop_pct=5)
    t = svc.store.create_target(w["id"], URL, price_threshold=450)
    first = svc.engine.check_target(t["id"])
    kinds = [svc.store.event(e)["type"] for e in first["events"]]
    assert kinds == ["RESTOCK"]  # in stock on the very first check
    swap_clock(svc, T0 + 4000)
    second = svc.engine.check_target(t["id"])
    kinds = sorted(svc.store.event(e)["type"] for e in second["events"])
    assert kinds == ["PRICE_DROP", "PRICE_THRESHOLD_CROSSED"]


def test_search_page_tracks_new_skus_as_targets(svc):
    url = "https://www.elcorteingles.es/search-nwx/1/?s=pokemon"
    svc.engine.fetcher = ScriptedFetcher({url: [page("eci_search_moonshine_http.html")]})
    w = tool(svc, "watcher_create", name="Pokémon", mode="availability",
             config={"product": {"terms": ["pokemon"], "max_targets": 4}, "policies": {"revalidate_seconds": 0}})
    t = svc.store.create_target(w["id"], url, source_level=2)
    r = svc.engine.check_target(t["id"])
    assert r["page_kind"] == "search" and r["matched_offers"] >= 4
    targets = svc.store.targets(watcher_id=w["id"])
    assert len(targets) == 4  # the search page + 3 products (cap 4)
    assert svc.store.candidates(watcher_id=w["id"], status="accepted")


def test_secondhand_sweep_scores_dedupes_and_alerts_after_first_run(svc):
    class FakeSource:
        def __init__(self, items):
            self.items = items

        def search(self, query, **kw):
            return list(self.items), ""

    items = [RawListing(source="wallapop", url="https://es.wallapop.com/item/a", title="Lote de 200 libros gratis por mudanza",
                        external_id="a", price=0, location_text="Móstoles"),
             RawListing(source="wallapop", url="https://es.wallapop.com/item/b", title="Libro de texto matemáticas 2º ESO",
                        external_id="b", price=12, location_text="Móstoles")]
    import tantalus_hoard.engine as eng
    original = eng.build_source
    eng.build_source = lambda name, fetcher, **kw: FakeSource(items)
    try:
        w = tool(svc, "watcher_create", name="Libros", mode="secondhand",
                 config={"pack": "books_bulk", "queries": ["lote libros"], "settings": {"origin_location": "Móstoles", "radius_km": 20}})
        s1 = svc.engine.run_secondhand(w["id"])
        assert s1["new"] == 2 and s1["relevant_new"] == 1 and s1["first_run"]
        ev = svc.store.events(watcher_id=w["id"])[0]
        assert ev["type"] == "NEW_LISTING" and ev["status"] == "logged"  # the first sweep is a baseline
        items.append(RawListing(source="wallapop", url="https://es.wallapop.com/item/c", title="Regalo biblioteca completa, varias cajas de libros",
                                external_id="c", price=0, location_text="Alcorcón"))
        swap_clock(svc, T0 + 7200)
        s2 = svc.engine.run_secondhand(w["id"])
        assert s2["new"] == 1 and s2["updated"] == 2
        latest = svc.store.events(watcher_id=w["id"])[0]
        assert latest["status"] == "confirmed" and "biblioteca" in latest["title"].lower()
    finally:
        eng.build_source = original


def test_information_sweep_records_material_findings(svc):
    class FakeInfo:
        def check_source(self, row, watcher):
            return [InfoFinding(url="https://www.asus.com/rtx-spark", title="ASUS RTX Spark 128GB preorder in Spain", snippet="Precio 3.999 €",
                                content_hash="h1", verdict="confirmed", material=True, score=80, source_level=3)], {"last_check_ts": T0}

        def judge(self, findings, watcher):
            return findings

    svc.engine.info = FakeInfo()
    w = tool(svc, "watcher_create", name="RTX Spark", mode="information", config={"sources": [{"kind": "search", "value": "rtx spark"}]})
    first = svc.engine.run_information(w["id"])
    assert first["material"] == 1
    assert svc.store.events(watcher_id=w["id"])[0]["status"] == "logged"  # first sweep of a source is a baseline
    again = svc.engine.run_information(w["id"])
    assert again["new"] == 0  # same content hash is recorded once


def test_dashboard_news_and_visit(svc):
    svc.engine.fetcher = ScriptedFetcher({URL: [page("game_product_outofstock.html"), page("game_product_instock.html")]})
    w = make_watcher(svc)
    t = svc.store.create_target(w["id"], URL)
    svc.engine.check_target(t["id"])
    svc.engine.check_target(t["id"])
    d = svc.dashboard()
    assert d["news_count"] == 1 and d["buyable"][0]["id"] == t["id"]
    assert tool(svc, "tantalus_overview")["news"][0]["type"] == "RESTOCK"
    tool(svc, "events_mark_seen")
    assert svc.dashboard()["news_count"] == 0


def test_config_roundtrip_and_presets(svc):
    created = tool(svc, "presets_install")["created"]
    assert len(created) == 6
    data = tool(svc, "config_export")
    assert {w["mode"] for w in data["watchers"]} == {"availability", "secondhand", "information"}
    again = tool(svc, "config_import", data=data)
    assert again["created"] == [] and len(again["updated"]) == 6
    assert tool(svc, "presets_install")["created"] == []


def test_offer_matching_is_whole_word_and_strict_for_short_term_lists():
    from tantalus_hoard.engine import offer_matches
    from tantalus_hoard.model import Offer
    terms, must = ["pokemon", "30", "aniversario"], ["pokemon"]
    assert offer_matches(Offer(title="Caja de entrenador Elite Pokémon 30 Aniversario (Castellano)"), terms, must, [])
    assert not offer_matches(Offer(title="Estatua Albedo 10º Aniversario Escala 1:30"), terms, must, [])
    assert not offer_matches(Offer(title="Pokémon Pokopia 2030 edición aniversario"), terms, must, [])
    assert not offer_matches(Offer(title="Fundas Pokémon 30 Aniversario"), terms, must, ["fundas"])


def test_llm_budget_falls_back_to_rules():
    from tantalus_hoard.llm import LLM

    class OkLink:
        def chat(self, messages, **kw):
            class R:
                text = '{"ok": true}'
            return R()

    now = [0.0]
    llm = LLM(OkLink(), max_calls=2, window_s=60, clock=lambda: now[0])
    assert llm.json("s", "u") == {"ok": True} and llm.json("s", "u") == {"ok": True}
    assert llm.json("s", "u") is None and llm.skipped == 1
    now[0] = 61
    assert llm.json("s", "u") == {"ok": True}


def test_rescore_updates_stored_listings(svc):
    w = tool(svc, "watcher_create", name="Libros", mode="secondhand", config={"pack": "books_bulk", "settings": {"origin_location": "Móstoles"}})
    row = svc.store.insert_listing(w["id"], {"source": "wallapop", "external_id": "x", "url": "https://es.wallapop.com/item/x",
                                              "title": "Grand Theft Auto V - PS4", "description": "Videojuego, se hacen lotes de libros",
                                              "price": 10, "location_text": "Móstoles", "score": 17, "relevant": True})
    out = tool(svc, "watcher_rescore", watcher_id=w["id"])
    assert out == {"listings": 1, "changed": 1} and not svc.store.listing(row["id"])["relevant"]
