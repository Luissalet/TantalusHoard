"""What the user wants and what they already own: the lists a mail deal is checked against.

Sources, all read-only and all optional:

* the game library of the sibling game-collection app (a JSON file it keeps on this computer): games that are still in the
  backlog and owned on no platform, or tagged ``wishlist`` / ``deseado`` / ``wanted``, are wanted; games owned on a platform
  (or played) are owned and never raise an alert;
* the book collection app is a cloud database with no local file, so it is reported as unreachable instead of guessed;
* the user's own list in Settings (``mail.deals.wishlist``: one title per line or comma separated);
* the watchers of this app (matched by the caller through ``watch_hits``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from .parse import fold

WANT_TAGS = {"wishlist", "deseado", "deseados", "wanted", "quiero", "lista de deseos", "lista-de-deseos"}
GENERIC = {"the", "game", "games", "juego", "juegos", "libro", "libros", "book", "books", "demo", "test", "pack", "bundle", "edition"}
SOURCE_GAMES = "gamerhoard"
SOURCE_BOOKS = "bookhoard"
SOURCE_MANUAL = "manual"
SOURCE_STORE = "store"
SOURCE_WATCH = "watch"


def norm(text: Any) -> str:
    """Folded alphanumeric words separated by single spaces ("Assassin's Creed: Origins" -> "assassins creed origins")."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(str(text or "")).replace("'", "").replace("\u2019", "")))


def contains(haystack: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {haystack} "


def usable(title: str) -> bool:
    n = norm(title)
    return len(n) >= 4 and n not in GENERIC and not n.isdigit()


# ----------------------------------------------------------------------------- sources
def games_library_path(configured: str = "") -> Path | None:
    """The setting, then the env var the game app honours, then its default place. ``None`` when no such file exists."""
    for raw in (configured, os.environ.get("GAMERHOARD_DATA_FILE", ""), str(Path.home() / ".gamerhoard" / "library.json")):
        raw = str(raw or "").strip().strip('"')
        if raw and Path(raw).is_file():
            return Path(raw)
    return None


def load_games(path: Path | None) -> dict[str, Any]:
    """``{"wanted": [...], "owned": [...], "status": {...}}`` from the game app's library file."""
    status: dict[str, Any] = {"source": SOURCE_GAMES, "reachable": False, "wanted": 0, "owned": 0, "detail": ""}
    out = {"wanted": [], "owned": [], "status": status}
    if path is None:
        status["detail"] = "library file not found (set mail.deals.gamerhoard_file or GAMERHOARD_DATA_FILE)"
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        games = data.get("games") if isinstance(data, dict) else None
        if not isinstance(games, list):
            raise ValueError("unknown format")
    except (OSError, ValueError) as error:
        status["detail"] = f"library file not readable ({type(error).__name__})"
        return out
    for game in games:
        if not isinstance(game, dict) or not str(game.get("title") or "").strip():
            continue
        entry = {"title": str(game["title"]).strip(), "source": SOURCE_GAMES,
                 "steam_app": str(game.get("steamAppId") or "")}
        tags = {fold(t) for t in game.get("tags") or []}
        owned_on = [p for p in game.get("ownedPlatforms") or [] if str(p).strip()]
        played = bool(game.get("playtimeMinutes")) or game.get("state") in ("playing", "paused", "completed", "dropped")
        if tags & WANT_TAGS and not owned_on:
            out["wanted"].append(entry)
        elif owned_on or played:
            out["owned"].append(entry)
        elif game.get("state") == "backlog":
            out["wanted"].append(entry)
    status.update(reachable=True, wanted=len(out["wanted"]), owned=len(out["owned"]), detail=f"{len(games)} games read")
    return out


def books_status() -> dict[str, Any]:
    return {"source": SOURCE_BOOKS, "reachable": False, "wanted": 0, "owned": 0,
            "detail": "cloud database, no local file to read; add the titles you want to mail.deals.wishlist"}


def parse_manual(raw: Any) -> list[dict[str, str]]:
    items = raw.replace(";", "\n").replace(",", "\n").split("\n") if isinstance(raw, str) else list(raw or [])
    seen, out = set(), []
    for item in items:
        title = str(item).strip()
        if title and norm(title) not in seen:
            seen.add(norm(title))
            out.append({"title": title, "source": SOURCE_MANUAL, "steam_app": ""})
    return out[:300]


class Wishlist:
    """The wanted and owned titles, ready for matching."""

    def __init__(self, wanted: Iterable[dict[str, str]] = (), owned: Iterable[dict[str, str]] = (), sources: Iterable[dict[str, Any]] = ()):
        self.wanted = [w for w in wanted if usable(w["title"])]
        self.owned = [w for w in owned if usable(w["title"])]
        self.sources = list(sources)
        self._wanted_n = [(norm(w["title"]), w) for w in self.wanted]
        self._owned_n = [(norm(w["title"]), w) for w in self.owned]

    @classmethod
    def load(cls, *, games_file: str = "", manual: Any = "") -> "Wishlist":
        games = load_games(games_library_path(games_file))
        manual_items = parse_manual(manual)
        sources = [games["status"], books_status(), {"source": SOURCE_MANUAL, "reachable": True, "wanted": len(manual_items), "owned": 0,
                                                     "detail": "mail.deals.wishlist"}]
        return cls(games["wanted"] + manual_items, games["owned"], sources)

    def summary(self) -> dict[str, Any]:
        return {"wanted": len(self.wanted), "owned": len(self.owned), "sources": self.sources}


def _candidates(deal: dict[str, Any]) -> list[tuple[str, bool]]:
    """Texts of the deal a title can be found in: ``(normalised text, is an item title of its own)``."""
    out = [(norm(deal.get("title")), deal.get("kind") == "item")]
    out += [(norm(t), True) for t in deal.get("titles") or []]
    return [(n, item) for n, item in out if n]


def match_title(deal: dict[str, Any], entries: list[tuple[str, dict[str, str]]]) -> dict[str, str] | None:
    app = str(deal.get("item_key") or "")
    for phrase, entry in entries:
        if entry.get("steam_app") and app == f"app:{entry['steam_app']}":
            return entry
    for text, is_item in _candidates(deal):
        for phrase, entry in entries:
            if contains(text, phrase) or (is_item and len(text.split()) >= 2 and contains(phrase, text)):
                return entry
    return None


def evaluate(deal: dict[str, Any], wishlist: Wishlist, *, store_wishlist: bool = False,
             watch_hits: Callable[[str], list[dict[str, str]]] | None = None) -> dict[str, Any]:
    """``{"matched", "wishlist", "owned", "source", "label"}`` for one deal.

    ``wishlist`` is True when the title is on a wishlist (the user's lists, or the shop's own wishlist mails);
    ``matched`` adds the watchers of this app. An owned title never matches."""
    owned = match_title(deal, wishlist._owned_n)
    want = match_title(deal, wishlist._wanted_n)
    result = {"matched": False, "wishlist": False, "owned": bool(owned and not want), "source": "", "label": ""}
    if owned and not want:
        result["label"] = owned["title"]
        return result
    if want:
        result.update(matched=True, wishlist=True, source=want["source"], label=want["title"])
    elif store_wishlist and deal.get("kind") == "item":
        result.update(matched=True, wishlist=True, source=SOURCE_STORE, label=str(deal.get("title") or ""))
    elif watch_hits is not None:
        hits = watch_hits(" ".join([str(deal.get("title") or ""), *map(str, deal.get("titles") or [])]))
        if hits:
            result.update(matched=True, source=SOURCE_WATCH, label=hits[0]["name"], watcher_id=hits[0]["id"])
    return result
