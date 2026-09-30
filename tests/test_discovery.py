from __future__ import annotations

import json

import pytest

from tantalus_hoard import discovery
from tantalus_hoard.discovery import classify_host, discover, product_terms, url_shape
from tantalus_hoard.model import SearchHit


@pytest.fixture(autouse=True)
def no_extract_helpers(monkeypatch):
    monkeypatch.setattr(discovery, "_extract_helpers", lambda: None)


class FakeSearch:
    def __init__(self, hits_by_query: dict[str, list[SearchHit]], errors: dict[str, str] | None = None):
        self.hits_by_query, self.errors, self.calls = hits_by_query, errors or {}, []

    def search(self, query, limit=10, *, freshness_days=None, engines=None):
        self.calls.append((query, limit, freshness_days))
        return list(self.hits_by_query.get(query, [])), dict(self.errors)


def hit(url, title="", snippet="", rank=1, engine="ddg"):
    return SearchHit(url=url, title=title, snippet=snippet, engine=engine, rank=rank)


WATCHER = {"name": "Pokemon 30 aniversario ETB", "config": {"discovery": {"queries": ["site:game.es Pokémon 30 aniversario ETB"]},
                                                          "seller_policy": "retail_only"}}
Q = WATCHER["config"]["discovery"]["queries"][0]


def test_classify_host_table():
    assert classify_host("www.game.es") == (1, "GAME", "retailer")
    assert classify_host("marketplace.nvidia.com") == (1, "NVIDIA Marketplace", "retailer")
    assert classify_host("www.nvidia.com") == (3, "NVIDIA", "official")
    assert classify_host("es-store.msi.com")[2] == "retailer"
    assert classify_host("old.reddit.com") == (4, "Reddit", "community")
    assert classify_host("es.wallapop.com") == (5, "Wallapop", "marketplace")
    assert classify_host("www.idealo.es") == (5, "Idealo", "aggregator")
    assert classify_host("blog.example.org") == (5, "", "other")
    assert classify_host("notgame.es")[2] == "other"  # suffix match must respect the dot


def test_extract_helpers_override(monkeypatch):
    monkeypatch.setattr(discovery, "_extract_helpers", lambda: (lambda host: "Shopz" if host == "shopz.example" else None,
                                                               lambda host: 2 if host == "shopz.example" else None))
    assert classify_host("shopz.example") == (2, "Shopz", "retailer")
    assert classify_host("www.game.es") == (1, "GAME", "retailer")
    monkeypatch.setattr(discovery, "_extract_helpers", lambda: (lambda h: 1 / 0, lambda h: 1 / 0))
    assert classify_host("www.game.es") == (1, "GAME", "retailer")  # a broken helper never breaks discovery


def test_url_shape():
    assert url_shape("https://www.game.es/producto/pokemon-etb-123456") == "product"
    assert url_shape("https://www.amazon.es/dp/B0ABC12345") == "product"
    assert url_shape("https://www.x.es/juguetes/etb-caja-4000123456.html") == "product"
    assert url_shape("https://www.game.es/buscar?q=pokemon") == "listing"
    assert url_shape("https://www.x.es/categoria/cartas") == "listing"
    assert url_shape("https://www.x.es/") == "listing"
    assert url_shape("https://www.x.es/noticias/algo") == "page"


def test_product_terms_from_name_and_queries():
    terms = product_terms(WATCHER)
    assert {"pokemon", "30", "aniversario", "etb"} <= set(terms) and "site" not in terms and "game.es" not in terms
    explicit = {"name": "x", "config": json.dumps({"discovery": {"terms": ["RTX Spark", "128GB"]}})}
    assert product_terms(explicit) == ["rtx", "spark", "128gb"]


def test_scoring_order_and_dedupe_against_existing():
    hits = [
        hit("https://www.game.es/producto/etb-pokemon-30-aniversario-123456", "ETB Pokémon 30 Aniversario", "Reserva ya", 1),
        hit("https://www.game.es/buscar?q=pokemon+30+aniversario", "Resultados Pokémon 30 aniversario ETB", "", 2),
        hit("https://www.pokemon.com/es/tcg/30-aniversario", "Pokémon TCG 30 Aniversario ETB", "", 3),
        hit("https://www.reddit.com/r/PokemonTCG/comments/1/etb_30_aniversario", "ETB 30 aniversario hilo", "", 4),
        hit("https://www.idealo.es/precios/1/pokemon-etb", "Pokémon ETB 30 aniversario", "", 5),
        hit("https://www.game.es/producto/ya-lo-sigo-999999", "Pokémon ETB 30 aniversario", "", 6),
    ]
    cands, errors = discover(WATCHER, FakeSearch({Q: hits}), existing_urls={"https://game.es/producto/ya-lo-sigo-999999/?utm_source=z"})
    assert errors == {}
    assert [c["host"] for c in cands] == ["game.es", "game.es", "pokemon.com", "reddit.com", "idealo.es"]
    assert cands[0]["source_level"] == 1 and cands[1]["source_level"] == 2
    assert cands[2]["source_level"] == 3 and cands[3]["source_level"] == 4 and cands[4]["source_level"] == 5
    assert [c["score"] for c in cands] == sorted((c["score"] for c in cands), reverse=True)
    first = cands[0]
    assert first["retailer"] == "GAME" and first["query"] == Q and first["engine"] == "ddg" and "retailer product page" in first["reason"]
    assert set(first) >= {"url", "host", "title", "snippet", "source_level", "retailer", "engine", "query", "score", "reason"}


def test_marketplaces_dropped_unless_seller_policy_allows():
    hits = [hit("https://es.wallapop.com/item/etb-pokemon-30-aniversario-1", "ETB Pokémon 30 aniversario", "", 1)]
    assert discover(WATCHER, FakeSearch({Q: hits}))[0] == []
    allowed = {"name": WATCHER["name"], "config": {"discovery": WATCHER["config"]["discovery"], "seller_policy": "retail_plus_marketplace"}}
    cands, _ = discover(allowed, FakeSearch({Q: hits}))
    assert len(cands) == 1 and cands[0]["source_level"] == 5 and "marketplace" in cands[0]["reason"]


def test_include_exclude_allowlist_and_relevance():
    cfg = {"discovery": {"queries": ["q"], "include": ["reserva"], "exclude": ["proxy", "custom"], "retailers": ["game.es", "Carrefour"],
                         "terms": ["etb", "aniversario"]}}
    watcher = {"name": "w", "config": cfg}
    hits = [hit("https://www.game.es/producto/etb-aniversario-1", "ETB aniversario", "reserva abierta", 1),
            hit("https://www.game.es/producto/etb-proxy-2", "ETB aniversario proxy", "", 2),          # excluded word
            hit("https://www.fnac.es/producto/etb-aniversario-3", "ETB aniversario", "", 3),          # not allow-listed
            hit("https://www.carrefour.es/p/etb-aniversario-4", "ETB aniversario", "", 4),            # allowed by name
            hit("https://www.game.es/producto/otra-cosa-5", "Mando inalámbrico", "", 5)]             # off topic
    cands, _ = discover(watcher, FakeSearch({"q": hits}))
    assert [c["host"] for c in cands] == ["game.es", "carrefour.es"]
    assert "include: reserva" in cands[0]["reason"]


def test_errors_and_missing_queries_and_engine_agreement():
    cands, errors = discover(WATCHER, FakeSearch({Q: []}, errors={"bing": "blocked: http_429"}))
    assert cands == [] and errors == {"bing": "blocked: http_429"}
    assert discover({"name": "x", "config": {}}, FakeSearch({}))[1] == {"discovery": "no queries configured"}

    single = discover(WATCHER, FakeSearch({Q: [hit("https://www.game.es/producto/etb-pokemon-30-aniversario-1", "ETB Pokémon 30 aniversario", "", 3)]}))[0][0]
    both = discover(WATCHER, FakeSearch({Q: [hit("https://www.game.es/producto/etb-pokemon-30-aniversario-1", "ETB Pokémon 30 aniversario", "", 3, engine="ddg+bing")]}))[0][0]
    assert both["score"] > single["score"]


def test_duplicate_urls_across_queries_keep_best():
    watcher = {"name": "n", "config": {"discovery": {"queries": ["a", "b"], "terms": ["etb"]}}}
    url = "https://www.game.es/producto/etb-1"
    search = FakeSearch({"a": [hit(url + "?utm_source=1", "ETB", "", 9)], "b": [hit(url, "ETB", "", 1)]})
    cands, _ = discover(watcher, search)
    assert len(cands) == 1 and cands[0]["query"] == "b"


def test_llm_refines_middle_band_and_falls_back():
    class FakeLLM:
        def __init__(self, reply):
            self.reply, self.prompts = reply, []

        def json(self, system, user, **kw):
            self.prompts.append(user)
            return self.reply

    hits = [hit("https://blog.example.org/noticias/etb-pokemon-30-aniversario", "ETB Pokémon 30 aniversario", "", 4)]
    base = discover(WATCHER, FakeSearch({Q: hits}))[0][0]["score"]
    llm = FakeLLM({"relevant": False, "reason": "blog"})
    cands, _ = discover(WATCHER, FakeSearch({Q: hits}), llm=llm)
    assert 35 <= base <= 65
    assert cands[0]["score"] == round(base - 25, 1) and "<page>" in llm.prompts[0] and "model: not relevant" in cands[0]["reason"]
    assert discover(WATCHER, FakeSearch({Q: hits}), llm=FakeLLM(None))[0][0]["score"] == base


def test_weak_term_overlap_is_dropped():
    # 4 product terms: a page matching only "pokemon" is not a candidate
    weak = [hit("https://www.pokemon.com/es", "Pokémon oficial", "Noticias", 1)]
    assert discover(WATCHER, FakeSearch({Q: weak}))[0] == []
