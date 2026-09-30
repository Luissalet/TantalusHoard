"""De-duplication cascade over existing rows (dicts, like DB rows) and within a batch."""

import sqlite3

from tantalus_hoard.model import RawListing
from tantalus_hoard.secondhand.dedupe import DedupeIndex, dedupe_batch, find_duplicate


def row(url, title, price=5.0, location="Móstoles", external_id="", source="facebook"):
    return {"source": source, "url": url, "title": title, "price": price, "location_text": location,
            "external_id": external_id}


def test_same_url_with_tracking_params_is_a_duplicate():
    existing = [row("https://facebook.com/marketplace/item/111/", "Regalo lote de libros")]
    hit, why = find_duplicate(row("https://facebook.com/marketplace/item/111/?ref=xyz", "Regalo lote de libros"), existing)
    assert hit is existing[0] and why == "url"


def test_external_id_wins_even_with_different_url_and_title():
    existing = [row("https://a/1", "Caja de libros gratis", external_id="222")]
    hit, why = find_duplicate(row("https://b/other", "Otro título", price=9.0, external_id="222"), existing)
    assert hit is existing[0] and why == "external_id"


def test_external_id_is_scoped_to_the_source():
    existing = [row("https://a/1", "Algo", external_id="222", source="wallapop")]
    hit, _ = find_duplicate(row("https://b/2", "Distinto", price=1.0, external_id="222", source="facebook"), existing)
    assert hit is None


def test_title_location_price_fallback():
    existing = [row("https://fb/333/", "Vaciado de casa, muchos libros", price=0.0, location="Alcorcón")]
    hit, why = find_duplicate(row("https://fb/999999/", "Vaciado de casa, muchos libros", price=0.0, location="Alcorcón"), existing)
    assert hit is existing[0] and why == "title_location_price"


def test_same_title_in_another_town_is_not_merged():
    existing = [row("https://fb/1/", "Lote 12 libros por 8€", price=8.0, location="Madrid")]
    hit, _ = find_duplicate(row("https://fb/2/", "Lote 12 libros por 8€", price=8.0, location="Getafe"), existing)
    assert hit is None


def test_similar_title_same_price_is_merged():
    existing = [row("https://fb/444/", "Regalo colección completa de libros de aventuras", price=0.0)]
    hit, why = find_duplicate(row("https://fb/555/", "Regalo colección completa de libros de aventura", price=0.0), existing)
    assert hit is existing[0] and why == "similar_title"


def test_similar_title_different_price_is_not_merged():
    existing = [row("https://fb/444/", "Regalo colección completa de libros de aventuras", price=0.0)]
    hit, _ = find_duplicate(row("https://fb/555/", "Regalo colección completa de libros de aventura", price=7.0), existing)
    assert hit is None


def test_different_listings_are_not_merged():
    existing = [row("https://fb/666/", "Regalo libros de cocina", price=0.0)]
    hit, _ = find_duplicate(row("https://fb/777/", "Vendo bicicleta de montaña", price=50.0), existing)
    assert hit is None


def test_cross_source_is_opt_in():
    existing = [row("https://a/1", "Pokémon ETB precintado", price=55.0, source="wallapop")]
    incoming = row("https://b/2", "Pokémon ETB precintado", price=55.0, source="facebook")
    assert find_duplicate(incoming, existing)[0] is None
    assert find_duplicate(incoming, existing, cross_source=True)[0] is existing[0]


def test_works_with_sqlite_rows_and_raw_listings():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table listings (source text, url text, title text, price real, location_text text, external_id text)")
    con.execute("insert into listings values ('wallapop','https://es.wallapop.com/item/abc-1','Lote 2 libros',4.0,'Móstoles','1')")
    rows = con.execute("select * from listings").fetchall()
    incoming = RawListing(source="wallapop", url="https://es.wallapop.com/item/other", title="Lote 2 libros", price=4.0,
                          location_text="Móstoles", external_id="9")
    hit, why = find_duplicate(incoming, rows)
    assert hit is rows[0] and why == "title_location_price"


def test_dedupe_batch_catches_repeats_inside_the_batch():
    a = RawListing(source="wallapop", url="https://x/item/1", title="Lote libros", price=5.0, external_id="1")
    b = RawListing(source="wallapop", url="https://x/item/1?utm=z", title="Lote libros", price=5.0, external_id="1")
    c = RawListing(source="wallapop", url="https://x/item/2", title="Bicicleta", price=90.0, external_id="2")
    known = [row("https://x/item/2", "Bicicleta", price=90.0, source="wallapop", external_id="2")]
    fresh, dups = dedupe_batch([a, b, c], known)
    assert fresh == [a]
    assert [(d[0], d[2]) for d in dups] == [(b, "external_id"), (c, "external_id")]


def test_index_add_and_len():
    index = DedupeIndex()
    assert len(index) == 0
    index.add(row("https://a/1", "x"))
    assert len(index) == 1 and index.find(row("https://a/1", "y"))[1] == "url"
