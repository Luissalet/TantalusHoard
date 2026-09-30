"""Spanish / English phrase rules over the visible text and the page's buttons, plus price parsing.

The rules are deliberately conservative: a control (button, submit input, ``role=button``, a link styled as a
button) is judged by its own short text and by whether it is disabled or hidden; free text is only trusted
inside a window that starts at the page's ``<h1>`` (the product headline area) and inside elements whose
id / class talks about availability. Sold-out badges on *other* products of a "you may also like" strip are
therefore not mistaken for the product's own state.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup, Tag

from ..model import IN_STOCK, MARKETPLACE_ONLY, OUT_OF_STOCK, PREORDER, RESTOCK_SCHEDULED, UNKNOWN
from .text import _is_hidden

EVIDENCE_MAX = 160
WINDOW_CHARS = 1600        # text after the <h1> in which free-text availability phrases are trusted
PRICE_WINDOW_CHARS = 500   # ... and where a bare price is accepted


# ---------------------------------------------------------------------------------------------- normalising
def norm(text: str) -> str:
    """Lower-case, accent-free, single-spaced — used for every phrase comparison."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace("\xa0", " ")
    text = re.sub(r"[​‌﻿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def snippet(text: str, limit: int = EVIDENCE_MAX) -> str:
    text = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------------------------- numbers
def parse_number(value: Any) -> Optional[float]:
    """'1.234,56' -> 1234.56, '1,234.56' -> 1234.56, '59,99' -> 59.99, '499.99' -> 499.99, 12 -> 12.0."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d.,\-]", "", str(value).replace("\xa0", " ").replace(" ", ""))
    text = text.lstrip("-") if text.count("-") > 1 else text
    if not re.search(r"\d", text):
        return None
    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:  # both present: the right-most one is the decimal mark
        decimal, thousands = ("," if comma > dot else "."), ("." if comma > dot else ",")
        text = text.replace(thousands, "").replace(decimal, ".")
    elif comma >= 0 or dot >= 0:
        mark = "," if comma >= 0 else "."
        pieces = text.split(mark)
        if len(pieces) > 2:                              # 1.234.567 -> thousands
            text = "".join(pieces)
        elif len(pieces[-1]) == 3 and len(pieces[0]) <= 3 and pieces[0] not in ("", "0"):
            text = "".join(pieces)                       # 1.234 / 2,099 -> thousands
        else:
            text = pieces[0] + "." + pieces[1]           # 59,99 / 499.99 -> decimal
    try:
        return float(text)
    except ValueError:
        return None


_CUR = r"(?:€|eur\b|euros?\b|\$|usd\b|£|gbp\b)"
_NUM = r"\d{1,3}(?:[.\s\u00a0]\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_PRICE_SUFFIX = re.compile(rf"(?<![\w.,])(?P<num>{_NUM})\s?(?P<cur>{_CUR})", re.I)
_PRICE_PREFIX = re.compile(rf"(?P<cur>{_CUR})\s?(?P<num>{_NUM})(?![\w])", re.I)
_CURRENCY = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP"}


@dataclass
class PriceHit:
    value: float
    currency: str
    start: int
    end: int
    raw: str


def find_prices(text: str) -> list[PriceHit]:
    """Every price-looking amount in ``text`` in reading order ('1.234,56 €', '€1,234.56', '59,99€')."""
    hits: dict[int, PriceHit] = {}
    for regex in (_PRICE_SUFFIX, _PRICE_PREFIX):
        for match in regex.finditer(text or ""):
            value = parse_number(match.group("num"))
            if value is None or value <= 0:
                continue
            currency = _CURRENCY.get(match.group("cur").lower(), "EUR")
            hit = PriceHit(value, currency, match.start(), match.end(), match.group(0))
            # a suffix and a prefix match can overlap ("$5 EUR"); keep the first found per start
            hits.setdefault(match.start(), hit)
    ordered = sorted(hits.values(), key=lambda h: h.start)
    cleaned: list[PriceHit] = []
    for hit in ordered:
        if cleaned and hit.start < cleaned[-1].end:
            continue
        cleaned.append(hit)
    return cleaned


def parse_price(text: str) -> Optional[tuple[float, str]]:
    """First price in ``text`` as ``(amount, currency)``."""
    hits = find_prices(text)
    return (hits[0].value, hits[0].currency) if hits else None


_STANDALONE = re.compile(rf"^\W{{0,3}}(?:desde|from|ahora|ahora solo|only|precio|price)?\W{{0,3}}(?:{_NUM})\s?{_CUR}\W{{0,3}}$|^\W{{0,3}}{_CUR}\s?(?:{_NUM})\W{{0,3}}$", re.I)


def standalone_price(text: str) -> Optional[tuple[float, str]]:
    """The price of a text node that is *only* a price ('59,99 €', 'Desde 12,99€'); ``None`` for prose."""
    text = (text or "").strip()
    if len(text) > 32 or not _STANDALONE.match(text):
        return None
    return parse_price(text)


# ---------------------------------------------------------------------------------------------- dates
_MONTHS = {
    "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3, "abril": 4, "abr": 4, "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9, "setiembre": 9, "sep": 9,
    "sept": 9, "octubre": 10, "oct": 10, "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12, "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b"), "dmy"),
    (re.compile(rf"\b(\d{{1,2}})\s+(?:de\s+)?({_MONTH_RE})\.?(?:\s+(?:de\s+)?(\d{{4}}))?\b", re.I), "d_mon"),
    (re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "mon_d"),
]


def parse_date(text: str, *, today: Optional[date] = None) -> Optional[str]:
    """First date in ``text`` as ISO (dd/mm/yyyy, '15 de octubre de 2026', 'October 15, 2026', ISO).

    A date without a year takes the next occurrence relative to ``today``.
    """
    today = today or date.today()
    best: Optional[tuple[int, str]] = None
    for regex, kind in _DATE_PATTERNS:
        match = regex.search(text or "")
        if not match:
            continue
        try:
            if kind == "ymd":
                y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif kind == "dmy":
                d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif kind == "d_mon":
                d, m = int(match.group(1)), _MONTHS[match.group(2).lower()]
                y = int(match.group(3)) if match.group(3) else today.year
                if not match.group(3) and date(y, m, d) < today:
                    y += 1
            else:
                m, d, y = _MONTHS[match.group(1).lower()], int(match.group(2)), int(match.group(3))
            iso = date(y, m, d).isoformat()
        except (ValueError, KeyError):
            continue
        if best is None or match.start() < best[0]:
            best = (match.start(), iso)
    return best[1] if best else None


# ---------------------------------------------------------------------------------------------- phrase tables (normalised)
BUY = ("anadir al carrito", "anadir a la cesta", "anadir a mi cesta", "anadir a cesta", "anadir al carro", "agregar al carrito",
       "comprar ahora", "comprar ya", "comprar ", "add to cart", "add to basket", "add to bag", "buy now", "buy it now",
       "anadir a la bolsa")
PREORDER_PH = ("reservar", "reserva ya", "reserva ahora", "reservalo", "preventa", "pre-order", "preorder", "pre order",
               "precompra", "pre-reserva", "prereserva", "pre reserva")
SOLDOUT = ("agotado", "agotada", "agotados", "sin stock", "no disponible", "out of stock", "sold out", "currently unavailable",
           "temporalmente agotado", "actualmente no disponible", "sin existencias", "no hay stock", "fuera de stock",
           "unavailable")
NOTIFY = ("avisame", "avisarme", "avisadme", "notify me", "notifica me", "notificame", "email me when",
          "notify when", "recibir aviso", "avisame cuando")
PICKUP = ("recogida en tienda", "recoger hoy", "recoger en tienda", "disponible en tienda", "disponible para recoger",
          "click & collect", "click and collect", "clic y recoger", "clic & recoger", "recogida gratuita en tienda")
MARKETPLACE_ONLY_PH = ("otras opciones de compra", "ver todas las opciones de compra", "see all buying options",
                       "other sellers on amazon", "otros vendedores en amazon", "ver opciones de compra")
POSITIVE_STOCK = ("en stock", "in stock", "disponible", "available", "ultimas unidades", "quedan", "solo queda", "only ")
_NEGATED = ("no disponible", "not available", "not in stock", "no en stock", "sin stock", "out of stock", "unavailable",
            "no esta disponible", "no hay stock", "fuera de stock", "agotado", "sold out")

_RESTOCK_CUE = re.compile(
    r"(disponible a partir del?|disponible desde el?|disponible el|vuelve a estar disponible|reposicion prevista|"
    r"available from|available on|back in stock|expected (?:on|by)|reposicion el|nuevo stock)", re.I)
_RELEASE_CUE = re.compile(r"(fecha de lanzamiento|lanzamiento|fecha de salida|salida|release date|releases?|sale el|sale a la venta el)", re.I)
_SELLER = re.compile(
    r"(?:(?:vendido y enviado por|enviado y vendido por|vendido por|sold and shipped by|sold by|ships from and sold by)\s*:?|"
    r"(?:vendedor|seller)\s*:)\s*(?P<name>[A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ&][^\n|;,]{1,60}?)(?=\s{2,}|\.\s|\n|$|\s+y\s+enviado|\s+and\s+shipped|\s+\(|\s+[-–]\s)", re.I)
_CTRL_CLASS = re.compile(r"(^|[-_ ])(btn|button|cta|buy|add-?to-?cart|addtocart|basket)([-_ ]|$)", re.I)
_DISABLED_CLASS = re.compile(r"(^|[-_ ])(disabled|is-disabled|btn-disabled|inactive)([-_ ]|$)", re.I)
_AVAIL_HINT = re.compile(r"availab|stock|agotad|soldout|sold-out|out-of-stock|outofstock|unavailable|disponib|existencias", re.I)
_SKIP_CTRL_CLASS = re.compile(r"shortcut|nav-assist|keyboard", re.I)
_OLD_PRICE_CUE = re.compile(r"(antes|was|pvpr?|precio (?:anterior|original|habitual|recomendado)|rrp|msrp|list price|precio de lista|ahorra|save)\W*$", re.I)


# ---------------------------------------------------------------------------------------------- scan result
@dataclass
class Control:
    kind: str            # buy | preorder | soldout | notify | pickup
    text: str
    disabled: bool
    hidden: bool = False


@dataclass
class PhraseScan:
    state: str = UNKNOWN
    buy_button: Optional[bool] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    seller: Optional[str] = None
    restock_date: Optional[str] = None
    preorder_date: Optional[str] = None
    pickup_hint: bool = False
    mixed_signals: bool = False
    evidence: list[str] = field(default_factory=list)
    controls: list[Control] = field(default_factory=list)
    store_availability: dict[str, str] = field(default_factory=dict)

    @property
    def decided(self) -> bool:
        return self.state != UNKNOWN


# ---------------------------------------------------------------------------------------------- controls
def _has_phrase(text: str, phrases: Iterable[str]) -> bool:
    """Whole-word match of any phrase in ``text`` (accent / case-insensitive).

    A phrase written with a trailing space ("comprar ") only counts at the start of the text, so a button
    called "Comprar y recoger" matches but a sentence that merely contains the word does not.
    """
    t = norm(text)
    if not t:
        return False
    for phrase in phrases:
        p = phrase.strip()
        if phrase.endswith(" "):
            if t == p or t.startswith(p + " "):
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", t):
            return True
    return False


def leads_with(text: str, phrases_: Iterable[str]) -> bool:
    """True when the (short) text *starts* with one of the phrases: 'No disponible.', 'Agotado temporalmente'.

    Stricter than ``_has_phrase`` — 'Imagen no disponible del color' merely contains a phrase and does not count.
    """
    t = norm(text)
    if not t or len(t) > 70:
        return False
    return any(t == p.strip() or t.startswith(p.strip() + " ") or t.startswith(p.strip() + ".") or t.startswith(p.strip() + ",")
               for p in phrases_)


def classify_control_text(text: str) -> Optional[str]:
    """Kind of a control from its own (short) text, or ``None``. Order: sold-out > notify > preorder > buy."""
    t = norm(text)
    if not t or len(t) > 70:
        return None
    if _has_phrase(t, SOLDOUT):
        return "soldout"
    if _has_phrase(t, NOTIFY):
        return "notify"
    if _has_phrase(t, PICKUP) and not _has_phrase(t, BUY):
        return "pickup"
    if _has_phrase(t, PREORDER_PH) and "tienda" not in t and "recoger" not in t:
        return "preorder"
    if _has_phrase(t, BUY):
        return "buy"
    return None


def _control_text(tag: Tag) -> str:
    text = tag.get_text(" ", strip=True)
    if not text and tag.name == "input":
        text = str(tag.get("value") or "")
    if not text:
        text = str(tag.get("aria-label") or tag.get("title") or "")
    return text


def _is_disabled(tag: Tag) -> bool:
    attrs = tag.attrs or {}
    if "disabled" in attrs or str(attrs.get("aria-disabled", "")).lower() == "true":
        return True
    classes = attrs.get("class") or []
    classes = classes.split() if isinstance(classes, str) else classes
    if any(_DISABLED_CLASS.search(c) for c in classes):
        return True
    fieldset = tag.find_parent("fieldset")
    return bool(fieldset is not None and "disabled" in (fieldset.attrs or {}))


def _hidden_chain(tag: Tag, levels: int = 6) -> bool:
    node: Optional[Tag] = tag
    for _ in range(levels):
        if node is None or node.name in ("body", "html", "[document]"):
            return False
        if _is_hidden(node):
            return True
        node = node.parent if isinstance(node.parent, Tag) else None
    return False


def collect_controls(soup: BeautifulSoup) -> list[Control]:
    controls: list[Control] = []
    seen: set[int] = set()
    for tag in soup.select("button, input[type=submit], input[type=button], [role=button], a[class]"):
        if id(tag) in seen:
            continue
        seen.add(id(tag))
        classes = tag.get("class") or []
        classes = classes.split() if isinstance(classes, str) else classes
        if tag.name == "a" and tag.get("role") != "button" and not any(_CTRL_CLASS.search(c) for c in classes):
            continue
        if any(_SKIP_CTRL_CLASS.search(c) for c in classes) or tag.find_parent(["nav", "footer"]):
            continue
        text = _control_text(tag)
        if not text or (tag.get_text(strip=True) and len(tag.get_text(" ", strip=True)) > 70):
            continue
        kind = classify_control_text(text)
        if not kind:
            continue
        controls.append(Control(kind, snippet(text, 80), _is_disabled(tag), _hidden_chain(tag)))
    return controls


# ---------------------------------------------------------------------------------------------- window
def product_window(text: str, chars: int = WINDOW_CHARS) -> str:
    """The stretch of visible text that follows the first line that looks like the page headline.

    ``text`` is the newline-separated ``full_text``; the caller passes the h1's own text through
    ``window_after``. This helper handles the fallback: no headline -> the first ``chars`` characters.
    """
    return text[:chars]


def window_after(text: str, headline: str, chars: int = WINDOW_CHARS) -> str:
    if headline:
        index = text.find(headline)
        if index >= 0:
            return text[index + len(headline): index + len(headline) + chars]
    return text[:chars]


# ---------------------------------------------------------------------------------------------- the scan
def scan(soup: BeautifulSoup, text: str, *, headline: str = "", profile: Any = None, hints: Optional[dict] = None,
         today: Optional[date] = None) -> PhraseScan:
    hints = hints or {}
    out = PhraseScan()
    window = window_after(text, headline)
    price_window = window[:PRICE_WINDOW_CHARS]
    controls = collect_controls(soup)
    out.controls = controls

    # ---- profile selectors (site-specific, precise)
    def _selector_hit(selectors: Iterable[str]) -> Optional[Tag]:
        for selector in selectors or ():
            try:
                for tag in soup.select(selector):
                    if not _hidden_chain(tag):
                        return tag
            except Exception:  # noqa: BLE001 — a bad selector in a profile must not break extraction
                continue
        return None

    buy_sel = _selector_hit(getattr(profile, "buy_selectors", ()))
    sold_sel = _selector_hit(getattr(profile, "soldout_selectors", ()))
    active_buy = [c for c in controls if c.kind == "buy" and not c.disabled and not c.hidden]
    active_pre = [c for c in controls if c.kind == "preorder" and not c.disabled and not c.hidden]
    off_buy = [c for c in controls if c.kind in ("buy", "preorder") and c.disabled and not c.hidden]
    ctrl_sold = [c for c in controls if c.kind in ("soldout", "notify") and not c.hidden]
    if buy_sel is not None and not _is_disabled(buy_sel):
        out.evidence.append(snippet(f"selector: {_control_text(buy_sel) or buy_sel.get('id') or buy_sel.name}"))
        active_buy = active_buy or [Control("buy", _control_text(buy_sel), False)]
    if sold_sel is not None:
        out.evidence.append(snippet(f"selector: {sold_sel.get_text(' ', strip=True)[:100]}"))
        ctrl_sold = ctrl_sold or [Control("soldout", sold_sel.get_text(" ", strip=True)[:80], False)]

    # ---- availability-hinted elements (their own short text)
    hint_texts: list[str] = []
    for tag in soup.find_all(attrs={"id": _AVAIL_HINT}) + soup.find_all(attrs={"class": _AVAIL_HINT}):
        if _hidden_chain(tag) or tag.find_parent(["nav", "footer"]):
            continue
        own = tag.get_text(" ", strip=True)
        if 2 <= len(own) <= 120:
            hint_texts.append(own)
    hint_sold = next((h for h in hint_texts if leads_with(h, SOLDOUT) or leads_with(h, NOTIFY)), None)
    hint_pos = next((h for h in hint_texts if _has_phrase(h, POSITIVE_STOCK) and not any(n in norm(h) for n in _NEGATED)), None)
    window_sold = next((line for line in window.splitlines() if leads_with(line, SOLDOUT) or leads_with(line, NOTIFY)), None)
    market_only = next((line for line in text.splitlines() if len(line) <= 90 and _has_phrase(line, MARKETPLACE_ONLY_PH)), None)

    # ---- dates
    for line in window.splitlines():
        if len(line) > 140:
            continue
        if _RESTOCK_CUE.search(norm(line)) or _RESTOCK_CUE.search(line):
            iso = parse_date(line, today=today)
            if iso and not out.restock_date:
                out.restock_date = iso
                out.evidence.append(snippet(line))
        elif _RELEASE_CUE.search(norm(line)):
            iso = parse_date(line, today=today)
            if iso and not out.preorder_date:
                out.preorder_date = iso
                out.evidence.append(snippet(line))

    # ---- price and seller
    for hit in find_prices(price_window):
        before = price_window[max(0, hit.start - 24): hit.start]
        if _OLD_PRICE_CUE.search(before):
            continue
        out.price, out.currency = hit.value, hit.currency
        out.evidence.append(snippet(price_window[max(0, hit.start - 30): hit.end + 10]))
        break
    seller_match = _SELLER.search(text[:60_000])
    if seller_match:
        out.seller = snippet(seller_match.group("name").strip(" .:"), 80)
        out.evidence.append(snippet(seller_match.group(0)))

    # ---- pickup hint (never decides the state on its own: it needs a target store, see __init__)
    pickup_line = next((line for line in text.splitlines() if len(line) <= 120 and _has_phrase(line, PICKUP)), None)
    if pickup_line or any(c.kind == "pickup" for c in controls):
        out.pickup_hint = True
        out.evidence.append(snippet(pickup_line or "pickup control"))

    store_ids = [str(s) for s in (hints.get("store_ids") or []) if s]
    if store_ids and pickup_line:
        low = norm(text)
        for store in store_ids:
            position = low.find(norm(store))
            if position >= 0 and any(norm(p) in low[max(0, position - 200): position + 200] for p in PICKUP):
                out.store_availability[store] = "LOCAL_PICKUP"

    # ---- decide
    strong_sold = bool(ctrl_sold) or hint_sold
    if active_pre and not (ctrl_sold and not active_buy):
        out.state, out.buy_button = PREORDER, True
        out.evidence.append(snippet(f"control: {active_pre[0].text}"))
    elif active_buy:
        out.state, out.buy_button = IN_STOCK, True
        out.evidence.append(snippet(f"control: {active_buy[0].text}"))
        if strong_sold:
            out.mixed_signals = True
    elif ctrl_sold or hint_sold or off_buy:
        out.state = RESTOCK_SCHEDULED if out.restock_date else OUT_OF_STOCK
        out.buy_button = False
        out.evidence.append(snippet("control: " + (ctrl_sold[0].text if ctrl_sold else off_buy[0].text) if (ctrl_sold or off_buy) else str(hint_sold)))
    elif window_sold:
        out.state = RESTOCK_SCHEDULED if out.restock_date else OUT_OF_STOCK
        out.buy_button = False
        out.evidence.append(snippet(window_sold))
    elif market_only:
        out.state = MARKETPLACE_ONLY
        out.evidence.append(snippet(market_only))
    elif hint_pos:
        out.state = IN_STOCK
        out.evidence.append(snippet(hint_pos))
    if out.state == PREORDER and not out.preorder_date and out.restock_date:
        out.preorder_date = out.restock_date
        out.restock_date = None
    if out.state not in (PREORDER,):
        out.preorder_date = out.preorder_date if out.state == UNKNOWN else None
    out.evidence = list(dict.fromkeys(e for e in out.evidence if e))
    return out
