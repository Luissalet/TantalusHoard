"""Facebook Marketplace: pure parsing (HTML fixtures, ported Radar de Libros cases) and the source with a fake browser."""

from contextlib import contextmanager

import pytest

from tantalus_hoard.errors import TantalusError
from tantalus_hoard.secondhand import build_source, score_listing, PACKS
from tantalus_hoard.secondhand.facebook import (Card, FacebookSource, detect_block, extract_cards, extract_group_ref,
                                                extract_post_id, marketplace_card_to_listing, post_card_to_listing,
                                                split_card_text, split_post_text)


# ----------------------------------------------------------------------------- ported: group ref / post id / post text
def test_extract_group_ref():
    assert extract_group_ref("https://www.facebook.com/groups/123456789/") == "123456789"
    assert extract_group_ref("https://www.facebook.com/groups/intercambio.libros.madrid/") == "intercambio.libros.madrid"
    assert extract_group_ref("https://www.facebook.com/groups/123456789?ref=bookmarks") == "123456789"
    assert extract_group_ref("123456789") == "123456789"
    assert extract_group_ref("  https://facebook.com/groups/myslug/  ") == "myslug"
    assert extract_group_ref("") is None and extract_group_ref(None) is None and extract_group_ref("   ") is None


def test_extract_post_id():
    assert extract_post_id("https://www.facebook.com/someuser/posts/987654321") == "987654321"
    assert extract_post_id("https://www.facebook.com/permalink.php?story_fbid=555666777&id=111") == "555666777"
    assert extract_post_id("https://www.facebook.com/groups/123/permalink/456/") == "456"
    assert extract_post_id("https://www.facebook.com/some/other/path") is None


def test_split_post_text():
    price, title, description = split_post_text("Regalo unos 80 libros por mudanza, recoger esta semana")
    assert price == "Regalo" and title == description == "Regalo unos 80 libros por mudanza, recoger esta semana"
    price, title, _ = split_post_text("Vendo 70 libros por 10€ todos juntos, urge")
    assert price == "10€" and "70 libros" in title
    assert split_post_text("Tengo un montón de novelas antiguas en casa")[0] is None
    _, title, description = split_post_text("  Regalo   libros \n\n  de texto  ")
    assert title == description == "Regalo libros de texto"
    _, title, description = split_post_text("a" * 150)
    assert title == "a" * 120 + "…" and len(title) == 121 and description == "a" * 150
    assert split_post_text("") == (None, "", "") and split_post_text("   ") == (None, "", "")


def test_split_card_text_finds_the_price_by_pattern_not_position():
    assert split_card_text(["Lote de 50 libros", "€5", "Móstoles, Madrid"]) == ("€5", "Lote de 50 libros", "Móstoles, Madrid")
    assert split_card_text(["Gratis", "Caja de libros para recoger hoy", "Alcorcón"]) == ("Gratis", "Caja de libros para recoger hoy", "Alcorcón")
    assert split_card_text(["Solo título largo aquí"]) == (None, "Solo título largo aquí", None)
    assert split_card_text(["10 €"]) == ("10 €", "", None)
    assert split_card_text([]) == (None, "", None)


# ----------------------------------------------------------------------------- HTML fixtures
MARKETPLACE_HTML = """
<html><body>
<script>var a = '<a href="/marketplace/item/999/">fake</a>';</script>
<div role="main">
  <a href="/marketplace/item/111222333/?ref=search&referral_code=x">
    <img src="https://scontent/img1.jpg" alt=""><div><span>€5</span></div>
    <div><span>Lote de 50 libros</span></div><div><span>Móstoles, Madrid</span></div>
  </a>
  <a href="/marketplace/item/111222333/?ref=search"><span>Lote de 50 libros</span></a>
  <a href="https://www.facebook.com/marketplace/item/444555666/">
    <span>Gratis</span><span>Regalo biblioteca completa por mudanza</span><span>Alcorcón, Madrid</span>
  </a>
  <a href="/marketplace/category/books">Libros</a>
  <a href="/marketplace/item/777/"><span>Solo precio</span></a>
  <a href="/marketplace/item/888/"></a>
</div></body></html>
"""

POSTS_HTML = """
<div>
  <a href="https://www.facebook.com/groups/12345/posts/987654321/"><span>Regalo unos 80 libros por mudanza</span> <span>recoger en Getafe</span></a>
  <a href="/permalink.php?story_fbid=555&id=1"><span>Vendo 70 libros por 10€</span></a>
  <a href="/marketplace/item/111/">no es un post</a>
</div>
"""


def test_extract_marketplace_cards_by_url_pattern_and_merge_duplicates():
    cards = extract_cards(MARKETPLACE_HTML, "marketplace")
    urls = [c.url for c in cards]
    assert "https://www.facebook.com/marketplace/item/999/" not in urls           # inside <script>
    assert [u.split("/")[5] for u in urls] == ["111222333", "444555666", "777", "888"]
    first = cards[0]
    assert first.lines == ["€5", "Lote de 50 libros", "Móstoles, Madrid"] and first.image_url == "https://scontent/img1.jpg"
    assert cards[1].lines[0] == "Gratis"


def test_marketplace_cards_become_listings():
    cards = extract_cards(MARKETPLACE_HTML, "marketplace")
    listings = [x for x in (marketplace_card_to_listing(c, "libros", origin_text="Móstoles, Madrid") for c in cards) if x]
    assert [l.external_id for l in listings] == ["111222333", "444555666", "777"]   # empty card dropped
    lot, free, no_title = listings
    assert lot.source == "facebook" and lot.surface == "marketplace" and lot.matched_query == "libros"
    assert lot.url == "https://www.facebook.com/marketplace/item/111222333/"          # tracking stripped
    assert lot.title == "Lote de 50 libros" and lot.price == 5.0 and lot.price_raw == "€5" and lot.distance_km == 0.0
    assert free.price == 0.0 and free.location_text == "Alcorcón, Madrid" and 2 <= free.distance_km <= 6
    assert no_title.title == "Solo precio" and no_title.price is None


def test_marketplace_listings_score_with_the_books_pack():
    cards = extract_cards(MARKETPLACE_HTML, "marketplace")
    lot = marketplace_card_to_listing(cards[0], "libros", origin_text="Móstoles, Madrid")
    free = marketplace_card_to_listing(cards[1], "libros", origin_text="Móstoles, Madrid")
    settings = {"origin_location": "Móstoles, Madrid"}
    assert score_listing(lot, PACKS["books_bulk"], settings).relevant
    assert score_listing(free, PACKS["books_bulk"], settings).score >= 20


def test_post_cards_become_listings_without_invented_location():
    cards = extract_cards(POSTS_HTML, "post")
    assert len(cards) == 2
    group_post = post_card_to_listing(cards[0], "libros", surface="group")
    assert group_post.external_id == "987654321" and group_post.surface == "group" and group_post.price_raw == "Regalo"
    assert group_post.price == 0.0 and group_post.location_text is None and "80 libros" in group_post.description
    assert post_card_to_listing(cards[1], "libros").price == 10.0
    assert post_card_to_listing(Card(url="https://x", lines=[]), "q") is None


def test_broken_html_keeps_what_was_read():
    truncated = extract_cards("<a href='/marketplace/item/1/'><span>Título largo</span>", "marketplace")
    assert [c.lines for c in truncated] == [["Título largo"]]
    assert extract_cards("", "marketplace") == [] and extract_cards(None, "post") == []


def test_detect_block_never_tries_to_pass_it():
    assert detect_block("https://www.facebook.com/checkpoint/1234", "", has_cards=False) == "checkpoint"
    assert detect_block("https://www.facebook.com/login/?next=x", "", has_cards=False) == "login"
    assert detect_block("https://www.facebook.com/marketplace/", "<div>Please solve this CAPTCHA</div>", has_cards=False) == "captcha"
    assert detect_block("https://www.facebook.com/marketplace/", "captcha in a script", has_cards=True) == ""
    assert detect_block("https://www.facebook.com/marketplace/", "", has_cards=False) == ""


# ----------------------------------------------------------------------------- source with a fake browser
class FakeMouse:
    def __init__(self):
        self.wheels = 0

    def wheel(self, x, y):
        self.wheels += 1


class FakePage:
    def __init__(self, ctx):
        self.ctx = ctx
        self.url = ""
        self.mouse = FakeMouse()
        self.closed = False

    def goto(self, url, timeout=None):
        self.ctx.visited.append(url)
        self.url = self.ctx.redirect.get(url, url)

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        for key, html in self.ctx.pages.items():
            if key in self.url:
                return html
        return "<html></html>"

    def close(self):
        self.closed = True
        self.ctx.closed_pages += 1


class FakeContext:
    def __init__(self, pages, cookies=("c_user",), redirect=None):
        self.pages = pages
        self._cookies = [{"name": n} for n in cookies]
        self.visited = []
        self.redirect = redirect or {}
        self.closed_pages = 0
        self.page_objects = []

    def cookies(self, url=None):
        return self._cookies

    def new_page(self):
        page = FakePage(self)
        self.page_objects.append(page)
        return page


class FakeFetcher:
    def __init__(self, context=None, error=None):
        self.context, self.error = context, error

    @contextmanager
    def browser_session(self):
        if self.error:
            raise self.error
        yield self.context


def source(context=None, error=None, **kw):
    sleeps = []
    src = FacebookSource(FakeFetcher(context, error), sleep=sleeps.append, uniform=lambda a, b: (a + b) / 2, **kw)
    src.sleeps = sleeps
    return src


def test_ensure_ready_uses_the_c_user_cookie():
    assert source(FakeContext({}, cookies=("c_user", "xs"))).ensure_ready() == (True, "")
    ready, message = source(FakeContext({}, cookies=("datr",))).ensure_ready()
    assert ready is False and "sesión" in message
    ready, message = source(error=TantalusError("needs_human", "El navegador no está disponible", "Instala Playwright")).ensure_ready()
    assert ready is False and "navegador" in message and "Playwright" in message
    assert FacebookSource.login_url == "https://www.facebook.com/login" == source(FakeContext({})).login_url


def test_search_marketplace_returns_listings_and_paces_itself():
    ctx = FakeContext({"marketplace/search": MARKETPLACE_HTML})
    src = source(ctx)
    listings, error = src.search("lote libros", location_text="Móstoles, Madrid", max_price=20, min_price=0, limit=2)
    assert error == "" and [l.external_id for l in listings] == ["111222333", "444555666"]
    url = ctx.visited[0]
    assert url.startswith("https://www.facebook.com/marketplace/search/?") and "query=lote%20libros" in url
    assert "maxPrice=20" in url and "minPrice=0" in url and "sortBy=creation_time_descend" in url
    page = ctx.page_objects[0]
    assert page.mouse.wheels == 4 and page.closed is True
    assert len(src.sleeps) == 1 + 4 and all(s > 0 for s in src.sleeps)          # human pauses: before + between scrolls
    assert listings[0].distance_km == 0.0


def test_search_without_session_needs_a_human():
    listings, error = source(FakeContext({}, cookies=("datr",))).search("libros")
    assert listings == [] and error.startswith("needs_human")


def test_checkpoint_and_captcha_are_reported_never_solved():
    ctx = FakeContext({}, redirect={"https://www.facebook.com/marketplace/search/?query=libros&exact=false&sortBy=creation_time_descend": "https://www.facebook.com/checkpoint/123"})
    listings, error = source(ctx).search("libros")
    assert listings == [] and error.startswith("needs_human") and "checkpoint" in error
    listings, error = source(FakeContext({"marketplace/search": "<p>Complete the CAPTCHA</p>"})).search("libros")
    assert listings == [] and error.startswith("needs_human") and "captcha" in error


def test_no_cards_reports_selector_error_and_saves_debug_html(tmp_path):
    src = source(FakeContext({"marketplace/search": "<html><body>nothing here</body></html>"}), debug_dir=tmp_path)
    listings, error = src.search("libros")
    assert listings == [] and error.startswith("selector")
    assert len(list(tmp_path.glob("fb_*.html"))) == 1


def test_browser_unavailable_is_an_error_string():
    listings, error = source(error=TantalusError("fetch_failed", "sin Chromium")).search("libros")
    assert listings == [] and error.startswith("browser") and "Chromium" in error
    listings, error = source(error=TantalusError("needs_human", "perfil bloqueado")).search("libros")
    assert error.startswith("needs_human")
    listings, error = source(error=RuntimeError("boom")).search("libros")
    assert listings == [] and "boom" in error
    assert source(FakeContext({})).search("  ") == ([], "facebook: empty query")


def test_posts_and_groups_are_optional_and_never_sink_marketplace():
    ctx = FakeContext({"marketplace/search": MARKETPLACE_HTML, "search/posts": POSTS_HTML,
                       "groups/12345/search": POSTS_HTML})
    off, _ = source(ctx).search("libros")
    assert {l.surface for l in off} == {"marketplace"} and len(ctx.visited) == 1

    ctx = FakeContext({"marketplace/search": MARKETPLACE_HTML, "search/posts": POSTS_HTML, "groups/12345/search": POSTS_HTML})
    on, error = source(ctx, search_posts=True, groups=["https://www.facebook.com/groups/12345/", "  "]).search("libros", limit=50)
    assert error == "" and {l.surface for l in on} == {"marketplace", "post", "group"}
    assert any("/groups/12345/search/?q=libros" in u for u in ctx.visited)

    class ExplodingContext(FakeContext):
        def new_page(self):
            page = super().new_page()
            if len(self.page_objects) > 1:
                def boom(url, timeout=None):
                    raise RuntimeError("group is private")
                page.goto = boom
            return page

    ctx = ExplodingContext({"marketplace/search": MARKETPLACE_HTML})
    results, error = source(ctx, search_posts=True, groups=["999"]).search("libros")
    assert len(results) == 3 and error == ""


def test_registry_builds_facebook_with_options():
    src = build_source("facebook", FakeFetcher(FakeContext({})), search_posts=True, groups=["https://facebook.com/groups/abc/"])
    assert isinstance(src, FacebookSource) and src.groups == ["abc"] and src.search_posts is True
