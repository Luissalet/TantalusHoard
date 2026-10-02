"""Every money parser of the app reads amounts through hoard_link.money (regression inputs are measured bugs)."""

from tantalus_hoard.extract import nvidia, phrases
from tantalus_hoard.mail import parse as mail_parse
from tantalus_hoard.notify.labels import format_price
from tantalus_hoard.radar import core as radar_core, stocktcg
from tantalus_hoard.rules import summary_for
from tantalus_hoard.secondhand.price import format_price_display, normalize_price


def test_stocktcg_reads_a_thousands_comma_with_a_dot_decimal():
    # was 1.23: the old parser dropped everything after the first "." once it saw no comma
    assert stocktcg.parse_price("€1,234.56") == (1234.56, "EUR")
    assert stocktcg.parse_price("1,234.56 €") == (1234.56, "EUR")
    assert stocktcg.parse_price("1.234,56 €") == (1234.56, "EUR")
    assert stocktcg.parse_price("1.599,00 SEK") == (1599.0, "SEK")
    assert stocktcg.parse_price("49,99") == (49.99, "EUR")        # a bare number is read in the default currency
    assert stocktcg.parse_price("sin precio") == (None, "EUR")


def test_second_hand_prices_with_thousands_are_no_longer_cut():
    # was 234.0 / 234.56: the pattern started at the first digit run after the separator
    assert normalize_price("1.234 €").price_eur == 1234.0
    assert normalize_price("1.234,56 €").price_eur == 1234.56
    assert normalize_price("1 299 €").price_eur == 1299.0
    assert normalize_price("12,5 EUR").price_eur == 12.5
    assert normalize_price("5 $").price_eur is None               # only euros are a price in euros
    assert normalize_price("Gratis").is_free


def test_displayed_prices_use_spanish_separators():
    assert format_price_display(1234.5) == "1.234,50 €" and format_price_display(5.0) == "5 €"
    assert format_price(1234.5, "EUR") == "1.234,50 €" and format_price(50, "EUR") == "50 €"
    assert format_price(1234.5, "EUR", "en") == "€1,234.50" and format_price("x", "EUR") == ""
    assert radar_core.money(1234.56) == "1.234,56 €" and radar_core.money(None) == ""
    assert "baja de 1.234,50 a 999,00 €" in summary_for("PRICE_DROP", title="T", retailer="", state="IN_STOCK", price=999.0,
                                                          currency="EUR", old_price=1234.5, extra={"drop_pct": 19})


def test_mail_prices_read_both_styles_and_skip_percentages():
    found = mail_parse.prices("antes 1.299,99 € ahora 999 € y -30% o $1,299.00")
    assert [(round(a, 2), c) for _p, a, c in found] == [(1299.99, "EUR"), (999.0, "EUR"), (1299.0, "USD")]


def test_phrase_price_helpers_and_nvidia_price():
    assert phrases.parse_number("desde 59,99 EUR") == 59.99 and phrases.parse_number("1,234.56") == 1234.56
    assert phrases.amount_and_currency("€1,234.56") == (1234.56, "EUR")
    assert nvidia._price("1.299,00 €") == 1299.0 and nvidia._price(499) == 499.0 and nvidia._price("") is None
