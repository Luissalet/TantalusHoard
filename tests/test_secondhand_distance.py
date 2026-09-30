"""Distance by municipality table (ported from Radar de Libros) plus the extended table."""

from tantalus_hoard.secondhand.distance import (KNOWN_MUNICIPALITIES, coords_distance_km, estimate_distance_km,
                                                estimate_listing_distance, find_municipality_coords, haversine_km,
                                                is_in_active_municipalities, list_known_municipalities,
                                                resolve_origin)


def test_known_and_unknown_municipality():
    assert find_municipality_coords("Móstoles, Madrid") is not None
    assert find_municipality_coords("Un lugar inventado que no existe") is None


def test_prefers_more_specific_match():
    assert find_municipality_coords("Alcalá de Henares, Madrid") == find_municipality_coords("alcala de henares")
    assert find_municipality_coords("San Sebastián de los Reyes, Madrid") == KNOWN_MUNICIPALITIES["san sebastian de los reyes"]
    assert find_municipality_coords("San Sebastián") == KNOWN_MUNICIPALITIES["san sebastian"]
    assert find_municipality_coords("Sevilla la Nueva") == KNOWN_MUNICIPALITIES["sevilla la nueva"]


def test_word_boundaries_avoid_partial_matches():
    assert find_municipality_coords("Cuenca del Pinto") is not None  # cuenca / pinto as whole words
    assert find_municipality_coords("Pintoresco lugar") is None       # "pinto" inside another word


def test_same_place_is_zero_and_unknown_is_none():
    assert estimate_distance_km("Móstoles, Madrid", "Móstoles, Madrid") == 0.0
    assert estimate_distance_km("Móstoles, Madrid", "Sitio desconocido") is None
    assert estimate_distance_km("Sitio desconocido", "Móstoles, Madrid") is None


def test_real_distances_are_plausible():
    assert 44 <= estimate_distance_km("Móstoles", "Alcalá de Henares") <= 50
    assert 480 <= estimate_distance_km("Madrid", "Barcelona") <= 520
    assert 2 <= estimate_distance_km("Móstoles", "Alcorcón") <= 6


def test_table_covers_madrid_towns_and_provincial_capitals():
    names = list_known_municipalities()
    assert len(KNOWN_MUNICIPALITIES) >= 150
    for expected in ("Rivas-Vaciamadrid", "Colmenar Viejo", "Zaragoza", "Vitoria-Gasteiz", "Teruel", "Melilla",
                     "A Coruña", "Santa Cruz de Tenerife", "Aranjuez", "Tres Cantos"):
        assert expected in names
    assert find_municipality_coords("Rivas Vaciamadrid") == find_municipality_coords("Rivas-Vaciamadrid")
    assert find_municipality_coords("La Coruña") == find_municipality_coords("A Coruña")


def test_list_known_municipalities_deduplicates_aliases_and_sorts():
    names = list_known_municipalities()
    assert names.count("Las Rozas de Madrid") == 1 and "Las Rozas" not in names
    assert "Alcalá de Henares" in names and "Móstoles" in names
    assert names == sorted(names)


def test_all_coordinates_are_in_spain():
    for name, (lat, lon) in KNOWN_MUNICIPALITIES.items():
        assert 27.5 < lat < 44.0 and -18.5 < lon < 4.5, name


def test_is_in_active_municipalities():
    assert is_in_active_municipalities("Piso en Móstoles centro", ["Móstoles", "Getafe"]) is True
    assert is_in_active_municipalities("Piso en Alcorcón centro", ["Móstoles", "Getafe"]) is False
    assert is_in_active_municipalities("Piso en Móstoles centro", []) is False
    assert is_in_active_municipalities("Piso en Móstoles centro", None) is False
    assert is_in_active_municipalities(None, ["Móstoles"]) is False
    assert is_in_active_municipalities("", ["Móstoles"]) is False


def test_haversine_and_coordinate_helpers():
    assert haversine_km(40.0, -3.0, 40.0, -3.0) == 0
    assert coords_distance_km(None, 1, 2, 3) is None
    assert coords_distance_km("x", 1, 2, 3) is None
    assert resolve_origin("Madrid") == KNOWN_MUNICIPALITIES["madrid"]
    assert resolve_origin("Madrid", 41.0, 2.0) == (41.0, 2.0)


def test_listing_distance_prefers_gps_then_town_text():
    gps = estimate_listing_distance(origin_lat=40.3223, origin_lon=-3.8649, listing_lat=40.3459, listing_lon=-3.8274)
    assert gps is not None and gps < 6
    by_text = estimate_listing_distance(origin_text="Móstoles", location_text="Alcorcón")
    assert by_text is not None and by_text < 6
    assert estimate_listing_distance(origin_text="Móstoles", location_text="???") is None
    assert estimate_listing_distance(location_text="Alcorcón") is None
