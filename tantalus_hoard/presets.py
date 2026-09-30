"""Ready-made watchers: three example alerts (a trading-card restock in Madrid shops, DGX Spark on sale in Spain, RTX Spark
128 GB in Europe), the free-books radar absorbed from Radar de Libros,
and a disabled second-hand variant for sealed Pokémon products.

Installed once on the first start (empty database) and on demand with ``presets_install``. Each preset is plain
data: the same shape ``watcher_create`` accepts, plus the targets to create.
"""

from __future__ import annotations

import copy
from typing import Any

from .model import (LOCAL_RESTOCK, MODE_AVAILABILITY, MODE_INFORMATION, MODE_SECONDHAND, NEW_SKU, PREORDER_OPEN,
                    PRICE_DROP, PRICE_THRESHOLD_CROSSED, RESTOCK, RESTOCK_DATE_CONFIRMED)

POKEMON_STORES: list[str] = []  # add the shops you want to prefer

PRESETS: list[dict[str, Any]] = [
    {
        "id": "pokemon-30-etb-madrid",
        "name": "Pokémon JCC 30.º aniversario — ETB y Booster Bundle",
        "mode": MODE_AVAILABILITY,
        "interval_min": 20,
        "discovery_interval_h": 12,
        "notes": "Stock o reposición confirmada en tiendas de la zona. Sin reventa.",
        "config": {
            "product": {"terms": ["pokemon", "30", "aniversario"], "must": ["pokemon"], "types": ["Elite Trainer Box", "ETB", "Booster Bundle"],
                        "exclude": ["funda", "fundas", "protector", "carpeta", "sleeves", "tapete"], "language": "es"},
            "region": "ES-MD",
            "stores": POKEMON_STORES,
            "policies": {"seller": "retail_only", "alert_on": [RESTOCK, LOCAL_RESTOCK, PREORDER_OPEN, NEW_SKU, RESTOCK_DATE_CONFIRMED],
                         "require_confidence": 75, "revalidate_seconds": 60, "cooldown_minutes": 20, "scalper_multiplier": 1.3},
            "discovery": {
                "queries": ["site:game.es Pokémon 30 aniversario ETB", "site:elcorteingles.es Pokémon 30 aniversario Elite Trainer Box",
                            "site:carrefour.es Pokémon 30 aniversario cartas", '"30 aniversario" Pokémon "Booster Bundle" España',
                            '"30 aniversario" Pokémon ETB Madrid', '"30 aniversario" Pokémon Madrid stock'],
                "terms": ["pokemon", "30", "aniversario"],
                "retailers": ["game.es", "elcorteingles.es", "carrefour.es", "xtralife.com", "amazon.es", "mediamarkt.es", "fnac.es",
                              "toysrus.es", "juguettos.com"],
            },
        },
        "targets": [
            {"url": "https://www.game.es/buscar/pokemon%2030%20aniversario", "label": "GAME — búsqueda «pokemon 30 aniversario»",
             "retailer": "GAME", "source_level": 2, "fetch_tier": "browser"},
            {"url": "https://www.elcorteingles.es/search-nwx/1/?s=pokemon+30+aniversario", "label": "El Corte Inglés — búsqueda",
             "retailer": "El Corte Inglés", "source_level": 2},
            {"url": "https://www.xtralife.com/buscar/pokemon%2030%20aniversario", "label": "xtralife — búsqueda",
             "retailer": "xtralife", "source_level": 2, "fetch_tier": "browser"},
        ],
    },
    {
        "id": "dgx-spark-es",
        "name": "NVIDIA DGX Spark Founders Edition — compra en España",
        "mode": MODE_AVAILABILITY,
        "interval_min": 60,
        "discovery_interval_h": 24,
        "notes": "Avisar solo cuando se pueda comprar en España; interés especial si cuesta 4.800 € o menos.",
        "config": {
            "product": {"terms": ["dgx", "spark"], "exclude": ["rtx 50", "geforce"]},
            "region": "ES",
            "policies": {"seller": "retail_only", "alert_on": [RESTOCK, PREORDER_OPEN, PRICE_THRESHOLD_CROSSED, PRICE_DROP],
                         "price_threshold": 4800, "require_confidence": 75, "revalidate_seconds": 60, "cooldown_minutes": 30},
            "discovery": {
                "queries": ["site:nvidia.com/es-es DGX Spark comprar España", "site:nvidia.com DGX Spark marketplace Spain",
                            "DGX Spark comprar España precio"],
                "terms": ["dgx", "spark"],
            },
        },
        "targets": [
            {"url": "https://marketplace.nvidia.com/es-es/enterprise/personal-ai-supercomputers/dgx-spark/",
             "label": "NVIDIA Marketplace España — DGX Spark", "retailer": "NVIDIA", "source_level": 1, "adapter": "html",
             "price_threshold": 4800},
            {"url": "nvidia-api:search?term=DGX%20Spark&locale=es-es", "label": "NVIDIA API de productos — «DGX Spark»",
             "retailer": "NVIDIA", "source_level": 1, "price_threshold": 4800},
        ],
    },
    {
        "id": "dgx-spark-es-news",
        "name": "DGX Spark — venta en España (noticias)",
        "mode": MODE_INFORMATION,
        "interval_min": 180,
        "notes": "Complementa al vigilante de disponibilidad: el marketplace de NVIDIA bloquea la lectura automática, así que se "
                 "vigilan las noticias de venta, precio y distribuidores en España.",
        "config": {
            "sources": [
                {"kind": "search", "value": '"DGX Spark" España precio', "label": "España precio"},
                {"kind": "search", "value": '"DGX Spark" comprar disponible', "label": "Disponible"},
            ],
            "info": {"must_terms": ["dgx spark"], "boost_terms": ["españa", "precio", "euros", "€", "disponible", "comprar", "stock",
                                                                "reserva", "tienda"],
                     "exclude_terms": [], "freshness_days": 14,
                     "official_domains": ["nvidia.com", "asus.com", "dell.com", "hp.com", "lenovo.com", "msi.com", "gigabyte.com", "acer.com"]},
        },
        "targets": [],
    },
    {
        "id": "rtx-spark-128gb-eu",
        "name": "RTX Spark / N1X 128 GB en Europa",
        "mode": MODE_INFORMATION,
        "interval_min": 180,
        "notes": "Solo novedad material y fiable: páginas oficiales de OEM, precio, reserva/compra, fecha, SKU de 128 GB. "
                 "Separar hechos de filtraciones.",
        "config": {
            "sources": [
                {"kind": "search", "value": '"RTX Spark" 128GB', "label": "RTX Spark 128 GB"},
                {"kind": "search", "value": 'N1X 128GB', "label": "N1X 128 GB"},
                {"kind": "search", "value": '"RTX Spark" España precio', "label": "España"},
                {"kind": "search", "value": '"RTX Spark" Europe price preorder', "label": "Europa"},
                {"kind": "feed", "value": "https://nvidianews.nvidia.com/releases.xml", "label": "NVIDIA Newsroom"},
            ],
            "info": {"must_terms": ["rtx spark", "n1x"], "boost_terms": ["128gb", "128 gb", "europe", "europa", "españa", "spain",
                                                                         "preorder", "reserva", "precio", "price", "egpu", "benchmark"],
                     "exclude_terms": ["dgx spark"], "freshness_days": 30,
                     "official_domains": ["nvidia.com", "asus.com", "lenovo.com", "dell.com", "hp.com", "msi.com", "gigabyte.com",
                                          "acer.com"]},
        },
        "targets": [],
    },
    {
        "id": "libros-gratis-lote",
        "name": "Libros gratis o casi gratis en lote",
        "mode": MODE_SECONDHAND,
        "interval_min": 45,
        "notes": "Radar de Libros integrado: lotes, bibliotecas y vaciados de piso cerca de casa.",
        "config": {"pack": "books_bulk", "sources": ["wallapop"],
                   "queries": ["lote libros", "libros gratis", "biblioteca completa", "regalo libros", "vaciado piso libros"],
                   "settings": {"origin_location": "Madrid", "radius_km": 20, "price_ceiling": 15}},
        "targets": [],
    },
    {
        "id": "pokemon-etb-segunda-mano",
        "name": "Pokémon ETB precintada de segunda mano (≤ 1,3 × PVP)",
        "mode": MODE_SECONDHAND,
        "enabled": False,
        "interval_min": 60,
        "notes": "Desactivado: la tarea original ignora la reventa. Actívalo si quieres vigilar Wallapop sin pasar de 1,3 × PVP.",
        "config": {"pack": "collectibles_sealed", "sources": ["wallapop"],
                   "queries": ["pokemon 30 aniversario etb", "pokemon etb precintado", "booster bundle pokemon 30 aniversario"],
                   "settings": {"origin_location": "Madrid", "radius_km": 40, "msrp": 55, "include_all": ["30 aniversario"]}},
        "targets": [],
    },
]


def list_presets() -> list[dict[str, Any]]:
    return [{"id": p["id"], "name": p["name"], "mode": p["mode"], "notes": p.get("notes", ""), "targets": len(p.get("targets") or []),
             "enabled": p.get("enabled", True)} for p in PRESETS]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    for p in PRESETS:
        if p["id"] == preset_id:
            return copy.deepcopy(p)
    return None
