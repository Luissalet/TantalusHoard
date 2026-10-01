"""Reading the mailbox through Faustus: runs ``faustus_reader.py`` under Faustus's own Python.

The mail password never reaches Tantalus. Tantalus sends one JSON request and gets back one JSON line: header
summaries for the noise report, or the text of the messages from the shops it asked for. The mailbox is read-only on the
other side (see ``faustus_reader.py``); nothing here can send, move, flag or delete a message.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

READER = Path(__file__).with_name("faustus_reader.py")
STATUS_TIMEOUT_S = 60
HEADERS_TIMEOUT_S = 180
SCAN_TIMEOUT_S = 420


class MailSource:
    """``runner`` is injectable: ``runner(request, timeout) -> dict`` (tests use a fake mailbox)."""

    def __init__(self, notifier: Any = None, settings_get: Optional[Callable[[str, Optional[str]], Optional[str]]] = None,
                 runner: Optional[Callable[[dict[str, Any], int], dict[str, Any]]] = None, process_runner: Optional[Callable[..., Any]] = None):
        self.notifier = notifier
        self.get = settings_get or (lambda key, default=None: default)
        self.runner = runner
        self.process_runner = process_runner or subprocess.run

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
        return self._call({"action": "scan", "since_days": int(days), "max": int(limit), "sender_domains": sender_domains,
                           "skip": skip}, SCAN_TIMEOUT_S)
