"""Static tables for the mail features: the shops whose mail counts as deals, and who consumes which mail.

Everything here is data, not logic: a shop is a sender domain (every sub-domain counts) and a kind. The user can
pick a subset in Settings and add sender domains of their own (``mail.deals.stores`` / ``mail.deals.domains``).
"""

from __future__ import annotations

from typing import Any, Iterable

GAME, BOOK, MIXED = "game", "book", "retail"

# id, display name, kind, sender domains, wishlist_style: the shop itself mails "an item of your wishlist is on sale".
DEFAULT_STORES: list[dict[str, Any]] = [
    {"id": "steam", "name": "Steam", "kind": GAME, "domains": ["steampowered.com"], "wishlist_style": True},
    {"id": "gog", "name": "GOG", "kind": GAME, "domains": ["gog.com"], "wishlist_style": False},
    {"id": "game", "name": "GAME", "kind": GAME, "domains": ["mail-game.net", "game.es"], "wishlist_style": False},
    {"id": "epic", "name": "Epic Games Store", "kind": GAME, "domains": ["epicgames.com"], "wishlist_style": True},
    {"id": "humble", "name": "Humble", "kind": GAME, "domains": ["humblebundle.com"], "wishlist_style": False},
    {"id": "fanatical", "name": "Fanatical", "kind": GAME, "domains": ["fanatical.com"], "wishlist_style": False},
    {"id": "gmg", "name": "Green Man Gaming", "kind": GAME, "domains": ["greenmangaming.com"], "wishlist_style": False},
    {"id": "igs", "name": "Instant Gaming", "kind": GAME, "domains": ["instant-gaming.com"], "wishlist_style": False},
    {"id": "xtralife", "name": "xtralife", "kind": GAME, "domains": ["xtralife.com"], "wishlist_style": False},
    {"id": "bibliostock", "name": "Bibliostock", "kind": BOOK, "domains": ["bibliostock.com"], "wishlist_style": False},
    {"id": "casadellibro", "name": "Casa del Libro", "kind": BOOK, "domains": ["casadellibro.com"], "wishlist_style": False},
    {"id": "agapea", "name": "Agapea", "kind": BOOK, "domains": ["agapea.com"], "wishlist_style": False},
    {"id": "planeta", "name": "Planeta de Libros", "kind": BOOK, "domains": ["planetadelibros.com"], "wishlist_style": False},
    {"id": "bookdepository", "name": "Book Depository", "kind": BOOK, "domains": ["bookdepository.com"], "wishlist_style": False},
    {"id": "fnac", "name": "Fnac", "kind": MIXED, "domains": ["fnac.es"], "wishlist_style": False},
]
STORE_BY_ID = {s["id"]: s for s in DEFAULT_STORES}


def domain_of(address: str) -> str:
    """Lower-case domain of an e-mail address (or of a bare domain)."""
    return str(address or "").rsplit("@", 1)[-1].strip().lower().strip(".")


def domain_matches(domain: str, suffix: str) -> bool:
    domain, suffix = domain.lower().strip("."), suffix.lower().strip(".")
    return bool(suffix) and (domain == suffix or domain.endswith("." + suffix))


def clean_domains(raw: Any) -> list[str]:
    """``"a.com, b.org"`` or a list -> ``["a.com", "b.org"]`` (lower case, no scheme, no path, no duplicates)."""
    items = raw.replace(";", ",").replace("\n", ",").split(",") if isinstance(raw, str) else list(raw or [])
    out: list[str] = []
    for item in items:
        item = str(item).strip().lower()
        item = item.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1].strip(".")
        if item and "." in item and all(c.isalnum() or c in ".-" for c in item) and item not in out:
            out.append(item)
    return out[:40]


def selected_stores(stores_setting: str = "", extra_domains: Any = "") -> list[dict[str, Any]]:
    """The stores that count: the ids in the setting (empty = every default store) plus the user's own domains."""
    wanted = [s.strip().lower() for s in str(stores_setting or "").replace(";", ",").split(",") if s.strip()]
    chosen = [s for s in DEFAULT_STORES if not wanted or s["id"] in wanted]
    extra = clean_domains(extra_domains)
    known = {d for s in chosen for d in s["domains"]}
    for domain in extra:
        if domain not in known:
            chosen = chosen + [{"id": "custom:" + domain, "name": domain, "kind": MIXED, "domains": [domain], "wishlist_style": False}]
    return chosen


def store_for(address_or_domain: str, stores: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    domain = domain_of(address_or_domain)
    for store in stores:
        if any(domain_matches(domain, d) for d in store["domains"]):
            return store
    return None


def sender_domains(stores: Iterable[dict[str, Any]]) -> list[str]:
    return [d for s in stores for d in s["domains"]]


# ----------------------------------------------------------------------------- who consumes which mail
# A static map, on purpose: the other apps decide for themselves what they read. This only answers "does any Hoard look
# at this sender?" for the noise report. Domain suffixes first, then role words in the local part of the address.
CONSUMER_TANTALUS = "Tantalus (ofertas)"
CONSUMER_LEDGER = "Ledger (pagos)"
CONSUMER_PHILEAS = "Phileas (envíos)"
CONSUMER_KAFKA = "Kafka (papeles)"
CONSUMER_JOBHUNTER = "JobHunter (empleo)"

CONSUMER_DOMAINS: dict[str, list[str]] = {
    CONSUMER_LEDGER: [
        "paypal.com", "paypal.es", "stripe.com", "revolut.com", "wise.com", "n26.com", "bbva.es", "bbva.com", "santander.es", "bancosantander.es",
        "caixabank.es", "ing.es", "openbank.es", "bankinter.com", "sabadell.com", "bizum.es", "netflix.com", "spotify.com", "disneyplus.com",
        "hbomax.com", "max.com", "github.com", "dropbox.com", "adobe.com", "notion.so", "canva.com", "figma.com", "cloudflare.com",
        "supabase.com", "squarespace.com", "whois.com", "digimobil.es", "repsol.com", "okx.com",
    ],
    CONSUMER_PHILEAS: [
        "ups.com", "dhl.com", "dhl.de", "correos.es", "inpost.es", "seur.com", "gls-spain.es", "gls-group.eu", "gls-group.com", "mrw.es", "nacex.es",
        "cttexpress.com", "correosexpress.com", "paack.co", "zeleris.com", "cainiao.com", "dpd.com", "dpd.es", "fedex.com", "tnt.com", "postnl.nl",
        "temuemail.com", "temu.com", "aliexpress.com", "pccomponentes.com", "wallapop.com", "vinted.es", "ebay.com", "ebay.es", "miravia.es",
    ],
    CONSUMER_KAFKA: [
        "agenciatributaria.gob.es", "seg-social.es", "dgt.es", "sepe.gob.es", "gob.es", "mapfre.es", "lineadirecta.com", "iberdrola.es",
        "endesa.com", "naturgy.es", "holaluz.com", "movistar.es", "vodafone.es", "orange.es", "masmovil.es", "unir.net",
    ],
    CONSUMER_JOBHUNTER: [
        "jobs2web.com", "tecnoempleo.com", "infojobs.net", "indeed.com", "glassdoor.com", "workablemail.com", "lever.co", "greenhouse.io",
        "ashbyhq.com", "remotehunter.com", "welcometothejungle.com", "getmanfred.com",
    ],
}
CONSUMER_LOCAL_WORDS: dict[str, tuple[str, ...]] = {
    CONSUMER_LEDGER: ("factura", "invoice", "receipt", "billing", "payment", "pagos", "recibo"),
    CONSUMER_PHILEAS: ("tracking", "shipping", "shipment", "envio", "delivery", "pedido", "orders", "order-update", "seguimiento"),
    CONSUMER_JOBHUNTER: ("jobs", "job-alert", "jobalert", "careers", "talent", "recruit", "hiring"),
    CONSUMER_KAFKA: ("notificaciones", "sede", "tramites", "gestiones"),
}


def consumers_for(domain: str, local_parts: Iterable[str] = (), stores: Iterable[dict[str, Any]] = ()) -> list[str]:
    """Names of the Hoards that read mail from this sender domain; empty when none is known."""
    domain = domain.lower()
    out: list[str] = []
    if store_for(domain, stores):
        out.append(CONSUMER_TANTALUS)
    for name, suffixes in CONSUMER_DOMAINS.items():
        if any(domain_matches(domain, s) for s in suffixes) and name not in out:
            out.append(name)
    locals_ = [str(p).lower() for p in local_parts]
    for name, words in CONSUMER_LOCAL_WORDS.items():
        if name not in out and any(w in p for p in locals_ for w in words):
            out.append(name)
    return out
