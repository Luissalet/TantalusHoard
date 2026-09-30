"""Microdata (``itemprop``) and OpenGraph / ``product:*`` meta tags -> Offers and page metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..model import IN_STOCK, MARKETPLACE_ONLY, OUT_OF_STOCK, PREORDER, UNKNOWN, Offer
from .jsonld import map_availability
from .phrases import norm, parse_number, snippet

# OpenGraph product:availability vocabulary (Facebook / Pinterest flavours) -> state
OG_AVAILABILITY = {
    "in stock": IN_STOCK, "instock": IN_STOCK, "available for order": IN_STOCK, "available": IN_STOCK,
    "out of stock": OUT_OF_STOCK, "oos": OUT_OF_STOCK, "outofstock": OUT_OF_STOCK, "discontinued": OUT_OF_STOCK,
    "sold out": OUT_OF_STOCK, "soldout": OUT_OF_STOCK,
    "preorder": PREORDER, "pre-order": PREORDER, "pre order": PREORDER, "pending": PREORDER, "presale": PREORDER,
    "backorder": PREORDER, "backordered": PREORDER,
}


@dataclass
class PageMeta:
    title: str = ""
    image: str = ""
    url: str = ""
    og_type: str = ""
    site_name: str = ""
    description: str = ""
    offer: Optional[Offer] = None        # from product:* meta
    microdata: list[Offer] = field(default_factory=list)
    has_product_markup: bool = False


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag is not None and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _itemprop_value(tag: Tag) -> str:
    for attr in ("content", "href", "src", "value", "datetime"):
        if tag.get(attr):
            return str(tag[attr]).strip()
    return tag.get_text(" ", strip=True)


def _first(scope: Tag, prop: str) -> Optional[Tag]:
    return scope.find(attrs={"itemprop": prop})


def _microdata_offers(soup: BeautifulSoup, base_url: str) -> tuple[list[Offer], bool]:
    offers: list[Offer] = []
    scopes = soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/(?:Product|IndividualProduct|ProductModel)", re.I)})
    for scope in scopes:
        offer = Offer(method="microdata")
        name = _first(scope, "name")
        offer.title = snippet(_itemprop_value(name), 200) if name is not None else None
        for prop, attr in (("sku", "sku"), ("gtin13", "ean"), ("brand", "brand"), ("image", "image")):
            tag = _first(scope, prop)
            if tag is not None:
                value = _itemprop_value(tag)
                if prop == "brand" and tag.find(attrs={"itemprop": "name"}) is not None:
                    value = _itemprop_value(tag.find(attrs={"itemprop": "name"}))
                if prop == "gtin13" and not re.fullmatch(r"\d{8,14}", value):
                    continue
                setattr(offer, attr, urljoin(base_url, value) if prop == "image" else value)
        # the offer scope, when one is declared
        zone = scope.find(attrs={"itemtype": re.compile(r"schema\.org/(?:Aggregate)?Offer", re.I)}) or scope
        price = _first(zone, "price") or _first(zone, "lowPrice")
        if price is not None:
            offer.price = parse_number(_itemprop_value(price))
            offer.evidence.append(snippet(f"itemprop price: {_itemprop_value(price)}"))
        currency = _first(zone, "priceCurrency")
        if currency is not None:
            offer.currency = _itemprop_value(currency)[:3].upper() or None
        availability = _first(zone, "availability")
        if availability is not None:
            raw = _itemprop_value(availability)
            offer.availability = map_availability(raw)
            offer.evidence.append(snippet(f"itemprop availability: {raw}"))
        seller = _first(zone, "seller")
        if seller is not None:
            inner = seller.find(attrs={"itemprop": "name"})
            offer.seller = snippet(_itemprop_value(inner if inner is not None else seller), 80) or None
        url = _first(scope, "url")
        if url is not None:
            offer.url = urljoin(base_url, _itemprop_value(url))
        if offer.price is not None or offer.availability != UNKNOWN or offer.title:
            offers.append(offer)
    if offers:
        return offers, True
    # loose itemprops without a Product scope (some themes only mark price + availability)
    price = soup.find(attrs={"itemprop": "price"})
    availability = soup.find(attrs={"itemprop": "availability"})
    if price is not None or availability is not None:
        offer = Offer(method="microdata")
        if price is not None:
            offer.price = parse_number(_itemprop_value(price))
            offer.evidence.append(snippet(f"itemprop price: {_itemprop_value(price)}"))
            currency = soup.find(attrs={"itemprop": "priceCurrency"})
            offer.currency = _itemprop_value(currency)[:3].upper() if currency is not None else None
        if availability is not None:
            offer.availability = map_availability(_itemprop_value(availability))
            offer.evidence.append(snippet(f"itemprop availability: {_itemprop_value(availability)}"))
        return [offer], True
    return [], False


def extract_meta(soup: BeautifulSoup, base_url: str = "") -> PageMeta:
    meta = PageMeta()
    meta.og_type = _meta(soup, "og:type").lower()
    meta.title = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
    meta.image = _meta(soup, "og:image", "twitter:image")
    meta.url = _meta(soup, "og:url")
    meta.site_name = _meta(soup, "og:site_name")
    meta.description = _meta(soup, "og:description", "description")
    if meta.image:
        meta.image = urljoin(base_url, meta.image)

    amount = _meta(soup, "product:sale_price:amount") or _meta(soup, "product:price:amount", "og:price:amount")
    availability = _meta(soup, "product:availability", "og:availability")
    if amount or availability:
        offer = Offer(method="opengraph", title=meta.title or None, image=meta.image or None, url=meta.url or None)
        if amount:
            offer.price = parse_number(amount)
            offer.currency = (_meta(soup, "product:sale_price:currency", "product:price:currency", "og:price:currency") or "").upper() or None
            offer.evidence.append(snippet(f"product:price:amount={amount}"))
            if _meta(soup, "product:sale_price:amount") and _meta(soup, "product:price:amount"):
                offer.extra["list_price"] = parse_number(_meta(soup, "product:price:amount"))
        if availability:
            offer.availability = OG_AVAILABILITY.get(norm(availability), UNKNOWN)
            offer.evidence.append(snippet(f"product:availability={availability}"))
        offer.brand = _meta(soup, "product:brand") or None
        offer.sku = _meta(soup, "product:retailer_item_id", "product:sku") or None
        meta.offer = offer
    meta.microdata, micro = _microdata_offers(soup, base_url)
    meta.has_product_markup = bool(meta.offer) or micro or meta.og_type in ("product", "og:product", "product.item")
    return meta
