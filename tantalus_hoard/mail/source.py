"""Reading the mailbox through Faustus: runs ``faustus_reader.py`` under Faustus's own Python.

The mail password never reaches Tantalus. Tantalus sends one JSON request and gets back one JSON line: header
summaries for the noise report, or the text of the messages from the shops it asked for. The mailbox is read-only on the
other side (see ``faustus_reader.py``); nothing here can send, move, flag or delete a message.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

READER = Path(__file__).with_name("faustus_reader.py")
STATUS_TIMEOUT_S = 60
HEADERS_TIMEOUT_S = 180
SCAN_TIMEOUT_S = 420
SOURCE_MODES = ("auto", "hub", "faustus")     # mail.source: the family hub's mail gateway, Faustus's own helper, or the hub with a fallback
HUB_PAGE_MAX = 500
WATERMARK_KEY = "mail.hub.since_id"            # the hub's message id up to which deals have been read


class MailSource:
    """``runner`` is injectable: ``runner(request, timeout) -> dict`` (tests use a fake mailbox)."""

    def __init__(self, notifier: Any = None, settings_get: Optional[Callable[[str, Optional[str]], Optional[str]]] = None,
                 runner: Optional[Callable[[dict[str, Any], int], dict[str, Any]]] = None, process_runner: Optional[Callable[..., Any]] = None,
                 settings_set: Optional[Callable[[str, str], None]] = None, hub_mail: Any = None,
                 clock: Callable[[], float] = time.time):
        self.notifier = notifier
        self.get = settings_get or (lambda key, default=None: default)
        self.put = settings_set or (lambda key, value: None)
        self.runner = runner
        self.process_runner = process_runner or subprocess.run
        self.clock = clock
        self._hub_mail = hub_mail                   # an object with available/register_interest/messages/claim; default: hoard_link.fam_mail
        self._registered: Optional[tuple[str, ...]] = None
        self._pending_since: Optional[int] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the family hub's mail gateway (pattern 2)
    @property
    def hub(self) -> Any:
        if self._hub_mail is None:
            from ..hoard_link import fam_mail
            self._hub_mail = fam_mail
        return self._hub_mail

    def source_setting(self) -> str:
        """``mail.source``: ``auto`` (hub when its gateway is ready, else Faustus's helper), ``hub`` (only the hub), ``faustus``."""
        value = str(self.get("mail.source", "auto") or "auto").strip().lower()
        return value if value in SOURCE_MODES else "auto"

    def hub_up(self) -> bool:
        try:
            return bool(self.hub.available())
        except Exception:  # noqa: BLE001
            return False

    def source_status(self) -> dict[str, Any]:
        setting = self.source_setting()
        up = self.hub_up() if setting != "faustus" else False
        return {"setting": setting, "effective": "hub" if (setting == "hub" or (setting == "auto" and up)) else "faustus",
                "hub_available": up, "interest_registered": self._registered is not None,
                "hub_since_id": int(self.get(WATERMARK_KEY, "0") or 0),
                "noise_report": "faustus"}        # the noise report needs every header: the hub never provides that to an app

    def forget_interest(self) -> None:
        with self._lock:
            self._registered = None

    def ensure_interest(self, sender_domains: list[str], *, force: bool = False) -> dict[str, Any]:
        """Tell the hub which sale mails Tantalus wants: the sender domains of the shops it knows (promotions of those senders).
        Registered once per process and again whenever the shops or domains change."""
        signature = tuple(sorted({d.lower() for d in sender_domains if d}))
        with self._lock:
            if not force and self._registered == signature:
                return {"ok": True, "cached": True}
        if not signature:
            return {"ok": False, "error": "no sender domains"}
        try:
            answer = self.hub.register_interest({"from_domains": list(signature)})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"hub interest: {type(exc).__name__}"}
        if isinstance(answer, dict) and answer.get("ok"):
            with self._lock:
                self._registered = signature
            return {"ok": True}
        return {"ok": False, "error": str((answer or {}).get("error") or "the hub refused the interest")[:200]}

    def _scan_hub(self, days: int, limit: int, sender_domains: list[str], skip: list[str]) -> dict[str, Any]:
        registered = self.ensure_interest(sender_domains)
        if not registered.get("ok"):
            return {"ok": False, "error": registered.get("error") or "hub interest failed", "accounts": [], "messages": [], "via": "hub"}
        since = int(self.get(WATERMARK_KEY, "0") or 0)
        page = self.hub.messages(since_id=since, limit=min(max(1, int(limit)), HUB_PAGE_MAX), full=True)
        if not page.get("ok"):
            return {"ok": False, "error": str(page.get("error") or "hub mail failed")[:200], "accounts": [], "messages": [], "via": "hub"}
        raw = [m for m in page.get("messages") or [] if isinstance(m, dict)]
        cutoff = self.clock() - int(days) * 86400
        skipped = set(skip)
        out = []
        for m in raw:
            ts = m.get("ts")
            if str(m.get("message_id") or "") in skipped or (ts and float(ts) < cutoff):
                continue
            out.append({"message_id": m.get("message_id") or "", "subject": m.get("subject") or "", "from_name": m.get("from_name") or "",
                        "from_address": str(m.get("from_address") or m.get("from_addr") or "").lower(), "ts": ts,
                        "text": m.get("text") or "", "links": m.get("links") or [], "images": m.get("images") or [],
                        "hub_id": m.get("id")})
        self._pending_since = int(page.get("last_id") or since)
        return {"ok": True, "error": "", "accounts": [{"account": "hub", "matches": len(raw), "new": len(out)}], "messages": out,
                "via": "hub", "more": len(raw) >= min(max(1, int(limit)), HUB_PAGE_MAX)}

    def commit(self) -> None:
        """Remember how far the hub's mail was read. Called by the scan once its messages are stored."""
        pending, self._pending_since = self._pending_since, None
        if pending is not None:
            self.put(WATERMARK_KEY, str(pending))

    def claim(self, hub_id: Any, ref: str) -> None:
        """Tell the hub "this mail is mine" so it leaves the unowned tray. Errors are ignored: claims are hints."""
        if hub_id in (None, ""):
            return
        try:
            self.hub.claim([int(hub_id)], "deal", ref)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ plumbing
    def _call(self, request: dict[str, Any], timeout: int) -> dict[str, Any]:
        if self.runner is not None:
            return self.runner(request, timeout)
        root = self.notifier.faustus_dir() if self.notifier is not None else None
        if root is None:
            return {"ok": False, "error": "Faustus folder not found (set notify.email.faustus_dir or TANTALUS_FAUSTUS_DIR)"}
        python = self.notifier.faustus_python(root)
        if python is None:
            return {"ok": False, "error": "Faustus has no venv with Python"}
        owner = str(self.get("notify.email.faustus_owner", "") or "").strip()
        if owner:
            request = {**request, "owner": owner}
        env = {k: v for k, v in os.environ.items() if not k.startswith("TANTALUS_")}   # Tantalus's own secrets stay here
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            done = self.process_runner([python, str(READER), str(root)], input=json.dumps(request), capture_output=True, text=True,
                                       encoding="utf-8", timeout=timeout, cwd=str(root), env=env,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"mail reader timed out after {timeout}s"}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"mail reader: {type(exc).__name__}"}
        lines = [line for line in (getattr(done, "stdout", "") or "").splitlines() if line.strip().startswith("{")]
        try:
            answer = json.loads(lines[-1]) if lines else {}
        except ValueError:
            answer = {}
        if not isinstance(answer, dict) or "ok" not in answer:
            return {"ok": False, "error": f"mail reader exit {getattr(done, 'returncode', '?')}"}
        return answer

    # ------------------------------------------------------------------ actions
    def status(self) -> dict[str, Any]:
        return self._call({"action": "status"}, STATUS_TIMEOUT_S)

    def headers(self, days: int = 30, limit: int = 4000) -> dict[str, Any]:
        return self._call({"action": "headers", "since_days": int(days), "max": int(limit)}, HEADERS_TIMEOUT_S)

    def scan(self, days: int, limit: int, sender_domains: list[str], skip: list[str]) -> dict[str, Any]:
        mode = self.source_setting()
        if mode == "hub" or (mode == "auto" and self.hub_up()):
            answer = self._scan_hub(days, limit, sender_domains, skip)
            if answer.get("ok") or mode == "hub":
                return answer
            # auto: the hub could not give the mail this time, so Faustus's own helper reads it
        return self._call({"action": "scan", "since_days": int(days), "max": int(limit), "sender_domains": sender_domains,
                           "skip": skip}, SCAN_TIMEOUT_S)
