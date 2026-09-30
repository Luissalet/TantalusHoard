"""Readable text, the chrome-ratio quality gate and the normalised content hash.

Two views of a page's text:

* ``full_text`` — everything visible (scripts / styles / templates / hidden nodes removed). Phrase rules use it
  because buy buttons and stock notes live inside forms and headers.
* ``readable_text`` — what a person would call the content: additionally drops ``nav``, ``header``,
  ``footer``, ``aside``, ``form`` and cookie / consent banners. Information sentries diff and hash this.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ALWAYS_DROP = {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "head", "title", "meta", "link"}
CHROME_TAGS = {"nav", "header", "footer", "aside", "form"}
_NOISE_TOKENS = re.compile(r"cookie|consent|onetrust|cookiebot|gdpr|cmp-|newsletter", re.I)
_HIDDEN_CLASS = {"hidden", "d-none", "is-hidden", "u-hidden", "hide", "sr-only", "visually-hidden", "is-template"}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)

# Words that mostly occur in site chrome (menus, footers, account / cart links, social links).
NAV_WORDS = frozenset("""
inicio home menú menu categorías categorias buscar búsqueda busqueda login registro registrarse cuenta
carrito cesta pedidos favoritos wishlist ayuda contacto contáctanos contactanos envíos envios devoluciones
cookies privacidad aviso legal términos terminos condiciones política politica mapa sitio newsletter suscríbete
suscribete síguenos siguenos facebook instagram twitter youtube tiktok pinterest whatsapp idioma país pais
ofertas novedades outlet tiendas blog trabaja sobre nosotros empresa prensa afiliados sign account cart search
help shipping returns terms privacy careers stores
""".split())


def _is_hidden(tag: Tag) -> bool:
    attrs = tag.attrs or {}
    if "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true":
        return True
    if attrs.get("type") == "hidden":
        return True
    style = attrs.get("style")
    if style and _HIDDEN_STYLE.search(str(style)):
        return True
    classes = attrs.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    return any(c in _HIDDEN_CLASS for c in classes)


def _walk(node: Tag, drop_chrome: bool) -> Iterable[str]:
    """Yield visible text pieces in document order (iterative: pages can nest very deeply)."""
    stack: list = [iter(node.children)]
    while stack:
        try:
            child = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            piece = str(child).strip()
            if piece:
                yield piece
            continue
        if not isinstance(child, Tag):
            continue
        name = (child.name or "").lower()
        if name in ALWAYS_DROP or _is_hidden(child):
            continue
        if drop_chrome:
            if name in CHROME_TAGS:
                continue
            ident = " ".join(filter(None, [str(child.attrs.get("id", "")), " ".join(child.attrs.get("class") or [])]))
            if ident and _NOISE_TOKENS.search(ident):
                continue
        if name in ("br", "hr"):
            yield "\n"
            continue
        stack.append(iter(child.children))


def _join(pieces: Iterable[str]) -> str:
    lines: list[str] = []
    for piece in pieces:
        piece = re.sub(r"\s+", " ", piece.replace("\xa0", " ")).strip()
        if piece:
            lines.append(piece)
    return "\n".join(lines)


def full_text(soup: BeautifulSoup) -> str:
    root = soup.body or soup
    return _join(_walk(root, drop_chrome=False))


def readable_text(soup: BeautifulSoup) -> str:
    root = soup.body or soup
    return _join(_walk(root, drop_chrome=True))


def chrome_ratio(text: str) -> float:
    """Share of words that sit in short, menu-looking lines (<= 4 words containing a nav word).

    0.0 for prose, close to 1.0 for a page that is only a menu / footer. The value is documented and stable
    so it can be tested and tuned.
    """
    total = 0
    chrome = 0
    for line in text.splitlines():
        words = re.findall(r"[^\W\d_]+", line.lower())
        if not words:
            continue
        total += len(words)
        if len(words) <= 4 and any(w in NAV_WORDS for w in words):
            chrome += len(words)
    return chrome / total if total else 1.0


def quality_ok(text: str, *, min_words: int = 40, max_chrome: float = 0.6) -> bool:
    """False for empty / tiny / navigation-only text (a JS shell, an error page, a stripped-down block page)."""
    words = len(re.findall(r"\w+", text))
    return words >= min_words and chrome_ratio(text) < max_chrome


_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?\b", re.I)
_ISO_STAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?(?:z|[+-]\d{2}:?\d{2})?\b", re.I)
_RELATIVE = re.compile(r"\b(?:hace|ago|quedan?|faltan?)\s+\d+\s+(?:segundos?|minutos?|horas?|d[ií]as?|seconds?|minutes?|hours?|days?)\b", re.I)
_TOKEN = re.compile(r"\b[0-9a-f]{16,}\b", re.I)


def normalise_for_hash(text: str) -> str:
    """NFKC + casefold, then drop volatile noise so a re-render does not look like a change.

    Removed: ISO timestamps, clock times (12:34, 12:34:56), relative times ("hace 5 minutos",
    "quedan 3 horas"), long hex tokens (session / cache ids). Kept on purpose: every other digit — prices,
    quantities and dates are exactly what a sentry has to notice. Whitespace is collapsed.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("​", "").replace("‌", "").replace("﻿", "")
    for pattern in (_ISO_STAMP, _RELATIVE, _CLOCK, _TOKEN):
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalise_for_hash(text).encode("utf-8")).hexdigest()
