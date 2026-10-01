"""Mail deals end to end with a fake mailbox: quiet first scan, matches notify once, owned/old/expired never alert, noise report, tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import T0, tool
from tantalus_hoard.errors import TantalusError
from tantalus_hoard.mail import faustus_reader
from tantalus_hoard.mail.source import MailSource
from tantalus_hoard.scheduler import Scheduler, lane_of

MONTHS = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
HOUR = 3600


class FakeMailbox:
    """Stands in for the Faustus mail reader: returns scripted mails, honours the skip list and the cap."""

    def __init__(self):
        self.messages: list[dict] = []
        self.headers: list[dict] = []
        self.requests: list[dict] = []
        self.error = ""

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error:
            return {"ok": False, "error": self.error}
        if request["action"] == "scan":
            skip = set(request["skip"])
            fresh = [m for m in self.messages if m["message_id"] not in skip]
            return {"ok": True, "error": "", "accounts": [{"account": "main", "matches": len(self.messages), "new": len(fresh)}],
                    "messages": fresh[:request["max"]]}
        if request["action"] == "headers":
            return {"ok": True, "error": "", "accounts": [{"account": "main", "in_window": len(self.headers), "read": len(self.headers)}], "messages": self.headers}
        return {"ok": True, "error": "", "accounts": []}


@pytest.fixture
def mailbox(svc):
    box = FakeMailbox()
    svc.mail.source = MailSource(runner=box)
    return box


def when(offset_s: float) -> float:
    return T0 + offset_s


def end_text(days_ahead: float) -> str:
    end = datetime.fromtimestamp(T0 + days_ahead * 86400, timezone.utc)
    return f"La oferta finaliza el {end.day} {MONTHS[end.month - 1]} 7:00pm CEST."


def steam(mid, title, pct=40, *, age_s=HOUR, app=111, days_ahead=3):
    old, new = 20.0, round(20.0 * (100 - pct) / 100, 2)
    return {"message_id": mid, "subject": f"\u00a1{title}, de tu lista de deseados de Steam, est\u00e1 en oferta!", "from_address": "noreply@steampowered.com",
            "ts": when(-age_s), "text": f"{title}\n-{pct}%\n{old:.2f}\u20ac {new:.2f}\u20ac\n\n{end_text(days_ahead)}",
            "links": [{"url": f"https://store.steampowered.com/app/{app}/{title.replace(' ', '_')}/?snr=1", "label": ""}], "images": ["Steam", title]}


def gog(mid, titles, *, subject="Weekend deals: up to -70%", age_s=HOUR, days_ahead=2):
    return {"message_id": mid, "subject": subject, "from_address": "news@gog.com", "ts": when(-age_s),
            "text": f"Up to -70% off. {end_text(days_ahead)}", "links": [{"url": "https://www.gog.com/en/games?discounted=true", "label": "Deals"}],
            "images": list(titles)}


def events(svc, type_="MAIL_DEAL"):
    return svc.store.events(types=[type_], limit=50)


# ----------------------------------------------------------------------------- the quiet first scan, then alerts
def test_first_scan_is_quiet_and_later_matches_notify_once(svc, mailbox):
    mailbox.messages = [steam("<a>", "Hades II", 30, app=1145350), steam("<b>", "Celeste", 50, app=504230)]
    first = svc.mail.run()
    assert first["ok"] and first["quiet"] and first["deals_new"] == 2 and first["matched_new"] == 2 and first["notified"] == 0
    assert svc.notifier.sent == [] and events(svc) == []
    assert svc.db.get_setting("mail.deals.first_scan_done") == "1"
    assert all(d["quiet"] and d["matched"] and d["match_source"] == "store" for d in svc.mail.deals())

    mailbox.messages.append(steam("<c>", "Hollow Knight", 60, app=367520))
    second = svc.mail.run()
    assert not second["quiet"] and second["scanned"] == 1 and second["deals_new"] == 1 and second["notified"] == 1
    (event,) = events(svc)
    assert event["title"] == "Hollow Knight" and event["severity"] == "high" and event["status"] == "confirmed"
    assert event["url"] == "https://store.steampowered.com/app/367520/Hollow_Knight/" and event["price"] == 8.0 and event["old_price"] == 20.0
    assert "Steam" in event["summary"] and "-60%" in event["summary"] and "lista de deseados" in event["summary"]
    assert len(svc.notifier.sent) == 1 and svc.notifier.sent[0][0]["type"] == "MAIL_DEAL"

    third = svc.mail.run()                                   # the same mails again: nothing new, nothing sent
    assert third["scanned"] == 0 and third["notified"] == 0 and len(events(svc)) == 1
    assert mailbox.requests[-1]["skip"] and "<c>" in mailbox.requests[-1]["skip"]


def test_unmatched_deals_are_kept_but_silent(svc, mailbox):
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    mailbox.messages = [gog("<g1>", ["Some Unknown Game", "Another Thing"])]
    out = svc.mail.run()
    assert out["deals_new"] == 1 and out["matched_new"] == 0 and out["notified"] == 0
    (deal,) = svc.mail.deals()
    assert deal["store"] == "GOG" and deal["matched"] is False and deal["discount_pct"] == 70 and deal["up_to"] and deal["ends_known"]
    assert events(svc) == [] and svc.notifier.sent == []


def test_own_wishlist_match_notifies_a_campaign(svc, mailbox):
    svc.set_settings({"mail.deals.wishlist": "Hades II, Tunic"})
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    mailbox.messages = [gog("<g1>", ["Hades II", "Filler"])]
    out = svc.mail.run()
    assert out["matched_new"] == 1 and out["notified"] == 1
    (event,) = events(svc)
    assert event["data"]["match_source"] == "manual" and event["data"]["match_label"] == "Hades II" and event["data"]["store"] == "GOG"
    assert event["severity"] == "medium"                      # "up to 70%" is a range, not a sure 70%


def test_a_watcher_match_notifies_with_the_watcher(svc, mailbox):
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    watcher = svc.store.create_watcher(name="Dishonored", mode="availability", config={"product": {"terms": ["dishonored"]}})
    mailbox.messages = [gog("<g1>", ["Dishonored: Death of the Outsider", "Other"])]
    svc.mail.run()
    (event,) = events(svc)
    assert event["watcher_id"] == watcher["id"] and event["data"]["match_source"] == "watch"


def test_owned_games_never_notify(svc, mailbox, tmp_path):
    library = tmp_path / "library.json"
    library.write_text(json.dumps({"format": "gamerhoard-library", "version": 1, "games": [
        {"title": "Celeste", "state": "completed", "ownedPlatforms": ["PC"], "tags": []},
        {"title": "Hades II", "state": "backlog", "ownedPlatforms": [], "tags": [], "steamAppId": 1145350}]}), encoding="utf-8")
    svc.set_settings({"mail.deals.gamerhoard_file": str(library)})
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    mailbox.messages = [steam("<a>", "Celeste", 70, app=504230), steam("<b>", "Hades II", 20, app=1145350)]
    out = svc.mail.run()
    assert out["owned"] == 1 and out["notified"] == 1
    assert [e["title"] for e in events(svc)] == ["Hades II"]
    by_title = {d["title"]: d for d in svc.mail.deals()}
    assert by_title["Celeste"]["owned"] and not by_title["Celeste"]["matched"] and by_title["Hades II"]["match_source"] == "gamerhoard"


def test_old_mail_is_remembered_but_never_alerts(svc, mailbox):
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    mailbox.messages = [steam("<old>", "Tunic", 50, age_s=5 * 86400, days_ahead=2)]
    out = svc.mail.run()
    assert out["deals_new"] == 1 and out["notified"] == 0
    assert events(svc) == [] and svc.mail.deals()[0]["notified"] is True      # nothing will alert about it later either
    assert svc.mail.run()["notified"] == 0


def test_event_dedupe_across_mails_of_the_same_sale(svc, mailbox):
    svc.set_settings({"mail.deals.wishlist": "Hades II"})
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    mailbox.messages = [gog("<g1>", ["Hades II"]), gog("<g2>", ["Hades II"], age_s=2 * HOUR)]
    out = svc.mail.run()
    assert out["deals_new"] == 2 and out["notified"] == 1 and len(events(svc)) == 1       # same shop, title and end date: one alert


def test_deals_expire_at_the_end_date_or_after_the_ttl(svc, mailbox):
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    plain = {"message_id": "<p>", "subject": "Oferta: -20% en libros", "from_address": "clientes@bibliostock.com", "ts": when(-3 * 86400),
             "text": "-20% en todo", "links": [], "images": []}
    mailbox.messages = [gog("<ended>", ["x"], age_s=5 * 86400, days_ahead=-1), plain]
    svc.set_settings({"mail.deals.ttl_days": "2"})
    out = svc.mail.run()
    assert out["deals_new"] == 2
    states = {d["store_id"]: d["status"] for d in svc.mail.deals(status="")}
    assert states == {"gog": "expired", "bibliostock": "expired"}          # ended yesterday / older than the 2-day window
    assert svc.mail.deals(status="active") == []
    assert svc.counts()["mail_deals"] == 0


def test_failures_back_off_and_a_disabled_scan_is_never_due(svc, mailbox):
    assert svc.mail.due(T0)
    mailbox.error = "login failed"
    out = svc.mail.run()
    assert not out["ok"] and "login failed" in out["errors"][0]
    assert not svc.mail.due(T0 + 60) and svc.mail.due(T0 + 181 * 60)
    assert svc.mail.status()["last_error"] == "login failed" and not svc.mail.status()["first_scan_done"]
    svc.set_settings({"mail.deals.enabled": "0"})
    assert not svc.mail.due(T0 + 10 ** 7)


def test_a_backlog_larger_than_one_run_keeps_the_scan_quiet_until_it_is_consumed(svc, mailbox):
    mailbox.messages = [steam(f"<m{i}>", f"Game Number {i}", 40, app=1000 + i) for i in range(4)]
    first = svc.mail.run(limit=3)
    assert first["scanned"] == 3 and svc.db.get_setting("mail.deals.first_scan_done") != "1"
    second = svc.mail.run(limit=3)
    assert second["scanned"] == 1 and second["quiet"] and svc.db.get_setting("mail.deals.first_scan_done") == "1"
    assert svc.notifier.sent == []


def test_rebuild_reads_everything_again_quietly(svc, mailbox):
    svc.set_settings({"mail.deals.wishlist": "Hades II"})
    mailbox.messages = [gog("<g1>", ["Hades II"])]
    svc.mail.run()
    assert svc.mail.repo.counts()["deals"] == 1
    mailbox.messages.append(steam("<s1>", "Tunic", 40, app=553420))
    out = tool(svc, "mail_deals_scan", rebuild=True)["result"]
    assert out["cleared"] == {"deals": 1, "mails_seen": 1} and out["quiet"] and out["deals_new"] == 2 and out["notified"] == 0
    assert svc.mail.repo.counts()["deals"] == 2 and svc.notifier.sent == [] and svc.db.get_setting("mail.deals.first_scan_done") == "1"


def test_rematch_picks_up_a_wishlist_added_later(svc, mailbox):
    svc.db.set_setting("mail.deals.first_scan_done", "1")
    mailbox.messages = [gog("<g1>", ["Hades II"])]
    svc.mail.run()
    assert not svc.mail.deals()[0]["matched"] and events(svc) == []
    svc.set_settings({"mail.deals.wishlist": "Hades II"})
    out = svc.mail.rematch()
    assert out == {"changed": 1, "notified": 1} and svc.mail.deals()[0]["matched"] and len(events(svc)) == 1


# ----------------------------------------------------------------------------- tools and settings
def test_tools_list_scan_dismiss_and_noise(svc, mailbox):
    mailbox.messages = [steam("<a>", "Hades II", 30, app=1145350), gog("<g1>", ["Unknown"])]
    mailbox.headers = [
        {"from_address": "deals@shop.example", "ts": T0 - 100, "list_unsubscribe": "<https://shop.example/u>", "one_click": False, "category": "promotions", "from_self": False},
        {"from_address": "deals@shop.example", "ts": T0 - 200, "list_unsubscribe": "<https://shop.example/u>", "one_click": False, "category": "promotions", "from_self": False},
        {"from_address": "deals@shop.example", "ts": T0 - 250, "list_unsubscribe": "<https://shop.example/u>", "one_click": False, "category": "promotions", "from_self": False},
        {"from_address": "noreply@steampowered.com", "ts": T0 - 300, "list_unsubscribe": "", "one_click": False, "category": "", "from_self": False}]
    scanned = tool(svc, "mail_deals_scan")
    assert scanned["result"]["deals_new"] == 2 and scanned["mail"]["counts"]["active"] == 2
    listing = tool(svc, "mail_deals", matched_only=True)
    assert listing["count"] == 1 and listing["deals"][0]["title"] == "Hades II" and listing["deals"][0]["age_days"] < 1
    assert "message_id" not in listing["deals"][0] and "read-only" in listing["note"]
    assert tool(svc, "mail_deals", store="gog")["count"] == 1 and tool(svc, "mail_deals", query="hades")["count"] == 1
    deal_id = listing["deals"][0]["id"]
    assert tool(svc, "mail_deal_set", deal_id=deal_id, status="dismissed")["deal"]["status"] == "dismissed"
    assert tool(svc, "mail_deals")["count"] == 1 and tool(svc, "mail_deals", status="dismissed")["count"] == 1
    assert tool(svc, "mail_deal_set", deal_id=deal_id, status="active")["deal"]["status"] == "active"
    with pytest.raises(TantalusError):
        tool(svc, "mail_deal_set", deal_id=9999, status="dismissed")
    report = tool(svc, "mail_noise_report", days=30)
    assert report["ok"] and report["inbound"] == 4 and report["top"][0]["domain"] == "shop.example" and report["noise"][0]["domain"] == "shop.example"
    assert report["top"][0]["unsubscribe"]["http"] == "https://shop.example/u" and report["cached"] is False
    assert tool(svc, "mail_noise_report", days=30)["cached"] is True
    assert tool(svc, "mail_deals_scan", rematch_only=True)["result"] == {"changed": 0, "notified": 0}


def test_noise_report_surfaces_a_mailbox_error(svc, mailbox):
    mailbox.error = "no account"
    assert tool(svc, "mail_noise_report") == {"ok": False, "error": "no account", "window_days": 30, "note": tool(svc, "mail_noise_report")["note"]}


def test_settings_are_validated(svc):
    defaults = svc.settings()
    assert defaults["mail.deals.enabled"] == "1" and defaults["mail.deals.interval_min"] == "180" and defaults["mail.noise.days"] == "30"
    saved = svc.set_settings({"mail.deals.stores": "steam, gog", "mail.deals.domains": "Shop.Example.org, https://x.example.net/a", "mail.deals.ttl_days": 10})
    assert saved["mail.deals.domains"] == "shop.example.org, x.example.net" and saved["mail.deals.ttl_days"] == "10"
    assert [s["id"] for s in svc.mail.stores()] == ["steam", "gog", "custom:shop.example.org", "custom:x.example.net"]
    for bad in ({"mail.deals.interval_min": "5"}, {"mail.deals.history_days": "abc"}, {"mail.deals.stores": "steam, nonsense"}, {"mail.deals.enabled": "maybe"}):
        with pytest.raises(TantalusError):
            svc.set_settings(bad)


def test_status_and_counts_report_the_mail_block(svc, mailbox):
    assert svc.status()["mail"]["enabled"] is True and svc.status()["counts"]["mail_deals"] == 0
    detail = svc.mail.status()
    assert detail["first_scan_done"] is False and len(detail["stores"]) >= 10
    assert {s["source"] for s in detail["wishlist"]["sources"]} == {"gamerhoard", "bookhoard", "manual"}
    assert next(s for s in detail["wishlist"]["sources"] if s["source"] == "bookhoard")["reachable"] is False


def test_the_ui_reaches_the_mail_tools_and_the_new_event_type_is_labelled(client):
    out = client.post("/api/ui/call", json={"name": "mail_deals", "arguments": {}}).json()
    assert out["count"] == 0 and out["mail"]["enabled"] is True
    from tantalus_hoard.notify import label
    assert label("MAIL_DEAL", "es") == "Oferta del correo" and label("MAIL_DEAL", "en") == "Mail deal"
    assert client.get("/api/status").json()["mail"]["counts"]["active"] == 0


# ----------------------------------------------------------------------------- scheduler and safety
def test_the_scan_is_an_extra_job_kind_in_the_sweeps_lane():
    ran = []

    class Engine:
        pass

    class NoStore:
        def pending_revalidations(self, now):
            return []

        def due_targets(self, now, limit=30):
            return []

        def watchers(self, enabled=True):
            return []

    sched = Scheduler(Engine(), NoStore(), extra={"mail_deals": (lambda now: now > 5, lambda ref: ran.append(ref) or {"ok": True})})
    assert lane_of("mail_deals") == "sweeps"
    assert sched.enqueue_due(1) == 0 and sched.enqueue_due(10) == 1
    assert sched.run_now("mail_deals", "all", timeout=1) == {"ok": True} or ran    # no loop running: runs inline


def test_the_reader_cannot_modify_a_mailbox():
    source = Path(faustus_reader.__file__).read_text(encoding="utf-8")
    for forbidden in ("smtplib", "sendmail", "send_message", "EXPUNGE", "conn.expunge", "conn.store", "conn.copy", "conn.append", "conn.delete", "conn.create",
                      "conn.rename", '"STORE"', '"COPY"', '"MOVE"', "\\Seen", "\\Deleted", "RFC822", "BODY[]"):
        assert forbidden not in source, forbidden
    assert "readonly=True" in source and "BODY.PEEK[]" in source and "BODY.PEEK[HEADER.FIELDS" in source


def test_the_reader_answers_without_a_faustus_folder(tmp_path):
    answer = faustus_reader.handle({"action": "status"}, str(tmp_path))
    assert answer["ok"] is False and "error" in answer


def test_source_reports_a_missing_faustus_folder_and_parses_the_helper_answer():
    class NoFaustus:
        def faustus_dir(self):
            return None

    assert MailSource(NoFaustus()).status()["ok"] is False

    class Notifier:
        def faustus_dir(self):
            return Path(".")

        @staticmethod
        def faustus_python(root):
            return "python"

    class Done:
        stdout = "noise\n" + json.dumps({"ok": True, "messages": []}) + "\n"
        returncode = 0

    seen = {}

    def runner(command, **kwargs):
        seen.update(kwargs)
        return Done()
    out = MailSource(Notifier(), process_runner=runner).headers(7, 10)
    assert out == {"ok": True, "messages": []} and json.loads(seen["input"]) == {"action": "headers", "since_days": 7, "max": 10}
    assert not any(k.startswith("TANTALUS_") for k in seen["env"])
