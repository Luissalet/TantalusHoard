"""Wallapop parsing (from a real response captured 2026-09-30) and the source with a fake fetcher."""

import json
from pathlib import Path

import pytest

from tantalus_hoard.errors import TantalusError
from tantalus_hoard.model import FetchResult
from tantalus_hoard.secondhand import SOURCES, build_source, score_listing, PACKS
from tantalus_hoard.secondhand.wallapop import (HEADERS, SEARCH_URL, WallapopSource, extract_items, next_page_token,
                                                parse_item, parse_search)

FIXTURES = Path(__file__).parent / "fixtures"
DATA = json.loads((FIXTURES / "wallapop_search.json").read_text(encoding="utf-8"))
ETB = json.loads((FIXTURES / "wallapop_search_etb.json").read_text(encoding="utf-8"))
ORIGIN = (40.3223, -3.8649)  # Móstoles


class FakeFetcher:
    """Duck-typed fetcher: answers get_json from a queue of (FetchResult, data) or exceptions."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ok(data, status=200):
    return FetchResult(url=SEARCH_URL, status=status, ok=True, tier="api"), data


def failed(**kw):
    return FetchResult(url=SEARCH_URL, ok=False, **kw), None


# ----------------------------------------------------------------------------- fixture parsing
def test_fixture_is_a_real_full_page():
    assert 30 <= len(extract_items(DATA)) <= 40
    assert next_page_token(DATA)


def test_parse_search_reads_every_item_of_the_fixture():
    listings = parse_search(DATA, query="libros lote", origin=ORIGIN)
    assert len(listings) == len(extract_items(DATA))
    for item in listings:
        assert item.source == "wallapop" and item.external_id and item.title
        assert item.url.startswith("https://es.wallapop.com/item/")
        assert item.currency == "EUR" and item.price is not None and item.price >= 0
        assert item.matched_query == "libros lote" and item.surface == "marketplace"
        assert item.latitude is not None and item.distance_km is not None and item.distance_km < 40  # radius filter was 30 km
        assert item.listing_date and item.listing_date.startswith("2026-")
        assert item.image_url and item.image_url.startswith("https://cdn.wallapop.com/")
        assert item.shipping in (True, False) and item.reserved in (True, False)


def test_parse_item_maps_the_documented_fields():
    raw = extract_items(DATA)[0]
    item = parse_item(raw, query="q", origin=ORIGIN)
    assert item.external_id == raw["id"] and item.url.endswith(raw["web_slug"])
    assert item.price == raw["price"]["amount"]
    assert item.image_url == raw["images"][0]["urls"]["big"]
    assert item.location_text.split(",")[0] == raw["location"]["city"]
    assert item.shipping is raw["shipping"]["item_is_shippable"]
    assert item.extra["user_id"] == raw["user_id"] and item.extra["category_id"] == raw["category_id"]


def test_lote_titles_from_the_fixture_score_as_book_lots():
    listings = parse_search(DATA, origin=ORIGIN)
    lote = next(i for i in listings if i.title == "Lote 2 libros")
    assert lote.location_text.startswith("Móstoles") and 0 <= lote.distance_km < 8
    result = score_listing(lote, PACKS["books_bulk"], {"origin_location": "Móstoles, Madrid", "radius_km": 30})
    assert result.relevant and "lot" in {s.key for s in result.signals}


def test_etb_fixture_parses_and_accessories_do_not_pass_the_sealed_pack():
    listings = parse_search(ETB, origin=ORIGIN)
    assert len(listings) == len(extract_items(ETB)) >= 4
    acrylic = next(i for i in listings if i.title.lower().startswith("acrilico"))
    result = score_listing(acrylic, PACKS["collectibles_sealed"], {"msrp": 54.99})
    assert result.relevant is False


def test_parser_is_defensive():
    assert parse_item({}) is None
    assert parse_item({"id": "x"}) is None                       # no title
    assert parse_item({"title": "t"}) is None                    # no id
    minimal = parse_item({"id": "a1", "title": "Algo"})
    assert minimal.url == "https://es.wallapop.com/item/a1" and minimal.price is None and minimal.shipping is None
    assert minimal.reserved is None and minimal.image_url is None and minimal.location_text is None
    odd = parse_item({"id": 7, "title": "T", "price": 12.5, "reserved": True, "shipping": {"user_allows_shipping": False},
                      "images": ["https://img/1.jpg"], "location": {"city": "Madrid", "region2": "Madrid"},
                      "created_at": 1790772546}, origin=ORIGIN)
    assert odd.external_id == "7" and odd.price == 12.5 and odd.reserved is True and odd.shipping is False
    assert odd.image_url == "https://img/1.jpg" and odd.location_text == "Madrid" and odd.listing_date.startswith("2026")
    assert extract_items(None) == [] and extract_items({"data": {"section": {"payload": {}}}}) == []
    assert extract_items({"search_objects": [{"id": "1"}, "junk"]}) == [{"id": "1"}]
    assert parse_search({"data": {"section": {"payload": {"items": [{"id": "1", "title": "ok"}, "junk", {"id": 2}]}}}})[0].title == "ok"


def test_free_item_has_price_zero():
    item = parse_item({"id": "f1", "title": "REGALO GRATIS libros", "price": {"amount": 0.0, "currency": "EUR"}})
    assert item.price == 0.0


# ----------------------------------------------------------------------------- source behaviour
def test_search_sends_headers_and_params_as_verified_live():
    fetcher = FakeFetcher(ok(DATA))
    listings, error = WallapopSource(fetcher).search("lote libros", latitude=40.3223, longitude=-3.8649, radius_km=30,
                                                     max_price=10, min_price=0, limit=40)
    url, kwargs = fetcher.calls[0]
    assert url == SEARCH_URL and error == "" and len(listings) >= 30
    assert kwargs["headers"] == HEADERS and kwargs["headers"]["X-DeviceOS"] == "0"
    assert kwargs["respect_robots"] is False and kwargs["accept"] == "json" and kwargs["min_interval_s"] == 5.0
    p = kwargs["params"]
    assert p["keywords"] == "lote libros" and p["latitude"] == 40.3223 and p["longitude"] == -3.8649
    assert p["distance_in_km"] == 30 and "distance" not in p  # the metres parameter is ignored by the API
    assert p["max_sale_price"] == 10 and p["min_sale_price"] == 0 and p["order_by"] == "newest" and p["source"] == "search_box"


def test_search_without_coordinates_omits_location_params():
    params = WallapopSource.build_params("x", latitude=None, longitude=None, radius_km=30, max_price=None, min_price=None,
                                         order_by="bogus")
    assert params == {"keywords": "x", "source": "search_box", "order_by": "newest"}


def test_limit_truncates_and_single_page_is_enough_for_40():
    fetcher = FakeFetcher(ok(DATA))
    listings, _ = WallapopSource(fetcher).search("libros", latitude=1, longitude=1, limit=5)
    assert len(listings) == 5 and len(fetcher.calls) == 1


def test_pagination_follows_next_page_and_stops_on_an_empty_page():
    page = lambda ids, tok: {"data": {"section": {"payload": {"items": [{"id": i, "title": f"t{i}"} for i in ids]}}},
                             "meta": {"next_page": tok}}
    fetcher = FakeFetcher(ok(page("ab", "TOK1")), ok(page("cd", "TOK2")), ok(page("", "TOK3")))
    listings, error = WallapopSource(fetcher, max_pages=5).search("x", latitude=1, longitude=1, limit=100)
    assert [i.external_id for i in listings] == ["a", "b", "c", "d"] and error == ""
    assert "next_page" not in fetcher.calls[0][1]["params"]
    assert fetcher.calls[1][1]["params"]["next_page"] == "TOK1" and fetcher.calls[2][1]["params"]["next_page"] == "TOK2"


def test_pagination_dedupes_repeated_items_and_respects_max_pages():
    page = {"data": {"section": {"payload": {"items": [{"id": "a", "title": "t"}]}}}, "meta": {"next_page": "T"}}
    fetcher = FakeFetcher(ok(page), ok(page), ok(page))
    listings, _ = WallapopSource(fetcher, max_pages=2).search("x", latitude=1, longitude=1, limit=100)
    assert len(listings) == 1 and len(fetcher.calls) == 2


def test_default_pages_follow_the_limit():
    page = lambda n: ok({"data": {"section": {"payload": {"items": [{"id": f"{n}-{i}", "title": "t"} for i in range(40)]}}},
                         "meta": {"next_page": "T"}})
    fetcher = FakeFetcher(page(1), page(2), page(3))
    listings, _ = WallapopSource(fetcher).search("x", latitude=1, longitude=1, limit=80)
    assert len(listings) == 80 and len(fetcher.calls) == 2


def test_blocked_and_failed_answers_become_error_strings_not_exceptions():
    listings, error = WallapopSource(FakeFetcher(failed(blocked=True, block_reason="http_403", status=403))).search("x", latitude=1, longitude=1)
    assert listings == [] and "blocked" in error and "http_403" in error
    listings, error = WallapopSource(FakeFetcher(failed(error="timeout"))).search("x")
    assert listings == [] and "fetch_failed" in error and "timeout" in error
    listings, error = WallapopSource(FakeFetcher(TantalusError("offline", "sin red"))).search("x")
    assert listings == [] and "offline" in error
    listings, error = WallapopSource(FakeFetcher(RuntimeError("boom"))).search("x")
    assert listings == [] and "boom" in error
    assert WallapopSource(FakeFetcher()).search("   ") == ([], "wallapop: empty query")


def test_second_page_failure_keeps_the_first_page():
    fetcher = FakeFetcher(ok(DATA), failed(error="429", blocked=True, block_reason="http_429"))
    listings, error = WallapopSource(fetcher, max_pages=2).search("x", latitude=1, longitude=1, limit=100)
    assert len(listings) >= 30 and "http_429" in error


def test_registry_builds_sources():
    assert set(SOURCES) == {"wallapop", "facebook"}
    source = build_source("wallapop", FakeFetcher(), max_pages=1)
    assert isinstance(source, WallapopSource) and source.max_pages == 1
    with pytest.raises(KeyError):
        build_source("milanuncios", FakeFetcher())
