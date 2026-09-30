"""Per-host knowledge about Spanish retailers: what they need, what blocks us, how to read them.

Importable without side effects (a plain table plus lookups); discovery imports ``retailer_for_host`` and
``source_level_for_host``. Facts marked "verified" were measured on 2026-09-30 from a residential connection in
Spain (plain HTTP with a desktop UA, and Playwright + Edge headless).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SiteProfile:
    host: str                                   # registrable host without "www."
    name: str                                   # display name of the retailer / marketplace
    is_retailer: bool = True                    # a first-party shop (or the manufacturer's own store)
    is_marketplace: bool = False                # third-party sellers: a seller name is NOT the retailer
    source_level: int = 1                       # spec 3.3: 1 retailer page ... 4 community ... 5 aggregator
    needs_browser: bool = False                 # plain HTTP gets a JS shell or an interstitial
    browser_paths: tuple[str, ...] = ()         # ... or only these path prefixes need the browser
    blocks_automation: str = ""                 # block seen even with headless Edge: cloudflare | akamai | datadome
    api_adapter: str = ""                       # name of an API adapter in extract/ (e.g. "nvidia")
    first_party_sellers: tuple[str, ...] = ()   # seller names that mean "the retailer itself"
    buy_selectors: tuple[str, ...] = ()         # CSS: an active buy control
    soldout_selectors: tuple[str, ...] = ()     # CSS: a sold-out marker
    price_selectors: tuple[str, ...] = ()       # CSS: the main price node(s)
    seller_selectors: tuple[str, ...] = ()      # CSS: the seller line
    title_selectors: tuple[str, ...] = ()       # CSS: the product title when the first <h1> is not it
    tile_selector: str = ""                     # CSS: one result card on search / listing pages
    product_url: str = ""                       # regex on the URL path(+query) of a product page
    search_url: str = ""                        # regex on the URL path(+query) of a search page
    min_interval_s: float = 20.0                # politeness interval hint for the fetcher
    notes: str = ""


_PROFILES: tuple[SiteProfile, ...] = (
    SiteProfile(
        "game.es", "GAME", first_party_sellers=("game", "game.es"),
        browser_paths=("/buscar",),
        product_url=r"/\d{4,7}/?$", search_url=r"^/buscar",
        tile_selector="div.search-item",
        notes="verified: product/category pages over http 200 with JSON-LD Product (Offer availability, price); "
              "search pages are rendered by JS (need the browser; http only has WebSite/Organization JSON-LD).",
    ),
    SiteProfile(
        "elcorteingles.es", "El Corte Inglés", needs_browser=False, blocks_automation="akamai",
        first_party_sellers=("el corte inglés", "el corte ingles"),
        notes="verified: over plain http with a current desktop User-Agent the search page answers 200 (server-rendered) and "
              "carries the product list in window.__MOONSHINE_STATE__ (see moonshine.py); with an older User-Agent, or in headless "
              "Edge / Chromium, Akamai answers 'Access Denied' 403. The verdict depends on the client fingerprint: keep the http "
              "tier first and treat a 403 as blocked (open_for_human is the manual way in).",
    ),
    SiteProfile(
        "carrefour.es", "Carrefour", needs_browser=True, blocks_automation="cloudflare",
        first_party_sellers=("carrefour",),
        notes="verified: Cloudflare 'Attention Required' 403 over http and headless Edge.",
    ),
    SiteProfile(
        "amazon.es", "Amazon", needs_browser=False, first_party_sellers=("amazon", "amazon.es", "amazon eu sarl", "amazon eu s.à r.l."),
        buy_selectors=("#add-to-cart-button", "#buy-now-button", "input[name='submit.add-to-cart']"),
        soldout_selectors=("#outOfStock",),
        price_selectors=("#corePrice_feature_div .a-offscreen", "#corePrice_desktop .a-offscreen", "#apex_desktop .a-offscreen"),
        seller_selectors=("#merchant-info", "#sellerProfileTriggerId"),
        title_selectors=("#productTitle",),
        tile_selector="div[data-component-type='s-search-result'][data-asin]",
        product_url=r"/(?:dp|gp/product)/[A-Z0-9]{10}", search_url=r"^/s\b",
        notes="verified: plain http gets a tiny Akamai bot-manager interstitial (HTTP 200, 'bm-verify'); headless Edge passes it "
              "after ~9 s and renders the full page. Some product pages answer a 'Seguir comprando' /errors_page/validateCaptcha check.",
    ),
    SiteProfile(
        "pccomponentes.com", "PcComponentes", needs_browser=True, blocks_automation="cloudflare",
        first_party_sellers=("pccomponentes",),
        notes="verified: Cloudflare 'Un momento...' (Turnstile) 403 over http and headless Edge.",
    ),
    SiteProfile(
        "mediamarkt.es", "MediaMarkt", first_party_sellers=("mediamarkt", "mediamarkt.es", "media markt"),
        browser_paths=("/es/search",),
        product_url=r"/es/product/", search_url=r"^/es/search",
        notes="verified: product pages over http 200 with JSON-LD BuyAction->Product->Offer (seller names a marketplace seller when "
              "third-party); search over http has an empty ItemList, headless Edge renders ItemList with price (no availability).",
    ),
    SiteProfile(
        "fnac.es", "Fnac", needs_browser=True, blocks_automation="datadome", first_party_sellers=("fnac",),
        notes="verified: DataDome 403 (x-datadome header, captcha-delivery.com) over http; headless Edge got a "
              "'Fnac.es no está disponible' maintenance page (403).",
    ),
    SiteProfile(
        "xtralife.com", "xtralife", needs_browser=True, first_party_sellers=("xtralife",),
        product_url=r"^/producto/", search_url=r"^/buscar/",
        tile_selector="app-link.search-results-grid-wrapper",
        notes="verified: Angular app — http returns a 5 KB shell; headless Edge renders JSON-LD Product/Offer and "
              "product:price:* / product:availability meta.",
    ),
    SiteProfile(
        "marketplace.nvidia.com", "NVIDIA", api_adapter="nvidia", blocks_automation="akamai",
        first_party_sellers=("www.nvidia.com", "nvidia"), needs_browser=False,
        notes="verified: HTML answers Akamai 'Access Denied' over http and ERR_HTTP2_PROTOCOL_ERROR in headless Edge; "
              "the partners API (api.nvidia.partners/edge/product/search) answers 200 JSON to plain http.",
    ),
    SiteProfile(
        "store.nvidia.com", "NVIDIA", api_adapter="nvidia", blocks_automation="akamai", first_party_sellers=("www.nvidia.com", "nvidia"),
        notes="verified: 3 KB shell over http, ERR_HTTP2_PROTOCOL_ERROR in headless Edge; use the partners API.",
    ),
    SiteProfile(
        "toysrus.es", "Toys R Us", needs_browser=True, blocks_automation="cloudflare", first_party_sellers=("toys r us", "toysrus"),
        notes="verified: Cloudflare 'Just a moment' 403 over http and headless Edge.",
    ),
    SiteProfile(
        "juguettos.com", "Juguettos", first_party_sellers=("juguettos",), search_url=r"buscar|s=",
        notes="verified: home and search 200 over http and headless Edge (PrestaShop, no JSON-LD on listing pages).",
    ),
    SiteProfile(
        "cardmarket.com", "Cardmarket", is_retailer=False, is_marketplace=True, source_level=4, needs_browser=True,
        blocks_automation="cloudflare",
        notes="marketplace of individual sellers (a seller is not the retailer). verified: Cloudflare 403 over http and headless Edge.",
    ),
    SiteProfile(
        "wallapop.com", "Wallapop", is_retailer=False, is_marketplace=True, source_level=4,
        notes="second-hand marketplace; the second-hand sources use its JSON API, not HTML.",
    ),
    SiteProfile(
        "ebay.es", "eBay", is_retailer=False, is_marketplace=True, source_level=4, blocks_automation="akamai",
        notes="marketplace. verified: Akamai 403 'Error Page | eBay' over http and headless Edge.",
    ),
)

SITE_PROFILES: dict[str, SiteProfile] = {p.host: p for p in _PROFILES}
# Extra hosts that belong to one profile.
_ALIASES = {"marketplace.nvidia.com": ("nvidia.com",), "amazon.es": ("amazon.com",), "ebay.es": ("ebay.com", "ebay.co.uk")}


def _host_of(value: str) -> str:
    value = (value or "").strip().lower()
    if "://" in value or value.startswith("//"):
        value = urlsplit(value if "://" in value else "https:" + value).hostname or ""
    value = value.split("/", 1)[0].split(":", 1)[0]
    return value[4:] if value.startswith("www.") else value


def profile_for_host(host_or_url: str) -> Optional[SiteProfile]:
    """The profile of a host or URL (exact host or any subdomain of it); ``None`` when unknown."""
    host = _host_of(host_or_url)
    if not host:
        return None
    for key, profile in SITE_PROFILES.items():
        if host == key or host.endswith("." + key):
            return profile
    for key, aliases in _ALIASES.items():
        if any(host == a or host.endswith("." + a) for a in aliases):
            return SITE_PROFILES[key]
    return None


def retailer_for_host(host_or_url: str) -> Optional[str]:
    """Display name of the retailer behind a host ("GAME", "Amazon"...), ``None`` for unknown hosts.

    Marketplaces answer with their own name too ("Cardmarket"); check ``is_marketplace`` for the difference.
    """
    profile = profile_for_host(host_or_url)
    return profile.name if profile else None


def is_marketplace_host(host_or_url: str) -> bool:
    profile = profile_for_host(host_or_url)
    return bool(profile and profile.is_marketplace)


def source_level_for_host(host_or_url: str, *, page_kind: str = "") -> Optional[int]:
    """Spec 3.3 source level for a known host: 1 retailer page, 2 retailer search / listing page, 4 marketplace /
    community. ``None`` for hosts not in the table (discovery classifies those itself)."""
    profile = profile_for_host(host_or_url)
    if profile is None:
        return None
    if profile.is_retailer and page_kind in ("search", "listing"):
        return 2
    return profile.source_level


def needs_browser(host_or_url: str, path: str = "") -> bool:
    """True when plain HTTP is known not to work for this host (or path prefix)."""
    profile = profile_for_host(host_or_url)
    if profile is None:
        return False
    if profile.needs_browser:
        return True
    if not path and "://" in (host_or_url or ""):
        path = urlsplit(host_or_url).path
    return any(path.startswith(prefix) for prefix in profile.browser_paths)


def is_first_party_seller(seller: str, host_or_url: str) -> Optional[bool]:
    """Is ``seller`` the retailer itself on this host? ``None`` when unknown (no profile / no seller)."""
    profile = profile_for_host(host_or_url)
    if profile is None or not seller:
        return None
    if profile.is_marketplace:
        return False
    name = _key(seller)
    candidates = {_key(c) for c in (profile.name, *profile.first_party_sellers)}
    for candidate in candidates:
        if name == candidate:
            return True
        if candidate and name.startswith(candidate + " "):
            rest = set(name[len(candidate):].split())
            if rest <= _CORPORATE_WORDS:      # "Amazon EU Sarl", "GAME España", but not "Game Over Store"
                return True
    return False


def _key(value: str) -> str:
    value = "".join(c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9. ]", "", value).strip()


_CORPORATE_WORDS = frozenset({"espana", "spain", "es", "sl", "slu", "sa", "eu", "sarl", "com", "inc", "ltd",
                              "europe", "iberia", "online", "tienda", "store", "oficial", "official"})


def kind_of_url(url: str) -> str:
    """'product' | 'search' | '' from the host profile's URL patterns."""
    profile = profile_for_host(url)
    if profile is None:
        return ""
    parts = urlsplit(url)
    path = parts.path + (("?" + parts.query) if parts.query else "")
    if profile.search_url and re.search(profile.search_url, path, re.I):
        return "search"
    if profile.product_url and re.search(profile.product_url, path):
        return "product"
    return ""
