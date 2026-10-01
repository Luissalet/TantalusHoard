"""The aggregator radar: parsers over saved stocktcg.net / stocktcg.es pages, and the cycle end to end with a fake fetcher
(quiet first cycle except a release that is today, chain restocks alert, scalper prices and other editions only log)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from conftest import FakeNotifier, _no_network, make_config, tool
from tantalus_hoard.model import IN_STOCK, OUT_OF_STOCK, PREORDER, FetchResult
from tantalus_hoard.radar import stocktcg as st
from tantalus_hoard.radar.core import Radar, local_today
from tantalus_hoard.services import Services

FIX = Path(__file__).resolve().parent / "fixtures" / "stocktcg"
# 2026-10-02 10:00 in Madrid (CEST) = 08:00 UTC: the release day of "30 Aniversario Oleada 2" in the saved calendar
RELEASE_DAY = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc).timestamp()


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ======================================================================================== parsers
def test_pulse_rows_are_shop_offers_with_product_keys():
    rows = st.parse_pulse(json.loads(read("net_pulse.json")))
    assert rows and all(r.source == st.SOURCE_NET and r.store and r.state in (IN_STOCK, PREORDER) for r in rows)
    etb = [r for r in rows if r.product_key == "30th-anniversary--etb"]
    assert etb and etb[0].fmt == "ETB" and "30th Celebration" in etb[0].set_name and etb[0].seen_ts


def test_product_page_lists_every_shop_with_language_price_and_preorders():
    head, rows = st.parse_product_page(read("net_product_etb.html"), "https://stocktcg.net/p/30th-anniversary--etb")
    assert head["product_key"] == "30th-anniversary--etb" and head["fmt"] == "ETB" and head["stores_in_stock"] == 12
    shops = [r for r in rows if r.kind == "listing"]
    assert len(shops) == 12 and all(r.state == IN_STOCK and r.price and r.url.startswith("http") for r in shops)
    kame = next(r for r in shops if r.store == "Kame House Cards")
    assert kame.price == 119.95 and kame.lang == "ES" and kame.store_slug == "kame-house-cards"
    variant = next(r for r in rows if r.kind == "variant")
    assert variant.state == PREORDER and variant.price == 1500.0
    amazon = next(r for r in rows if r.store_slug == "amazon")
    assert amazon.extra.get("invite") and amazon.url == "https://www.amazon.es/dp/B0H8T5GC4W"  # no affiliate tag


def test_store_page_splits_in_stock_and_recently_sold_out():
    head, rows = st.parse_store_page(read("net_store_carrefour.html"), "carrefour")
    assert head["store"] == "Carrefour" and head["in_stock"] == 13
    buy = [r for r in rows if r.state == IN_STOCK]
    out = [r for r in rows if r.state == OUT_OF_STOCK]
    assert 12 <= len(buy) <= 13 and all(r.url.startswith("https://www.carrefour.es/") for r in buy)
    mini = [r for r in out if r.product_key == "30th-anniversary--mini-tin"]
    assert {r.lang for r in mini} == {"ES", "EN"} and mini[0].price == 16.99 and "Mini Tin" == mini[0].fmt


def test_release_calendar_and_release_page():
    rels = st.parse_releases(read("net_releases.html"))
    oleada2 = next(r for r in rels if r.slug == "30-aniversario-oleada-2")
    assert oleada2.date == "2026-10-02" and oleada2.stores_total == 68 and oleada2.stores_buyable == 18 and oleada2.stores_soldout == 50
    assert oleada2.url == "https://stocktcg.net/lanzamientos/30-aniversario-oleada-2" and oleada2.offers
    page = st.parse_release_page(read("net_release_oleada2.html"), oleada2.url, today=date(2026, 10, 1))
    assert page.date == "2026-10-02" and page.title.startswith("30 Aniversario Oleada 2")
    sold = {s["slug"] for s in page.soldout}
    assert {"carrefour", "game", "alcampo", "amazon", "toys-r-us"} <= sold
    states = {r.state for r in page.offers}
    assert PREORDER in states and OUT_OF_STOCK in states
    assert all(r.release == "30-aniversario-oleada-2" and r.product_key for r in page.offers)


def test_stocktcg_es_feed_and_calendar():
    feed = st.parse_es_feed(read("es_home.html"))
    assert len(feed) > 10 and all(r.source == st.SOURCE_ES and r.store and r.url.startswith("http") for r in feed)
    assert not any("stocktcg.es/B0" in r.url for r in feed)
    rels = st.parse_es_releases(read("es_releases.html"))
    pk = [r for r in rels if r.game == "pokemon" and r.date == "2026-10-02"]
    assert pk and "Mini latas" in pk[0].products


def test_prices_and_dates():
    assert st.parse_price("1.599,00 SEK") == (1599.0, "SEK")
    assert st.parse_price("119,95 €") == (119.95, "EUR")
    assert st.parse_price("") == (None, "EUR")
    assert st.parse_spanish_date("2 de octubre", today=date(2026, 9, 30)) == "2026-10-02"
    assert st.parse_spanish_date("3 de enero", today=date(2026, 12, 20)) == "2027-01-03"
    assert local_today(RELEASE_DAY) == date(2026, 10, 2)


# ======================================================================================== the cycle
class FakeFetcher:
    """Serves the saved pages by URL; anything else (a shop page) is not reachable."""

    def __init__(self, pages: dict[str, str]):
        self.pages = dict(pages)
        self.calls: list[str] = []

    def get(self, url, **_kw):
        self.calls.append(url)
        text = self.pages.get(url)
        if text is None:
            return FetchResult(url=url, status=0, error="not in the test fixtures")
        return FetchResult(url=url, final_url=url, status=200, text=text, ok=True, content_type="text/html")

    def get_json(self, url, **kw):
        fr = self.get(url, **kw)
        return fr, (json.loads(fr.text) if fr.ok else None)


def base_pages() -> dict[str, str]:
    pages = {
        f"{st.NET}/api/pulse.json": read("net_pulse.json"),
        f"{st.NET}/lanzamientos": read("net_releases.html"),
        f"{st.NET}/lanzamientos/30-aniversario-oleada-2": read("net_release_oleada2.html"),
        f"{st.NET}/tiendas/carrefour": read("net_store_carrefour.html"),
        f"{st.NET}/tiendas/game": read("net_store_game.html"),
        f"{st.NET}/p/30th-anniversary--etb": read("net_product_etb.html"),
        f"{st.ES}/": read("es_home.html"),
        f"{st.ES}/lanzamientos": read("es_releases.html"),
    }
    return pages


@pytest.fixture
def radar_svc(tmp_path):
    clock = {"now": RELEASE_DAY}
    s = Services(make_config(tmp_path), link=None, clock_fn=lambda: clock["now"], sleep_fn=lambda _s: None,
                 http_transport=_no_network(), notifier=FakeNotifier(), install_presets=False)
    s.clock_box = clock
    s.radar.fetcher = FakeFetcher(base_pages())
    w = s.store.create_watcher(name="Pokémon 30 aniversario", mode="availability", interval_min=20, config={
        "product": {"terms": ["pokemon", "30", "aniversario"], "must": ["pokemon"], "exclude": ["funda"]},
        "policies": {"seller": "retail_only", "scalper_multiplier": 1.3},
        "radar": {"enabled": True, "chains": ["game", "carrefour", "el-corte-ingles", "alcampo", "amazon", "toys-r-us"],
                  "languages": ["ES", "EN"]}})
    s.watcher = w
    yield s
    s.stop()


def events(svc, type_=None):
    rows = svc.store.events(limit=500)
    return [e for e in rows if type_ is None or e["type"] == type_]


def test_first_cycle_is_quiet_but_announces_todays_release_with_where(radar_svc):
    svc = radar_svc
    result = svc.radar.run()
    summary = result["watchers"][0]
    assert summary["baseline"] and summary["matched"] > 20 and summary["releases"] >= 3
    rel = events(svc, "RELEASE")
    assert len(rel) == 1 and rel[0]["status"] == "confirmed" and rel[0]["severity"] == "high"
    text = rel[0]["summary"]
    assert text.startswith("Hoy (vie 2 oct) sale «30 Aniversario Oleada 2")
    assert "Carrefour agotado" in text and "GAME agotado" in text and "Preventa o stock:" in text
    assert not [e for e in events(svc) if e["type"] in ("RESTOCK", "PREORDER_OPEN")]
    assert svc.notifier.sent and svc.notifier.sent[-1][0]["type"] == "RELEASE"
    # the dashboard and the tools see the release and the chains
    dash = svc.dashboard()
    today = [r for r in dash["releases"] if r["days"] == 0]
    assert today and today[0]["data"]["where"]["chains"]
    chains = {c["slug"]: c for c in dash["chains"]}
    assert "carrefour" in chains and chains["carrefour"]["soldout"]
    listed = tool(svc, "releases_list")
    assert any(r["date"] == "2026-10-02" and r["where"]["shops"] for r in listed["releases"])
    # a second cycle the same day does not repeat the release alert
    svc.clock_box["now"] += 600
    svc.radar.run()
    assert len(events(svc, "RELEASE")) == 1


def test_chain_restock_alerts_and_scalper_shop_only_logs(radar_svc):
    svc = radar_svc
    svc.radar.run()
    # Carrefour now has the 30th anniversary mini tin in stock (it was in "recently sold out")
    page = read("net_store_carrefour.html")
    row = ('<tr><td><div class="st-prod"><span><a href="/p/30th-anniversary--mini-tin">Pokémon 30Th Aniversario Mini Latas, +6 Años</a>'
           '<span class="st-lang">ES</span></span></div></td><td class="st-fmt"><span class="badge">30th Celebration / Celebración 30 Aniversario · '
           'Mini Tin</span></td><td><span class="price">16,99 €</span></td><td class="st-act"><a class="st-go" '
           'href="https://www.carrefour.es/pokemon-30th-aniversario-mini-latas/R-123/p" data-store="carrefour">Comprar</a></td></tr>')
    page = page.replace("<tbody>", "<tbody>" + row, 1)
    svc.radar.fetcher.pages[f"{st.NET}/tiendas/carrefour"] = page
    # and a small shop lists the ETB at three times the chain price
    product = read("net_product_etb.html")
    shop = ('<tr><td><a class="pp-store" href="/tiendas/reventa-shop">Reventa Shop</a></td><td data-l="Producto">ETB 30 Aniversario Español'
            '<span class="pp-lang">ES</span></td><td data-l="Precio"><span class="price">189,00 €</span></td><td class="pp-ppp"></td>'
            '<td class="pp-act"><a class="btn-buy pp-go" href="https://reventa.example/etb30">Comprar</a></td></tr>')
    product = product.replace('<tbody>', '<tbody>' + shop, 1)
    svc.radar.fetcher.pages[f"{st.NET}/p/30th-anniversary--etb"] = product
    svc.db.execute("UPDATE radar_pages SET last_fetch_ts = 0")  # every page is due again
    svc.clock_box["now"] += 600
    svc.radar.run()
    restocks = events(svc, "RESTOCK")
    carrefour = [e for e in restocks if e["data"].get("store_slug") == "carrefour"]
    assert len(carrefour) == 1 and carrefour[0]["status"] == "confirmed" and carrefour[0]["severity"] == "high"
    assert carrefour[0]["url"].startswith("https://www.carrefour.es/") and "Carrefour: en stock" in carrefour[0]["summary"]
    assert "según stocktcg.net" in carrefour[0]["summary"]
    resale = [e for e in restocks if e["data"].get("store_slug") == "reventa-shop"]
    if resale:  # only when a chain price is known for the ETB; then it must stay silent
        assert resale[0]["status"] == "logged"
    offers = tool(svc, "radar_offers", store="carrefour", buyable=True)
    assert any("Mini Latas" in o["title"] for o in offers["offers"])


def test_radar_setup_tool_and_scheduler_registration(radar_svc):
    svc = radar_svc
    w = svc.store.create_watcher(name="Otro", mode="availability", config={"product": {"terms": ["one", "piece"]}})
    out = tool(svc, "radar_setup", watcher_id=w["id"], enabled=True, chains=["GAME", "Carrefour"], languages=["es"])
    assert out["radar"]["enabled"] and out["radar"]["chains"] == ["game", "carrefour"] and out["radar"]["languages"] == ["ES"]
    assert "radar" in svc.scheduler.extra
    assert svc.radar.due(svc.clock()) is True
    status = tool(svc, "radar_status")
    assert {x["name"] for x in status["radar"]["watchers"]} >= {"Otro", "Pokémon 30 aniversario"}


def test_a_page_read_for_the_first_time_after_the_baseline_stays_quiet(radar_svc):
    svc = radar_svc
    svc.radar.fetcher.pages.pop(f"{st.NET}/p/30th-anniversary--etb")
    svc.radar.run()                                   # baseline without the ETB product page
    svc.radar.fetcher.pages[f"{st.NET}/p/30th-anniversary--etb"] = read("net_product_etb.html")
    svc.db.execute("UPDATE radar_pages SET last_fetch_ts = 0")
    svc.clock_box["now"] += 600
    svc.radar.run()                                   # the product page is new: its 12 shops were already there
    assert svc.radar.offers(product_key="30th-anniversary--etb", buyable=True)
    assert not [e for e in events(svc) if e["type"] in ("RESTOCK", "PREORDER_OPEN") and e["data"].get("product_key") == "30th-anniversary--etb"]
