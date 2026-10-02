"""Turn one sale mail (subject, text, links, image alt texts) into deal rows.

Pure functions: no network, no I/O, nothing is fetched or opened. ``parse_mail`` never raises; a mail it cannot
make sense of yields no rows. Two shapes are understood:

* a shop that mails "an item of your wishlist is on sale" (``wishlist_style``): one row per item, with the
  discount, the old and new price and the item link when the mail carries them;
* any other sale mail: one ``campaign`` row (cleaned subject, up to 12 item titles taken from the image alt texts,
  the best discount, the end date when the mail states one).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from ..hoard_link.money import find_prices as _find_prices, parse_amount as _parse_amount
from .stores import domain_matches, domain_of

MAX_TITLES = 12
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


def fold(text: Any) -> str:
    """Lower case without accents (same length for ordinary Latin text)."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")[:80]


def clean_title(text: Any, limit: int = 140) -> str:
    """Strip emoji, symbols and zero-width characters; collapse whitespace."""
    out = []
    for ch in str(text or ""):
        cat = unicodedata.category(ch)
        if cat in ("So", "Sk", "Cs", "Cf", "Co", "Cn") or ord(ch) >= 0x1F000 or ch in "\u200b\u200c\u200d\ufeff\u00ad":
            out.append(" ")
        else:
            out.append(ch)
    s = re.sub(r"\s+", " ", "".join(out)).strip(" \t-:|*_")
    s = re.sub(r"^(?:re|fwd?|rv)\s*:\s*", "", s, flags=re.I).strip()
    return s[:limit]


# ----------------------------------------------------------------------------- numbers
def parse_amount(raw: str) -> float | None:
    """``1.299,99`` / ``19,99`` / ``19.99`` / ``1,299.50`` -> float (the commons' rules); ``None`` outside (0, 100000)."""
    value = _parse_amount(raw)
    return float(value) if value is not None and 0 < value < 100000 else None


def prices(text: str) -> list[tuple[int, float, str]]:
    """Every price in the text as ``(position, amount, currency)``; a number needs a currency marker (``%`` is none)."""
    return [(h.start, float(h.amount), h.currency) for h in _find_prices(text or "") if 0 < h.amount < 100000]


DISC_NEG = re.compile(r"(?<![\w.,])[-\u2212\u2013]\s?(\d{1,3})\s?%")
DISC_WORD = re.compile(r"(?<![\w.,])(\d{1,3})\s?%\s*(?:de\s+)?(?:off\b|dto\b|descuento|discount|rebaja)", re.I)
DISC_WORD2 = re.compile(r"\b(?:dto\.?|descuento|discount|off|save|ahorra)\s*(?:de|of|del|up to|hasta)?\s*(\d{1,3})\s?%", re.I)
UPTO_RE = re.compile(r"\b(?:hasta(?:\s+el)?|up\s+to|upto)\s+(?:un\s+)?[-\u2212]?(\d{1,3})\s?%", re.I)


def strip_urls(text: str) -> str:
    return URL_RE.sub(" ", text or "")


def discounts(text: str) -> tuple[int | None, bool]:
    """Best discount percentage in the text and whether it is a range / "up to" ("-10%, -15%, -20%", "hasta el 70%")."""
    body = strip_urls(text)
    values = []
    for rx in (DISC_NEG, DISC_WORD, DISC_WORD2, UPTO_RE):
        values += [int(v) for v in rx.findall(body)]
    values = [v for v in values if 1 <= v <= 100]
    if not values:
        return None, False
    return max(values), bool(UPTO_RE.search(body)) or len(set(values)) > 1


SALE_WORDS = ("oferta", "rebaja", "descuento", "dto", "% off", "sale", "deal", "saldos", "promo", "black friday", "liquidacion",
              "outlet", "2x1", "3x2", "cupon", "coupon", "gratis", "free", "lista de deseados", "wishlist", "wish list", "ahorra",
              "price drop", "bajada", "ofertas", "flash", "off", "rebajas", "descuentos", "promos", "cupones", "deals", "sales")
MIN_PLAIN_DISCOUNT = 10   # a "5% dto" footer on every newsletter is not a sale
NOT_DEAL = ("pedido", "factura", "order confirmation", "your order", "tu compra", "recibo", "receipt", "contrasena", "password",
            "verification", "verificacion", "codigo de", "security", "seguridad", "tu cuenta", "your account", "bienvenid", "welcome to",
            "shipping", "envio", "enviado", "delivered", "entregado", "invoice", "reembolso", "refund", "suscripcion", "subscription")


def looks_like_sale(subject: str, text: str) -> bool:
    subj = fold(subject)
    pct, _ = discounts(subject + "\n" + (text or "")[:6000])
    strong = any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", subj) for w in SALE_WORDS)
    if any(w in subj for w in NOT_DEAL) and (pct is None or pct < MIN_PLAIN_DISCOUNT) and not strong:
        return False
    return strong or (pct is not None and pct >= MIN_PLAIN_DISCOUNT)


# ----------------------------------------------------------------------------- end of the sale
MONTHS = {
    "ene": 1, "enero": 1, "jan": 1, "january": 1, "feb": 2, "febrero": 2, "february": 2, "mar": 3, "marzo": 3, "march": 3,
    "abr": 4, "abril": 4, "apr": 4, "april": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6, "june": 6, "jul": 7, "julio": 7, "july": 7,
    "ago": 8, "agosto": 8, "aug": 8, "august": 8, "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9, "september": 9,
    "oct": 10, "octubre": 10, "october": 10, "nov": 11, "noviembre": 11, "november": 11, "dic": 12, "diciembre": 12, "dec": 12,
    "december": 12,
}
MONTH_RX = "|".join(sorted(MONTHS, key=len, reverse=True))
TZ_HOURS = {"cest": 2, "cet": 1, "utc": 0, "gmt": 0, "z": 0, "bst": 1, "wet": 0, "west": 1, "eet": 2, "eest": 3, "pst": -8, "pdt": -7,
            "mst": -7, "mdt": -6, "est": -5, "edt": -4, "cst": -6, "cdt": -5, "pt": -8, "et": -5}
CUE = r"(?:finaliza|finalizan|termina|terminan|acaba|acaban|hasta|valid[oa]\s+hasta|vence|ends?|ending|until|through|expires?|till|before)"
TIME = r"(?:(?:a\s+las|at|a\s+la|@)\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|h\b|hs\b)?\s*(cest|cet|utc|gmt|bst|pst|pdt|est|edt|cst|cdt|mst|mdt|pt|et|z)?"
DATE_ES = re.compile(CUE + r"\W{0,25}?(?:(?:el|on|the|dia)\s+)*(?:(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\W{0,3})?"
                     r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:de\s+)?(" + MONTH_RX + r")\b\.?(?:\s*(?:de\s+)?(\d{4}))?(?:\W{1,4}" + TIME + r")?")
DATE_EN = re.compile(CUE + r"\W{0,25}?(?:(?:on|the)\s+)*(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\W{0,3})?(" + MONTH_RX +
                     r")\b\.?\s*(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?(?:\W{1,4}" + TIME + r")?")
HOURS_RE = re.compile(r"\b(?:for|durante|por|en solo|in)\s+(\d{1,3})\s?(?:h|hs|hr|hrs|horas|hours)\b|\b(\d{1,3})\s?(?:h|hs)\b(?=\W)", re.I)
TODAY_RE = re.compile(r"\b(?:solo\s+hoy|solamente\s+hoy|only\s+today|today\s+only|ends\s+today|termina\s+hoy|finaliza\s+hoy|hoy\s+ultimo\s+dia)\b")


def _last_sunday(year: int, month: int) -> datetime:
    d = datetime(year, month, 31, tzinfo=timezone.utc)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def madrid_offset(moment: datetime) -> int:
    """UTC offset (hours) of central Europe at ``moment``: summer time from the last Sunday of March to that of October."""
    year = moment.year
    start = _last_sunday(year, 3).replace(hour=1)
    end = _last_sunday(year, 10).replace(hour=1)
    return 2 if start <= moment.astimezone(timezone.utc) < end else 1


def _local_ts(year: int, month: int, day: int, hour: int, minute: int, offset: int | None) -> int | None:
    try:
        naive = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    if offset is None:
        offset = madrid_offset(naive)
    return int((naive - timedelta(hours=offset)).timestamp())


def _clock(hh: str | None, mm: str | None, ampm: str | None) -> tuple[int, int, bool]:
    """(hour, minute, explicit): a missing time means the end of that day."""
    if hh is None or hh == "":
        return 23, 59, False
    hour, minute = int(hh), int(mm or 0)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return 23, 59, False
    return hour, minute, True


def end_of_sale(subject: str, text: str, mail_ts: int) -> int | None:
    """Epoch seconds at which the sale ends, when the mail says so ("finaliza el 1 OCT 7:00pm CEST", "ends October 1st, 2026,
    at 1 PM UTC", "for 72H", "solo hoy"); ``None`` otherwise. A date without a year is the next one after the mail date."""
    blob = fold(strip_urls(subject + "\n" + (text or "")[:8000]))
    blob = re.sub(r"\s+,", ",", blob)
    blob = re.sub(r"[ \t\r\n]+", " ", blob)
    mail = datetime.fromtimestamp(mail_ts or datetime.now(timezone.utc).timestamp(), timezone.utc)
    best: int | None = None
    for rx, order in ((DATE_ES, "dm"), (DATE_EN, "md")):
        for m in rx.finditer(blob):
            g = m.groups()
            day, mon = (g[0], g[1]) if order == "dm" else (g[1], g[0])
            month = MONTHS.get(mon)
            if not month:
                continue
            year = int(g[2]) if g[2] else None
            hh = g[3]
            if hh and not (g[4] or g[5] or g[6] or re.search(r"(?:a\s+las|a\s+la|at|@)\s*" + hh, m.group(0))):
                hh = None  # a bare number after the date is not a time
            hour, minute, explicit = _clock(hh, g[4] if hh else None, g[5] if g[5] in ("am", "pm") else None)
            tz = TZ_HOURS.get(g[6] or "") if hh else None
            ts = None
            for yr in ([year] if year else [mail.year, mail.year + 1]):
                ts = _local_ts(yr, month, int(day), hour, minute, tz)
                if ts is not None and (year or ts >= mail_ts - 2 * 86400):
                    break
            if ts is not None and (best is None or ts < best):
                best = ts
    if best is not None:
        return best
    m = HOURS_RE.search(blob)
    if m:
        hours = int(m.group(1) or m.group(2))
        if (m.group(1) and 1 <= hours <= 24 * 14) or hours in (12, 24, 36, 48, 72, 96, 120, 168):
            return int(mail_ts) + hours * 3600
    if TODAY_RE.search(blob):
        return _local_ts(mail.year, mail.month, mail.day, 23, 59, None)
    return None


# ----------------------------------------------------------------------------- titles and links
JUNK_ALT = ("icon", "logo", "facebook", "instagram", "twitter", "youtube", "tiktok", "linkedin", "pinterest", "spacer", "pixel", "badge",
            "avatar", "separator", "unsubscribe", "navegador", "browser", "app store", "google play", "whatsapp", "telegram", "discord",
            "reddit", "twitch", "social", "banner", "header", "footer", "button", "boton", "newsletter")
JUNK_EXACT = {"steam", "gog", "gog.com", "image", "imagen", "comprar", "buy now", "shop now", "ver oferta", "view deal", "ver mas", "more",
              "learn more", "click here", "aqui", "here", "mas info", "info", "new", "nuevo", "sale", "oferta", "ofertas", "deal", "deals",
              "free", "gratis", "ver ahora", "ver todo", "ver todas", "shop", "tienda", "store", "play", "jugar", "descargar"}


def good_alt(alt: Any) -> bool:
    shown = clean_title(alt, 100)
    low = fold(shown)
    if len(shown) < 3 or len(shown) > 90 or low in JUNK_EXACT:
        return False
    if any(w in low for w in JUNK_ALT) or re.match(r"^(https?:|www\.)", low) or re.search(r"\.(png|jpe?g|gif|webp|svg)$", low):
        return False
    if re.fullmatch(r"[\d\s.,%\u20ac$\u00a3:+x/-]+", low) or DISC_NEG.search(shown) and len(shown) < 12:
        return False
    return True


def titles_from(images: Any, exclude: str = "") -> list[str]:
    """Item titles taken from image alt texts: social icons, logos, buttons and discount badges are dropped."""
    out: list[str] = []
    seen = {fold(exclude)} if exclude else set()
    for alt in images or []:
        if not good_alt(alt):
            continue
        shown = clean_title(alt, 100)
        key = fold(shown)
        if key in seen:
            continue
        seen.add(key)
        out.append(shown)
        if len(out) >= MAX_TITLES:
            break
    return out


REDIRECT_KEYS = ("url", "u", "redirect", "redirect_url", "redirecturl", "link", "target", "dest", "destination", "to", "r", "q")
TRACK_PREFIXES = ("utm_", "mc_", "_hs")
TRACK_EXACT = ("snr", "ser", "gclid", "fbclid", "cid", "eid", "c2id", "goal", "mkt_tok", "e")
SKIP_LINK = ("unsubscribe", "darse-de-baja", "darsedebaja", "preferenc", "optout", "opt-out", "manage-subscription", "mailchi.mp", "list-manage.com",
             "view-in-browser", "viewinbrowser", "webversion", "privacy", "privacidad", "terms", "condiciones", "mailto:", "facebook.com",
             "instagram.com", "twitter.com", "youtube.com", "tiktok.com", "linkedin.com", "pinterest.com", "/help", "/ayuda")


def unwrap(url: str) -> str:
    """The destination of a redirect link that carries it as a parameter (never fetched, only read from the text)."""
    for _ in range(2):
        try:
            query = parse_qsl(urlparse(url).query, keep_blank_values=False)
        except ValueError:
            break
        inner = next((unquote(v) for k, v in query if k.lower() in REDIRECT_KEYS and unquote(v).lower().startswith("http")), "")
        if not inner:
            break
        url = inner
    return url


def clean_url(url: str) -> str:
    """Drop the tracking parameters and the fragment; keep the rest of the link exactly as the mail has it."""
    url = unwrap(str(url or "").strip())
    try:
        parts = urlparse(url)
        keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in TRACK_EXACT and not k.lower().startswith(TRACK_PREFIXES)]
        return urlunparse(parts._replace(query=urlencode(keep), fragment=""))[:600]
    except ValueError:
        return url[:600]


def store_links(links: Any, store: dict[str, Any]) -> list[tuple[str, str]]:
    """``(clean url, label)`` of the links that lead to the shop itself, without unsubscribe, social or tracker links."""
    out, seen = [], set()
    for link in links or []:
        raw = str(link.get("url") if isinstance(link, dict) else link or "")
        label = str(link.get("label") or "") if isinstance(link, dict) else ""
        url = clean_url(raw)
        low = url.lower()
        host = domain_of(urlparse(url).netloc.split(":")[0]) if "://" in url else ""
        if not host or not any(domain_matches(host, d) for d in store.get("domains", [])) or any(w in low for w in SKIP_LINK):
            continue
        if url not in seen:
            seen.add(url)
            out.append((url, label))
    return out


SALE_PATH = re.compile(r"oferta|offer|sale|deal|descuento|rebaja|promo|discount|outlet|saldo|special", re.I)


def best_link(found: list[tuple[str, str]]) -> str:
    deep = [(u, label) for u, label in found if len(urlparse(u).path.strip("/")) > 1]
    for u, label in deep:
        if SALE_PATH.search(u) or SALE_PATH.search(label):
            return u
    return (deep or found or [("", "")])[0][0]


# ----------------------------------------------------------------------------- rows
WISH_ES = re.compile(r"^\W*(?P<t>.+?),?\s+de\s+tu\s+lista\s+de\s+(?:deseados|deseos)", re.I)
WISH_EN = re.compile(r"^\W*(?P<t>.+?),?\s+(?:from|on|in)\s+your\s+(?:\w+\s+)?wish\s?list", re.I)
MORE_RE = re.compile(r"\s+(?:y|and)\s+(?:otros?|\d+|more|other)\s*(?P<n>\d+)?.*$", re.I)
APP_RE = re.compile(r"/app/(\d+)(?:/([^/?#]+))?")


def _blocks(text: str) -> list[dict[str, Any]]:
    """``-30%`` followed by an old and a new price (in either order): one block per discount badge in the text."""
    body = strip_urls(text)
    badges = list(DISC_NEG.finditer(body))
    out = []
    for i, d in enumerate(badges):
        stop = min(badges[i + 1].start() if i + 1 < len(badges) else len(body), d.end() + 160)
        found = prices(body[d.end():stop])
        old = new = None
        cur = ""
        if len(found) >= 2:
            a, b = found[0][1], found[1][1]
            old, new, cur = max(a, b), min(a, b), found[0][2]
        elif found:
            new, cur = found[0][1], found[0][2]
        out.append({"pct": int(d.group(1)), "old": old, "new": new, "cur": cur})
    return out


def _apps(links: Any) -> list[tuple[str, str]]:
    out, seen = [], set()
    for link in links or []:
        m = APP_RE.search(clean_url(str(link.get("url") if isinstance(link, dict) else link or "")))
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append((m.group(1), (m.group(2) or "").replace("_", " ")))
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", fold(text)) if len(w) > 1}


def wishlist_items(subject: str, text: str, images: Any, links: Any, store: dict[str, Any], ends: int | None) -> list[dict[str, Any]]:
    """One row per item of a "an item of your wishlist is on sale" mail."""
    match = WISH_ES.search(subject) or WISH_EN.search(subject)
    first, extra = "", 0
    if match:
        title = clean_title(match.group("t"))
        more = MORE_RE.search(title)
        if more:
            extra = int(more.group("n") or 0)
            title = title[:more.start()]
        first = title.strip(" ,\u00a1!")
    titles = ([first] if first else []) + [t for t in titles_from(images, first) if fold(t) != fold(first) and fold(first) not in fold(t)]
    blocks = _blocks(text)
    count = len(blocks) or min(len(titles), 1 + extra if extra else len(titles))
    if not count:
        return []
    apps = _apps(links)
    pct_all, _ = discounts(text + "\n" + subject)
    rows = []
    for i in range(min(count, MAX_TITLES)):
        block = blocks[i] if i < len(blocks) else {}
        title = titles[i] if i < len(titles) else ""
        app = apps[i] if len(apps) == count else next((a for a in apps if title and _words(a[1]) and _words(a[1]) <= _words(title)), None)
        if not title and app and app[1]:
            title = clean_title(app[1].title())
        if not title:
            title = f"Item {i + 1}"
        url = ""
        if app:
            for u, _label in store_links(links, store):
                if f"/app/{app[0]}" in u:
                    url = u
                    break
            url = url or f"https://store.steampowered.com/app/{app[0]}/"
        rows.append({"item_key": f"app:{app[0]}" if app else "t:" + slug(title), "kind": "item", "title": title, "titles": [title],
                     "discount_pct": block.get("pct") if block else (pct_all if count == 1 else None), "up_to": False,
                     "price": block.get("new") if block else None, "old_price": block.get("old") if block else None,
                     "currency": block.get("cur", "") if block else "", "ends_ts": ends, "ends_known": ends is not None, "url": url})
    return rows


def campaign_row(subject: str, text: str, images: Any, found_links: list[tuple[str, str]], ends: int | None) -> dict[str, Any]:
    pct, up_to = discounts(subject + "\n" + text)
    return {"item_key": "campaign", "kind": "campaign", "title": clean_title(subject, 160) or "(sin asunto)", "titles": titles_from(images, subject),
            "discount_pct": pct, "up_to": up_to, "price": None, "old_price": None, "currency": "", "ends_ts": ends,
            "ends_known": ends is not None, "url": best_link(found_links)}


def parse_mail(record: dict[str, Any], store: dict[str, Any]) -> list[dict[str, Any]]:
    """Deal rows of one mail from ``store``; ``[]`` when it is not a sale mail. Never raises."""
    try:
        subject = clean_title(record.get("subject"), 200)
        text = str(record.get("text") or "")
        ts = int(record.get("ts") or 0)
        if not looks_like_sale(subject, text):
            return []
        ends = end_of_sale(subject, text, ts)
        found = store_links(record.get("links"), store)
        home = "https://www." + str((store.get("domains") or [""])[0]) + "/" if store.get("domains") else ""
        rows = []
        if store.get("wishlist_style") or WISH_ES.search(subject) or WISH_EN.search(subject):
            rows = wishlist_items(subject, text, record.get("images"), record.get("links"), store, ends)
            for row in rows:
                row["url"] = row["url"] or best_link(found)
        rows = rows or [campaign_row(subject, text, record.get("images"), found, ends)]
        for row in rows:
            row["url"] = row["url"] or home           # mail links are often tracker links: fall back to the shop's own address
        return rows
    except Exception:  # noqa: BLE001 - one odd mail must never stop a scan
        return []
