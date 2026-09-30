"""Approximate distance from a table of municipalities (no external geocoding, nothing leaves the machine).

The table holds approximate town-centre coordinates: the ~75 largest municipalities of the Comunidad de Madrid
plus the Spanish provincial capitals and a few other large cities. That is enough for an indicative distance in
km, not for navigation. A location that is not in the table simply has no estimated distance.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

from .textutil import normalize_text

# (display name, latitude, longitude, extra aliases). The normalised display name is always a key too.
_PLACES: list[tuple[str, float, float, tuple[str, ...]]] = [
    # --- Comunidad de Madrid
    ("Madrid", 40.4168, -3.7038, ()),
    ("Móstoles", 40.3223, -3.8649, ()),
    ("Alcalá de Henares", 40.4819, -3.3639, ()),
    ("Fuenlabrada", 40.2842, -3.7943, ()),
    ("Leganés", 40.3272, -3.7635, ()),
    ("Getafe", 40.3057, -3.7327, ()),
    ("Alcorcón", 40.3459, -3.8274, ()),
    ("Torrejón de Ardoz", 40.4550, -3.4770, ()),
    ("Parla", 40.2379, -3.7749, ()),
    ("Alcobendas", 40.5475, -3.6420, ()),
    ("Las Rozas de Madrid", 40.4923, -3.8735, ("las rozas",)),
    ("San Sebastián de los Reyes", 40.5460, -3.6280, ("sanse",)),
    ("Pozuelo de Alarcón", 40.4324, -3.8133, ()),
    ("Rivas-Vaciamadrid", 40.3260, -3.5180, ("rivas vaciamadrid", "rivas")),
    ("Coslada", 40.4238, -3.5608, ()),
    ("Valdemoro", 40.1908, -3.6763, ()),
    ("Majadahonda", 40.4735, -3.8720, ()),
    ("Collado Villalba", 40.6357, -4.0069, ()),
    ("Aranjuez", 40.0311, -3.6033, ()),
    ("Arganda del Rey", 40.3007, -3.4374, ("arganda",)),
    ("Boadilla del Monte", 40.4055, -3.8752, ()),
    ("Pinto", 40.2489, -3.6994, ()),
    ("Colmenar Viejo", 40.6590, -3.7650, ()),
    ("Tres Cantos", 40.6000, -3.7070, ()),
    ("San Fernando de Henares", 40.4240, -3.5330, ()),
    ("Galapagar", 40.5790, -4.0000, ()),
    ("Arroyomolinos", 40.2792, -3.9082, ()),
    ("Villaviciosa de Odón", 40.3573, -3.9017, ()),
    ("Navalcarnero", 40.2890, -4.0120, ()),
    ("Mejorada del Campo", 40.3920, -3.4800, ()),
    ("Paracuellos de Jarama", 40.5000, -3.5300, ()),
    ("Ciempozuelos", 40.1590, -3.6180, ()),
    ("Torrelodones", 40.5780, -3.9270, ()),
    ("Humanes de Madrid", 40.2510, -3.8280, ("humanes",)),
    ("Griñón", 40.2150, -3.8500, ()),
    ("Cubas de la Sagra", 40.2010, -3.8250, ()),
    ("Casarrubuelos", 40.1900, -3.8450, ()),
    ("Serranillos del Valle", 40.2140, -3.9020, ()),
    ("Batres", 40.2040, -3.9880, ()),
    ("San Martín de la Vega", 40.2050, -3.5700, ()),
    ("Velilla de San Antonio", 40.3690, -3.4960, ()),
    ("Daganzo de Arriba", 40.5450, -3.4600, ("daganzo",)),
    ("Meco", 40.5530, -3.3230, ()),
    ("Villalbilla", 40.4370, -3.3010, ()),
    ("Loeches", 40.3730, -3.4030, ()),
    ("Morata de Tajuña", 40.2280, -3.4470, ()),
    ("Perales de Tajuña", 40.2960, -3.3540, ()),
    ("Villarejo de Salvanés", 40.1660, -3.2760, ()),
    ("Chinchón", 40.1400, -3.4230, ()),
    ("San Lorenzo de El Escorial", 40.5880, -4.1440, ("san lorenzo de el escorial",)),
    ("El Escorial", 40.5740, -4.1280, ()),
    ("Guadarrama", 40.6720, -4.0870, ()),
    ("Cercedilla", 40.7420, -4.0630, ()),
    ("Manzanares el Real", 40.7280, -3.8640, ()),
    ("Soto del Real", 40.7500, -3.7910, ()),
    ("Miraflores de la Sierra", 40.8120, -3.7690, ()),
    ("Torrelaguna", 40.8300, -3.5340, ()),
    ("Algete", 40.5970, -3.4970, ()),
    ("Valdemorillo", 40.5040, -4.0680, ()),
    ("Villanueva del Pardillo", 40.4930, -3.9670, ()),
    ("Villanueva de la Cañada", 40.4471, -4.0033, ()),
    ("Moralzarzal", 40.6730, -3.9720, ()),
    ("Alpedrete", 40.6660, -4.0270, ()),
    ("Colmenarejo", 40.5600, -4.0180, ()),
    ("Brunete", 40.4048, -3.9924, ()),
    ("Sevilla la Nueva", 40.3559, -4.0217, ()),
    ("Aldea del Fresno", 40.3040, -4.2000, ()),
    ("Fuente el Saz de Jarama", 40.6360, -3.5100, ()),
    ("Ajalvir", 40.5180, -3.4790, ()),
    ("Cobeña", 40.5350, -3.5790, ()),
    ("Camarma de Esteruelas", 40.5300, -3.3810, ()),
    ("Torres de la Alameda", 40.3470, -3.3740, ()),
    ("El Molar", 40.7290, -3.5990, ()),
    ("Rascafría", 40.9100, -3.8770, ()),
    # --- Provincial capitals (and the two autonomous cities)
    ("A Coruña", 43.3623, -8.4115, ("la coruna", "coruna")),
    ("Albacete", 38.9943, -1.8585, ()),
    ("Alicante", 38.3452, -0.4810, ("alacant",)),
    ("Almería", 36.8340, -2.4637, ()),
    ("Ávila", 40.6564, -4.6818, ()),
    ("Badajoz", 38.8794, -6.9707, ()),
    ("Palma", 39.5696, 2.6502, ("palma de mallorca",)),
    ("Barcelona", 41.3874, 2.1686, ()),
    ("Bilbao", 43.2630, -2.9350, ()),
    ("Burgos", 42.3439, -3.6969, ()),
    ("Cáceres", 39.4753, -6.3724, ()),
    ("Cádiz", 36.5271, -6.2886, ()),
    ("Castellón de la Plana", 39.9864, -0.0513, ("castellon",)),
    ("Ciudad Real", 38.9863, -3.9291, ()),
    ("Córdoba", 37.8882, -4.7794, ()),
    ("Cuenca", 40.0704, -2.1374, ()),
    ("Girona", 41.9794, 2.8214, ("gerona",)),
    ("Granada", 37.1773, -3.5986, ()),
    ("Guadalajara", 40.6337, -3.1669, ()),
    ("San Sebastián", 43.3183, -1.9812, ("donostia",)),
    ("Huelva", 37.2614, -6.9447, ()),
    ("Huesca", 42.1401, -0.4089, ()),
    ("Jaén", 37.7796, -3.7849, ()),
    ("León", 42.5987, -5.5671, ()),
    ("Lleida", 41.6176, 0.6200, ("lerida",)),
    ("Logroño", 42.4627, -2.4450, ()),
    ("Lugo", 43.0097, -7.5568, ()),
    ("Málaga", 36.7213, -4.4214, ()),
    ("Murcia", 37.9922, -1.1307, ()),
    ("Ourense", 42.3358, -7.8639, ("orense",)),
    ("Oviedo", 43.3614, -5.8494, ()),
    ("Palencia", 42.0096, -4.5288, ()),
    ("Las Palmas de Gran Canaria", 28.1235, -15.4363, ("las palmas",)),
    ("Pamplona", 42.8125, -1.6458, ("iruna",)),
    ("Pontevedra", 42.4310, -8.6444, ()),
    ("Salamanca", 40.9701, -5.6635, ()),
    ("Santa Cruz de Tenerife", 28.4636, -16.2518, ()),
    ("Santander", 43.4623, -3.8100, ()),
    ("Segovia", 40.9429, -4.1088, ()),
    ("Sevilla", 37.3891, -5.9845, ()),
    ("Soria", 41.7664, -2.4790, ()),
    ("Tarragona", 41.1189, 1.2445, ()),
    ("Teruel", 40.3456, -1.1065, ()),
    ("Toledo", 39.8628, -4.0273, ()),
    ("Valencia", 39.4699, -0.3763, ()),
    ("Valladolid", 41.6523, -4.7245, ()),
    ("Vitoria-Gasteiz", 42.8467, -2.6716, ("vitoria", "gasteiz", "vitoria gasteiz")),
    ("Zamora", 41.5034, -5.7467, ()),
    ("Zaragoza", 41.6488, -0.8891, ()),
    ("Ceuta", 35.8894, -5.3213, ()),
    ("Melilla", 35.2923, -2.9381, ()),
    # --- Other large cities
    ("Vigo", 42.2406, -8.7207, ()),
    ("Gijón", 43.5322, -5.6611, ("gijon",)),
    ("Elche", 38.2699, -0.7126, ("elx",)),
    ("Cartagena", 37.6257, -0.9966, ()),
    ("Jerez de la Frontera", 36.6850, -6.1261, ()),
    ("Marbella", 36.5100, -4.8850, ()),
    ("Hospitalet de Llobregat", 41.3596, 2.1002, ("l hospitalet", "hospitalet")),
    ("Badalona", 41.4500, 2.2474, ()),
    ("Terrassa", 41.5633, 2.0084, ("tarrasa",)),
    ("Sabadell", 41.5431, 2.1094, ()),
    ("Benidorm", 38.5411, -0.1225, ()),
    ("Torrevieja", 37.9787, -0.6822, ()),
]

# normalised name -> (lat, lon)
KNOWN_MUNICIPALITIES: dict[str, tuple[float, float]] = {}
# normalised name -> display name ("Móstoles"); aliases map to their canonical display name
_DISPLAY_NAMES: dict[str, str] = {}
_ALIAS_KEYS: set[str] = set()

for _display, _lat, _lon, _aliases in _PLACES:
    _key = normalize_text(_display)
    KNOWN_MUNICIPALITIES[_key] = (_lat, _lon)
    _DISPLAY_NAMES[_key] = _display
    for _alias in _aliases:
        _akey = normalize_text(_alias)
        KNOWN_MUNICIPALITIES[_akey] = (_lat, _lon)
        _DISPLAY_NAMES[_akey] = _display
        _ALIAS_KEYS.add(_akey)

# Longest names first so "Alcalá de Henares" wins over "Madrid" and "San Sebastián de los Reyes" over "San Sebastián".
_MATCH_ORDER: list[tuple[str, "re.Pattern[str]"]] = [
    (name, re.compile(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"))
    for name in sorted(KNOWN_MUNICIPALITIES, key=len, reverse=True)
]


def list_known_municipalities() -> list[str]:
    """Display names of the known municipalities, sorted, aliases folded into their canonical name."""
    return sorted({display for display in _DISPLAY_NAMES.values()})


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two coordinates."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_municipality(location_text: Optional[str]) -> Optional[str]:
    """Normalised key of the most specific known municipality mentioned in a location text, or None."""
    if not location_text:
        return None
    normalized = normalize_text(location_text)
    for name, pattern in _MATCH_ORDER:
        if pattern.search(normalized):
            return name
    # Hyphenated spellings ("Rivas-Vaciamadrid", "Vitoria-Gasteiz") match the table keys with spaces.
    spaced = normalized.replace("-", " ")
    if spaced != normalized:
        for name, pattern in _MATCH_ORDER:
            if pattern.search(spaced):
                return name
    return None


def find_municipality_coords(location_text: Optional[str]) -> Optional[tuple[float, float]]:
    """Coordinates of the most specific known municipality mentioned in a location text."""
    key = find_municipality(location_text)
    return KNOWN_MUNICIPALITIES[key] if key else None


def is_in_active_municipalities(location_text: Optional[str], active_municipalities: Optional[Iterable[str]]) -> bool:
    """True when the listing's location text mentions one of the user's active municipalities.

    Only ever adds points: with nothing configured or an unknown location it is simply False.
    """
    if not location_text or not active_municipalities:
        return False
    normalized = normalize_text(location_text)
    for name in active_municipalities:
        wanted = normalize_text(name)
        if wanted and re.search(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])", normalized):
            return True
    return False


def estimate_distance_km(origin_text: Optional[str], location_text: Optional[str]) -> Optional[float]:
    """Approximate km between two location texts, or None when either municipality is unknown."""
    origin = find_municipality_coords(origin_text)
    target = find_municipality_coords(location_text)
    if origin is None or target is None:
        return None
    return round(haversine_km(*origin, *target), 1)


def coords_distance_km(lat1: Optional[float], lon1: Optional[float], lat2: Optional[float],
                       lon2: Optional[float]) -> Optional[float]:
    """Haversine over optional coordinates (None when any is missing or not numeric)."""
    try:
        values = [float(v) for v in (lat1, lon1, lat2, lon2)]  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(haversine_km(*values), 1)


def resolve_origin(origin_text: Optional[str] = None, latitude: Any = None, longitude: Any = None
                   ) -> Optional[tuple[float, float]]:
    """Origin coordinates: explicit lat/lon first, else the known municipality named in ``origin_text``."""
    try:
        if latitude is not None and longitude is not None:
            return float(latitude), float(longitude)
    except (TypeError, ValueError):
        pass
    return find_municipality_coords(origin_text)


def estimate_listing_distance(*, origin_text: Optional[str] = None, origin_lat: Any = None, origin_lon: Any = None,
                              location_text: Optional[str] = None, listing_lat: Any = None,
                              listing_lon: Any = None) -> Optional[float]:
    """Best distance estimate: listing GPS vs origin coordinates > listing town vs origin coordinates."""
    origin = resolve_origin(origin_text, origin_lat, origin_lon)
    if origin is None:
        return None
    direct = coords_distance_km(origin[0], origin[1], listing_lat, listing_lon)
    if direct is not None:
        return direct
    target = find_municipality_coords(location_text)
    if target is None:
        return None
    return round(haversine_km(*origin, *target), 1)
