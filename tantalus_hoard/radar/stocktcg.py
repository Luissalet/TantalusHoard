"""Parsers for the two public TCG stock aggregators of Spain: stocktcg.net and stocktcg.es.

Pure functions: HTML / JSON in, plain dataclasses out. Nothing here touches the network.

stocktcg.net (server-rendered, robots allow everything but /clientes and /portal):
  * ``/api/pulse.json``           live feed of restocks and new listings across ~190 shops;
  * ``/p/<slug>``                 one product: every shop with verified stock (price, edition language, pre-order flag);
  * ``/tiendas/<slug>``           one shop: what is in stock now and what sold out recently (GAME, Carrefour, El Corte Inglés...);
  * ``/lanzamientos``             release calendar with the date and the shops that already have stock or pre-orders;
  * ``/lanzamientos/<slug>``      one release: shops where it is sold out, and per format the shops with pre-order / stock.
stocktcg.es (Astro, robots allow everything but the affiliate redirects ``/B0...``):
  * ``/``                         live feed of restocks and deals (shop, price, timestamp);
  * ``/lanzamientos``             release calendar (date, set, products).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..extract.phrases import amount_and_currency
from ..model import IN_STOCK, OUT_OF_STOCK, PREORDER, UNKNOWN

NET = "https://stocktcg.net"
ES = "https://stocktcg.es"
SOURCE_NET = "stocktcg.net"
SOURCE_ES = "stocktcg.es"

# Big chains: their stock is what most people can actually buy at the shelf price. Slugs as stocktcg.net names them.
CHAIN_SLUGS = ("game", "carrefour", "el-corte-ingles", "alcampo", "amazon", "toys-r-us", "toy-planet")
CHAIN_HOSTS = {"game": "game.es", "carrefour": "carrefour.es", "el-corte-ingles": "elcorteingles.es", "alcampo": "alcampo.es",
               "amazon": "amazon.es", "toys-r-us": "toysrus.es", "toy-planet": "toy-planet.com"}

_MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
           "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


@dataclass
class RadarOffer:
    """One shop's offer for one product, as an aggregator reports it."""

    source: str                       # stocktcg.net | stocktcg.es
    store: str                        # shop display name ("Carrefour")
    store_slug: str                   # normalised shop key ("carrefour")
    title: str                        # the shop's own product title
    url: str = ""                     # the shop's product page (buy / reserve link)
    state: str = UNKNOWN              # IN_STOCK | PREORDER | OUT_OF_STOCK
    price: Optional[float] = None
    currency: str = "EUR"
    lang: str = ""                    # edition language as the aggregator labels it (ES, EN, FR, JA...)
    set_name: str = ""                # "30th Celebration / Celebración 30 Aniversario"
    fmt: str = ""                     # "ETB", "Mini Tin", "Booster Bundle"...
    product_key: str = ""             # aggregator product slug ("30th-anniversary--etb") when known
    image: str = ""
    seen_ts: Optional[float] = None   # when the aggregator saw it (feeds)
    kind: str = ""                    # restock | new | deal | listing | soldout | invite | variant
    release: str = ""                 # release slug when the offer comes from a release page
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.store_slug}|{self.url or self.title}"

    def match_text(self) -> str:
        return " ".join(filter(None, (self.title, self.set_name, self.fmt, self.product_key.replace("-", " "),
                                      self.release.replace("-", " "))))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Release:
    """A product release in an aggregator calendar."""

    source: str
    slug: str
    title: str
    date: str = ""                    # ISO date ("" when the calendar has none yet)
    kind: str = ""                    # "Mini latas / Bundle (30th Celebration)"
    game: str = "pokemon"
    url: str = ""                     # the release page on the aggregator
    products: list[str] = field(default_factory=list)  # product chips ("Mini latas", "Booster Bundle")
    stores_total: Optional[int] = None
    stores_buyable: Optional[int] = None
    stores_soldout: Optional[int] = None
    soldout: list[dict[str, str]] = field(default_factory=list)   # [{"store", "slug"}] where it is already sold out
    offers: list[RadarOffer] = field(default_factory=list)        # buyable / sold-out rows (release page) or chips (calendar)
    buy_url: str = ""

    def match_text(self) -> str:
        return " ".join(filter(None, (self.title, self.kind, " ".join(self.products), self.slug.replace("-", " "))))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["offers"] = [o.to_dict() for o in self.offers]
        return d


# ================================================================================================ helpers
def slugify(name: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", (name or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def parse_price(text: str) -> tuple[Optional[float], str]:
    """'119,95 €' -> (119.95, 'EUR'); '1.599,00 SEK' -> (1599.0, 'SEK'); '€1,234.56' -> (1234.56, 'EUR'); '' -> (None, 'EUR')."""
    return amount_and_currency(text)


def _text(node: Optional[Tag]) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def _own_text(node: Optional[Tag], skip: tuple[str, ...]) -> str:
    """Text of a node without the children whose class is in ``skip``."""
    if node is None:
        return ""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, Tag):
            if set(child.get("class") or []) & set(skip):
                continue
            parts.append(child.get_text(" ", strip=True))
        else:
            parts.append(str(child).strip())
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()


def _slug_from_href(href: str, prefix: str) -> str:
    path = urlsplit(href or "").path
    return path[len(prefix):].strip("/") if path.startswith(prefix) else ""


def parse_spanish_date(text: str, *, today: Optional[date] = None) -> str:
    """'2 de octubre' -> '2026-10-02' (the next occurrence near today when the year is missing)."""
    m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s+de\s+(\d{4}))?", (text or "").lower())
    if not m or m.group(2) not in _MONTHS:
        return ""
    day, month = int(m.group(1)), _MONTHS[m.group(2)]
    today = today or date.today()
    year = int(m.group(3)) if m.group(3) else today.year
    try:
        found = date(year, month, day)
    except ValueError:
        return ""
    if not m.group(3) and (found - today).days < -180:
        found = date(year + 1, month, day)
    return found.isoformat()


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


# ================================================================================================ stocktcg.net
def parse_pulse(data: Any) -> list[RadarOffer]:
    """``/api/pulse.json``: ``{"ts", "stats", "restocks": [{store, slug, url, price, currency, title, setName, fmtName,
    tipo, ts, ppage, img}]}``. Every row is a shop that has the product right now."""
    rows = data.get("restocks") if isinstance(data, dict) else None
    out: list[RadarOffer] = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("store"):
            continue
        tipo = str(row.get("tipo") or "").upper()
        state = PREORDER if "PRE" in tipo else IN_STOCK
        price = row.get("price")
        out.append(RadarOffer(
            source=SOURCE_NET, store=str(row["store"]), store_slug=str(row.get("slug") or slugify(row["store"])),
            title=str(row.get("title") or ""), url=str(row.get("url") or ""), state=state,
            price=float(price) if isinstance(price, (int, float)) else None, currency=str(row.get("currency") or "EUR"),
            set_name=str(row.get("setName") or ""), fmt=str(row.get("fmtName") or ""),
            product_key=_slug_from_href(str(row.get("ppage") or ""), "/p/"), image=str(row.get("img") or ""),
            seen_ts=_iso_ts(row.get("ts")), kind=tipo.lower() or "restock"))
    return out


def pulse_stats(data: Any) -> dict[str, Any]:
    return dict(data.get("stats") or {}) if isinstance(data, dict) else {}


def _iso_ts(value: Any) -> Optional[float]:
    """'2026-10-01T22:22:50' (Madrid local time on stocktcg.net) -> epoch seconds."""
    from datetime import datetime, timedelta, timezone
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        # Europe/Madrid: CEST (+2) from the last Sunday of March to the last Sunday of October, CET (+1) otherwise.
        offset = 2 if _madrid_dst(moment) else 1
        moment = moment.replace(tzinfo=timezone(timedelta(hours=offset)))
    return moment.timestamp()


def _madrid_dst(moment: Any) -> bool:
    from datetime import datetime
    year = moment.year

    def last_sunday(month: int) -> datetime:
        d = datetime(year, month, 31)
        while d.weekday() != 6:
            d = d.replace(day=d.day - 1)
        return d
    start, end = last_sunday(3).replace(hour=2), last_sunday(10).replace(hour=3)
    return start <= moment.replace(tzinfo=None) < end


def parse_product_page(html: str, url: str = "") -> tuple[dict[str, Any], list[RadarOffer]]:
    """``/p/<slug>``: the product head (name, format, count of shops, cheapest) and one offer per shop row."""
    soup = _soup(html)
    product_key = _slug_from_href(url, "/p/")
    name = _text(soup.find("h1"))
    set_name, _, fmt = name.partition(" · ")
    image = ""
    img = soup.select_one(".pp-img img")
    if img is not None:
        image = img.get("src") or ""
    head: dict[str, Any] = {"product_key": product_key, "name": name, "set_name": set_name.strip(), "fmt": fmt.strip(),
                            "url": url, "image": image}
    answer = _text(soup.select_one(".pd-answer"))
    m = re.search(r"en\s+(\d+)\s+tiendas", answer)
    head["stores_in_stock"] = int(m.group(1)) if m else None
    cm = soup.select_one(".pp-cmref")
    head["cardmarket"] = _text(cm)
    offers: list[RadarOffer] = []
    for table in soup.select("table.pp-offers"):
        heading = table.find_previous(["h2", "h3"])
        variant = "variante" in _text(heading).lower()
        for tr in table.select("tbody tr"):
            store_a = tr.select_one("a.pp-store")
            if store_a is None:
                continue
            cells = tr.find_all("td")
            product_cell = cells[1] if len(cells) > 1 else None
            lang = _text(product_cell.select_one(".pp-lang")) if product_cell is not None else ""
            title = _own_text(product_cell, ("pp-lang",))
            price, currency = parse_price(_text(tr.select_one(".price")))
            buy = tr.select_one("a.btn-buy, a.pp-go")
            preorder = tr.select_one(".preorder") is not None or "PRE-VENTA" in title.upper() or "PREVENTA" in title.upper()
            eta = _text(tr.select_one(".pp-eta"))
            offers.append(RadarOffer(
                source=SOURCE_NET, store=_text(store_a), store_slug=_slug_from_href(store_a.get("href") or "", "/tiendas/") or slugify(_text(store_a)),
                title=title, url=(buy.get("href") if buy is not None else "") or "", state=PREORDER if preorder else IN_STOCK,
                price=price, currency=currency, lang=lang, set_name=head["set_name"], fmt=head["fmt"], product_key=product_key,
                image=image, kind="variant" if variant else "listing", extra={"eta": eta} if eta else {}))
    amz = soup.select_one(".pp-amz")
    if amz is not None:
        link = amz.select_one("a")
        price, currency = parse_price(_text(amz.select_one("b")))
        spans = amz.find_all("span", recursive=False)
        title = _text(spans[1]) if len(spans) > 1 else head["name"]
        offers.append(RadarOffer(source=SOURCE_NET, store="Amazon", store_slug="amazon", title=title,
                                 url=_strip_affiliate((link.get("href") if link is not None else "") or ""), state=PREORDER,
                                 price=price, currency=currency, set_name=head["set_name"], fmt=head["fmt"], product_key=product_key,
                                 image=image, kind="invite", extra={"invite": True}))
    return head, offers


def _strip_affiliate(url: str) -> str:
    """Amazon links on the aggregators carry an affiliate tag; the alert links to the plain product page."""
    parts = urlsplit(url)
    if parts.hostname and "amazon." in parts.hostname:
        m = re.search(r"/dp/([A-Z0-9]{10})", parts.path)
        if m:
            return f"https://{parts.hostname}/dp/{m.group(1)}"
    return url


def parse_store_page(html: str, store_slug: str = "") -> tuple[dict[str, Any], list[RadarOffer]]:
    """``/tiendas/<slug>``: the shop's in-stock products (first table) and its recently sold-out ones (``details``)."""
    soup = _soup(html)
    name = _text(soup.find("h1")) or store_slug
    head: dict[str, Any] = {"store": name, "store_slug": store_slug}
    desc = soup.find("meta", attrs={"name": "description"})
    m = re.search(r"(\d+)\s+productos.*?(\d+)\s+en stock", (desc.get("content") if desc is not None else "") or "")
    if m:
        head["tracked"], head["in_stock"] = int(m.group(1)), int(m.group(2))
    offers: list[RadarOffer] = []
    soldout_box = soup.select_one("details.st-soldout")
    for table in soup.select("table.st-table"):
        soldout = soldout_box is not None and soldout_box in table.parents
        for tr in table.select("tbody tr"):
            link = tr.select_one(".st-prod a")
            if link is None:
                continue
            fmt_badge = _text(tr.select_one(".st-fmt"))
            set_name, _, fmt = fmt_badge.rpartition(" · ")
            if not set_name:
                set_name, fmt = "", fmt_badge
            cells = tr.find_all("td")
            price_text = _text(tr.select_one(".price")) or (_text(cells[2]) if len(cells) > 2 else "")
            price, currency = parse_price(price_text)
            buy = tr.select_one("a.st-go")
            seen = _text(cells[3]) if soldout and len(cells) > 3 else ""
            row_text = _text(tr).upper()
            state = OUT_OF_STOCK if soldout else (PREORDER if "PREVENTA" in row_text or "PRE-VENTA" in row_text else IN_STOCK)
            thumb = tr.select_one("img.st-thumb")
            offers.append(RadarOffer(
                source=SOURCE_NET, store=name, store_slug=store_slug or slugify(name), title=_text(link),
                url=_strip_affiliate((buy.get("href") if buy is not None else "") or ""), state=state, price=price, currency=currency,
                lang=_text(tr.select_one(".st-lang")), set_name=set_name.strip(), fmt=fmt.strip(),
                product_key=_slug_from_href(link.get("href") or "", "/p/"), image=(thumb.get("src") if thumb is not None else "") or "",
                kind="soldout" if soldout else "listing", extra={"seen": seen} if seen else {}))
    return head, offers


def parse_releases(html: str, *, today: Optional[date] = None) -> list[Release]:
    """``/lanzamientos``: one ``article.release`` per release with ``time[datetime]``, title link, type, counts, shop chips."""
    soup = _soup(html)
    out: list[Release] = []
    for art in soup.select("article.release"):
        link = art.select_one("a.rel-link") or art.select_one("h3 a")
        title = _text(link) or _text(art.find("h3"))
        if not title:
            continue
        href = (link.get("href") if link is not None else "") or ""
        slug = _slug_from_href(href, "/lanzamientos/") or slugify(title)
        when = art.select_one("time[datetime]")
        iso = (when.get("datetime") if when is not None else "") or ""
        state = _text(art.select_one(".rel-state"))
        nums = [int(n) for n in re.findall(r"(\d+)\s+(?:tiendas|con stock|con preventa|agotadas)", state)]
        rel = Release(source=SOURCE_NET, slug=slug, title=title, date=iso[:10], kind=_text(art.select_one(".rel-type")),
                      url=urljoin(NET, href) if href else "")
        labels = re.findall(r"(\d+)\s+(tiendas|con stock|con preventa|agotadas)", state)
        for value, label in labels:
            if label == "tiendas":
                rel.stores_total = int(value)
            elif label in ("con stock", "con preventa"):
                rel.stores_buyable = int(value)
            elif label == "agotadas":
                rel.stores_soldout = int(value)
        del nums
        for chip in art.select("a.rel-chip"):
            store = _text(chip.find("b"))
            price, currency = parse_price(_own_text(chip, ("pre-badge",)).replace(store, "", 1))
            pre = chip.select_one(".pre-badge") is not None
            rel.offers.append(RadarOffer(source=SOURCE_NET, store=store, store_slug=chip.get("data-store") or slugify(store),
                                         title=title, url=_strip_affiliate(chip.get("href") or ""), state=PREORDER if pre else IN_STOCK,
                                         price=price, currency=currency, release=slug, kind="listing"))
        out.append(rel)
    return out


def parse_release_page(html: str, url: str = "", *, today: Optional[date] = None) -> Release:
    """``/lanzamientos/<slug>``: date, counts, the shops where it is already sold out, and per product card the rows
    (Preventa / En stock / Agotado) with shop, title, price and link."""
    soup = _soup(html)
    slug = _slug_from_href(url, "/lanzamientos/")
    title = _text(soup.find("h1"))
    rel = Release(source=SOURCE_NET, slug=slug or slugify(title), title=title, kind=_text(soup.select_one(".rl-type")), url=url,
                  date=parse_spanish_date(_text(soup.select_one(".rl-date")), today=today))
    for stat in soup.select(".rl-stat"):
        n = re.search(r"\d+", _text(stat))
        if not n:
            continue
        classes = set(stat.get("class") or [])
        if "on" in classes:
            rel.stores_buyable = int(n.group(0))
        elif "off" in classes:
            rel.stores_soldout = int(n.group(0))
        else:
            rel.stores_total = int(n.group(0))
    box = soup.select_one(".rl-soldout")
    if box is not None:
        for a in box.find_all("a"):
            rel.soldout.append({"store": _text(a), "slug": _slug_from_href(a.get("href") or "", "/tiendas/") or slugify(_text(a))})
    for card in soup.select("article.rc-card"):
        product_key = card.get("data-producto") or ""
        tags = [_text(t) for t in card.select(".rc-tag")]
        fmt = tags[0] if tags else ""
        lang = next((t for t in tags[1:] if re.fullmatch(r"[A-Z]{2}", t)), "")
        name = _text(card.select_one(".rc-name"))
        set_name = name.partition(" · ")[0]
        img = card.select_one(".rc-img img")
        for row in card.select("li.rc-row"):
            classes = set(row.get("class") or [])
            status = _text(row.select_one(".rc-st")).lower()
            if "st-out" in classes or "agotad" in status:
                state = OUT_OF_STOCK
            elif "st-pre" in classes or "preventa" in status or "reserva" in status:
                state = PREORDER
            else:
                state = IN_STOCK
            store_node = row.select_one(".rc-store")
            store = _text(store_node.find("b")) if store_node is not None else ""
            buy = row.select_one("a.rc-buy, a.btn-buy")
            price, currency = parse_price(_text(row.select_one(".rc-price")))
            rel.offers.append(RadarOffer(
                source=SOURCE_NET, store=store,
                store_slug=((buy.get("data-store") if buy is not None else "") or slugify(store)),
                title=_text(store_node.find("small")) if store_node is not None else name,
                url=_strip_affiliate(((buy.get("href") if buy is not None else "") or "")), state=state, price=price, currency=currency,
                lang=lang, set_name=set_name, fmt=fmt, product_key=product_key, image=(img.get("src") if img is not None else "") or "",
                release=rel.slug, kind="listing"))
    return rel


# ================================================================================================ stocktcg.es
def parse_es_feed(html: str) -> list[RadarOffer]:
    """stocktcg.es home: ``a.card`` per detection (tag RESTOCK / CHOLLO / PREVENTA...), shop, price, ``data-ts``."""
    soup = _soup(html)
    out: list[RadarOffer] = []
    seen: set[tuple[str, str]] = set()
    for card in soup.select("a.card[href]"):
        name = _text(card.select_one(".card-name"))
        store = _text(card.select_one(".card-store"))
        if not name or not store:
            continue
        href = card.get("href") or ""
        asin = re.match(r"^(?:https?://(?:www\.)?stocktcg\.es)?/(B0[A-Z0-9]{8})\b", href)
        if asin:
            href = f"https://www.amazon.es/dp/{asin.group(1)}"  # the site's affiliate redirect, to the plain product page
        elif href.startswith("/"):
            continue
        key = (store, href)
        if key in seen:
            continue
        seen.add(key)
        tag = _text(card.select_one(".tag")).upper()
        state = PREORDER if "PRE" in tag else IN_STOCK
        price, currency = parse_price(_text(card.select_one(".card-price")))
        ago = card.select_one(".card-ago[data-ts]")
        ts = None
        if ago is not None:
            try:
                ts = float(ago.get("data-ts"))
            except (TypeError, ValueError):
                ts = None
        img = card.select_one(".card-thumb img")
        out.append(RadarOffer(
            source=SOURCE_ES, store=store, store_slug=slugify(store), title=name, url=_strip_affiliate(href), state=state, price=price,
            currency=currency, image=(img.get("src") if img is not None else "") or "", seen_ts=ts, kind=tag.lower() or "restock",
            set_name="30 Aniversario" if card.get("data-aniv30") == "1" else "",
            extra={"game": card.get("data-game") or "", "deal": card.get("data-chollo") == "1"}))
    return out


def parse_es_releases(html: str) -> list[Release]:
    """stocktcg.es ``/lanzamientos``: ``li.cal-item[data-game][data-date]`` with the set name and its product chips."""
    soup = _soup(html)
    out: list[Release] = []
    for item in soup.select("li.cal-item"):
        title = _text(item.select_one(".cal-set"))
        if not title:
            continue
        cta = item.select_one("a.cal-cta")
        iso = item.get("data-date") or ""
        game = item.get("data-game") or ""
        out.append(Release(source=SOURCE_ES, slug=slugify(f"{game}-{title}-{iso}"), title=title, date=iso[:10], game=game,
                           url=f"{ES}/lanzamientos", products=[_text(c) for c in item.select(".cal-chip")],
                           buy_url=(cta.get("href") if cta is not None else "") or ""))
    return out


def parse_json(text: str) -> Any:
    try:
        return json.loads(text or "")
    except ValueError:
        return None
