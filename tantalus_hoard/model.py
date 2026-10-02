"""Shared vocabulary: states, event types, and the plain dataclasses every module passes around.

This file is the contract between the fetch ladder, the extractors, the rule engine, the second-hand
sources, the information sentries, discovery and the notifiers. Nothing here touches the network or
the database.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .hoard_link.web.fetch import FetchResult  # noqa: F401  (the commons' result type, re-exported for the app)

# ----------------------------------------------------------------------------- availability states
IN_STOCK = "IN_STOCK"                    # can be bought now
LOCAL_PICKUP = "LOCAL_PICKUP"            # pickup available at a target store
PREORDER = "PREORDER"                    # reservation / pre-order open
RESTOCK_SCHEDULED = "RESTOCK_SCHEDULED"  # future restock with a reliable date
COMING_SOON = "COMING_SOON"              # announced but not on sale yet ("Próximamente", "coming soon")
OUT_OF_STOCK = "OUT_OF_STOCK"            # explicitly sold out
UNAVAILABLE_REGION = "UNAVAILABLE_REGION"  # exists but not sold / shipped to the region
MARKETPLACE_ONLY = "MARKETPLACE_ONLY"    # only third-party sellers / resale
UNKNOWN = "UNKNOWN"                      # cannot be determined safely

STATES = (IN_STOCK, LOCAL_PICKUP, PREORDER, RESTOCK_SCHEDULED, COMING_SOON, OUT_OF_STOCK, UNAVAILABLE_REGION, MARKETPLACE_ONLY, UNKNOWN)
BUYABLE = frozenset({IN_STOCK, LOCAL_PICKUP, PREORDER})
NOT_BUYABLE = frozenset({OUT_OF_STOCK, UNAVAILABLE_REGION, MARKETPLACE_ONLY, UNKNOWN, RESTOCK_SCHEDULED, COMING_SOON})

# ----------------------------------------------------------------------------- event types
RESTOCK = "RESTOCK"
LOCAL_RESTOCK = "LOCAL_RESTOCK"
PREORDER_OPEN = "PREORDER_OPEN"
PRICE_DROP = "PRICE_DROP"
PRICE_THRESHOLD_CROSSED = "PRICE_THRESHOLD_CROSSED"
NEW_SKU = "NEW_SKU"
RESTOCK_DATE_CONFIRMED = "RESTOCK_DATE_CONFIRMED"
SOLD_OUT = "SOLD_OUT"
NEW_LISTING = "NEW_LISTING"          # second-hand: a listing that passes the pack's score
LISTING_PRICE_DROP = "LISTING_PRICE_DROP"
INFO_CHANGE = "INFO_CHANGE"          # information sentry: material news
CANDIDATE_FOUND = "CANDIDATE_FOUND"  # discovery: a new URL proposed as target
NEEDS_HUMAN = "NEEDS_HUMAN"          # a target is blocked behind CAPTCHA / login
MAIL_DEAL = "MAIL_DEAL"              # a sale mail from a game store / book retailer that matches a wishlist or a watch
SALE_OPEN = "SALE_OPEN"              # a product that said "Próximamente" can now be bought or reserved
RELEASE = "RELEASE"                  # radar: a matching release enters the calendar, is near, or comes out today (with where to buy)

EVENT_TYPES = (RESTOCK, LOCAL_RESTOCK, PREORDER_OPEN, PRICE_DROP, PRICE_THRESHOLD_CROSSED, NEW_SKU,
               RESTOCK_DATE_CONFIRMED, SOLD_OUT, NEW_LISTING, LISTING_PRICE_DROP, INFO_CHANGE, CANDIDATE_FOUND, NEEDS_HUMAN, MAIL_DEAL, RELEASE, SALE_OPEN)

# Event statuses: pending (waiting revalidation) -> confirmed (alert-worthy) | logged (recorded, below threshold)
# | dismissed (by the user or by a failed revalidation).
EVENT_STATUSES = ("pending", "confirmed", "logged", "dismissed")

# ----------------------------------------------------------------------------- watcher modes
MODE_AVAILABILITY = "availability"   # stock / price of concrete products at concrete retailers
MODE_SECONDHAND = "secondhand"       # Wallapop / Facebook Marketplace searches, scored by a pack
MODE_INFORMATION = "information"     # change-intelligence: official pages, feeds, searches
MODES = (MODE_AVAILABILITY, MODE_SECONDHAND, MODE_INFORMATION)

# Seller policies (availability mode)
SELLER_RETAIL_ONLY = "retail_only"
SELLER_RETAIL_PLUS_MARKETPLACE = "retail_plus_marketplace"
SELLER_ANY_BELOW = "any_below"       # any seller, as long as the price is under price_ceiling
SELLER_POLICIES = (SELLER_RETAIL_ONLY, SELLER_RETAIL_PLUS_MARKETPLACE, SELLER_ANY_BELOW)

# Source levels (spec §3.3): 1 retailer page / inventory endpoint, 2 retailer search / category, 3 official
# announcement, 4 community, 5 aggregator / cache / search snippet.
SOURCE_LEVELS = {1: "retailer", 2: "retailer_search", 3: "official_announcement", 4: "community", 5: "aggregator"}

# Target statuses
TARGET_STATUSES = ("active", "paused", "needs_human", "error")
# Candidate statuses
CANDIDATE_STATUSES = ("proposed", "accepted", "rejected")


# ----------------------------------------------------------------------------- dataclasses
@dataclass
class Offer:
    """One product offer found on a page (a product page yields one; a list / search page may yield many)."""

    availability: str = UNKNOWN
    price: Optional[float] = None
    currency: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    sku: Optional[str] = None
    ean: Optional[str] = None
    brand: Optional[str] = None
    image: Optional[str] = None
    seller: Optional[str] = None
    seller_is_retailer: Optional[bool] = None   # None = unknown
    buy_button: Optional[bool] = None           # an active add-to-cart / buy / reserve control was seen
    preorder_date: Optional[str] = None         # ISO date when known
    restock_date: Optional[str] = None          # ISO date when a dated restock is announced
    store_availability: dict[str, str] = field(default_factory=dict)  # store_id/name -> state
    evidence: list[str] = field(default_factory=list)  # short literal snippets that justify the fields
    method: str = ""                            # jsonld | microdata | opengraph | api:<name> | site:<host> | phrases | llm
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Extraction:
    """Result of running the extractors over one FetchResult."""

    offers: list[Offer] = field(default_factory=list)
    page_kind: str = "unknown"      # product | listing | search | article | blocked | unknown
    title: str = ""
    text_excerpt: str = ""          # readable text (for info sentries / LLM / evidence), <= 20k chars
    content_hash: str = ""          # sha256 of the normalised readable text (quality-gated)
    quality_ok: bool = True         # False when the extraction is degenerate (nav-only, empty, blocked)
    methods: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def primary(self) -> Optional[Offer]:
        return self.offers[0] if self.offers else None


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""
    rank: int = 0
    published: Optional[str] = None


@dataclass
class RawListing:
    """A second-hand listing as a source returns it (before scoring). Mirrors Radar de Libros."""

    source: str
    url: str
    title: str
    external_id: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    price_raw: Optional[str] = None
    currency: Optional[str] = "EUR"
    location_text: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    distance_km: Optional[float] = None
    listing_date: Optional[str] = None
    image_url: Optional[str] = None
    seller_name: Optional[str] = None
    shipping: Optional[bool] = None
    reserved: Optional[bool] = None
    matched_query: Optional[str] = None
    surface: str = "marketplace"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoreSignal:
    key: str
    points: float
    label: str


@dataclass
class ListingScore:
    relevant: bool
    score: float
    signals: list[ScoreSignal] = field(default_factory=list)
    reason: str = ""
    category: str = ""
    method: str = "rules"            # rules | rules+llm
    quantity_min: Optional[int] = None
    quantity_max: Optional[int] = None
    distance_km: Optional[float] = None


@dataclass
class InfoFinding:
    """One piece of news an information sentry found."""

    url: str
    title: str
    snippet: str = ""
    kind: str = "search_hit"        # page_change | feed_item | search_hit
    published: Optional[str] = None
    content_hash: str = ""
    diff: str = ""                  # for page_change: short +/- summary
    verdict: str = "unknown"        # confirmed | leak | estimate | irrelevant | unknown
    material: bool = False
    score: float = 0.0
    reason: str = ""
    source_level: int = 5
    method: str = "rules"
    publisher: str = ""             # the publisher's site when the URL is a news-aggregator redirect
