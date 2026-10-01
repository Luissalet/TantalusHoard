"""COMING_SOON ("Próximamente") -> buyable raises SALE_OPEN; other transitions keep their events."""

from tantalus_hoard import rules
from tantalus_hoard.model import COMING_SOON, IN_STOCK, OUT_OF_STOCK, PREORDER, Offer


def drafts(prev, state, price=59.99):
    return rules.transitions(target={"id": "t1"}, prev_state=prev, prev_price=None, state=state, offer=Offer(availability=state, price=price),
                             policies=rules.policies_for({}), ceiling=None, first_check=False)


def test_coming_soon_to_in_stock_is_sale_open():
    out = drafts(COMING_SOON, IN_STOCK)
    assert [d["type"] for d in out] == ["SALE_OPEN"] and out[0]["sale_kind"] == "sale" and out[0]["severity"] == "high"


def test_coming_soon_to_preorder_is_sale_open_with_reservations():
    out = drafts(COMING_SOON, PREORDER)
    assert out[0]["type"] == "SALE_OPEN" and out[0]["sale_kind"] == "preorder"
    text = rules.summary_for("SALE_OPEN", title="ETB", retailer="GAME", state=PREORDER, price=59.99, currency="EUR", extra=out[0])
    assert "reservas abiertas en GAME" in text and "Próximamente" in text


def test_sold_out_to_coming_soon_is_quiet_and_restock_unchanged():
    assert drafts(OUT_OF_STOCK, COMING_SOON) == []
    assert [d["type"] for d in drafts(OUT_OF_STOCK, IN_STOCK)] == ["RESTOCK"]


def test_watchers_that_want_restocks_get_sale_open(svc):
    w = svc.store.create_watcher(name="w", mode="availability", config={"policies": {"alert_on": ["RESTOCK"], "revalidate_seconds": 0}})
    t = svc.store.create_target(w["id"], "https://www.game.es/x/266954", label="UPC")
    svc.store.update_target(t["id"], last_state=COMING_SOON, extra={"last_known_state": COMING_SOON, "checked_once": True})
    from tantalus_hoard.engine import rules as r
    conf = r.Confidence(85, [])
    ev = svc.engine._raise({"type": "SALE_OPEN", "old_state": COMING_SOON, "new_state": IN_STOCK, "price": 249.99, "old_price": None,
                            "severity": "high", "sale_kind": "sale"}, target=svc.store.target(t["id"]), watcher=w,
                           offer=Offer(availability=IN_STOCK, price=249.99, currency="EUR"), conf=conf, policies=r.policies_for(w["config"]),
                           title="UPC", retailer="GAME")
    assert ev["status"] == "confirmed" and "ya a la venta en GAME" in ev["summary"]
