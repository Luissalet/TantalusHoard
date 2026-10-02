"""Price and quantity helpers (ported from Radar de Libros)."""

import pytest

from tantalus_hoard.model import RawListing
from tantalus_hoard.secondhand.price import (compute_price_per_book, format_price_display, normalize_price,
                                             price_info_for)
from tantalus_hoard.secondhand.quantity import estimate_quantity


def test_gratis_is_free():
    info = normalize_price("Gratis")
    assert info.is_free is True and info.price_eur == 0.0


def test_zero_euros_is_free():
    info = normalize_price("0 €")
    assert info.is_free is True and info.price_eur == 0.0


def test_simple_price_and_decimal_variants():
    assert normalize_price("5 €").price_eur == 5.0
    assert normalize_price("5 €").is_free is False
    assert normalize_price("12,50 €").price_eur == 12.5
    assert normalize_price("€10.00").price_eur == 10.0
    assert normalize_price("10 EUR").price_eur == 10.0


def test_unknown_price_is_none_never_invented():
    assert normalize_price(None).price_eur is None
    assert normalize_price("").price_eur is None
    info = normalize_price("Contactar para más info")
    assert info.price_eur is None and info.is_free is False


def test_price_per_book():
    assert compute_price_per_book(10.0, 70, 70) == pytest.approx(round(10.0 / 70, 3), abs=1e-9)
    assert compute_price_per_book(10.0, None, None) is None
    assert compute_price_per_book(None, 50, 50) is None
    assert compute_price_per_book(20.0, 80, 120) == 0.2  # midpoint of the range


def test_format_price_display():
    assert format_price_display(0.0, True) == "Gratis"
    assert format_price_display(None, False) == "Precio desconocido"
    assert format_price_display(5.0, False) == "5 €"
    assert format_price_display(5.5, False) == "5,50 €"  # Spanish decimals, like every other price the app shows


def test_price_info_for_listing_prefers_numeric_price_then_raw_text():
    numeric = RawListing(source="wallapop", url="u", title="t", price=0.0)
    assert price_info_for(numeric).is_free is True
    raw = RawListing(source="facebook", url="u", title="t", price_raw="12,50 €")
    assert price_info_for(raw).price_eur == 12.5
    none = RawListing(source="facebook", url="u", title="t")
    assert price_info_for(none).price_eur is None


# ----------------------------------------------------------------------------- quantity
def test_exact_number_of_books():
    result = estimate_quantity("Vendo 50 libros de segunda mano")
    assert (result.minimum, result.maximum, result.approximate) == (50, 50, False)


def test_approximate_number_with_unos():
    result = estimate_quantity("Regalo unos 100 libros por mudanza")
    assert result.approximate is True
    assert result.minimum <= 100 <= result.maximum and result.minimum < result.maximum


def test_boxes_with_number():
    result = estimate_quantity("3 cajas de libros para recoger")
    assert (result.minimum, result.maximum) == (36, 60)


def test_qualitative_phrases():
    assert estimate_quantity("Me deshago de biblioteca completa, todo debe salir").minimum > 0
    assert estimate_quantity("Regalo colección completa de novelas").has_estimate
    assert estimate_quantity("Tengo muchísimos libros, los regalo todos").has_estimate


def test_varias_cajas_word_quantity():
    result = estimate_quantity("Vaciado de piso, varias cajas de libros")
    assert (result.minimum, result.maximum) == (3 * 12, 5 * 20)


def test_no_quantity_information():
    result = estimate_quantity("Vendo un libro de texto de matemáticas")
    assert result.has_estimate is False and result.minimum is None


def test_representative_and_display():
    assert estimate_quantity("50 libros").representative == 50
    text = estimate_quantity("3 cajas de libros").display()
    assert "36" in text and "≈" in text
