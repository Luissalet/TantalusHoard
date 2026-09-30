"""Scoring: Radar de Libros behaviour (books_bulk), the generic pack, collectibles_sealed and the model refinement."""

import copy
from datetime import datetime, timedelta, timezone

import pytest

from tantalus_hoard.model import ListingScore, RawListing
from tantalus_hoard.secondhand import PACKS, get_pack, score_listing
from tantalus_hoard.secondhand.classifier import is_borderline
from tantalus_hoard.secondhand.packs import BOOKS_BULK
from tantalus_hoard.secondhand.scoring import resolve_config, score_rules

BOOKS = PACKS["books_bulk"]
GENERIC = PACKS["generic"]
SEALED = PACKS["collectibles_sealed"]
ORIGIN = {"origin_location": "Móstoles, Madrid"}


def listing(title, price_raw=None, description="", location="Móstoles, Madrid", price=None, **extra):
    return RawListing(source="facebook", url="https://fb/1", title=title, description=description or None,
                      price=price, price_raw=price_raw, location_text=location, **extra)


def books(title, price_raw, description="", location="Móstoles, Madrid", **settings):
    return score_listing(listing(title, price_raw, description, location), BOOKS, {**ORIGIN, **settings})


def keys(result):
    return {s.key for s in result.signals}


# ----------------------------------------------------------------------------- books_bulk: false positives
@pytest.mark.parametrize("title,price", [
    ("Envío gratis comprando dos libros", "8 €"),
    ("Libro gratis con suscripción", "Gratis"),
    ("Vendo libros, regalo marcapáginas", "5 €"),
    ("Descarga gratis", "Gratis"),
    ("Curso con libro gratis", "Gratis"),
])
def test_false_positives_are_not_relevant(title, price):
    assert books(title, price).relevant is False


def test_free_ebook_is_not_relevant():
    # unknown location on purpose: isolates the ebook signal from an incidental proximity bonus
    assert books("Libro electrónico gratis", "Gratis", location=None).relevant is False


def test_hard_rejects_explain_themselves_and_score_zero():
    result = books("Libro gratis con suscripción", "Gratis")
    assert result.score == 0 and result.category == "false_positive"
    assert result.signals[0].key == "hard_reject" and "suscripción" in result.reason


def test_hard_reject_yields_to_a_physical_bulk_signal():
    result = books("Regalo lote de libros, descarga gratis el índice", "Gratis")
    assert "hard_reject" not in keys(result) and result.relevant is True


# ----------------------------------------------------------------------------- books_bulk: interesting ads
@pytest.mark.parametrize("title,price", [
    ("Regalo unos 80 libros por mudanza", "Gratis"),
    ("Caja de libros gratis, recoger hoy", "Gratis"),
    ("Me deshago de biblioteca completa", None),
    ("Regalo colección de novelas", "Gratis"),
    ("Lote de 50 libros a 5 €", "5 €"),
])
def test_interesting_ads_are_relevant(title, price):
    result = books(title, price)
    assert result.relevant is True and result.score > 0 and result.category == "bulk_free_books"


def test_free_complete_library_scores_very_high_with_explainable_signals():
    result = books("Regalo biblioteca completa, nos mudamos y no caben", "Gratis")
    assert result.score >= 20
    assert {"free_or_zero_price", "complete_library", "moving", "very_close_location"} <= keys(result)
    assert all(s.label for s in result.signals)
    assert result.quantity_min and result.quantity_max
    assert result.signals == sorted(result.signals, key=lambda s: s.points, reverse=True)


def test_cheap_bulk_lot_not_penalised_for_price_and_has_price_per_book():
    result = books("Lote de 70 libros por 10 euros, todos juntos", "10 €", location=None)
    assert "high_price" not in keys(result) and "lot" in keys(result) and result.relevant


def test_expensive_small_lot_is_penalised():
    result = books("2 libros antiguos de coleccionista, pieza única", "25 €", location=None)
    assert "high_price" in keys(result)


def test_digital_ebook_offsets_free_signal():
    assert books("Libro electrónico gratis, descarga en pdf", "Gratis", location=None).score <= 0


def test_individual_normal_sale_is_not_boosted():
    result = books("Vendo libro de Harry Potter en buen estado", "8 €", location=None)
    assert result.score <= 0 and "individual_sale" in keys(result)


def test_weights_are_configurable_and_far_away_gets_no_bonus():
    near = books("Regalo lote de libros", "Gratis")
    far = books("Regalo lote de libros", "Gratis", location="Barcelona")
    assert near.score > far.score and far.distance_km and far.distance_km > 400
    boosted = books("Regalo libros", "Gratis", location=None, weights={"free_or_zero_price": 1000})
    assert boosted.score >= 1000


def test_llm_disabled_still_scores_and_method_is_rules():
    result = books("Regalo lote de libros por mudanza", "Gratis")
    assert result.method == "rules" and result.relevant


def test_distance_known_and_unknown():
    assert books("Regalo libros", "Gratis", location="Móstoles, Madrid").distance_km == 0.0
    assert books("Regalo libros", "Gratis", location="Un lugar inventado que no existe").distance_km is None


def test_active_municipality_boosts_score_and_inactive_does_not():
    base = books("Regalo lote de libros", "Gratis", location="Getafe, Madrid")
    boosted = books("Regalo lote de libros", "Gratis", location="Getafe, Madrid", municipalities=["Getafe"])
    other = books("Regalo lote de libros", "Gratis", location="Getafe, Madrid", municipalities=["Parla", "Pinto"])
    assert boosted.score > base.score and "in_active_municipality" in keys(boosted)
    assert other.score == base.score


def test_radius_extends_the_proximity_reach():
    default = books("Regalo lote de libros", "Gratis", location="Alcalá de Henares")
    wide = books("Regalo lote de libros", "Gratis", location="Alcalá de Henares", radius_km=100)
    assert "very_close_location" not in keys(default) and "very_close_location" in keys(wide)
    assert wide.score > default.score


def test_books_pack_keeps_radar_weights_exactly():
    w = BOOKS_BULK["weights"]
    assert w["free_or_zero_price"] == 10 and w["getting_rid_of_it"] == 8 and w["complete_library"] == 7
    assert (w["lot"], w["collection"], w["boxes"], w["moving"], w["many_books_mentioned"]) == (6, 6, 5, 5, 5)
    assert (w["high_quantity_detected"], w["very_close_location"], w["in_active_municipality"]) == (4, 3, 4)
    assert (w["free_shipping"], w["digital_or_ebook"], w["free_download"], w["textbook"], w["encyclopedia"]) == (-10, -10, -8, -7, -5)
    assert (w["individual_sale"], w["high_price"]) == (-5, -5)
    assert BOOKS_BULK["alert_min_score"] == 8
    assert BOOKS_BULK["default_queries"][:5] == ["lote libros", "libros gratis", "biblioteca completa", "regalo libros", "vaciado piso libros"]


def test_wallapop_style_numeric_price_zero_counts_as_free():
    item = RawListing(source="wallapop", url="u", title="REGALO GRATIS libros de cocina", price=0.0, location_text="Fuenlabrada, Madrid")
    result = score_listing(item, BOOKS, ORIGIN)
    assert "free_or_zero_price" in keys(result) and result.relevant


# ----------------------------------------------------------------------------- packs as data
def test_packs_have_the_required_shape_and_are_json_serialisable():
    import json
    for pack_id, pack in PACKS.items():
        for field in ("id", "name_es", "name_en", "description", "default_queries", "weights", "rules", "alert_min_score"):
            assert field in pack, (pack_id, field)
        assert pack["id"] == pack_id
        json.dumps(pack)
    assert len(GENERIC["default_queries"]) == 0 and len(SEALED["default_queries"]) >= 3


def test_get_pack_returns_an_independent_copy():
    pack = get_pack("generic")
    pack["weights"]["reserved"] = 0
    assert PACKS["generic"]["weights"]["reserved"] != 0
    assert get_pack("nope") is None


def test_resolve_config_applies_settings_overrides():
    cfg = resolve_config(GENERIC, {"msrp": "60", "max_price": 70, "include_any": ["etb"], "weights": {"reserved": -1},
                                   "rules": {"shipping": "required"}, "alert_min_score": 3})
    assert cfg["rules"]["msrp"] == "60" and cfg["rules"]["price_ceiling"] == 70
    assert cfg["rules"]["include_any"] == ["etb"] and cfg["rules"]["shipping"] == "required"
    assert cfg["weights"]["reserved"] == -1 and cfg["alert_min_score"] == 3.0
    assert GENERIC["rules"]["include_any"] == []  # the pack itself is untouched


# ----------------------------------------------------------------------------- generic pack
def g(title, settings=None, **kw):
    item = RawListing(source="wallapop", url="u", title=title, **kw)
    return score_listing(item, GENERIC, settings or {})


def test_generic_include_any_and_title_bonus():
    result = g("Nintendo Switch OLED como nueva", {"include_any": ["switch", "ps5"]}, price=200.0)
    assert result.relevant and {"include_any_match", "title_match"} <= keys(result)
    miss = g("Bicicleta de montaña", {"include_any": ["switch", "ps5"]}, price=200.0)
    assert miss.relevant is False and miss.category == "rejected" and "palabras clave" in miss.reason


def test_generic_include_all_requires_every_keyword():
    ok = g("Lego Star Wars Halcón Milenio sellado", {"include_all": ["lego", "halcon milenio"]}, price=100.0)
    assert ok.relevant and "include_all_match" in keys(ok)
    missing = g("Lego Star Wars X-Wing", {"include_all": ["lego", "halcon milenio"]}, price=100.0)
    assert missing.relevant is False and "obligatoria" in missing.reason


def test_generic_exclude_words_reject():
    result = g("Nintendo Switch para piezas, no funciona", {"include_any": ["switch"], "exclude": ["no funciona"]})
    assert result.relevant is False and result.signals[0].key == "hard_reject"


def test_generic_terms_match_whole_words_with_optional_plural():
    assert g("Caja de sobres pokemon", {"include_any": ["sobre"]}).relevant is True
    assert g("Sobrenatural", {"include_any": ["sobre"]}).relevant is False


def test_generic_price_ceiling_penalises():
    cheap = g("Switch OLED", {"include_any": ["switch"], "price_ceiling": 200}, price=180.0)
    dear = g("Switch OLED", {"include_any": ["switch"], "price_ceiling": 200}, price=260.0)
    assert "over_ceiling" not in keys(cheap) and cheap.relevant
    assert "over_ceiling" in keys(dear) and dear.relevant is False


def test_generic_msrp_bonus_scales_and_scalper_is_a_strong_negative():
    base = {"include_any": ["switch"], "msrp": 300}
    below = g("Switch OLED", base, price=240.0)          # 20 % under
    assert "below_msrp" in keys(below) and "20 %" in next(s.label for s in below.signals if s.key == "below_msrp")
    above = g("Switch OLED", base, price=330.0)
    assert "above_msrp" in keys(above) and "scalper" not in keys(above)
    scalper = g("Switch OLED", base, price=500.0)
    assert "scalper" in keys(scalper) and scalper.relevant is False and scalper.category == "scalper"
    custom = g("Switch OLED", {**base, "scalper_multiplier": 2.0}, price=500.0)
    assert "scalper" not in keys(custom)


def test_generic_suspiciously_cheap_is_flagged():
    result = g("Switch OLED", {"include_any": ["switch"], "msrp": 300}, price=60.0)
    assert "suspiciously_cheap" in keys(result)


def test_generic_shipping_modes():
    required = {"include_any": ["switch"], "shipping": "required"}
    assert "no_shipping" in keys(g("Switch", required, shipping=False))
    assert "no_shipping" not in keys(g("Switch", required, shipping=True))
    assert "no_shipping" not in keys(g("Switch", required))  # unknown: nothing invented
    forbidden = {"include_any": ["switch"], "shipping": "forbidden"}
    assert "shipping_offered" in keys(g("Switch", forbidden, shipping=True))


def test_generic_reserved_is_penalised():
    assert "reserved" in keys(g("Switch", {"include_any": ["switch"]}, reserved=True))
    assert "reserved" not in keys(g("Switch", {"include_any": ["switch"]}, reserved=False))


def test_generic_recency_bonus_uses_injected_clock():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    def iso(hours): return (now - timedelta(hours=hours)).isoformat()
    s = {"include_any": ["switch"], "now": now.timestamp()}
    fresh = g("Switch", s, listing_date=iso(1))
    today = g("Switch", s, listing_date=iso(10))
    old = g("Switch", s, listing_date=iso(72))
    assert "recent" in keys(fresh) and "recent" in keys(today) and "recent" not in keys(old)
    assert fresh.score > today.score > old.score


def test_generic_max_distance_marks_too_far():
    item = dict(location_text="Barcelona")
    far = g("Switch", {"include_any": ["switch"], "origin_location": "Madrid", "max_distance_km": 100}, **item)
    assert "too_far" in keys(far) and far.distance_km > 400
    near = g("Switch", {"include_any": ["switch"], "origin_location": "Madrid", "max_distance_km": 700}, **item)
    assert "too_far" not in keys(near)


def test_generic_uses_listing_gps_against_origin_coordinates():
    result = g("Switch", {"include_any": ["switch"], "latitude": 40.3223, "longitude": -3.8649, "radius_km": 30},
               latitude=40.3459, longitude=-3.8274)
    assert result.distance_km is not None and result.distance_km < 6 and "very_close_location" in keys(result)


# ----------------------------------------------------------------------------- collectibles_sealed
def sealed(title, price=None, description="", settings=None, **kw):
    item = RawListing(source="wallapop", url="u", title=title, description=description or None, price=price, **kw)
    return score_listing(item, SEALED, {"msrp": 54.99, **(settings or {})})


def test_sealed_etb_at_a_fair_price_is_relevant_and_explained():
    result = sealed("Pokémon Elite Trainer Box 30 Aniversario precintada", price=50.0)
    assert result.relevant and {"include_any_match", "sealed", "below_msrp"} <= keys(result)
    assert result.score >= SEALED["alert_min_score"]
    assert "precintado" in next(s.label for s in result.signals if s.key == "sealed")


def test_sealed_bonus_words():
    assert "sealed" in keys(sealed("ETB Ascended Heroes sellado", price=50.0))
    assert "sealed" in keys(sealed("ETB Ascended Heroes sin abrir", price=50.0))
    assert "new_item" in keys(sealed("ETB Ascended Heroes nuevo", price=50.0))
    assert "sealed" in keys(sealed("ETB Ascended Heroes nunca abierto", price=50.0))


@pytest.mark.parametrize("title,expected", [
    ("Caja vacía ETB Pokémon", "empty_box"),
    ("ETB Pokémon solo caja", "empty_box"),
    ("ETB Pokémon sin sobres", "empty_box"),
    ("ETB Pokémon proxy custom", "proxy_or_fake"),
    ("ETB Pokémon réplica", "proxy_or_fake"),
    ("ETB Pokémon fake", "proxy_or_fake"),
    ("ETB Pokémon abierto", "opened"),
    ("Acrílico protector para ETB Pokémon", "accessory"),
    ("Cartas sueltas de ETB Pokémon", "single_cards"),
])
def test_sealed_negatives_make_it_not_relevant(title, expected):
    result = sealed(title, price=50.0)
    assert expected in keys(result) and result.relevant is False, result.signals


def test_sealed_not_opened_is_not_treated_as_opened():
    for title in ("ETB Pokémon no abierto", "ETB Pokémon nunca abierto", "ETB Pokémon sin abrir"):
        assert "opened" not in keys(sealed(title, price=50.0))


def test_sealed_wanted_ads_and_trades():
    assert sealed("Busco ETB Pokémon 30 aniversario", price=50.0).relevant is False
    assert "trade" in keys(sealed("Cambio ETB 30 Aniversario Español por Inglés", price=50.0))


def test_sealed_scalper_default_multiplier_is_1_3():
    assert SEALED["rules"]["scalper_multiplier"] == 1.3
    ok = sealed("ETB 30 Aniversario precintada", price=70.0)     # 54.99 * 1.3 = 71.5
    scalper = sealed("ETB 30 Aniversario precintada", price=72.0)
    assert "scalper" not in keys(ok) and "above_msrp" in keys(ok)
    assert "scalper" in keys(scalper) and scalper.relevant is False and scalper.category == "scalper"
    assert "scalper" not in keys(sealed("ETB precintada", price=72.0, settings={"scalper_multiplier": 1.5}))


def test_sealed_suspiciously_cheap_is_flagged():
    assert "suspiciously_cheap" in keys(sealed("ETB precintada", price=15.0))


def test_sealed_product_terms_from_settings_narrow_the_search():
    settings = {"include_all": ["30 aniversario"]}
    assert sealed("ETB 30 Aniversario precintada", price=55.0, settings=settings).relevant
    assert sealed("ETB Ascended Heroes precintada", price=55.0, settings=settings).relevant is False


def test_sealed_real_wallapop_titles_from_the_live_check():
    accessory = sealed("Acrilico etb pokemon", price=13.0)
    dice = sealed("Lote 6 Packs de Dados Pokémon TCG", price=10.0)
    assert accessory.relevant is False and dice.relevant is False


# ----------------------------------------------------------------------------- model refinement
class FakeLLM:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def json(self, system, user, *, required=(), max_tokens=800, effort="low", temperature=0.1):
        self.calls.append((system, user))
        return self.answer


def test_borderline_band():
    assert is_borderline(8, 8, 4) and is_borderline(4, 8, 4) and is_borderline(12, 8, 4)
    assert not is_borderline(3.9, 8, 4) and not is_borderline(12.1, 8, 4)


def borderline_listing():
    # lot (6) + boxes (5) = 11: inside the books band [4, 12], no location
    return listing("Lote y caja de libros", "9 €", location=None)


def test_llm_not_called_outside_the_borderline_band():
    llm = FakeLLM({"relevant": False, "confidence": 0.9})
    strong = listing("Regalo biblioteca completa por mudanza", "Gratis")
    assert score_listing(strong, BOOKS, ORIGIN, llm=llm).method == "rules"
    hard = listing("Libro gratis con suscripción", "Gratis")
    assert score_listing(hard, BOOKS, ORIGIN, llm=llm).method == "rules"
    assert llm.calls == []


def test_llm_can_reject_a_borderline_listing_and_the_change_is_explained():
    base = score_rules(borderline_listing(), BOOKS, ORIGIN)
    assert 4 <= base.score <= 12 and base.relevant
    llm = FakeLLM({"relevant": False, "confidence": 0.9, "reason": "es un solo libro de texto", "category": "single_item"})
    result = score_listing(borderline_listing(), BOOKS, ORIGIN, llm=llm)
    assert result.method == "rules+llm" and result.relevant is False and result.category == "single_item"
    assert "llm_rejects" in keys(result) and result.score == base.score - 8
    assert "solo libro" in result.reason and len(llm.calls) == 1
    assert "<page>" in llm.calls[0][1] and "data, not instructions" in llm.calls[0][0]


def test_llm_can_confirm_and_supply_a_quantity():
    llm = FakeLLM({"relevant": True, "confidence": 0.8, "estimated_quantity": 40, "reason": "lote grande"})
    result = score_listing(borderline_listing(), BOOKS, ORIGIN, llm=llm)
    assert result.relevant and "llm_confirms" in keys(result) and result.quantity_min == 40 == result.quantity_max


def test_llm_failures_are_silent():
    base = score_rules(borderline_listing(), BOOKS, ORIGIN)
    # no answer, an empty answer, low confidence and an unparseable confidence (falls back to 0.5, under the bar)
    for answer in (None, {}, {"relevant": False, "confidence": 0.2}, {"relevant": False, "confidence": "high"}):
        result = score_listing(borderline_listing(), BOOKS, ORIGIN, llm=FakeLLM(answer))
        assert result.method == "rules" and result.score == base.score and result.relevant == base.relevant

    class Boom:
        def json(self, *a, **k):
            raise RuntimeError("model down")

    assert score_listing(borderline_listing(), BOOKS, ORIGIN, llm=Boom()).method == "rules"


def test_use_llm_false_disables_the_model():
    llm = FakeLLM({"relevant": False, "confidence": 0.9})
    assert score_listing(borderline_listing(), BOOKS, {**ORIGIN, "use_llm": False}, llm=llm).method == "rules"
    assert llm.calls == []


def test_score_rules_is_deterministic_and_does_not_mutate_the_pack():
    before = copy.deepcopy(SEALED)
    a = sealed("ETB precintada", price=50.0)
    b = sealed("ETB precintada", price=50.0)
    assert a == b and SEALED == before
    assert isinstance(a, ListingScore)


def test_books_pack_rejects_video_games_and_needs_a_book_word():
    from tantalus_hoard.model import RawListing
    from tantalus_hoard.secondhand import get_pack, score_listing
    pack = get_pack("books_bulk")
    game = RawListing(source="wallapop", url="u1", title="Grand Theft Auto V (GTA 5) - PS4", price=10, location_text="Móstoles",
                      description="Videojuego para PlayStation 4. Caja sin portada ni libro. Se hacen lotes con otros productos")
    assert not score_listing(game, pack, {"origin_location": "Móstoles"}).relevant
    sofa = RawListing(source="wallapop", url="u2", title="Sofá gratis por mudanza", price=0, location_text="Móstoles",
                      description="Lo regalo, hay que recogerlo")
    assert not score_listing(sofa, pack, {"origin_location": "Móstoles"}).relevant
    library = RawListing(source="wallapop", url="u3", title="Regalo biblioteca completa", price=0, location_text="Móstoles",
                         description="Vaciado de piso, cientos de libros")
    assert score_listing(library, pack, {"origin_location": "Móstoles"}).relevant


def test_books_pack_wants_lots_not_single_free_books():
    from tantalus_hoard.model import RawListing
    from tantalus_hoard.secondhand import get_pack, score_listing
    pack = get_pack("books_bulk")
    single = RawListing(source="wallapop", url="u4", title="Regalo libro Caminos abiertos", price=0, location_text="Móstoles",
                        description="Libro en buen estado")
    s = score_listing(single, pack, {"origin_location": "Móstoles"})
    assert s.score < pack["alert_min_score"] and any(sig.key == "not_bulk" for sig in s.signals)
