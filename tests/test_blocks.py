"""Anti-bot / login-wall detection: real pages captured 2026-09-30 plus synthetic edge cases. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tantalus_hoard.hoard_link.web.blocks import apply_block, detect_block
from tantalus_hoard.model import FetchResult

PAGES = Path(__file__).parent / "fixtures" / "pages"
HEADERS = json.loads((PAGES / "_response_headers.json").read_text(encoding="utf-8"))


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


BLOCKED = [
    # fixture, status, headers, url, expected
    ("amazon_interstitial_akamai", 200, {}, "https://www.amazon.es/s?k=x", "akamai"),      # 200 + bm-verify interstitial
    ("amazon_robot_check", 200, {}, "https://www.amazon.es/dp/B0D6WLV3QH", "captcha"),      # "Seguir comprando" validateCaptcha
    ("eci_akamai_denied", 403, {}, "https://www.elcorteingles.es/search-nwx/?s=x", "akamai"),
    ("nvidia_marketplace_akamai_denied", 403, HEADERS["nvidia_marketplace_akamai_denied.html"], "https://marketplace.nvidia.com/x", "akamai"),
    ("carrefour_cloudflare", 403, {}, "https://www.carrefour.es/?q=x", "cloudflare"),
    ("pccomponentes_cloudflare", 403, {}, "https://www.pccomponentes.com/buscar/?query=x", "cloudflare"),
    ("fnac_datadome", 403, HEADERS["fnac_datadome.html"], "https://www.fnac.es/", "datadome"),
    ("ebay_akamai_error", 403, HEADERS["ebay_akamai_error.html"], "https://www.ebay.es/sch/i.html?_nkw=x", "akamai"),
    ("fnac_maintenance_403", 403, {}, "https://www.fnac.es/SearchResult/ResultList.aspx", "http_403"),
]

FINE = [
    "game_product_instock", "game_product_outofstock", "game_search_rendered", "mediamarkt_product_buyaction",
    "mediamarkt_search_rendered", "xtralife_product_rendered", "xtralife_search_rendered", "xtralife_product_http_shell",
    "amazon_product_rendered", "amazon_search_rendered",
]


@pytest.mark.parametrize("name,status,headers,url,expected", BLOCKED)
def test_real_block_pages_are_classified(name, status, headers, url, expected):
    assert detect_block(status, page(name), headers, url) == expected


@pytest.mark.parametrize("name", FINE)
def test_real_content_pages_are_not_blocks(name):
    assert detect_block(200, page(name), {}, "https://shop.example/p/1") == ""


def test_bare_keywords_in_a_big_page_are_not_a_block():
    body = "<html><head><title>Shop</title><script>var captcha='x'; // akamai datadome px-captcha</script></head><body>" + "<p>producto</p>" * 8000 + "</body></html>"
    assert len(body) > 60_000
    assert detect_block(200, body, {"server": "AkamaiGHost"}, "https://x.es/p") == ""


def test_keywords_on_a_small_normal_page_do_not_trigger():
    body = "<html><head><title>Ficha</title><script src='/captcha.js'></script></head><body><h1>Pokémon</h1><p>Añadir al carrito</p></body></html>"
    assert detect_block(200, body, {}, "https://x.es/p") == ""


def test_cloudflare_header_and_markers():
    assert detect_block(403, "<html></html>", {"cf-mitigated": "challenge"}, "https://x.es/") == "cloudflare"
    small = "<html><head><title>Just a moment...</title></head><body>cloudflare<script src='/cdn-cgi/challenge-platform/h/b'></script></body></html>"
    assert detect_block(403, small, {}, "https://x.es/") == "cloudflare"
    # "Un momento" alone (no Cloudflare marker anywhere) is just a title
    assert detect_block(200, "<html><head><title>Un momento</title></head><body>hola</body></html>", {}, "https://x.es/") == ""


def test_perimeterx_and_generic_captcha_widget():
    px = '<html><body><div id="px-captcha"></div><p>Press &amp; Hold to confirm you are a human</p></body></html>'
    assert detect_block(403, px, {}, "https://x.es/") == "perimeterx"
    widget = '<html><body><form><div class="g-recaptcha" data-sitekey="abc"></div></form></body></html>'
    assert detect_block(200, widget, {}, "https://x.es/") == "captcha"
    # a real page that merely embeds a recaptcha in its newsletter form
    real = '<html><body>' + "<p>texto de producto largo</p>" * 200 + '<div class="g-recaptcha" data-sitekey="abc"></div></body></html>'
    assert detect_block(200, real, {}, "https://x.es/p") == ""


def test_datadome_needs_more_than_a_cookie_on_a_big_page():
    big = "<html><body>" + "<p>producto</p>" * 3000 + "</body></html>"
    assert detect_block(200, big, {"set-cookie": "datadome=abc; Path=/"}, "https://x.es/p") == ""
    assert detect_block(403, "<html>x</html>", {"x-datadome": "protected"}, "https://x.es/p") == "datadome"


def test_login_walls():
    assert detect_block(200, "<html><body>hola</body></html>", {}, "https://x.es/customer/account/login/?ref=1") == "login"
    assert detect_block(200, "<html><head><title>Inicia sesión</title></head><body>form</body></html>", {}, "https://x.es/acceso") == "login"
    assert detect_block(200, "<html><head><title>Sign in</title></head><body>form</body></html>", {}, "https://x.es/z") == "login"
    assert detect_block(401, "denied", {}, "https://x.es/api") == "login"
    # a product whose name contains "login" is not a login wall
    assert detect_block(200, "<html><head><title>Logitech Login mouse</title></head><body>x</body></html>", {}, "https://x.es/logitech-login") == ""


def test_plain_statuses():
    assert detect_block(403, "nope", {}, "https://x.es/") == "http_403"
    assert detect_block(429, "slow down", {}, "https://x.es/") == "http_429"
    assert detect_block(503, "down", {}, "https://x.es/") == "http_5xx"
    assert detect_block(500, "oops", {}, "https://x.es/") == "http_5xx"
    assert detect_block(404, "missing", {}, "https://x.es/") == ""
    assert detect_block(200, "<html><body>ok</body></html>", {}, "https://x.es/") == ""


def test_html_escaped_akamai_page_is_still_recognised():
    # Akamai escapes its own text ("Reference&#32;&#35;18...")
    assert detect_block(403, page("eci_akamai_denied"), {}, "https://www.elcorteingles.es/") == "akamai"


def test_apply_block_sets_flags():
    fr = FetchResult(url="u", status=403, text="x")
    apply_block(fr, "cloudflare")
    assert fr.blocked and not fr.ok and fr.block_reason == "cloudflare" and "Cloudflare" in fr.error
    fr = FetchResult(url="u", status=503, text="x")
    apply_block(fr, "http_5xx")
    assert not fr.blocked and not fr.ok and fr.block_reason == "http_5xx"
    fr = FetchResult(url="u", status=200, text="<html>ok</html>")
    apply_block(fr, "")
    assert fr.ok and not fr.blocked and fr.error == ""
    fr = FetchResult(url="u", status=404, text="gone")
    apply_block(fr, "")
    assert not fr.ok and fr.error == "HTTP 404"
