"""Mail deals: the parser, the shop tables, the wishlist matching and the noise aggregation (synthetic mails only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from tantalus_hoard.mail import noise, parse, stores, wishlist
from tantalus_hoard.mail.parse import parse_mail

MAIL_TS = int(datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc).timestamp())


def ts(year, month, day, hour=0, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


STEAM = stores.STORE_BY_ID["steam"]
GOG = stores.STORE_BY_ID["gog"]
BOOKS = stores.STORE_BY_ID["bibliostock"]


# ----------------------------------------------------------------------------- numbers and discounts
def test_parse_amount_handles_both_decimal_styles():
    assert parse.parse_amount("19,99") == 19.99
    assert parse.parse_amount("19.99") == 19.99
    assert parse.parse_amount("1.299,99") == 1299.99
    assert parse.parse_amount("1,299.50") == 1299.5
    assert parse.parse_amount("1.299") == 1299.0
    assert parse.parse_amount("abc") is None


def test_prices_need_a_currency_and_ignore_percentages():
    found = parse.prices("antes 19,99\u20ac ahora 13,99 \u20ac, $4.50 y -30%")
    assert [(round(a, 2), c) for _p, a, c in found] == [(19.99, "EUR"), (13.99, "EUR"), (4.5, "USD")]


def test_discounts_pick_the_best_and_flag_ranges():
    assert parse.discounts("-30% en todo") == (30, False)
    assert parse.discounts("-10%, -15%, -20% o -25% seg\u00fan importe") == (25, True)
    assert parse.discounts("hasta el 70% de descuento") == (70, True)
    assert parse.discounts("5% dto + env\u00edo GRATIS") == (5, False)
    assert parse.discounts("sin descuentos") == (None, False)
    assert parse.discounts("https://x.com/a-30%20b") == (None, False)   # a percent-encoded link is not a discount


def test_looks_like_sale_rejects_orders_and_accepts_offers():
    assert parse.looks_like_sale("Oferta flash: -40%", "")
    assert not parse.looks_like_sale("Tu pedido ha sido enviado", "gracias 5%")
    assert not parse.looks_like_sale("Bienvenido a la tienda", "hola")
    assert parse.looks_like_sale("Rebajas de verano", "")
    assert not parse.looks_like_sale("Autor del mes: nuevas lecturas", "disfruta con 5% dto + env\u00edo GRATIS")      # a footer perk is not a sale
    assert not parse.looks_like_sale("Pensabas que lo hab\u00edas visto todo", "Techtober, packs y novedades 19,99\u20ac")  # marketing without a discount
    assert parse.looks_like_sale("Novedades de la semana", "Hasta -40% en juegos seleccionados")
    assert parse.looks_like_sale("Cup\u00f3n del 7% adicional", "")


# ----------------------------------------------------------------------------- end of the sale
def test_end_of_sale_steam_style_with_time_zone():
    end = parse.end_of_sale("", "La oferta finaliza el 1 OCT 7:00pm CEST.", MAIL_TS)
    assert end == ts(2026, 10, 1, 17, 0)


def test_end_of_sale_english_with_odd_commas_and_utc():
    end = parse.end_of_sale("", "The offer ends on October 1st , 2026 , at 1 PM UTC", MAIL_TS)
    assert end == ts(2026, 10, 1, 13, 0)


def test_end_of_sale_without_time_is_end_of_day_madrid_and_year_is_inferred():
    end = parse.end_of_sale("Oferta", "Termina el 5 de octubre", MAIL_TS)
    assert end == ts(2026, 10, 5, 21, 59)                  # 23:59 CEST
    winter = parse.end_of_sale("", "ends 3 January", ts(2026, 12, 30))
    assert winter == ts(2027, 1, 3, 22, 59)                # next year, 23:59 CET


def test_end_of_sale_relative_forms():
    assert parse.end_of_sale("Weekend deals for 72H", "", MAIL_TS) == MAIL_TS + 72 * 3600
    assert parse.end_of_sale("Solo hoy: -50%", "", MAIL_TS) is not None
    assert parse.end_of_sale("Novedades", "nada que ver", MAIL_TS) is None


def test_a_bare_number_after_a_date_is_not_a_clock_time():
    end = parse.end_of_sale("", "hasta el 5 de octubre 10 productos", MAIL_TS)
    assert end == ts(2026, 10, 5, 21, 59)


# ----------------------------------------------------------------------------- titles and links
def test_clean_title_strips_emoji_and_prefixes():
    assert parse.clean_title("RE: \U0001F525 Gran  oferta \U0001F4E2") == "Gran oferta"


def test_alt_texts_drop_icons_logos_and_badges():
    alts = ["Facebook icon", "Hades II", "GOG logo", "-30%", "banner", "Hades II", "https://x.png", "Dishonored: Death of the Outsider", "Steam"]
    assert parse.titles_from(alts) == ["Hades II", "Dishonored: Death of the Outsider"]


def test_links_are_cleaned_and_unsubscribe_links_are_skipped():
    assert parse.clean_url("https://x.com/a?utm_source=m&id=7&mc_cid=1#top") == "https://x.com/a?id=7"
    wrapped = "https://tracker.example/r?url=https%3A%2F%2Fwww.gog.com%2Fgame%2Fhades_ii%3Futm_medium%3Dmail"
    assert parse.unwrap(wrapped) == "https://www.gog.com/game/hades_ii?utm_medium=mail"
    links = [{"url": "https://www.gog.com/unsubscribe?x=1", "label": ""}, {"url": "https://www.gog.com/en/games?discounted=true", "label": "Deals"},
             {"url": "https://www.facebook.com/gog", "label": ""}, {"url": "https://other.example/x", "label": ""}]
    assert [u for u, _l in parse.store_links(links, GOG)] == ["https://www.gog.com/en/games?discounted=true"]


# ----------------------------------------------------------------------------- whole mails
def steam_mail(**over):
    mail = {"subject": "\u00a1Hades II, de tu lista de deseados de Steam, est\u00e1 en oferta!", "ts": MAIL_TS,
            "text": "Hades II\n-30%\n19,99\u20ac 13,99\u20ac\n\nBlasphemous 2\n-50%\n24,99\u20ac 12,49\u20ac\n\nLa oferta finaliza el 1 OCT 7:00pm CEST.",
            "links": [{"url": "https://store.steampowered.com/app/1145350/Hades_II/?snr=1_5", "label": ""},
                      {"url": "https://store.steampowered.com/app/2114740/Blasphemous_2/?snr=1", "label": ""}],
            "images": ["Steam", "Hades II", "Blasphemous 2", "Facebook icon"]}
    mail.update(over)
    return mail


def test_steam_wishlist_mail_gives_one_row_per_item():
    rows = parse_mail(steam_mail(), STEAM)
    assert [r["item_key"] for r in rows] == ["app:1145350", "app:2114740"]
    first, second = rows
    assert (first["title"], first["discount_pct"], first["price"], first["old_price"], first["currency"]) == ("Hades II", 30, 13.99, 19.99, "EUR")
    assert (second["title"], second["discount_pct"], second["price"], second["old_price"]) == ("Blasphemous 2", 50, 12.49, 24.99)
    assert first["ends_ts"] == ts(2026, 10, 1, 17, 0) and first["ends_known"]
    assert first["url"] == "https://store.steampowered.com/app/1145350/Hades_II/"


def test_steam_mail_with_other_items_reads_the_title_from_the_subject():
    rows = parse_mail(steam_mail(subject="Hades II y otros 1 art\u00edculos de tu lista de deseados de Steam est\u00e1n en oferta"), STEAM)
    assert [r["title"] for r in rows] == ["Hades II", "Blasphemous 2"]


def test_campaign_mail_is_one_row_with_titles_and_best_discount():
    mail = {"subject": "Weekend deals for 72H: up to -75%", "ts": MAIL_TS, "images": ["Hades II", "Logo", "GOG icon", "Dishonored"],
            "text": "Up to -75% off. The offer ends on October 1st , 2026 , at 1 PM UTC.",
            "links": [{"url": "https://www.gog.com/en/games?discounted=true&utm_source=x", "label": "Deals"}]}
    rows = parse_mail(mail, GOG)
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["item_key"], row["discount_pct"], row["up_to"]) == ("campaign", "campaign", 75, True)
    assert row["titles"] == ["Hades II", "Dishonored"]
    assert row["ends_ts"] == ts(2026, 10, 1, 13, 0)
    assert row["url"] == "https://www.gog.com/en/games?discounted=true"


def test_book_newsletter_range_discount_and_no_end_date():
    rows = parse_mail({"subject": "( Casi) Todos los C\u00d3MICS con descuento", "ts": MAIL_TS,
                       "text": "** -10%, -15%, -20% o -25% seg\u00fan importe de pedido.",
                       "links": [{"url": "https://www.bibliostock.com/71-todos-los-comics-en-oferta?utm_source=a", "label": ""}], "images": ["Facebook icon"]}, BOOKS)
    assert len(rows) == 1 and rows[0]["discount_pct"] == 25 and rows[0]["up_to"] and not rows[0]["ends_known"]
    assert rows[0]["url"] == "https://www.bibliostock.com/71-todos-los-comics-en-oferta"


def test_non_sale_and_garbage_mails_give_nothing_and_never_raise():
    assert parse_mail({"subject": "Tu pedido ha sido enviado", "ts": MAIL_TS, "text": "gracias", "links": [], "images": []}, BOOKS) == []
    assert parse_mail({}, BOOKS) == []
    assert parse_mail({"subject": None, "text": None, "links": "x", "images": 5, "ts": "no"}, STEAM) == []


# ----------------------------------------------------------------------------- shops
def test_every_sub_domain_of_a_shop_counts_and_custom_domains_are_added():
    chosen = stores.selected_stores("steam, gog", "Shop.Example.org, https://deals.example.net/x, nope")
    assert [s["id"] for s in chosen] == ["steam", "gog", "custom:shop.example.org", "custom:deals.example.net"]
    assert stores.store_for("noreply@news.gog.com", chosen)["id"] == "gog"
    assert stores.store_for("x@notgog.com", chosen) is None
    assert len(stores.selected_stores("", "")) == len(stores.DEFAULT_STORES)
    assert stores.clean_domains("a.com; b.org\nA.com") == ["a.com", "b.org"]


def test_consumers_name_the_hoard_that_reads_a_sender():
    shops = stores.selected_stores()
    assert stores.consumers_for("steampowered.com", [], shops) == [stores.CONSUMER_TANTALUS]
    assert stores.CONSUMER_LEDGER in stores.consumers_for("paypal.com")
    assert stores.CONSUMER_PHILEAS in stores.consumers_for("ups.com")
    assert stores.consumers_for("random-newsletter.example") == []
    assert stores.CONSUMER_JOBHUNTER in stores.consumers_for("random.example", ["jobs-noreply"])


# ----------------------------------------------------------------------------- wishlist
def write_library(tmp_path, games):
    path = tmp_path / "library.json"
    path.write_text(json.dumps({"format": "gamerhoard-library", "version": 1, "games": games}), encoding="utf-8")
    return path


def test_library_wanted_and_owned(tmp_path):
    path = write_library(tmp_path, [
        {"title": "Hades II", "state": "backlog", "ownedPlatforms": [], "tags": [], "steamAppId": 1145350},
        {"title": "Celeste", "state": "backlog", "ownedPlatforms": ["PC"], "tags": []},
        {"title": "Hollow Knight", "state": "completed", "ownedPlatforms": [], "tags": []},
        {"title": "Tunic", "state": "playing", "ownedPlatforms": [], "tags": [], "playtimeMinutes": 30},
        {"title": "Outer Wilds", "state": "dropped", "ownedPlatforms": [], "tags": ["wishlist"]},
        {"title": "", "state": "backlog"}])
    loaded = wishlist.load_games(path)
    assert [g["title"] for g in loaded["wanted"]] == ["Hades II", "Outer Wilds"]
    assert {g["title"] for g in loaded["owned"]} == {"Celeste", "Hollow Knight", "Tunic"}
    assert loaded["status"]["reachable"] and loaded["status"]["wanted"] == 2


def test_missing_or_broken_library_is_reported_not_guessed(tmp_path):
    assert wishlist.load_games(None)["status"]["reachable"] is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert "not readable" in wishlist.load_games(bad)["status"]["detail"]
    assert wishlist.books_status()["reachable"] is False


def deal(title, titles=(), kind="campaign", item_key="campaign"):
    return {"title": title, "titles": list(titles), "kind": kind, "item_key": item_key}


def test_matching_by_title_steam_id_owned_and_store_wishlist():
    wl = wishlist.Wishlist([{"title": "Hades II", "source": "gamerhoard", "steam_app": "1145350"}, {"title": "Dishonored", "source": "manual", "steam_app": ""},
                            {"title": "Up", "source": "manual", "steam_app": ""}],
                           [{"title": "Celeste", "source": "gamerhoard", "steam_app": ""}])
    assert wl.summary()["wanted"] == 2                        # "Up" is too short to match safely
    hit = wishlist.evaluate(deal("Weekend deals", ["Dishonored: Death of the Outsider", "Other"]), wl)
    assert hit["matched"] and hit["wishlist"] and hit["label"] == "Dishonored" and hit["source"] == "manual"
    by_id = wishlist.evaluate(deal("Whatever the shop calls it", kind="item", item_key="app:1145350"), wl)
    assert by_id["matched"] and by_id["source"] == "gamerhoard"
    owned = wishlist.evaluate(deal("Celeste on sale", ["Celeste"]), wl)
    assert owned["owned"] and not owned["matched"]
    store_item = wishlist.evaluate(deal("Some Game", kind="item", item_key="app:9"), wl, store_wishlist=True)
    assert store_item["matched"] and store_item["source"] == "store"
    assert not wishlist.evaluate(deal("Some Game", kind="item", item_key="app:9"), wl)["matched"]
    owned_wishlisted = wishlist.evaluate(deal("Celeste", kind="item", item_key="app:5"), wl, store_wishlist=True)
    assert owned_wishlisted["owned"] and not owned_wishlisted["matched"]


def test_watch_hits_match_and_never_override_a_wishlist():
    wl = wishlist.Wishlist([{"title": "Hades II", "source": "manual", "steam_app": ""}])
    calls = []

    def hits(text):
        calls.append(text)
        return [{"id": "w1", "name": "Switch games"}] if "switch" in text.lower() else []
    assert wishlist.evaluate(deal("Switch eShop sale"), wl, watch_hits=hits)["source"] == "watch"
    assert wishlist.evaluate(deal("Hades II sale"), wl, watch_hits=hits)["source"] == "manual" and not calls[1:]


# ----------------------------------------------------------------------------- noise
def rec(address, day, *, category="", unsub="", own=False, one_click=False):
    return {"from_address": address, "ts": ts(2026, 9, day), "list_unsubscribe": unsub, "one_click": one_click, "category": category, "from_self": own}


def test_noise_report_counts_ranks_and_names_consumers():
    records = ([rec("deals@news.shop-a.example", d, category="promotions", unsub="<https://shop-a.example/u?t=1>, <mailto:u@shop-a.example>", one_click=True)
                for d in range(1, 9)]
               + [rec("hi@shop-b.example", d, unsub="<mailto:stop@shop-b.example>") for d in range(1, 4)]
               + [rec("noreply@steampowered.com", 5, category="promotions", unsub="<https://steam.example/u>")] * 2
               + [rec("billing@paypal.com", 7, category="updates", unsub="<https://p.example/u>")]
               + [rec("me@gmail.com", 2, own=True), rec("friend@example.org", 3)])
    report = noise.build_report(records, stores=stores.selected_stores(), days=30)
    assert (report["total"], report["own"], report["inbound"]) == (16, 1, 15)
    first = report["top"][0]
    assert first["domain"] == "shop-a.example" and first["count"] == 8 and first["share"] == round(8 / 15, 4)
    assert first["unsubscribe"] == {"available": True, "http": "https://shop-a.example/u?t=1", "mailto": "mailto:u@shop-a.example",
                                    "one_click": True, "mails_with_header": 8}
    assert first["categories"] == {"promotions": 8} and first["consumers"] == [] and first["last_date"] == "2026-09-08"
    by_domain = {r["domain"]: r for r in report["top"]}
    assert by_domain["steampowered.com"]["consumers"] == [stores.CONSUMER_TANTALUS]
    assert by_domain["paypal.com"]["mostly_promotional"] is False       # an "updates" mail with an unsubscribe header is a receipt, not noise
    assert by_domain["shop-b.example"]["mostly_promotional"] and by_domain["friend@example.org".split("@")[1]]["unsubscribe"]["available"] is False
    assert [r["domain"] for r in report["noise"]] == ["shop-a.example", "shop-b.example"]   # promotional, and no Hoard reads the promotions
    assert report["promotional"] == 8 + 3 + 2 and report["noise_count"] == 11 and report["noise_domains"] == 2
    assert by_domain["steampowered.com"]["promotions_read"] is True and "steampowered.com" not in {r["domain"] for r in report["noise"]}


def test_a_hoard_that_reads_receipts_does_not_excuse_the_promotions_of_the_same_sender():
    records = ([rec("orders@big-shop.example", d, category="updates") for d in range(1, 6)]
               + [rec("deals@big-shop.example", d, category="promotions", unsub="<https://big-shop.example/u>") for d in range(1, 8)]
               + [rec("notifications@ups.com", d, category="promotions", unsub="<https://ups.example/u>") for d in range(1, 5)])
    report = noise.build_report(records, stores=stores.selected_stores(), days=30)
    names = {r["domain"]: r for r in report["noise"]}
    assert "ups.com" in names and names["ups.com"]["consumers"] == [stores.CONSUMER_PHILEAS]     # parcel mails are read; its promotions are noise
    assert "big-shop.example" in names and report["noise"][0]["domain"] == "big-shop.example" and report["noise"][0]["promotional"] == 7


def test_registrable_domain():
    assert noise.registrable("news.mail.example.com") == "example.com"
    assert noise.registrable("shop.example.co.uk") == "example.co.uk"
    assert noise.registrable("example.com") == "example.com"
    assert noise.unsubscribe_links("<mailto:a@b.c>, <https://x.y/z>") == {"http": "https://x.y/z", "mailto": "mailto:a@b.c"}
