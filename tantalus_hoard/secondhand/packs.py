"""Scoring packs as data.

A pack is a plain dict (JSON-serialisable) so the UI, the database and the agent tools can show and edit it:

    id, name_es, name_en, description (Spanish), description_en, default_queries, weights, rules,
    alert_min_score, settings_defaults, settings_fields

``weights`` maps signal keys to points (positive or negative); ``rules`` says which signals exist and how they are
detected (regex patterns, hard rejects, thresholds, flags). ``scoring.score_listing`` is the only interpreter.
Per-watcher ``settings`` override the pack: ``settings["weights"]`` per key, ``settings["rules"]`` per key, and
the generic rule keys (``include_any``, ``msrp``, ``price_ceiling``...) at the top level of ``settings``.

Signal labels are Spanish (they are shown to the user); code and comments are English.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

# ----------------------------------------------------------------------------------------------- books_bulk
# Radar de Libros, exactly: same weights, patterns, hard rejects, thresholds and default queries.
_BOOKS_WEIGHTS: dict[str, float] = {
    # positive signals
    "free_or_zero_price": 10,       # gratis / regalo / 0 €
    "getting_rid_of_it": 8,         # "me los quito", "me deshago", "para recoger"
    "complete_library": 7,          # biblioteca completa
    "lot": 6,                       # lote
    "collection": 6,                # colección
    "boxes": 5,                     # caja/cajas
    "moving": 5,                    # mudanza
    "many_books_mentioned": 5,      # "muchos libros"
    "high_quantity_detected": 4,    # estimated quantity is high
    "very_close_location": 3,       # very close location
    "in_active_municipality": 4,    # location in one of the configured active municipalities
    # negative signals (typical false positives)
    "free_shipping": -10,           # envío gratis
    "digital_or_ebook": -10,        # ebook / PDF / digital
    "free_download": -8,            # descarga gratis
    "textbook": -7,                 # libro de texto
    "encyclopedia": -5,             # enciclopedia
    "individual_sale": -5,          # single book at a normal price
    "high_price": -5,               # high price for a lot
}

_BOOKS_PATTERNS: dict[str, dict[str, str]] = {
    "getting_rid_of_it": {"regex": r"\b(me lo[s]? quito|me deshago|para recoger)\b", "label": "se quiere deshacer de ellos"},
    "complete_library": {"regex": r"\bbiblioteca (completa|entera)\b", "label": "biblioteca completa"},
    "lot": {"regex": r"\blote(s)?\b", "label": "lote de libros"},
    "collection": {"regex": r"\bcoleccion(es)?\b", "label": "colección"},
    "boxes": {"regex": r"\bcaja(s)?\b", "label": "cajas de libros"},
    "moving": {"regex": r"\bmudanza(s)?\b|\bnos mudamos\b|\bme mudo\b|\bmudarnos\b", "label": "mudanza"},
    "many_books_mentioned": {"regex": r"\bmuch[íi]?simos?\b|\bmuchos libros\b", "label": "menciona muchos libros"},
    "free_shipping": {"regex": r"\benvio gratis\b|\bgastos de envio gratis\b|\benvio gratuito\b", "label": "envío gratis (no el lote)"},
    "digital_or_ebook": {
        "regex": (r"\bebook\b|\be-book\b|\bpdf\b|\bversion digital\b|\blibro(s)? digital(es)?\b"
                  r"|\blibro(s)? electronico(s)?\b|\bdescarga digital\b"),
        "label": "parece digital/ebook",
    },
    "free_download": {"regex": r"\bdescarga gratis\b|\bdescarga gratuita\b", "label": "descarga gratuita (no físico)"},
    "textbook": {"regex": r"\blibros? de texto\b", "label": "libro de texto"},
    "encyclopedia": {"regex": r"\benciclopedia\b", "label": "enciclopedia"},
}

_BOOKS_LABELS: dict[str, str] = {
    "free_or_zero_price": "gratis",
    "high_quantity_detected": "cantidad estimada alta",
    "very_close_location": "ubicación cercana",
    "in_active_municipality": "en un municipio activo",
    "individual_sale": "venta individual",
    "high_price": "precio elevado",
}

_BOOKS_LLM_GOAL = (
    "The user runs a 'free books radar': they want listings that give away or sell for almost nothing books IN BULK "
    "(lots, collections, whole libraries, house clearances, moving boxes). Not relevant: a single book at a normal "
    "price, ebooks or digital downloads, a course that gives away a book, free shipping when buying, subscriptions."
)

BOOKS_BULK: dict[str, Any] = {
    "id": "books_bulk",
    "name_es": "Libros en lote",
    "name_en": "Books in bulk",
    "description": ("Lotes, colecciones y bibliotecas de libros gratis o casi gratis (vaciados, mudanzas). "
                    "Pesos y reglas de Radar de Libros."),
    "description_en": "Free or nearly free lots, collections and libraries of books (clearances, moves). Radar de Libros weights and rules.",
    "default_queries": [
        "lote libros", "libros gratis", "biblioteca completa", "regalo libros", "vaciado piso libros",
        "colección libros", "caja libros", "libros mudanza", "libros recoger", "libros 0 euros", "regalo biblioteca",
    ],
    "weights": _BOOKS_WEIGHTS,
    "rules": {
        "patterns": _BOOKS_PATTERNS,
        "labels": _BOOKS_LABELS,
        "hard_rejects": [
            {"regex": r"\bsuscripcion(es)?\b", "reason": "El anuncio exige una suscripción, no es un regalo directo",
             "unless": ["lot", "collection", "boxes", "moving", "complete_library"]},
            {"regex": r"\bcurso(s)?\s+(online|con|de)\b", "reason": "Parece un curso, no un lote de libros físicos",
             "unless": ["lot", "collection", "boxes", "moving", "complete_library"]},
            {"regex": r"\b(videojuego|videojuegos|playstation|ps[345]|xbox|nintendo|switch 2|consola)\b",
             "reason": "Es un videojuego o una consola, no libros", "unless": ["complete_library", "many_books_mentioned"]},
            {"regex": r"\bdescarga(s)?\b", "reason": "Parece una descarga digital, no libros físicos",
             "unless": ["lot", "collection", "boxes", "moving", "complete_library"]},
        ],
        "quantity": "books",
        "include_any": ["libro", "libros", "biblioteca", "novela", "novelas", "enciclopedia", "enciclopedias", "tomo", "tomos",
                        "comic", "comics", "manga", "mangas", "coleccion de libros", "saga", "edicion de bolsillo"],
        "include_scope": "title_lead",
        "bulk_keys": ["lot", "collection", "boxes", "moving", "complete_library", "many_books_mentioned",
                      "high_quantity_detected"],
        "high_quantity_threshold": 20,
        "distance_very_close_km": 5.0,
        "distance_max_relevant_km": 40.0,
        "high_price_threshold": 20.0,
        "cheap_per_unit_threshold": 1.0,
        "individual_sale_penalty": True,
        "relevant_above": 0,
        "category_relevant": "bulk_free_books",
        "category_irrelevant": "false_positive",
        "category_reject": "false_positive",
        "no_signal_reason": "Sin señales claras de lote grande o gratuito",
        "llm_goal": _BOOKS_LLM_GOAL,
        "llm_band": 4,
    },
    "alert_min_score": 8,
    "settings_defaults": {
        "origin_location": "Madrid",
        "radius_km": 20,
        "municipalities": [],
        "price_ceiling": 15.0,
    },
    "settings_fields": [
        {"key": "origin_location", "type": "text", "label_es": "Ubicación de referencia", "label_en": "Origin location"},
        {"key": "radius_km", "type": "number", "label_es": "Radio (km)", "label_en": "Radius (km)"},
        {"key": "municipalities", "type": "list", "label_es": "Municipios activos", "label_en": "Active municipalities"},
        {"key": "price_ceiling", "type": "number", "label_es": "Precio máximo (€)", "label_en": "Max price (€)"},
    ],
}

# ----------------------------------------------------------------------------------------------- generic engine
_GENERIC_WEIGHTS: dict[str, float] = {
    "include_any_match": 6,           # at least one "include any" keyword found
    "include_all_match": 4,           # all "include all" keywords found
    "title_match": 3,                 # a keyword is in the title, not only in the description
    "sealed": 8,                      # precintado / sellado / sealed (only when sealed_bonus is on)
    "new_item": 3,                    # nuevo / a estrenar (only when sealed_bonus is on)
    "receipt": 2,                     # invoice / receipt mentioned (only when sealed_bonus is on)
    "below_msrp": 8,                  # price at or under the MSRP; scales with the discount (full at 25 %+)
    "above_msrp": -6,                 # over the MSRP but under the scalper limit
    "scalper": -40,                   # price above msrp * scalper_multiplier
    "suspiciously_cheap": -12,        # far below the MSRP: fake / scam risk
    "over_ceiling": -25,              # price above the user's ceiling
    "very_close_location": 3,
    "in_active_municipality": 4,
    "too_far": -20,                   # farther than max_distance_km
    "no_shipping": -15,               # shipping required but not offered
    "shipping_offered": -10,          # shipping forbidden (pickup only) but offered
    "reserved": -12,                  # listing is reserved
    "recent": 3,                      # published in the last 3 h (a third of it within 24 h)
}

_SEALED_PATTERNS: dict[str, dict[str, str]] = {
    "sealed": {"regex": r"\b(precintad[oa]s?|sellad[oa]s?|sealed|sin abrir|factory sealed|nunca abiert[oa]s?)\b",
               "label": "precintado / sellado"},
    "new_item": {"regex": r"\b(nuev[oa]s?|a estrenar)\b", "label": "nuevo"},
    "receipt": {"regex": r"\b(factura|ticket de compra|comprado en)\b", "label": "con factura o ticket"},
}

_GENERIC_LABELS: dict[str, str] = {
    "very_close_location": "ubicación cercana",
    "in_active_municipality": "en un municipio activo",
    "reserved": "anuncio reservado",
    "no_shipping": "no ofrece envío",
    "shipping_offered": "ofrece envío (solo recogida deseada)",
    "over_ceiling": "por encima de tu precio máximo",
    "too_far": "fuera del radio",
}

_GENERIC_FIELDS: list[dict[str, str]] = [
    {"key": "include_any", "type": "list", "label_es": "Palabras clave (alguna)", "label_en": "Keywords (any)"},
    {"key": "include_all", "type": "list", "label_es": "Palabras clave (todas)", "label_en": "Keywords (all)"},
    {"key": "exclude", "type": "list", "label_es": "Palabras excluidas", "label_en": "Excluded words"},
    {"key": "price_ceiling", "type": "number", "label_es": "Precio máximo (€)", "label_en": "Max price (€)"},
    {"key": "msrp", "type": "number", "label_es": "PVP oficial (€)", "label_en": "MSRP (€)"},
    {"key": "scalper_multiplier", "type": "number", "label_es": "Multiplicador de reventa", "label_en": "Scalper multiplier"},
    {"key": "max_distance_km", "type": "number", "label_es": "Distancia máxima (km)", "label_en": "Max distance (km)"},
    {"key": "shipping", "type": "enum:any|required|forbidden", "label_es": "Envío", "label_en": "Shipping"},
    {"key": "sealed_bonus", "type": "bool", "label_es": "Bonus por precintado / nuevo", "label_en": "Sealed / new bonus"},
    {"key": "origin_location", "type": "text", "label_es": "Ubicación de referencia", "label_en": "Origin location"},
    {"key": "radius_km", "type": "number", "label_es": "Radio (km)", "label_en": "Radius (km)"},
]

GENERIC: dict[str, Any] = {
    "id": "generic",
    "name_es": "Genérico configurable",
    "name_en": "Generic (configurable)",
    "description": ("Puntuación por palabras clave (alguna / todas), exclusiones, precio máximo, PVP con multiplicador "
                    "anti-reventa, distancia, envío y anuncios reservados."),
    "description_en": "Keyword (any / all) scoring with exclusions, price ceiling, MSRP with an anti-scalper multiplier, distance, shipping and reserved listings.",
    "default_queries": [],
    "weights": _GENERIC_WEIGHTS,
    "rules": {
        "patterns": {},
        "labels": _GENERIC_LABELS,
        "hard_rejects": [],
        "include_any": [],
        "include_all": [],
        "exclude": [],
        "price_ceiling": None,
        "msrp": None,
        "scalper_multiplier": 1.5,
        "suspicious_ratio": 0.4,
        "max_distance_km": None,
        "enforce_radius": False,
        "shipping": "any",
        "sealed_bonus": False,
        "sealed_patterns": _SEALED_PATTERNS,
        "recency_bonus": True,
        "distance_very_close_km": 5.0,
        "distance_max_relevant_km": 40.0,
        "relevant_above": 0,
        "category_relevant": "match",
        "category_irrelevant": "no_match",
        "category_reject": "rejected",
        "no_signal_reason": "Sin señales claras de que sea lo que buscas",
        "llm_goal": "The user wants listings that match their keywords at a fair price from a genuine, available item.",
        "llm_band": 4,
    },
    "alert_min_score": 8,
    "settings_defaults": {"radius_km": 30},
    "settings_fields": _GENERIC_FIELDS,
}

# ----------------------------------------------------------------------------------------------- collectibles_sealed
_COLLECTIBLES_WEIGHTS = dict(_GENERIC_WEIGHTS)
_COLLECTIBLES_WEIGHTS.update({
    "empty_box": -40,
    "proxy_or_fake": -40,
    "opened": -30,
    "accessory": -25,
    "single_cards": -15,
    "trade": -8,
    "wanted": -50,
})

_COLLECTIBLES_PATTERNS: dict[str, dict[str, str]] = {
    "empty_box": {
        "regex": (r"\bvaci[oa]s?\b|\bcaja vacia\b|\bsolo (la )?caja\b|\bsolo el estuche\b|\bsin sobres\b"
                  r"|\bsin cartas\b|\bsin contenido\b|\bsolo caja\b|\bempty box\b"),
        "label": "caja vacía o sin sobres",
    },
    "proxy_or_fake": {
        "regex": r"\bproxy\b|\bproxies\b|\breplica\b|\bfake\b|\bfalso\b|\bfalsa\b|\bimitacion\b|\bcustom\b|\bno original\b",
        "label": "proxy, réplica o falsificación",
    },
    "opened": {
        "regex": r"(?<!no )(?<!nunca )(?<!sin )\babiert[oa]s?\b|\bopened\b",
        "label": "abierto",
    },
    "accessory": {
        "regex": r"\bacrilic[oa]s?\b|\bprotectores?\b|\bfundas?\b|\bexpositor(es)?\b|\bcarcasa\b|\btapete\b|\bplaymat\b",
        "label": "accesorio, no el producto",
    },
    "single_cards": {"regex": r"\bcartas? sueltas?\b|\bsingles?\b", "label": "cartas sueltas"},
    "trade": {"regex": r"\bcambio\b|\bintercambio\b|\bpermuto\b|\btrade\b", "label": "busca cambio"},
    "wanted": {"regex": r"\b(busco|compro|se busca|se compra|wanted)\b", "label": "es una búsqueda, no una venta"},
}

_COLLECTIBLES_LABELS = dict(_GENERIC_LABELS)

COLLECTIBLES_SEALED: dict[str, Any] = {
    "id": "collectibles_sealed",
    "name_es": "Coleccionismo precintado (TCG)",
    "name_en": "Sealed collectibles (TCG)",
    "description": ("Producto TCG precintado (Pokémon ETB, Booster Bundle...): premia precintado / sellado / nuevo, "
                    "descarta cajas vacías, proxies, réplicas, abiertos y accesorios, y penaliza la reventa por encima "
                    "de 1,3 × el PVP."),
    "description_en": "Sealed TCG product (Pokémon ETB, Booster Bundle...): rewards sealed / new, rejects empty boxes, proxies, replicas, opened items and accessories, penalises resale above 1.3 x MSRP.",
    "default_queries": ["pokemon etb", "elite trainer box pokemon", "caja de entrenador élite pokemon",
                        "booster bundle pokemon", "pokemon etb precintado"],
    "weights": _COLLECTIBLES_WEIGHTS,
    "rules": {
        **copy.deepcopy(GENERIC["rules"]),
        "patterns": _COLLECTIBLES_PATTERNS,
        "labels": _COLLECTIBLES_LABELS,
        "include_any": ["etb", "elite trainer box", "caja de entrenador", "booster bundle", "display", "booster box",
                        "caja de sobres"],
        "scalper_multiplier": 1.3,
        "sealed_bonus": True,
        "llm_goal": ("The user wants a genuine SEALED trading-card product (Pokémon Elite Trainer Box, Booster Bundle, "
                     "display) at a fair price. Not relevant: empty boxes, opened or resealed items, proxies, replicas, "
                     "accessories (sleeves, acrylic cases, playmats), single cards, wanted ads and trades."),
    },
    "alert_min_score": 12,
    "settings_defaults": {"radius_km": 50, "scalper_multiplier": 1.3},
    "settings_fields": _GENERIC_FIELDS,
}

PACKS: dict[str, dict[str, Any]] = {
    BOOKS_BULK["id"]: BOOKS_BULK,
    GENERIC["id"]: GENERIC,
    COLLECTIBLES_SEALED["id"]: COLLECTIBLES_SEALED,
}


def get_pack(pack_id: str) -> Optional[dict[str, Any]]:
    """A deep copy of the pack (safe to edit), or None."""
    pack = PACKS.get(pack_id)
    return copy.deepcopy(pack) if pack else None


def list_packs() -> list[dict[str, Any]]:
    """Light listing for the UI: id, names, description, default queries, alert threshold, settings fields."""
    return [
        {
            "id": p["id"], "name_es": p["name_es"], "name_en": p["name_en"], "description": p["description"],
            "description_en": p["description_en"], "default_queries": list(p["default_queries"]),
            "alert_min_score": p["alert_min_score"], "settings_defaults": copy.deepcopy(p["settings_defaults"]),
            "settings_fields": copy.deepcopy(p["settings_fields"]),
        }
        for p in PACKS.values()
    ]
