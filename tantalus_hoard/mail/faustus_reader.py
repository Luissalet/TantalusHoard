"""Read-only mail access through the account configured in Faustus (headers for the noise report, bodies for deals).

Tantalus runs this file with Faustus's own Python, inside the Faustus folder, so the mail password never leaves
Faustus: Tantalus only receives header summaries or the text of the messages it asked for. It reads one JSON
request on stdin:

    {"action": "status"}                                    -> which accounts would be read, nothing is fetched
    {"action": "headers", "since_days": 30, "max": 4000}    -> one small record per message (sender, date, List-Unsubscribe, Gmail
                                                                category); no subject, no body
    {"action": "scan", "since_days": 30, "max": 120,        -> messages whose sender belongs to one of ``sender_domains`` (newest
     "sender_domains": ["example.com", ...],                    first), minus the ``skip`` message ids; subject, text and links
     "skip": ["<message-id>", ...]}

and prints one JSON line. ``owner`` (optional) picks the Faustus user whose accounts are used; without it the only
owner that has accounts is used.

The mailbox is never modified: folders are opened read-only (EXAMINE) and every fetch uses BODY.PEEK, so nothing is
marked as read. There is no send, move, flag, label or delete code in this file. The Faustus connection helpers
(owner, accounts, folder selection, HTML to text) are adapted from the sibling paperwork app's mail helper.
It imports nothing from Tantalus: it must run under another interpreter, so it is stdlib-only.
"""

from __future__ import annotations

import contextlib
import email
import email.header
import email.utils
import html as _html
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

MAX_TEXT = 14_000
MAX_LINKS = 80
HEADER_CHUNK = 250
CATEGORIES = {"promotions": "promotions", "social": "social", "updates": "updates", "forums": "forums"}


def _mask(address: str) -> str:
    address = str(address or "")
    if "@" not in address:
        return "***" if address else ""
    local, _, domain = address.partition("@")
    return (local[:3] + "***@" + domain) if local else "***@" + domain


def _load_server(root: str):
    sys.path.insert(0, root)
    os.chdir(root)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
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


def _accounts(server, wanted):
    rows = [r for r in server._read_accounts_from_db() if r.get("enabled", 1)]
    try:
        rows = server._filter_accounts_for_owner(rows)
    except Exception:  # noqa: BLE001 - older Faustus builds
        pass
    if wanted:
        rows = [r for r in rows if wanted in (str(r.get("id")), str(r.get("account_name") or ""), str(r.get("imap_user") or ""))]
    return rows


def _selector(row: dict):
    for key in ("account_name", "imap_user", "id"):
        if row.get(key):
            return str(row[key])
    return None


# ------------------------------------------------------------------ message -> text
class _Text:
    BLOCK = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h\d|/table|p|div|tr|li|h\d)\b[^>]*>", re.I)
    DROP = re.compile(r"<\s*(style|script|head|title)\b.*?<\s*/\s*\1\s*>", re.I | re.S)
    HREF = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a\s*>""", re.I | re.S)
    TAG = re.compile(r"<[^>]+>")
    IMG_ALT = re.compile(r"""<img\b[^>]*?\balt\s*=\s*(["'])(.*?)\1""", re.I | re.S)

    @classmethod
    def from_html(cls, raw: str):
        raw = cls.DROP.sub(" ", raw)
        raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
        alts = [re.sub(r"\s+", " ", _html.unescape(a)).strip()[:160] for _q, a in cls.IMG_ALT.findall(raw)]
        alts = [a for a in alts if len(a) >= 3][:40]
        links = []
        for href, label in cls.HREF.findall(raw):
            label = re.sub(r"\s+", " ", _html.unescape(cls.TAG.sub(" ", label))).strip()
            href = _html.unescape(href).strip()
            if href.lower().startswith(("http://", "https://")):
                links.append({"url": href[:1500], "label": label[:160]})
        text = cls.BLOCK.sub("\n", raw)
        text = _html.unescape(cls.TAG.sub(" ", text))
        return cls.tidy(text), links, alts

    @staticmethod
    def tidy(text: str) -> str:
        text = re.sub(r"[\u200b-\u200f\u2060\u034f\ufeff\u00ad]", "", text.replace("\r", "").replace("\u00a0", " "))
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        out, blank = [], 0
        for line in lines:
            if not line:
                blank += 1
                if blank == 1 and out:
                    out.append("")
                continue
            blank = 0
            out.append(line)
        return "\n".join(out).strip()


def _decode(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    for charset in (part.get_content_charset(), "utf-8", "latin-1"):
        if not charset:
            continue
        try:
            return payload.decode(charset, errors="replace" if charset == "latin-1" else "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _header(msg, name: str, server=None) -> str:
    value = msg.get(name, "") or ""
    if server is not None:
        try:
            return str(server._decode_header(value))
        except Exception:  # noqa: BLE001
            pass
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # noqa: BLE001
        return str(value)


def _timestamp(msg):
    try:
        when = email.utils.parsedate_to_datetime(msg.get("Date", ""))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def message_to_record(msg, server=None) -> dict:
    """Subject, sender, date, plain text (HTML converted) and links of one parsed message. Attachments are ignored."""
    plain, html_text, links, alts = "", "", [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.is_multipart() or "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and not plain:
            plain = _Text.tidy(_decode(part))
        elif ctype == "text/html" and not html_text:
            html_text, links, alts = _Text.from_html(_decode(part))
    text = html_text if len(html_text) > len(plain) * 0.6 or not plain else plain
    for url in re.findall(r"https?://[^\s<>\"')\]]+", plain):
        links.append({"url": url[:1500], "label": ""})
    seen, unique = set(), []
    for link in links:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        unique.append(link)
    name, address = email.utils.parseaddr(_header(msg, "From", server))
    return {"message_id": (msg.get("Message-ID", "") or "").strip()[:300], "subject": _header(msg, "Subject", server)[:300],
            "from_name": name[:120], "from_address": address[:200].lower(), "ts": _timestamp(msg),
            "text": text[:MAX_TEXT], "links": unique[:MAX_LINKS], "images": alts}

# ------------------------------------------------------------------ IMAP helpers
def _uids(conn, criteria):
    try:
        status, data = conn.uid("SEARCH", None, *criteria)
    except Exception:  # noqa: BLE001
        return []
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _is_gmail(host: str) -> bool:
    return "gmail" in host or "googlemail" in host


def _folders_for(host: str):
    return ["[Gmail]/All Mail", "[Gmail]/Todos", "INBOX"] if _is_gmail(host) else ["INBOX"]


def _select_first(conn, server, folders) -> str:
    """Open the first folder that exists, READ-ONLY (EXAMINE): flags are never touched."""
    for folder in folders:
        try:
            status, _ = conn.select(server._q(folder) if hasattr(server, "_q") else folder, readonly=True)
        except Exception:  # noqa: BLE001
            continue
        if status == "OK":
            return folder
    try:  # localized Gmail "All Mail": look it up by its \All flag
        status, lines = conn.list()
        for line in lines or []:
            text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            if "\\All" in text:
                name = text.rsplit(' "', 1)[-1].rstrip('"') if '"' in text else text.split()[-1]
                if conn.select(f'"{name}"', readonly=True)[0] == "OK":
                    return name
    except Exception:  # noqa: BLE001
        pass
    conn.select("INBOX", readonly=True)
    return "INBOX"


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).strftime("%d-%b-%Y")


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _fetch_headers(conn, uids, fields: str) -> dict:
    """{uid: parsed header message} for many uids at once, headers only, nothing marked as read."""
    out = {}
    for chunk in _chunks(uids, HEADER_CHUNK):
        spec = b",".join(chunk).decode()
        try:
            status, data = conn.uid("FETCH", spec, f"(UID BODY.PEEK[HEADER.FIELDS ({fields})])")
        except Exception:  # noqa: BLE001
            continue
        if status != "OK":
            continue
        for item in data or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            found = re.search(rb"UID (\d+)", item[0])
            if found:
                out[found.group(1)] = email.message_from_bytes(item[1])
    return out


def _gmail_category_uids(conn, days: int) -> dict:
    """{uid: category} from Gmail's own category search; empty when the server is not Gmail."""
    mapping = {}
    for key, name in CATEGORIES.items():
        raw = f"category:{key} newer_than:{max(1, int(days))}d"
        for uid in _uids(conn, ["X-GM-RAW", '"' + raw + '"']):
            mapping.setdefault(uid, name)
    return mapping


def _own_addresses(row: dict) -> set:
    return {str(row.get(k) or "").lower() for k in ("imap_user", "from_address", "smtp_user")} - {""}


def _label(row: dict) -> str:
    return str(row.get("account_name") or _mask(row.get("imap_user") or ""))


# ------------------------------------------------------------------ actions
def headers(server, request: dict) -> dict:
    days = max(1, min(int(request.get("since_days") or 30), 365))
    limit = max(1, min(int(request.get("max") or 4000), 12000))
    messages, errors, scanned = [], [], []
    for row in _accounts(server, request.get("account")):
        host = str(row.get("imap_host") or "").lower()
        conn = None
        try:
            conn = server._imap_connect(_selector(row))
            folder = _select_first(conn, server, _folders_for(host))
            uids = sorted(set(_uids(conn, ["SINCE", _since(days)])), key=int, reverse=True)
            total = len(uids)
            uids = uids[:limit]
            categories = _gmail_category_uids(conn, days) if _is_gmail(host) else {}
            own = _own_addresses(row)
            parsed = _fetch_headers(conn, uids, "FROM DATE MESSAGE-ID LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST")
            for uid, msg in parsed.items():
                address = email.utils.parseaddr(_header(msg, "From", server))[1].lower()
                unsub = re.sub(r"\s+", " ", str(msg.get("List-Unsubscribe", "") or "")).strip()
                messages.append({"from_address": address[:200], "ts": _timestamp(msg), "list_unsubscribe": unsub[:800],
                                 "one_click": bool(msg.get("List-Unsubscribe-Post")), "category": categories.get(uid, ""),
                                 "from_self": address in own})
            scanned.append({"account": _label(row), "folder": folder, "in_window": total, "read": len(parsed),
                            "gmail_categories": bool(categories)})
        except Exception as exc:  # noqa: BLE001 - one bad account must not hide the others
            errors.append(f"{_label(row)}: {type(exc).__name__}: {str(exc)[:160]}")
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.logout()
    return {"ok": not errors or bool(messages) or bool(scanned), "error": "; ".join(errors), "accounts": scanned, "messages": messages}


def scan(server, request: dict) -> dict:
    days = max(1, min(int(request.get("since_days") or 30), 365))
    limit = max(1, min(int(request.get("max") or 120), 600))
    skip = {str(s) for s in (request.get("skip") or [])}
    domains = [re.sub(r"[^a-z0-9.\-]", "", str(d).lower()) for d in (request.get("sender_domains") or [])]
    domains = [d for d in domains if d][:80]
    if not domains:
        return {"ok": True, "error": "", "accounts": [], "messages": []}
    out, errors, scanned = [], [], []
    for row in _accounts(server, request.get("account")):
        host = str(row.get("imap_host") or "").lower()
        conn = None
        try:
            conn = server._imap_connect(_selector(row))
            folder = _select_first(conn, server, _folders_for(host))
            uids = []
            if _is_gmail(host):
                uids = _uids(conn, ["X-GM-RAW", '"from:(' + " OR ".join(domains) + f') newer_than:{days}d"'])
            else:
                for domain in domains:
                    uids += _uids(conn, ["SINCE", _since(days), "FROM", f'"{domain}"'])
            uids = sorted(set(uids), key=int, reverse=True)
            heads = _fetch_headers(conn, uids, "MESSAGE-ID")
            fresh = [u for u in uids if u in heads and str(heads[u].get("Message-ID", "") or "").strip() not in skip]
            fresh += [u for u in uids if u not in heads]
            scanned.append({"account": _label(row), "folder": folder, "matches": len(uids), "new": len(fresh)})
            own = _own_addresses(row)
            for uid in fresh:
                if len(out) >= limit:
                    break
                status, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK" or not data or not isinstance(data[0], tuple):
                    continue
                record = message_to_record(email.message_from_bytes(data[0][1]), server)
                if record["from_address"] not in own:
                    out.append(record)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{_label(row)}: {type(exc).__name__}: {str(exc)[:160]}")
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.logout()
    out.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    return {"ok": not errors or bool(out) or bool(scanned), "error": "; ".join(errors), "accounts": scanned, "messages": out}


def handle(request: dict, root: str) -> dict:
    try:
        server = _load_server(root)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Faustus mail module not loadable ({type(exc).__name__})"}
    owner = _pick_owner(server, str(request.get("owner") or "").strip())
    if owner:
        os.environ["ODYSSEUS_MCP_EMAIL_OWNER"] = owner
    try:
        rows = _accounts(server, request.get("account"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"accounts not readable ({type(exc).__name__})"}
    if not rows:
        return {"ok": False, "error": "Faustus has no enabled mail account" + (f" for {owner}" if owner else "")}
    action = request.get("action")
    if action == "headers":
        return headers(server, request)
    if action == "scan":
        return scan(server, request)
    return {"ok": True, "error": "", "accounts": [{"account": r.get("account_name") or "", "user": _mask(r.get("imap_user") or ""),
                                                   "server": f"{r.get('imap_host')}:{r.get('imap_port')}"} for r in rows]}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        request = {}
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
    real_stdout = sys.stdout
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        answer = handle(request if isinstance(request, dict) else {}, root)
    real_stdout.write(json.dumps(answer) + "\n")  # ASCII-safe whatever the console encoding is
    return 0


if __name__ == "__main__":
    sys.exit(main())
