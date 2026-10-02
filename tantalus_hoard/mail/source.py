"""Where the deal scan reads the mailbox from: the family hub's mail gateway first, the Faustus helper as the fallback.

Both halves are the commons': ``hoard_link.fam_mail.MailRouter`` chooses the source (``mail.source``: ``auto``, ``hub`` or
``faustus``), registers Tantalus's interest at the hub, keeps the watermark and claims what Tantalus filed, and
``FaustusHelper`` runs the vendored ``mail_helper.py`` under Faustus's own Python so the mail password never reaches Tantalus.
This class only adapts the answers to what the deal scan expects. The mailbox is read-only on the other side (the helper opens
folders with EXAMINE and fetches with BODY.PEEK); the only write action is the notification mail of the notifier.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..hoard_link.fam_mail import FaustusHelper, MailRouter

STATUS_TIMEOUT_S = 60
HEADERS_TIMEOUT_S = 180
SCAN_TIMEOUT_S = 420
SOURCE_MODES = ("auto", "hub", "faustus")     # mail.source: the family hub's mail gateway, Faustus's own helper, or the hub with a fallback
HUB_PAGE_MAX = 500
WATERMARK_KEY = "mail.hub.since_id"            # the hub's message id up to which deals have been read
HUB_FIELDS = ["images", "headers"]             # what the gateway adds to a message on request: image alt texts and sources, list headers


class _RunnerHelper:
    """A helper that answers through ``runner(request, timeout) -> dict`` (tests use a fake mailbox)."""

    def __init__(self, runner: Callable[[dict[str, Any], int], dict[str, Any]]):
        self.runner = runner

    def available(self) -> bool:
        return True

    def run(self, action: str, payload: Optional[dict[str, Any]] = None, timeout: float = 0) -> dict[str, Any]:
        return self.runner({**(payload or {}), "action": action}, int(timeout))


def _normalise(message: dict[str, Any]) -> dict[str, Any]:
    """The shape the parser reads: ``images`` is the list of alt texts (the gateway and the helper give ``{alt, src}`` objects, kept
    as ``image_urls``), ``from_address`` is lower case."""
    images = message.get("images") or []
    message["image_urls"] = [str(i["src"]) for i in images if isinstance(i, dict) and i.get("src")]
    message["images"] = [str(i.get("alt") or "") if isinstance(i, dict) else str(i) for i in images]
    message["from_address"] = str(message.get("from_address") or message.get("from_addr") or "").lower()
    message.setdefault("links", [])
    message.setdefault("text", "")
    return message


class MailSource:
    """``runner`` is injectable: ``runner(request, timeout) -> dict``; ``process_runner`` replaces ``subprocess.run`` under the helper."""

    def __init__(self, notifier: Any = None, settings_get: Optional[Callable[[str, Optional[str]], Optional[str]]] = None,
                 runner: Optional[Callable[[dict[str, Any], int], dict[str, Any]]] = None, process_runner: Optional[Callable[..., Any]] = None,
                 settings_set: Optional[Callable[[str, str], None]] = None, hub_mail: Any = None,
                 clock: Callable[[], float] = time.time):
        self.get = settings_get or (lambda key, default=None: default)
        self.put = settings_set or (lambda key, value: None)
        self.clock = clock
        if runner is not None:
            self.helper: Any = _RunnerHelper(runner)
        elif notifier is not None and getattr(notifier, "helper", None) is not None and process_runner is None:
            self.helper = notifier.helper            # one helper (and one status cache) for the notifier and the mail scan
        else:
            self.helper = FaustusHelper(lambda: self.get("notify.email.faustus_dir", None), owner=lambda: self.get("notify.email.faustus_owner", None),
                                        runner=process_runner, env_drop_prefixes=("TANTALUS_",), ask_hub=False, clock=clock)
        self.router = MailRouter(self.helper, source_getter=self.source_setting, interest=self._interest, claim_kind="deal",
                                 watermark_get=lambda: int(self.get(WATERMARK_KEY, "0") or 0),
                                 watermark_set=lambda value: self.put(WATERMARK_KEY, str(value)), page=min(100, HUB_PAGE_MAX), max_pages=6,
                                 hub=hub_mail, clock=clock)

    # ------------------------------------------------------------------ the family hub's mail gateway
    @staticmethod
    def _interest(criteria: dict[str, Any]) -> dict[str, Any]:
        domains = sorted({str(d).lower() for d in criteria.get("sender_domains") or [] if d})
        return {"from_domains": domains} if domains else {}

    def source_setting(self) -> str:
        """``mail.source``: ``auto`` (hub when its gateway is ready, else Faustus's helper), ``hub`` (only the hub), ``faustus``."""
        value = str(self.get("mail.source", "auto") or "auto").strip().lower()
        return value if value in SOURCE_MODES else "auto"

    def hub_up(self) -> bool:
        return self.router.hub_up()

    def source_status(self) -> dict[str, Any]:
        status = self.router.status()
        return {"setting": status["setting"], "effective": status["effective"], "hub_available": status["hub_available"],
                "interest_registered": status["interest_registered"], "hub_since_id": status["hub_since_id"],
                "noise_report": "faustus"}        # the noise report needs every header: the hub never provides that to an app

    def forget_interest(self) -> None:
        self.router.forget_interest()

    def ensure_interest(self, sender_domains: list[str], *, force: bool = False) -> dict[str, Any]:
        """Tell the hub which sale mails Tantalus wants: the sender domains of the shops it knows (promotions of those senders).
        Registered again whenever the shops or domains change."""
        if not any(sender_domains):
            return {"ok": False, "error": "no sender domains"}
        return self.router.ensure_interest({"sender_domains": sender_domains}, force=force)

    def commit(self) -> None:
        """Remember how far the hub's mail was read. Called by the scan once its messages are stored."""
        self.router.commit()

    def claim(self, hub_id: Any, ref: str) -> None:
        """Tell the hub "this mail is mine" so it leaves the unowned tray. Errors are ignored: claims are hints."""
        if hub_id in (None, ""):
            return
        self.router.claim({"hub_id": hub_id}, ref)

    # ------------------------------------------------------------------ actions
    def status(self) -> dict[str, Any]:
        return self.helper.run("status", None, STATUS_TIMEOUT_S)

    def headers(self, days: int = 30, limit: int = 4000) -> dict[str, Any]:
        return self.helper.run("headers", {"since_days": int(days), "max": int(limit)}, HEADERS_TIMEOUT_S)

    def scan(self, days: int, limit: int, sender_domains: list[str], skip: list[str]) -> dict[str, Any]:
        """``{ok, error, accounts, messages, via: "hub" | "faustus", more}``; a hub that cannot answer falls back to the helper in
        ``auto`` and is reported in ``hub``."""
        answer = self.router.scan_ex(since_days=int(days), limit=min(max(1, int(limit)), HUB_PAGE_MAX), sender_domains=sender_domains,
                                     skip=skip, fields=HUB_FIELDS)
        return {"ok": bool(answer.get("ok")), "error": str(answer.get("error") or ""), "accounts": answer.get("accounts") or [],
                "messages": [_normalise(m) for m in answer.get("messages") or [] if isinstance(m, dict)], "via": answer.get("source") or "",
                "more": bool(answer.get("more"))}
