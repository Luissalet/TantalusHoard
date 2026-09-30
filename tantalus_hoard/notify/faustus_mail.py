"""Send one e-mail through the account configured in Faustus.

Tantalus runs this file with Faustus's own Python, inside the Faustus folder, so the mail password never
leaves Faustus: Tantalus sees only ``{ok, error, from, to, account}``. It reads one JSON request on stdin:

    {"action": "status"}                                   -> which account would send, nothing is sent
    {"action": "send", "subject": "...", "text": "...", "html": "...", "to": ["..."]}   (to defaults to the account itself)

and prints one JSON line. ``owner`` (optional) picks the Faustus user whose accounts are used; without it the only
owner that has accounts is used. The file imports nothing from Tantalus: it must run under another interpreter.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

TIMEOUT_S = 25


def _mask(address: str) -> str:
    address = str(address or "")
    if "@" not in address:
        return "***" if address else ""
    local, _, domain = address.partition("@")
    return (local[:3] + "***@" + domain) if local else "***@" + domain


def _load_server(root: str):
    sys.path.insert(0, root)
    os.chdir(root)
    with contextlib.redirect_stdout(io.StringIO()):  # the module may print while importing; stdout carries our answer
        from mcp_servers import email_server  # type: ignore
    return email_server


def _pick_owner(server, requested: str) -> str:
    if requested:
        return requested
    for key in ("ODYSSEUS_MCP_EMAIL_OWNER", "ODYSSEUS_EMAIL_OWNER"):
        if os.environ.get(key, "").strip():
            return os.environ[key].strip()
    rows = [r for r in server._read_accounts_from_db() if r.get("enabled", 1)]
    owners = sorted({str(r.get("owner") or "").strip() for r in rows} - {""})
    return owners[0] if len(owners) == 1 else ""


def _connect(cfg: dict):
    host, port = cfg["smtp_host"], int(cfg.get("smtp_port") or 465)
    security = str(cfg.get("smtp_security") or "").strip().lower()
    # A stored mode that contradicts the well-known port (implicit TLS on 587, STARTTLS on 465) cannot work: trust the port.
    if port == 587 and security == "ssl":
        security = "starttls"
    elif port == 465 and security == "starttls":
        security = "ssl"
    if security not in ("ssl", "starttls", "none"):
        security = "starttls" if port == 587 else "ssl"
    context = ssl.create_default_context()
    if security == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_S, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=TIMEOUT_S)
        if security == "starttls":
            client.starttls(context=context)
    client.login(cfg["smtp_user"], cfg["smtp_password"])
    return client


def _addresses(value) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for item in value or []:
        item = re.sub(r"[\r\n]+", "", str(item)).strip()
        if item and re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", item):
            out.append(item)
    return out[:10]


def handle(request: dict, root: str) -> dict:
    try:
        server = _load_server(root)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Faustus mail module not loadable ({type(exc).__name__})"}
    owner = _pick_owner(server, str(request.get("owner") or "").strip())
    if owner:
        os.environ["ODYSSEUS_MCP_EMAIL_OWNER"] = owner
    try:
        _selector, cfg = server._resolve_send_config(request.get("account") or None)
    except Exception as exc:  # noqa: BLE001 — Faustus's messages carry no secrets
        return {"ok": False, "error": str(exc)[:200] or type(exc).__name__}
    sender = str(cfg.get("from_address") or cfg.get("smtp_user") or "")
    to = _addresses(request.get("to")) or _addresses(sender)
    info = {"account": cfg.get("account_name") or "", "from": _mask(sender), "to": [_mask(a) for a in to],
            "server": f"{cfg.get('smtp_host')}:{cfg.get('smtp_port')}"}
    if request.get("action") != "send":
        return {"ok": True, "error": "", **info}
    if not to:
        return {"ok": False, "error": "no recipient", **info}
    msg = EmailMessage()
    msg["Subject"] = re.sub(r"[\r\n]+", " ", str(request.get("subject") or "Tantalus"))[:200]
    msg["From"] = formataddr((str(request.get("from_name") or "Tantalus's Hoard"), sender))
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.partition("@")[2] or None)
    msg.set_content(str(request.get("text") or request.get("subject") or ""))
    if request.get("html"):
        msg.add_alternative(str(request["html"]), subtype="html")
    client = None
    try:
        client = _connect(cfg)
        client.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "error": "authentication failed", **info}
    except (smtplib.SMTPException, OSError) as exc:
        return {"ok": False, "error": type(exc).__name__, **info}
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.quit()
    return {"ok": True, "error": "", **info}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        request = {}
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
    real_stdout = sys.stdout
    with contextlib.redirect_stdout(io.StringIO()):
        answer = handle(request if isinstance(request, dict) else {}, root)
    real_stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
