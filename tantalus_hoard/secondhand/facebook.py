"""Facebook Marketplace (and, optionally, posts and groups), ported from Radar de Libros.

Deliberate design, kept from the original:

* the browser session is the app's persistent profile (``fetcher.browser_session()``); the login is ALWAYS
  manual (the app never sees or stores credentials). "Logged in" means the ``c_user`` cookie exists;
* no CAPTCHA solving, no security bypass: a login wall, checkpoint or CAPTCHA is reported as ``needs_human``;
* results are found by URL pattern (``/marketplace/item/<id>/``, ``/posts/``, ``/permalink/``...), never by the
  generated CSS classes that change with every Facebook deployment;
* random human-like pauses between actions;
* the location used by Marketplace is the one set in the user's own Facebook account; ``radius_km`` and
  ``location_text`` are only used for our own distance estimate;
* posts and groups are optional (off by default) and more fragile than Marketplace: a failure there never
  discards what Marketplace returned.

Parsing is pure: the page HTML is parsed with the standard library (``extract_cards``), so it is unit-tested
with small HTML fixtures and needs no live DOM selectors. The Playwright API used is the synchronous one.
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional

from ..errors import TantalusError
from ..model import RawListing
from .distance import estimate_distance_km
from .price import normalize_price

log = logging.getLogger("tantalus.secondhand.facebook")

FACEBOOK_URL = "https://www.facebook.com"
LOGIN_URL = "https://www.facebook.com/login"

MARKETPLACE_ITEM_URL_RE = re.compile(r"/marketplace/item/(\d+)")
# Wider net than Marketplace: Facebook has no single stable "post permalink" pattern.
POST_LINK_RE = re.compile(r"/posts/|/permalink/|story_fbid=|/groups/[^/?#]+/posts/")
_PRICE_LINE_RE = re.compile(r"(gratis|regalo|\d[\d.,]*\s*€|€\s*\d[\d.,]*)", re.IGNORECASE)
_GROUP_REF_RE = re.compile(r"facebook\.com/groups/([^/?#]+)", re.IGNORECASE)
_POST_ID_PATTERNS = [
    re.compile(r"/posts/(\d+)"),
    re.compile(r"/permalink/(\d+)"),
    re.compile(r"story_fbid=(\d+)"),
    re.compile(r"fbid=(\d+)"),
]

# Human-like pauses (seconds): ranges, not fixed values.
PAUSE_BEFORE_SEARCH = (2.0, 5.0)
PAUSE_BETWEEN_SCROLLS = (1.5, 3.5)
MAX_SCROLLS = 4
PAGE_LOAD_TIMEOUT_MS = 20_000
MAX_POST_TITLE_CHARS = 120

SORT_NEWEST = "creation_time_descend"


# ----------------------------------------------------------------------------------------------- pure parsing
@dataclass
class Card:
    """What the result page shows for one link: its URL, the visible text lines and the first image."""

    url: str
    lines: list[str] = field(default_factory=list)
    image_url: Optional[str] = None


class _CardParser(HTMLParser):
    """Collect ``<a href>`` elements whose href matches a pattern, with their text lines and first ``<img>``."""

    def __init__(self, link_re: "re.Pattern[str]") -> None:
        super().__init__(convert_charrefs=True)
        self.link_re = link_re
        self.cards: list[Card] = []
        self._current: Optional[Card] = None
        self._depth = 0  # nested <a> inside a card (invalid HTML, but Facebook does it)
        self._skip = 0   # inside <script>/<style>

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            if self._current is not None:
                self._depth += 1
                return
            href = attributes.get("href") or ""
            if href and self.link_re.search(href):
                self._current = Card(url=urllib.parse.urljoin(FACEBOOK_URL, href))
        elif tag == "img" and self._current is not None and self._current.image_url is None:
            src = attributes.get("src") or attributes.get("data-src")
            if src:
                self._current.image_url = src

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "a" and self._current is not None:
            if self._depth:
                self._depth -= 1
                return
            self.cards.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is None or self._skip:
            return
        for line in data.split("\n"):
            line = " ".join(line.split())
            if line:
                self._current.lines.append(line)


def extract_cards(html: str, kind: str = "marketplace") -> list[Card]:
    """Cards of a result page. ``kind``: ``marketplace`` (item links) or ``post`` (posts / permalinks).

    Cards with the same URL are merged (Facebook renders the same link twice: image and text).
    """
    parser = _CardParser(MARKETPLACE_ITEM_URL_RE if kind == "marketplace" else POST_LINK_RE)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as error:  # noqa: BLE001 — broken markup: keep what was read
        log.info("facebook: html parse stopped early: %s", error)
    if parser._current is not None and parser._current.lines:  # truncated page: keep the card being read
        parser.cards.append(parser._current)
    merged: dict[str, Card] = {}
    for card in parser.cards:
        key = card.url.split("?", 1)[0] if kind == "marketplace" else card.url
        known = merged.get(key)
        if known is None:
            merged[key] = card
            continue
        for line in card.lines:
            if line not in known.lines:
                known.lines.append(line)
        known.image_url = known.image_url or card.image_url
    return list(merged.values())


def split_card_text(lines: list[str]) -> tuple[Optional[str], str, Optional[str]]:
    """Heuristically split a Marketplace card into ``(price_raw, title, location)``.

    Facebook does not expose these fields separately in the result list and the order of the lines has changed
    between versions. The price line is found by pattern (not by position); of the remaining lines the longest
    is the title and the last of the others is usually the location.
    """
    remaining = list(lines)
    price_raw = None
    for line in remaining:
        if _PRICE_LINE_RE.fullmatch(line.strip()):
            price_raw = line.strip()
            remaining.remove(line)
            break
    if not remaining:
        return price_raw, "", None
    title = max(remaining, key=len)
    others = [line for line in remaining if line != title]
    return price_raw, title, (others[-1] if others else None)


def split_post_text(text: str) -> tuple[Optional[str], str, str]:
    """Split a post into ``(price_raw, synthetic_title, description)``.

    A post has no title of its own: the start of the text (truncated) is the title and the full text is the
    description. The price is searched anywhere in the text because posts mix it into sentences.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None, "", ""
    match = _PRICE_LINE_RE.search(cleaned)
    price_raw = match.group(0) if match else None
    title = cleaned if len(cleaned) <= MAX_POST_TITLE_CHARS else cleaned[:MAX_POST_TITLE_CHARS].rstrip() + "…"
    return price_raw, title, cleaned


def extract_group_ref(raw: Optional[str]) -> Optional[str]:
    """Group id or slug from a pasted URL or a bare id; None when nothing usable."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    match = _GROUP_REF_RE.search(raw)
    if match:
        return match.group(1)
    return raw.strip("/ ") or None


def extract_post_id(url: str) -> Optional[str]:
    """Stable numeric id of a post URL (several known patterns, tried in order), or None."""
    for pattern in _POST_ID_PATTERNS:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return None


def _with_price(listing: RawListing, price_raw: Optional[str]) -> RawListing:
    info = normalize_price(price_raw)
    listing.price_raw = price_raw
    listing.price = info.price_eur
    return listing


def marketplace_card_to_listing(card: Card, query: str, *, origin_text: Optional[str] = None) -> Optional[RawListing]:
    if not card.lines:
        return None
    price_raw, title, location = split_card_text(card.lines)
    if not title:
        return None
    match = MARKETPLACE_ITEM_URL_RE.search(card.url)
    listing = RawListing(
        source="facebook", url=card.url.split("?", 1)[0], title=title, external_id=match.group(1) if match else None,
        description=None,  # the result list has no full description
        location_text=location, image_url=card.image_url, matched_query=query, surface="marketplace",
    )
    _with_price(listing, price_raw)
    if origin_text and location:
        listing.distance_km = estimate_distance_km(origin_text, location)
    return listing


def post_card_to_listing(card: Card, query: str, *, surface: str = "post") -> Optional[RawListing]:
    text = " ".join(card.lines).strip()
    if not text:
        return None
    price_raw, title, description = split_post_text(text)
    if not title:
        return None
    listing = RawListing(
        source="facebook", url=card.url, title=title, external_id=extract_post_id(card.url), description=description,
        location_text=None,  # not reliable in posts without opening each one: never invented
        image_url=card.image_url, matched_query=query, surface=surface,
    )
    return _with_price(listing, price_raw)


def detect_block(url: str, html: str, *, has_cards: bool) -> str:
    """``"login"``, ``"checkpoint"``, ``"captcha"`` or ``""``. Never tries to get past any of them."""
    lowered = (url or "").lower()
    if "/checkpoint" in lowered:
        return "checkpoint"
    if "/login" in lowered or "login.php" in lowered:
        return "login"
    if not has_cards and re.search(r"captcha|security check|confirma que eres una persona", html or "", re.I):
        return "captcha"
    return ""


# ----------------------------------------------------------------------------------------------- source
class FacebookSource:
    """``FacebookSource(fetcher).search(query, ...)`` -> ``(listings, error)``; error is empty on success.

    Error strings start with a code: ``needs_human`` (login / checkpoint / captcha), ``browser`` (the browser rung
    is unavailable) or ``selector`` (no recognisable card: markup changed or a verification screen).
    """

    name = "facebook"
    login_url = LOGIN_URL

    def __init__(self, fetcher: Any, *, groups: Optional[list[str]] = None, search_posts: bool = False,
                 max_scrolls: int = MAX_SCROLLS, sleep: Callable[[float], None] = time.sleep,
                 uniform: Callable[[float, float], float] = random.uniform, debug_dir: Optional[Path] = None) -> None:
        self.fetcher = fetcher
        self.groups = [g for g in (extract_group_ref(x) for x in (groups or [])) if g]
        self.search_posts = search_posts
        self.max_scrolls = max_scrolls
        self._sleep = sleep
        self._uniform = uniform
        self.debug_dir = debug_dir

    # ---- session
    @staticmethod
    def logged_in(context: Any) -> bool:
        try:
            cookies = context.cookies(FACEBOOK_URL)
        except Exception:  # noqa: BLE001
            return False
        return any(c.get("name") == "c_user" for c in cookies or [])

    def ensure_ready(self) -> tuple[bool, str]:
        """``(ready, message)``. Not ready explains what the user has to do (log in by hand once)."""
        try:
            with self.fetcher.browser_session() as context:
                if self.logged_in(context):
                    return True, ""
        except TantalusError as exc:
            return False, f"{exc.message}" + (f" {exc.hint}" if exc.hint else "")
        except Exception as exc:  # noqa: BLE001
            return False, f"No se pudo comprobar la sesión de Facebook: {exc}"
        return False, ("No hay sesión de Facebook iniciada en el perfil del navegador. Inicia sesión a mano una vez "
                       "(botón «Iniciar sesión en Facebook») y vuelve a intentarlo.")

    # ---- search
    def search(self, query: str, *, latitude: Optional[float] = None, longitude: Optional[float] = None,
               location_text: Optional[str] = None, radius_km: float = 30, max_price: Optional[float] = None,
               min_price: Optional[float] = None, limit: int = 40, order_by: str = "newest"
               ) -> tuple[list[RawListing], str]:
        query = (query or "").strip()
        if not query:
            return [], "facebook: empty query"
        try:
            with self.fetcher.browser_session() as context:
                if not self.logged_in(context):
                    return [], "needs_human: no hay sesión de Facebook iniciada (inicia sesión a mano una vez)"
                results, errors = self._search_all(context, query, location_text, max_price, min_price, order_by)
        except TantalusError as exc:
            code = "needs_human" if exc.code == "needs_human" else "browser"
            return [], f"{code}: {exc.message}"
        except Exception as exc:  # noqa: BLE001 — sources never raise for expected failures
            return [], f"facebook: {type(exc).__name__}: {exc}"
        return results[: max(1, int(limit or 40))], "; ".join(errors)

    def marketplace_url(self, query: str, *, max_price: Optional[float] = None, min_price: Optional[float] = None,
                        order_by: str = "newest") -> str:
        params: dict[str, Any] = {"query": query, "exact": "false"}
        if min_price is not None:
            params["minPrice"] = int(min_price)
        if max_price is not None:
            params["maxPrice"] = int(max_price)
        if order_by == "newest":
            params["sortBy"] = SORT_NEWEST
        return f"{FACEBOOK_URL}/marketplace/search/?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)

    def _search_all(self, context: Any, query: str, origin_text: Optional[str], max_price: Optional[float],
                    min_price: Optional[float], order_by: str) -> tuple[list[RawListing], list[str]]:
        errors: list[str] = []
        url = self.marketplace_url(query, max_price=max_price, min_price=min_price, order_by=order_by)
        cards, error = self._load_cards(context, url, "marketplace", f"marketplace_{query}")
        results = [x for x in (self._safe(marketplace_card_to_listing, c, query, origin_text=origin_text) for c in cards) if x]
        if error and not results:
            errors.append(error)

        if self.search_posts:
            posts_url = f"{FACEBOOK_URL}/search/posts/?q=" + urllib.parse.quote(query)
            try:
                cards, err = self._load_cards(context, posts_url, "post", f"posts_{query}")
                results += [x for x in (self._safe(post_card_to_listing, c, query, surface="post") for c in cards) if x]
                if err and "selector" not in err:
                    errors.append(err)
            except Exception as exc:  # noqa: BLE001 — optional surface, never sinks Marketplace
                log.warning("facebook posts search failed (%r): %s", query, exc)
        for group in self.groups:
            group_url = f"{FACEBOOK_URL}/groups/{urllib.parse.quote(group, safe='')}/search/?q=" + urllib.parse.quote(query)
            try:
                cards, err = self._load_cards(context, group_url, "post", f"group_{group}_{query}")
                results += [x for x in (self._safe(post_card_to_listing, c, query, surface="group") for c in cards) if x]
                if err and "selector" not in err:
                    errors.append(err)
            except Exception as exc:  # noqa: BLE001 — a private or broken group never sinks the rest
                log.warning("facebook group %r search failed (%r): %s", group, query, exc)
        return results, errors

    @staticmethod
    def _safe(fn: Callable[..., Optional[RawListing]], *args: Any, **kwargs: Any) -> Optional[RawListing]:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — one broken card must not lose the rest
            log.info("facebook: skipped a card: %s", exc)
            return None

    def _pause(self, bounds: tuple[float, float]) -> None:
        self._sleep(self._uniform(*bounds))

    def _load_cards(self, context: Any, url: str, kind: str, label: str) -> tuple[list[Card], str]:
        """Open a result page, scroll like a person would, read the cards. Returns ``(cards, error)``."""
        self._pause(PAUSE_BEFORE_SEARCH)
        page = context.new_page()
        try:
            page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS)
            page.wait_for_timeout(2000)
            for _ in range(self.max_scrolls):
                page.mouse.wheel(0, 1800)
                self._pause(PAUSE_BETWEEN_SCROLLS)
            html = page.content()
            cards = extract_cards(html, kind)
            blocked = detect_block(getattr(page, "url", "") or "", html, has_cards=bool(cards))
            if blocked:
                return [], f"needs_human: Facebook pide {blocked} (hazlo a mano en el navegador de la app)"
            if not cards:
                self._save_debug(html, label)
                return [], ("selector: no se reconoce ninguna tarjeta en la página de Facebook (¿cambió el marcado o "
                            "hay una pantalla de verificación?)")
            return cards, ""
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    def _save_debug(self, html: str, label: str) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:50]
            (self.debug_dir / f"fb_{int(time.time())}_{slug}.html").write_text(html or "", encoding="utf-8")
        except OSError:
            pass
