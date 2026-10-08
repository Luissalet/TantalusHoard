"""Extractors: JSON-LD, microdata / OpenGraph, phrase rules, listings, text quality, site profiles, LLM fallback.

Real pages come from tests/fixtures/pages (captured 2026-09-30); everything else is synthetic. No network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from tantalus_hoard.extract import extract, phrases, sites
from tantalus_hoard.extract import jsonld as jsonld_mod
from tantalus_hoard.extract import listing
from tantalus_hoard.hoard_link.web import htmltext
from tantalus_hoard.extract.llm_extract import validate_answer
from tantalus_hoard.llm import LLM
from tantalus_hoard.model import (IN_STOCK, LOCAL_PICKUP, MARKETPLACE_ONLY, OUT_OF_STOCK, PREORDER, RESTOCK_SCHEDULED,
                                  UNKNOWN, FetchResult)

PAGES = Path(__file__).parent / "fixtures" / "pages"


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


def fr(html: str, url: str = "https://shop.example/p/123", status: int = 200) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=status, text=html, ok=status == 200, content_type="text/html")


def product(body: str, head: str = "", title: str = "Pokémon Elite Trainer Box") -> str:
    return f"<html><head><title>{title}</title>{head}</head><body><h1>{title}</h1>{body}</body></html>"


def ld(payload) -> str:
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


# ============================================================================================ real pages
GAME_IN = "https://www.game.es/HARDWARE/CONSOLA/NINTENDO-SWITCH-2/NINTENDO-SWITCH-2-V2/265218"
GAME_OUT = "https://www.game.es/coleccionables/cartas-pokmon/merchandising/sobres-de-cartas-pokemon-megaevolucion-oscuridad-absoluta/266109"


def test_game_in_stock_page_via_jsonld_aggregate_offer():
    ex = extract(fr(page("game_product_instock"), GAME_IN), hints={"sku": "265218", "ean": ""})
    assert ex.page_kind == "product" and ex.methods == ["jsonld"] and ex.quality_ok and ex.content_hash
    offer = ex.primary
    assert offer.availability == IN_STOCK and offer.price == 499.99 and offer.currency == "EUR"
    assert offer.sku == "265218" and offer.title == "Nintendo Switch 2 - V2"
    assert offer.buy_button is True                       # the page's "Añadir a la cesta" button
    assert offer.extra["sku_match"] is True
    assert "availability=InStock" in offer.evidence
    assert offer.extra.get("pickup_hint") is True         # "Disponible envío a tienda" is a hint, never a state
    assert offer.availability != LOCAL_PICKUP


def test_game_out_of_stock_page():
    offer = extract(fr(page("game_product_outofstock"), GAME_OUT)).primary
    assert offer.availability == OUT_OF_STOCK and offer.price == 5.99 and offer.sku == "266109"


def test_mediamarkt_buyaction_and_third_party_seller():
    url = "https://www.mediamarkt.es/es/product/_caja-regalo-smartbox-3-dias-magicos-por-europa-171836066.html"
    offer = extract(fr(page("mediamarkt_product_buyaction"), url)).primary
    assert offer.availability == IN_STOCK and offer.price == 199.9 and offer.ean == "3608111187954"
    assert offer.seller == "Smartbox España" and offer.seller_is_retailer is False   # a marketplace seller on a retailer host
    assert offer.brand == "SMARTBOX" and offer.image.startswith("https://assets.mmsrg.com/")


def test_xtralife_rendered_page_and_its_http_shell():
    url = "https://www.xtralife.com/producto/consola-nintendo-switch-oled-pokemon-scarlet-and-violet-imp-uk-switch-edicion-estandar/109709"
    rendered = extract(fr(page("xtralife_product_rendered"), url))
    assert rendered.primary.availability == IN_STOCK and rendered.primary.price == 469.95
    assert rendered.primary.ean == "0045496597399" and rendered.primary.sku == "109709"
    shell = extract(fr(page("xtralife_product_http_shell"), url))
    assert shell.primary.availability == UNKNOWN and not shell.quality_ok and not shell.content_hash
    assert any("browser" in n for n in shell.notes)


def test_amazon_product_without_a_buy_box_is_marketplace_only_not_a_bogus_price():
    offer = extract(fr(page("amazon_product_rendered"), "https://www.amazon.es/dp/B0F18B4CVX")).primary
    assert offer.availability == MARKETPLACE_ONLY and offer.price is None    # carousel prices must not leak in
    assert "Ver todas las opciones de compra" in offer.evidence
    assert offer.title.startswith("Caja de Entrenador Mega Premium") or "Entrenador" in offer.title


def test_search_pages_yield_many_offers():
    game = extract(fr(page("game_search_rendered"), "https://www.game.es/buscar/pokemon"))
    assert game.page_kind == "search" and len(game.offers) >= 25 and game.methods == ["tiles"]
    first = game.offers[0]
    assert first.title == "Pokémon Pokopia" and first.price == 69.99 and first.sku == "253592"
    assert first.url == "https://www.game.es/videojuegos/aventura/nintendo-switch-2/pokemon-pokopia/253592"
    amazon = extract(fr(page("amazon_search_rendered"), "https://www.amazon.es/s?k=pokemon+elite+trainer+box"))
    assert amazon.page_kind == "search" and len(amazon.offers) >= 15
    top = amazon.offers[1]
    assert top.url == "https://www.amazon.es/dp/B0GYVHLP4L" and top.sku == "B0GYVHLP4L" and top.price == 135.0
    assert top.availability == IN_STOCK and top.buy_button is True and "?" not in top.url and "/ref=" not in top.url
    assert all(o.price is None or o.price > 0 for o in amazon.offers)
    xtra = extract(fr(page("xtralife_search_rendered"), "https://www.xtralife.com/buscar/pokemon/"))
    assert xtra.page_kind == "search" and len(xtra.offers) >= 20
    assert all(o.url.startswith("https://www.xtralife.com/producto/") for o in xtra.offers)
    preorders = [o for o in xtra.offers if o.availability == PREORDER]
    assert len(preorders) == 4 and preorders[0].evidence[-1] == "Reserva"   # the "Reserva" label inside a card
    mm = extract(fr(page("mediamarkt_search_rendered"), "https://www.mediamarkt.es/es/search.html?query=pokemon"))
    assert mm.page_kind == "search" and mm.methods == ["jsonld"] and len(mm.offers) == 12
    assert mm.offers[0].price == 61.99 and mm.offers[0].url.endswith("1611439.html")


def test_generic_tile_detector_ignores_menus_and_finds_repeated_cards():
    cards = "".join(f'<li class="card"><a href="/p/{i}"><img src="/i/{i}.jpg" alt="Producto {i}"></a>'
                    f'<h3><a href="/p/{i}">Producto {i}</a></h3><span class="was">19,99 €</span><span class="now">{9 + i},99 €</span></li>'
                    for i in range(1, 5))
    html = ('<html><body><nav><a href="/x">Tarjetas a 10€</a><a href="/y">Packs 20€</a><a href="/z">Cajas 30€</a></nav>'
            f'<h1>Resultados</h1><ul>{cards}</ul></body></html>')
    ex = extract(fr(html, "https://unknown.example/buscar?q=x"))
    assert ex.page_kind == "search" and len(ex.offers) == 4
    assert [o.price for o in ex.offers] == [10.99, 11.99, 12.99, 13.99]        # struck-through price is not the price
    assert ex.offers[0].extra["list_price"] == 19.99 and ex.offers[0].title == "Producto 1"
    assert ex.offers[0].url == "https://unknown.example/p/1" and ex.offers[0].image == "https://unknown.example/i/1.jpg"


def test_tile_availability_from_short_lines_and_controls():
    tiles = ('<div class="t"><a href="/a">A</a><h3>Alfa</h3><b>10,00 €</b><span>Agotado</span></div>'
             '<div class="t"><a href="/b">B</a><h3>Beta</h3><b>20,00 €</b><button>Añadir al carrito</button></div>'
             '<div class="t"><a href="/c">C</a><h3>Gamma</h3><b>30,00 €</b><button>Reservar</button></div>')
    ex = extract(fr(f"<html><body><h1>Lista</h1><div>{tiles}</div></body></html>", "https://x.example/search?q=y"))
    assert [o.availability for o in ex.offers] == [OUT_OF_STOCK, IN_STOCK, PREORDER]


# ============================================================================================ JSON-LD
@pytest.mark.parametrize("value,state", [
    ("https://schema.org/InStock", IN_STOCK), ("http://schema.org/InStock", IN_STOCK), ("InStock", IN_STOCK),
    ("https://schema.org/OnlineOnly", IN_STOCK), ("https://schema.org/LimitedAvailability", IN_STOCK),
    ("https://schema.org/InStoreOnly", LOCAL_PICKUP), ("https://schema.org/OutOfStock", OUT_OF_STOCK),
    ("https://schema.org/SoldOut", OUT_OF_STOCK), ("https://schema.org/Discontinued", OUT_OF_STOCK),
    ("https://schema.org/PreOrder", PREORDER), ("https://schema.org/PreSale", PREORDER), ("https://schema.org/BackOrder", PREORDER),
    ("https://schema.org/Whatever", UNKNOWN), (None, UNKNOWN),
])
def test_schema_availability_mapping(value, state):
    assert jsonld_mod.map_availability(value) == state


def test_jsonld_graph_variants_offer_lists_and_fields():
    payload = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": "Tienda"},
        {"@type": ["Product", "Thing"], "name": "Figura", "sku": "F1", "gtin": "8435407312345", "mpn": "M-1",
         "brand": {"@type": "Brand", "name": "Marca"}, "image": ["/img/a.jpg", "/img/b.jpg"],
         "offers": [
             {"@type": "Offer", "price": "1.234,56", "priceCurrency": "EUR", "availability": "https://schema.org/InStock",
              "seller": {"@type": "Organization", "name": "Tienda Oficial"}, "url": "/f1"},
             {"@type": "Offer", "price": 999, "priceCurrency": "EUR", "availability": "https://schema.org/OutOfStock", "seller": "Otro"},
         ]},
    ]}
    ex = extract(fr(product("", ld(payload)), "https://shop.example/f1"))
    assert [o.seller for o in ex.offers] == ["Tienda Oficial", "Otro"]
    first = ex.offers[0]
    assert first.price == 1234.56 and first.ean == "8435407312345" and first.brand == "Marca" and first.extra["mpn"] == "M-1"
    assert first.image == "/img/a.jpg" and first.url == "https://shop.example/f1"


def test_jsonld_product_group_and_aggregate_offer():
    payload = {"@type": "ProductGroup", "name": "Camiseta", "hasVariant": [
        {"@type": "Product", "name": "Camiseta M", "sku": "C-M", "offers": {"@type": "Offer", "price": "19.90", "priceCurrency": "EUR", "availability": "InStock"}},
        {"@type": "Product", "name": "Camiseta L", "sku": "C-L", "offers": {"@type": "Offer", "price": "19.90", "priceCurrency": "EUR", "availability": "OutOfStock"}}]}
    agg = {"@type": "Product", "name": "Caja", "offers": {"@type": "AggregateOffer", "lowPrice": "5.99", "highPrice": "9.99", "priceCurrency": "EUR", "offerCount": 3}}
    variants = extract(fr(product("", ld(payload)), "https://shop.example/g")).offers
    assert {o.sku: o.availability for o in variants} == {"C-M": IN_STOCK, "C-L": OUT_OF_STOCK} and all(o.extra["variant"] for o in variants)
    only_agg = extract(fr(product("", ld(agg)), "https://shop.example/h")).primary
    assert only_agg.price == 5.99 and only_agg.extra["aggregate"] is True


def test_jsonld_invalid_blocks_are_tolerated():
    bad = '<script type="application/ld+json">{"@type": "Product", "name": "X", }</script>'   # trailing comma: repaired
    worse = '<script type="application/ld+json">{{{ not json</script>'
    good = ld({"@type": "Product", "name": "Y", "offers": {"@type": "Offer", "price": 3, "priceCurrency": "EUR", "availability": "InStock"}})
    ex = extract(fr(product("", bad + worse + good), "https://shop.example/y"))
    assert ex.primary.price == 3 and ex.primary.availability == IN_STOCK
    assert any("could not be parsed" in n for n in ex.notes)


def test_jsonld_availability_starts_makes_a_dated_restock_and_a_preorder_date():
    out = {"@type": "Product", "name": "A", "offers": {"@type": "Offer", "price": 10, "priceCurrency": "EUR",
                                                       "availability": "OutOfStock", "availabilityStarts": "2026-11-05T00:00:00Z"}}
    pre = {"@type": "Product", "name": "B", "offers": {"@type": "Offer", "price": 10, "priceCurrency": "EUR",
                                                       "availability": "PreOrder", "availabilityStarts": "2026-12-01"}}
    a = extract(fr(product("", ld(out)))).primary
    b = extract(fr(product("", ld(pre)))).primary
    assert a.availability == RESTOCK_SCHEDULED and a.restock_date == "2026-11-05"
    assert b.availability == PREORDER and b.preorder_date == "2026-12-01"


def test_jsonld_item_list_of_products_is_a_listing():
    items = [{"@type": "ListItem", "position": i, "item": {"@type": "Product", "name": f"P{i}", "url": f"/p/{i}",
                                                           "offers": {"@type": "Offer", "price": i * 10, "priceCurrency": "EUR"}}} for i in (1, 2, 3)]
    ex = extract(fr(f"<html><head><title>Cat</title>{ld({'@type': 'ItemList', 'itemListElement': items})}</head><body><p>x</p></body></html>",
                    "https://shop.example/categoria/pokemon"))
    assert ex.page_kind == "listing" and [o.price for o in ex.offers] == [10, 20, 30] and ex.offers[0].url == "https://shop.example/p/1"


def test_jsonld_related_products_do_not_become_offers():
    payload = {"@type": "Product", "name": "Main", "offers": {"@type": "Offer", "price": 5, "priceCurrency": "EUR", "availability": "InStock"},
               "isRelatedTo": [{"@type": "Product", "name": "Other", "offers": {"@type": "Offer", "price": 1}}]}
    ex = extract(fr(product("", ld(payload))))
    assert len(ex.offers) == 1 and ex.primary.title == "Main"


# ============================================================================================ microdata / OpenGraph
def test_microdata_offer():
    body = ('<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Lata</span>'
            '<div itemprop="offers" itemscope itemtype="https://schema.org/Offer"><meta itemprop="priceCurrency" content="EUR">'
            '<span itemprop="price" content="24.95">24,95 €</span><link itemprop="availability" href="https://schema.org/InStock"></div></div>')
    ex = extract(fr(product(body), "https://shop.example/l"))
    assert ex.methods == ["microdata"] and ex.primary.price == 24.95 and ex.primary.availability == IN_STOCK and ex.primary.currency == "EUR"


def test_opengraph_product_meta():
    head = ('<meta property="og:type" content="product"><meta property="og:title" content="Pack ETB"><meta property="og:image" content="/i.jpg">'
            '<meta property="product:price:amount" content="54.99"><meta property="product:price:currency" content="EUR">'
            '<meta property="product:availability" content="out of stock">')
    ex = extract(fr(product("", head), "https://shop.example/etb"))
    assert ex.methods == ["opengraph"] and ex.primary.availability == OUT_OF_STOCK and ex.primary.price == 54.99
    assert ex.primary.image == "https://shop.example/i.jpg"


def test_sale_price_meta_wins_over_list_price():
    head = ('<meta property="og:type" content="product"><meta property="product:price:amount" content="60"><meta property="product:sale_price:amount" content="45.5">'
            '<meta property="product:price:currency" content="EUR"><meta property="product:availability" content="instock">')
    offer = extract(fr(product("", head))).primary
    assert offer.price == 45.5 and offer.extra["list_price"] == 60.0


# ============================================================================================ phrase rules
@pytest.mark.parametrize("button,state,active", [
    ("<button>Añadir al carrito</button>", IN_STOCK, True),
    ("<button>Añadir a la cesta</button>", IN_STOCK, True),
    ("<button>Comprar ahora</button>", IN_STOCK, True),
    ("<button>Add to cart</button>", IN_STOCK, True),
    ("<input type='submit' value='Buy now'>", IN_STOCK, True),
    ("<button>Reservar</button>", PREORDER, True),
    ("<button>Reserva ya</button>", PREORDER, True),
    ("<button>Precompra</button>", PREORDER, True),
    ("<button>Pre-order</button>", PREORDER, True),
    ("<button>Agotado</button>", OUT_OF_STOCK, False),
    ("<button disabled>Sin stock</button>", OUT_OF_STOCK, False),
    ("<button>Avísame</button>", OUT_OF_STOCK, False),
    ("<button>Notify me</button>", OUT_OF_STOCK, False),
    ("<button>Currently unavailable</button>", OUT_OF_STOCK, False),
])
def test_control_phrases(button, state, active):
    offer = extract(fr(product(f'<p class="price">29,99 €</p>{button}'))).primary
    assert offer.availability == state and offer.buy_button is active


def test_disabled_buy_buttons_do_not_count():
    for attrs in ("disabled", 'aria-disabled="true"', 'class="btn btn-disabled"'):
        offer = extract(fr(product(f"<button {attrs}>Añadir al carrito</button>"))).primary
        assert offer.availability == OUT_OF_STOCK and offer.buy_button is False, attrs
    hidden = extract(fr(product('<button style="display:none">Añadir al carrito</button><p class="price">9,99 €</p>'))).primary
    assert hidden.availability == UNKNOWN


def test_navigation_links_named_comprar_are_not_buy_controls():
    html = ('<html><body><nav><a class="btn" href="/c">Comprar</a></nav><h1>Ficha</h1><p>texto sin más</p>'
            '<footer><button>Comprar en GAME</button></footer></body></html>')
    ex = extract(fr(html, "https://shop.example/ficha"))
    assert ex.offers == [] and ex.page_kind == "unknown"          # nav / footer controls decide nothing


def test_related_products_sold_out_badge_outside_the_window_is_ignored():
    filler = "".join(f"<p>Descripción larga del producto, párrafo {i}. </p>" for i in range(80))  # the commons collapse identical lines
    html = product(f'<p class="price">12,00 €</p>{filler}<div class="related"><span>Agotado</span></div>')
    offer = extract(fr(html)).primary
    assert offer.availability in (UNKNOWN,) and offer.buy_button is None


def test_sold_out_line_near_headline_and_restock_date():
    html = product('<p>Agotado</p><p>Disponible a partir del 15/10/2026</p><p class="price">39,99 €</p>')
    offer = extract(fr(html, "https://shop.example/x"), hints={"today": "2026-09-30"}).primary
    assert offer.availability == RESTOCK_SCHEDULED and offer.restock_date == "2026-10-15" and offer.price == 39.99
    assert any("Disponible a partir del" in e for e in offer.evidence)


def test_release_date_on_a_preorder():
    html = product('<p>Fecha de lanzamiento: 20 de noviembre de 2026</p><button>Reservar</button>')
    offer = extract(fr(html)).primary
    assert offer.availability == PREORDER and offer.preorder_date == "2026-11-20"


def test_marketplace_seller_and_only_other_offers():
    html = product('<p>Vendido por TiendaPirata</p><button>Añadir al carrito</button><p class="price">80,00 €</p>')
    offer = extract(fr(html, "https://www.amazon.es/dp/B000000000")).primary
    assert offer.seller == "TiendaPirata" and offer.seller_is_retailer is False and offer.availability == IN_STOCK
    first_party = extract(fr(product('<p>Vendido y enviado por Amazon</p><button>Añadir al carrito</button>'), "https://www.amazon.es/dp/B000000001")).primary
    assert first_party.seller == "Amazon" and first_party.seller_is_retailer is True
    only = extract(fr(product('<a>Ver todas las opciones de compra</a>'), "https://shop.example/x")).primary
    assert only.availability == MARKETPLACE_ONLY


def test_marketplace_hosts_never_claim_first_party():
    html = product('<button>Añadir al carrito</button>')
    offer = extract(fr(html, "https://www.cardmarket.com/es/Pokemon/Products/x")).primary
    assert offer.seller_is_retailer is False


def test_pickup_needs_a_target_store_to_change_the_state():
    html = product('<p>Sin stock online</p><p>Recogida en tienda: GAME Arturo Soria disponible hoy</p><button disabled>Añadir a la cesta</button>')
    plain = extract(fr(html)).primary
    assert plain.availability == OUT_OF_STOCK and plain.extra["pickup_hint"] is True
    targeted = extract(fr(html), hints={"store_ids": ["GAME Arturo Soria"]}).primary
    assert targeted.availability == LOCAL_PICKUP and targeted.store_availability == {"GAME Arturo Soria": "LOCAL_PICKUP"}


def test_structured_data_and_page_can_disagree_and_the_conflict_is_reported():
    payload = {"@type": "Product", "name": "Z", "offers": {"@type": "Offer", "price": 5, "priceCurrency": "EUR", "availability": "InStock"}}
    ex = extract(fr(product('<button disabled>Agotado</button>', ld(payload))))
    offer = ex.primary
    assert offer.availability == OUT_OF_STOCK and "conflict" in offer.extra and any("sold-out control" in n for n in ex.notes)
    assert offer.extra["structured_availability"] == IN_STOCK
    assert offer.buy_button is False and any("Agotado" in e for e in offer.evidence)


@pytest.mark.parametrize("structured_state", ["InStock", "PreOrder"])
def test_unavailable_purchase_controls_override_stale_structured_stock(structured_state):
    payload = {"@type": "Product", "name": "Switch MikroTik CRS812", "offers": {
        "@type": "Offer", "price": 1095.46, "priceCurrency": "EUR", "availability": structured_state}}
    body = '<a class="btn">Avísame cuando haya stock</a><button disabled>NO DISPONIBLE</button>'
    offer = extract(fr(product(body, ld(payload), title=payload["name"]))).primary
    assert offer.availability == OUT_OF_STOCK and offer.buy_button is False
    assert offer.price == 1095.46 and offer.extra.get("conflict")


def test_hidden_unavailable_controls_do_not_override_real_stock():
    payload = {"@type": "Product", "name": "Switch", "offers": {"@type": "Offer", "availability": "InStock"}}
    offer = extract(fr(product('<button hidden disabled>NO DISPONIBLE</button><button>Añadir al carrito</button>', ld(payload)))).primary
    assert offer.availability == IN_STOCK and offer.buy_button is True


def test_tecnologiamodular_real_page_overrides_instock_seo_metadata():
    url = "https://www.tecnologiamodular.es/comprar-ordenador-completo-barato-switch-mikrotik-crs812-ddq-2x400g-2x200g-8x50g"
    offer = extract(fr(page("tecnologiamodular_crs812_outofstock"), url)).primary
    assert offer.availability == OUT_OF_STOCK and offer.buy_button is False
    assert offer.extra["structured_availability"] == IN_STOCK
    assert offer.price == 1095.46 and "NO DISPONIBLE" in offer.evidence


def test_evidence_snippets_are_short():
    long_text = "Añadir al carrito " + "x" * 400
    offer = extract(fr(product(f'<button>{long_text[:60]}</button><p>Vendido por {"Y" * 300}</p>'))).primary
    assert all(len(e) <= 160 for e in offer.evidence)


def test_price_and_number_parsing():
    cases = {"1.234,56 €": 1234.56, "€1,234.56": 1234.56, "59,99€": 59.99, "499.99 EUR": 499.99, "2,099.00 €": 2099.0, "1.234 €": 1234.0}
    for raw, value in cases.items():
        assert phrases.parse_price(raw) == (value, "EUR"), raw
    assert phrases.parse_number("1.234.567") == 1234567.0 and phrases.parse_number("0,5") == 0.5 and phrases.parse_number("abc") is None
    assert phrases.standalone_price("Tarjetas a 10€") is None and phrases.standalone_price("59,99 €") == (59.99, "EUR")


def test_date_parsing():
    assert phrases.parse_date("Lanzamiento: 15 de octubre de 2026") == "2026-10-15"
    assert phrases.parse_date("Release date: October 15, 2026") == "2026-10-15"
    assert phrases.parse_date("15/10/2026") == "2026-10-15" and phrases.parse_date("sin fecha") is None


# ============================================================================================ text / quality / hash
def test_readable_text_drops_chrome_but_full_text_keeps_forms():
    html = ('<html><head><style>x{}</style><script>var a=1</script></head><body><nav>Inicio Menú</nav><header>Cabecera</header>'
            '<div id="cookie-banner">Aceptar cookies</div><main><h1>Título</h1><p>Contenido real del producto.</p>'
            '<form><button>Añadir al carrito</button></form></main><aside>Publicidad</aside><footer>Aviso legal</footer></body></html>')
    readable = htmltext.readable(html)[1]
    assert "Título" in readable and "Contenido real" in readable
    for junk in ("Inicio", "Cabecera", "cookies", "Publicidad", "Aviso legal", "Añadir", "var a"):
        assert junk not in readable
    assert "Añadir al carrito" in htmltext.readable(html, drop_chrome=False)[1]


def test_chrome_ratio_and_quality_gate():
    menu = "\n".join(["Inicio", "Mi cuenta", "Carrito", "Ayuda", "Contacto", "Envíos", "Ofertas", "Blog"] * 10)
    prose = "Este producto es una caja de entrenador élite con nueve sobres de mejora y accesorios de juego. " * 8
    assert htmltext.chrome_ratio(menu) > 0.9 and htmltext.chrome_ratio(prose) < 0.1
    assert htmltext.quality(menu) and htmltext.quality("hola") and not htmltext.quality(prose)
    ex = extract(fr("<html><body><nav>Inicio</nav><nav>Ayuda</nav></body></html>", "https://shop.example/"))
    assert not ex.quality_ok and ex.content_hash == ""


def test_content_hash_ignores_noise_but_not_prices():
    a = htmltext.content_hash("Caja de entrenador  élite\nPrecio 49,99 € actualizado 12:34:56 hace 5 minutos sesión 0123456789abcdef0123")
    b = htmltext.content_hash("caja de entrenador élite\nPrecio 49,99 € actualizado 18:01 hace 2 horas sesión fedcba9876543210fedc")
    c = htmltext.content_hash("Caja de entrenador élite\nPrecio 44,99 € actualizado 12:34:56")
    assert a == b and a != c and len(a) == 64


def test_extraction_carries_excerpt_title_and_bounded_text():
    ex = extract(fr(page("game_product_instock"), GAME_IN))
    assert ex.title.startswith("Nintendo Switch 2") and 0 < len(ex.text_excerpt) <= 20_000


# ============================================================================================ degenerate inputs
def test_blocked_and_empty_inputs():
    blocked = FetchResult(url="https://x.es/", status=403, blocked=True, block_reason="cloudflare", text=page("carrefour_cloudflare"))
    ex = extract(blocked)
    assert ex.page_kind == "blocked" and not ex.quality_ok and not ex.offers
    # a block page that arrives flagged ok (e.g. a cached copy) is still recognised
    sneaky = fr(page("amazon_interstitial_akamai"), "https://www.amazon.es/s?k=x")
    assert extract(sneaky).page_kind == "blocked"
    empty = extract(FetchResult(url="https://x.es/", status=200, ok=True, text=""))
    assert empty.page_kind == "unknown" and not empty.quality_ok
    off = extract(FetchResult(url="https://x.es/", block_reason="offline", error="offline"))
    assert off.page_kind == "blocked" and not off.quality_ok


def test_sku_and_ean_hints_are_compared():
    payload = {"@type": "Product", "name": "Z", "sku": "AB-12", "gtin13": "8435407312345",
               "offers": {"@type": "Offer", "price": 5, "priceCurrency": "EUR", "availability": "InStock"}}
    good = extract(fr(product("", ld(payload))), hints={"sku": "ab12", "ean": "8435407312345"}).primary
    assert good.extra["sku_match"] is True and good.extra["ean_match"] is True
    bad = extract(fr(product("", ld(payload))), hints={"sku": "ZZ-99", "ean": "1111111111111"}).primary
    assert bad.extra["sku_match"] is False and bad.extra["ean_match"] is False


# ============================================================================================ site profiles
def test_site_helpers():
    assert sites.retailer_for_host("https://www.game.es/x") == "GAME" and sites.retailer_for_host("shop.unknown.example") is None
    assert sites.retailer_for_host("es.amazon.es") == "Amazon" and sites.retailer_for_host("www.amazon.com") == "Amazon"
    assert sites.source_level_for_host("amazon.es") == 1 and sites.source_level_for_host("game.es", page_kind="search") == 2
    assert sites.source_level_for_host("cardmarket.com") == 4 and sites.source_level_for_host("nowhere.example") is None
    assert sites.is_marketplace_host("https://es.wallapop.com/item/x") and not sites.is_marketplace_host("game.es")
    assert sites.needs_browser("https://www.xtralife.com/producto/x/1") and sites.needs_browser("https://www.game.es/buscar/x")
    assert not sites.needs_browser("https://www.game.es/HARDWARE/x/1234") and not sites.needs_browser("https://unknown.example/")
    assert sites.profile_for_host("marketplace.nvidia.com").api_adapter == "nvidia"
    for host in ("game.es", "elcorteingles.es", "carrefour.es", "amazon.es", "pccomponentes.com", "mediamarkt.es", "fnac.es",
                 "xtralife.com", "marketplace.nvidia.com", "store.nvidia.com", "toysrus.es", "juguettos.com", "cardmarket.com",
                 "wallapop.com", "ebay.es"):
        assert sites.profile_for_host(host) is not None, host
    assert sites.kind_of_url("https://www.amazon.es/dp/B0F18B4CVX") == "product" and sites.kind_of_url("https://www.amazon.es/s?k=x") == "search"
    assert sites.is_first_party_seller("Amazon EU Sarl", "amazon.es") is True and sites.is_first_party_seller("Game Over Store", "game.es") is False


# ============================================================================================ LLM fallback
class Reply:
    def __init__(self, text):
        self.text = text


class FakeLink:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return Reply(json.dumps(self.answer) if not isinstance(self.answer, str) else self.answer)


HINT = {"title_hint": "Figura rara"}      # a watched target: the engine knows it is a product page
UNDECIDED = product('<p>Ficha del producto 1234</p><p>Unidades en tienda: ver abajo</p>', title="Figura rara")


def test_llm_is_used_only_when_deterministic_methods_fail_and_evidence_is_literal():
    answer = {"availability": "out_of_stock", "price": 59.99, "currency": "EUR", "seller": None, "buy_button": False,
              "evidence": ["Sin stock", "totalmente inventado"]}
    html = product('<div class="info">Sin stock por el momento</div><p class="price">59,99 €</p>')
    link = FakeLink(answer)
    # the deterministic layer cannot read this (no controls / hint classes): make sure the model is asked
    ex = extract(fr(html.replace("Sin stock por el momento", "Producto sin stock por el momento")), llm=LLM(link))
    offer = ex.primary
    assert offer.availability == OUT_OF_STOCK and offer.method == "llm" and "llm" in ex.methods
    assert offer.evidence == ["Sin stock"] and offer.price == 59.99      # invented snippet dropped, real one kept
    assert link.calls and "<page>" in link.calls[0][1]["content"]        # remote text is wrapped as data


def test_llm_answers_without_literal_evidence_are_dropped():
    text = "Producto sin stock por el momento 59,99 €"
    assert validate_answer({"availability": "in_stock", "price": 59.99, "evidence": ["Añadir al carrito"]}, text) is None
    assert validate_answer({"availability": "in_stock", "price": 59.99, "evidence": []}, text) is None
    assert validate_answer({"availability": "unknown", "evidence": ["sin stock"]}, text) is None
    assert validate_answer(None, text) is None and validate_answer({"availability": "in_stock"}, text) is None
    # the evidence exists but says the opposite of the claim
    assert validate_answer({"availability": "in_stock", "evidence": ["sin stock por el momento"]}, text) is None
    kept = validate_answer({"availability": "out_of_stock", "price": 12.5, "seller": "Nadie", "evidence": ["SIN   STOCK"]}, text)
    assert kept.availability == OUT_OF_STOCK and kept.price is None and kept.seller is None   # price / seller not in the text


def test_llm_never_runs_when_the_page_is_decided_or_the_model_is_absent():
    link = FakeLink({"availability": "in_stock", "evidence": ["Añadir al carrito"]})
    ex = extract(fr(product("<button>Añadir al carrito</button>")), llm=LLM(link))
    assert ex.primary.method == "phrases" and not link.calls
    assert extract(fr(UNDECIDED), hints=HINT, llm=None).primary.availability == UNKNOWN
    assert extract(fr(UNDECIDED), hints=HINT, llm=LLM(FakeLink("not json at all"))).primary.availability == UNKNOWN
    assert extract(fr(UNDECIDED), hints=HINT, llm=LLM(None)).primary.availability == UNKNOWN


# ------------------------------------------------------------------------------------------------ El Corte Inglés state
def test_eci_search_reads_the_embedded_product_state():
    ex = extract(fr(page("eci_search_moonshine_http"), url="https://www.elcorteingles.es/search-nwx/?s=pokemon+elite+trainer+box"),
                 hints={})
    assert ex.page_kind == "search" and ex.methods == ["site:state"] and ex.quality_ok
    assert len(ex.offers) == 12
    first = ex.offers[0]
    assert (first.sku, first.ean, first.price, first.currency) == ("A200971037", "0196214141995", 59.99, "EUR")
    assert first.availability == IN_STOCK and first.buy_button is True
    assert first.url == "https://www.elcorteingles.es/juguetes/A200971037-caja-pokemon-mega-greninja-ex-jcc-pokemon-bandai/"
    assert first.seller_is_retailer is True and first.brand == "Pokémon"
    assert any("ADD" in e for e in first.evidence)


def test_eci_state_never_claims_stock_without_the_add_status():
    html = page("eci_search_moonshine_http").replace('"status": "ADD"', '"status": "NOTIFY"')
    ex = extract(fr(html, url="https://www.elcorteingles.es/search-nwx/?s=x"), hints={})
    assert ex.offers and all(o.availability == UNKNOWN for o in ex.offers)
    coming = page("eci_search_moonshine_http").replace('"coming_soon": false', '"coming_soon": true')
    ex = extract(fr(coming, url="https://www.elcorteingles.es/search-nwx/?s=x"), hints={})
    assert ex.offers and all(o.availability == PREORDER for o in ex.offers)


def test_eci_profile_uses_http_first_and_amazon_too():
    assert sites.needs_browser("https://www.elcorteingles.es/search-nwx/?s=x") is False
    assert sites.needs_browser("https://www.amazon.es/dp/B0F18B4CVX") is False
    assert sites.needs_browser("https://www.game.es/buscar/pokemon") is True
    assert sites.needs_browser("https://www.xtralife.com/x") is True


# ------------------------------------------------------------------------------------------------ "Próximamente" (not on sale yet)
GAME_SOON = ("https://www.game.es/coleccionables/cartas-pokémon/merchandising/"
             "caja-ultra-premium-de-cartas-pokemon-30-aniversario-castellano-surtido/266954")
GAME_SOLD = "https://www.game.es/coleccionables/cartas-pokémon/merchandising/mini-lata-de-cartas-pokemon-30-aniversario-castellano-surtido/266948"


def test_game_unreleased_product_is_coming_soon_not_sold_out():
    """GAME marks unreleased products OutOfStock in JSON-LD but shows PRÓXIMAMENTE where the buy button will be."""
    offer = extract(fr(page("game_product_coming_soon"), GAME_SOON)).primary
    assert offer.availability == "COMING_SOON" and offer.buy_button is False
    assert any("PRÓXIMAMENTE" in e.upper() or "PROXIMAMENTE" in e.upper() for e in offer.evidence)


def test_game_sold_out_web_product_stays_sold_out():
    offer = extract(fr(page("game_product_soldout_web"), GAME_SOLD)).primary
    assert offer.availability == "OUT_OF_STOCK"
