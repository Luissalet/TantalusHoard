"""Notification channels: Windows toast, family hub, ntfy, Telegram and e-mail.

E-mail has two backends: plain SMTP with Tantalus's own credentials, or the account configured in Faustus
(``faustus_mail.py`` runs under Faustus's Python, so that password never reaches Tantalus). ``auto`` uses SMTP when
Tantalus has its own user and password and Faustus otherwise.

``Notifier.send(event, channels)`` returns one ``{channel, ok, error}`` per channel and never raises.
Secrets come only from ``config.secret(...)`` (environment / .env); the database holds only the
``notify.<channel>.enabled`` / ``min_severity`` / non-secret settings. Nothing secret is logged or
put in an error string.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import httpx

from ..config import REPO_ROOT
from .labels import LABELS, TYPE_TAGS, WORDS, compose, format_price, label

log = logging.getLogger("tantalus.notify")

CHANNELS = ("toast", "hub", "ntfy", "telegram", "email")
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
APP_ID = "Tantalus's Hoard"
POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
DEFAULT_ENABLED = {"toast": True, "hub": True, "ntfy": False, "telegram": False, "email": False}
HTTP_TIMEOUT_S = 10.0
EMAIL_BACKENDS = ("auto", "faustus", "smtp")
FAUSTUS_HELPER = Path(__file__).with_name("faustus_mail.py")
FAUSTUS_TIMEOUT_S = 60
FAUSTUS_STATUS_TTL_S = 300.0     # a good status is reused for five minutes, a failure for thirty seconds


def xml_escape(text: str) -> str:
    """Escape for XML text and attribute values; also drops characters XML 1.0 forbids."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")
    return html.escape(text, quote=True)


def _http_url(url: Any) -> str:
    url = str(url or "").strip()
    return url if urlsplit(url).scheme in ("http", "https") else ""


def _scrub(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return text


def build_toast_ps1(title: str, body: str, url: str = "") -> str:
    """PowerShell that shows one toast through Windows.UI.Notifications (no extra module needed)."""
    launch = _http_url(url)
    attrs = f' activationType="protocol" launch="{xml_escape(launch)}"' if launch else ""
    xml = (f'<toast{attrs}><visual><binding template="ToastGeneric"><text>{xml_escape(title[:120])}</text>'
           f'<text>{xml_escape(body[:300])}</text></binding></visual></toast>')
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
        f"$xml = @'\n{xml}\n'@\n"
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "$doc.LoadXml($xml)\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)\n"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{POWERSHELL_APP_ID}').Show($toast)\n"
    )


class Notifier:
    def __init__(self, config: Any, db_settings_get: Callable[[str, Optional[str]], Optional[str]], *, transport: Any = None,
                 clock: Callable[[], float] = time.time, smtp_factory: Optional[Callable[..., Any]] = None,
                 toast_backend: Optional[Callable[[str, str, str, bool], None]] = None,
                 powershell_runner: Optional[Callable[..., Any]] = None, platform: Optional[str] = None, icon_path: Optional[Path] = None,
                 faustus_runner: Optional[Callable[..., Any]] = None):
        self.config = config
        self.get = db_settings_get
        self.transport = transport
        self.clock = clock
        self.smtp_factory = smtp_factory or self._default_smtp
        self.toast_backend = toast_backend
        self.powershell_runner = powershell_runner or subprocess.run
        self.platform = platform or sys.platform
        self.icon_path = icon_path if icon_path is not None else REPO_ROOT / "app-icon.png"
        self.faustus_runner = faustus_runner or subprocess.run
        self._faustus_status: Optional[tuple[float, str, dict[str, Any]]] = None

    # ------------------------------------------------------------------ settings and status
    def _setting(self, key: str, default: str = "") -> str:
        try:
            value = self.get(key, None)
        except Exception:  # noqa: BLE001
            value = None
        return default if value in (None, "") else str(value)

    def _lang(self) -> str:
        lang = (self._setting("notify.language") or self._setting("ui.language") or "es").lower()[:2]
        return lang if lang in LABELS else "es"

    def _secret(self, name: str) -> str:
        try:
            return self.config.secret(name)
        except Exception:  # noqa: BLE001
            return ""

    def _enabled(self, channel: str) -> bool:
        raw = self._setting(f"notify.{channel}.enabled", "")
        return DEFAULT_ENABLED.get(channel, False) if raw == "" else raw.strip() == "1"

    def _min_severity(self, channel: str) -> str:
        value = self._setting(f"notify.{channel}.min_severity", "low").lower()
        return value if value in SEVERITY_RANK else "low"

    def _is_windows(self) -> bool:
        return self.platform.startswith("win")

    def _ntfy_topic(self) -> str:
        return self._secret("NTFY_TOPIC") or self._setting("notify.ntfy.topic")

    def _ntfy_server(self) -> str:
        return self._setting("notify.ntfy.server", "https://ntfy.sh").rstrip("/")

    def _email_settings(self) -> dict[str, Any]:
        user = self._secret("SMTP_USER")
        try:
            port = int(self._secret("SMTP_PORT") or 465)
        except ValueError:
            port = 465
        to = [a.strip() for a in (self._secret("SMTP_TO") or user).split(",") if a.strip()]
        return {"host": self._secret("SMTP_HOST") or "smtp.gmail.com", "port": port, "user": user, "password": self._secret("SMTP_PASSWORD"),
                "from": self._secret("SMTP_FROM") or user, "to": to}

    def _configured(self, channel: str) -> tuple[bool, str]:
        if channel == "toast":
            if not self._is_windows():
                return False, "not windows"
            try:
                import winotify  # noqa: F401
                return True, "winotify"
            except ImportError:
                return True, "PowerShell fallback"
        if channel == "hub":
            return True, "family event bus"
        if channel == "ntfy":
            return (True, f"{self._ntfy_server()}") if self._ntfy_topic() else (False, "missing topic (TANTALUS_NTFY_TOPIC or notify.ntfy.topic)")
        if channel == "telegram":
            missing = [f"TANTALUS_{n}" for n in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID") if not self._secret(n)]
            return (not missing), ("bot + chat id set" if not missing else "missing " + ", ".join(missing))
        if channel == "email":
            if self.email_backend() == "faustus":
                return self._faustus_configured()
            e = self._email_settings()
            missing = [n for n, v in (("TANTALUS_SMTP_USER", e["user"]), ("TANTALUS_SMTP_PASSWORD", e["password"]), ("TANTALUS_SMTP_TO", e["to"])) if not v]
            return (not missing), (f"SMTP {e['host']}:{e['port']}" if not missing else "missing " + ", ".join(missing))
        return False, "unknown channel"

    def channels_status(self) -> dict[str, dict[str, Any]]:
        status = {}
        for channel in CHANNELS:
            configured, detail = self._configured(channel)
            status[channel] = {"configured": configured, "enabled": self._enabled(channel), "detail": detail,
                               "min_severity": self._min_severity(channel)}
        status["email"]["backend"] = self.email_backend()
        status["email"]["backend_setting"] = self._email_backend_setting()
        status["email"]["faustus_dir"] = str(self.faustus_dir() or "")
        return status

    # ------------------------------------------------------------------ e-mail through Faustus
    def _email_backend_setting(self) -> str:
        value = self._setting("notify.email.backend", "auto").lower()
        return value if value in EMAIL_BACKENDS else "auto"

    def email_backend(self) -> str:
        """``faustus`` or ``smtp``: the backend a mail would use now."""
        mode = self._email_backend_setting()
        if mode != "auto":
            return mode
        if self._secret("SMTP_USER") and self._secret("SMTP_PASSWORD"):
            return "smtp"
        return "faustus" if self.faustus_dir() else "smtp"

    def faustus_dir(self) -> Optional[Path]:
        """The Faustus folder: setting, TANTALUS_FAUSTUS_DIR, FAUSTUS_DIR, then a ``faustus`` folder next to this app."""
        raw = [self._setting("notify.email.faustus_dir"), self._secret("FAUSTUS_DIR"), os.environ.get("FAUSTUS_DIR", "")]
        candidates = [Path(r).expanduser() for r in raw if r and r.strip()]
        if not any(r and r.strip() for r in raw):
            candidates += [REPO_ROOT.parent / "faustus", REPO_ROOT.parent.parent / "faustus"]
        for path in candidates:
            try:
                if (path / "mcp_servers" / "email_server.py").is_file():
                    return path.resolve()
            except OSError:
                continue
        return None

    @staticmethod
    def faustus_python(root: Path) -> Optional[str]:
        for rel in ("venv/Scripts/python.exe", ".venv/Scripts/python.exe", "venv/bin/python", ".venv/bin/python"):
            if (root / rel).is_file():
                return str(root / rel)
        return None

    def _faustus_call(self, request: dict[str, Any]) -> dict[str, Any]:
        root = self.faustus_dir()
        if root is None:
            return {"ok": False, "error": "Faustus folder not found (set notify.email.faustus_dir)"}
        python = self.faustus_python(root)
        if python is None:
            return {"ok": False, "error": "Faustus has no venv with Python"}
        owner = self._setting("notify.email.faustus_owner")
        if owner:
            request = {**request, "owner": owner}
        env = {k: v for k, v in os.environ.items() if not k.startswith("TANTALUS_")}   # Tantalus's own secrets stay here
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            done = self.faustus_runner([python, str(FAUSTUS_HELPER), str(root)], input=json.dumps(request), capture_output=True, text=True,
                                       encoding="utf-8", timeout=FAUSTUS_TIMEOUT_S, cwd=str(root), env=env,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"Faustus mail helper: {type(exc).__name__}"}
        lines = [line for line in (getattr(done, "stdout", "") or "").splitlines() if line.strip().startswith("{")]
        try:
            answer = json.loads(lines[-1]) if lines else {}
        except ValueError:
            answer = {}
        if not isinstance(answer, dict) or "ok" not in answer:
            return {"ok": False, "error": f"Faustus mail helper exit {getattr(done, 'returncode', '?')}"}
        return answer

    def faustus_status(self, refresh: bool = False) -> dict[str, Any]:
        key = str(self.faustus_dir() or "") + "|" + self._setting("notify.email.faustus_owner")
        cached = self._faustus_status
        if cached and not refresh and cached[1] == key:
            ttl = FAUSTUS_STATUS_TTL_S if cached[2].get("ok") else 30.0
            if self.clock() - cached[0] < ttl:
                return cached[2]
        result = self._faustus_call({"action": "status", "to": self._secret("SMTP_TO")})
        self._faustus_status = (self.clock(), key, result)
        return result

    def _faustus_configured(self) -> tuple[bool, str]:
        if self.faustus_dir() is None:
            return False, "Faustus folder not found (set notify.email.faustus_dir or TANTALUS_FAUSTUS_DIR)"
        st = self.faustus_status()
        if not st.get("ok"):
            return False, "Faustus: " + str(st.get("error") or "unavailable")[:200]
        name = f"{st.get('account')} " if st.get("account") else ""
        return True, f"Faustus account {name}<{st.get('from')}> → {', '.join(st.get('to') or [])}"

    # ------------------------------------------------------------------ sending
    def send(self, event: dict[str, Any], channels: list[str]) -> list[dict[str, Any]]:
        results = []
        rank = SEVERITY_RANK.get(str(event.get("severity") or "medium").lower(), 1)
        for channel in channels:
            if channel not in CHANNELS:
                results.append({"channel": channel, "ok": False, "error": "unknown channel"})
                continue
            if not self._enabled(channel):
                results.append({"channel": channel, "ok": False, "error": "disabled", "skipped": True})
                continue
            if rank < SEVERITY_RANK[self._min_severity(channel)]:
                results.append({"channel": channel, "ok": False, "error": "below minimum severity", "skipped": True})
                continue
            results.append(self._deliver(channel, event))
        return results

    def test(self, channel: str) -> dict[str, Any]:
        """Send a sample notification through one channel, ignoring its enabled flag and severity floor."""
        if channel not in CHANNELS:
            return {"channel": channel, "ok": False, "error": "unknown channel"}
        words = WORDS[self._lang()]
        sample = {"id": "test", "type": "INFO_CHANGE", "severity": "medium", "title": words["test_title"], "summary": words["test_body"],
                  "url": "", "price": None, "currency": None, "confidence": None, "watcher_name": "", "image": ""}
        return self._deliver(channel, sample)

    def _deliver(self, channel: str, event: dict[str, Any]) -> dict[str, Any]:
        configured, detail = self._configured(channel)
        if not configured:
            return {"channel": channel, "ok": False, "error": detail}
        try:
            error = getattr(self, "_send_" + channel)(event, *compose(event, self._lang()))
        except Exception as exc:  # noqa: BLE001 — a channel must never raise out of the engine
            error = f"{type(exc).__name__}"
            log.info("notify %s failed: %s", channel, type(exc).__name__)
        return {"channel": channel, "ok": not error, "error": error or "", "ts": self.clock()}

    # ------------------------------------------------------------------ channels (each returns '' on success or an error)
    def _send_toast(self, event: dict[str, Any], title: str, body: str) -> str:
        if not self._is_windows():
            return "not windows"
        url = _http_url(event.get("url"))
        high = str(event.get("severity")) == "high"
        if self.toast_backend is not None:
            self.toast_backend(title, body, url, high)
            return ""
        try:
            from winotify import Notification, audio  # type: ignore

            icon = str(self.icon_path) if self.icon_path and Path(self.icon_path).exists() else ""
            toast = Notification(app_id=APP_ID, title=title[:120], msg=body[:300], launch=url, **({"icon": icon} if icon else {}))
            if high:
                toast.set_audio(audio.Default, loop=False)
            toast.show()
            return ""
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001 — fall through to the PowerShell path
            log.info("winotify failed (%s); trying PowerShell", type(exc).__name__)
        return self._toast_powershell(title, body, url)

    def _toast_powershell(self, title: str, body: str, url: str) -> str:
        script = build_toast_ps1(title, body, url)
        fd, path = tempfile.mkstemp(suffix=".ps1", prefix="tantalus-toast-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig") as handle:  # BOM: Windows PowerShell 5.1 reads UTF-8 only with it
                handle.write(script)
            exe = shutil.which("powershell") or "powershell"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            done = self.powershell_runner([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path], capture_output=True, text=True,
                                          timeout=20, creationflags=flags)
            return "" if getattr(done, "returncode", 1) == 0 else f"powershell exit {getattr(done, 'returncode', '?')}"
        except (OSError, subprocess.SubprocessError) as exc:
            return type(exc).__name__
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _send_hub(self, event: dict[str, Any], title: str, body: str) -> str:
        from ..hoard_link import family

        payload = {"event_id": event.get("id"), "type": event.get("type"), "severity": event.get("severity"), "title": title,
                   "summary": event.get("summary") or body, "url": event.get("url"), "price": event.get("price"),
                   "currency": event.get("currency"), "confidence": event.get("confidence"), "watcher_name": event.get("watcher_name"),
                   "image": event.get("image")}
        try:
            accepted = family.emit("tantalus.alert", payload)
        except Exception:  # noqa: BLE001 — emit is documented not to raise; be safe anyway
            accepted = False
        return "" if accepted else "hub not configured"

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=HTTP_TIMEOUT_S, follow_redirects=False)

    def _send_ntfy(self, event: dict[str, Any], title: str, body: str) -> str:
        server, topic = self._ntfy_server(), self._ntfy_topic()
        if urlsplit(server).scheme not in ("http", "https"):
            return "invalid ntfy server"
        severity = str(event.get("severity") or "medium")
        try:
            conf = float(event.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0
        priority = (5 if conf >= 90 else 4) if severity == "high" else 3 if severity == "medium" else 2
        payload: dict[str, Any] = {"topic": topic, "title": title[:250], "message": body or title, "priority": priority,
                                   "tags": [TYPE_TAGS.get(str(event.get("type")), "bell")]}
        if _http_url(event.get("url")):
            payload["click"] = event["url"]
        if _http_url(event.get("image")):
            payload["attach"] = event["image"]
        headers = {"Content-Type": "application/json; charset=utf-8"}
        token = self._secret("NTFY_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            with self._client() as client:
                response = client.post(server + "/", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return type(exc).__name__
        return "" if 200 <= response.status_code < 300 else f"http {response.status_code}"

    def _send_telegram(self, event: dict[str, Any], title: str, body: str) -> str:
        token, chat = self._secret("TELEGRAM_TOKEN"), self._secret("TELEGRAM_CHAT_ID")
        text = f"<b>{html.escape(title)}</b>"
        if body:
            text += "\n" + html.escape(body)
        url = _http_url(event.get("url"))
        if url:
            text += f'\n<a href="{html.escape(url, quote=True)}">{WORDS[self._lang()]["open"]}</a>'
        payload = {"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": False}
        try:
            with self._client() as client:
                response = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
        except httpx.HTTPError as exc:
            return type(exc).__name__
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 200 and data.get("ok"):
            return ""
        return _scrub(str(data.get("description") or f"http {response.status_code}"), [token, chat])[:160]

    def telegram_discover_chat_id(self) -> dict[str, Any]:
        """Find the chat id after the user has written to the bot (getUpdates). Nothing is stored here."""
        return telegram_discover_chat_id(self._secret("TELEGRAM_TOKEN"), transport=self.transport)

    def _default_smtp(self, host: str, port: int, use_ssl: bool) -> Any:
        context = ssl.create_default_context()
        if use_ssl:
            return smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        client = smtplib.SMTP(host, port, timeout=20)
        client.starttls(context=context)
        return client

    @staticmethod
    def _email_parts(event: dict[str, Any], title: str, body: str) -> tuple[str, str, str]:
        subject = re.sub(r"[\r\n]+", " ", title)[:200]
        url = _http_url(event.get("url"))
        text = (body + (f"\n\n{url}" if url else "")) or title
        rows = "".join(f"<p>{html.escape(line)}</p>" for line in body.splitlines() if line.strip())
        link = f'<p><a href="{html.escape(url, quote=True)}">{html.escape(url)}</a></p>' if url else ""
        return subject, text, f"<html><body><h3>{html.escape(title)}</h3>{rows}{link}</body></html>"

    def _send_email(self, event: dict[str, Any], title: str, body: str) -> str:
        subject, text, html_body = self._email_parts(event, title, body)
        if self.email_backend() == "faustus":
            to = [a.strip() for a in self._secret("SMTP_TO").split(",") if a.strip()]
            answer = self._faustus_call({"action": "send", "subject": subject, "text": text, "html": html_body, "to": to})
            if not answer.get("ok"):
                self._faustus_status = None      # re-check the account on the next status
            return "" if answer.get("ok") else str(answer.get("error") or "Faustus mail failed")[:200]
        e = self._email_settings()
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"], msg["To"] = e["from"] or e["user"], ", ".join(e["to"])
        msg.set_content(text)
        msg.add_alternative(html_body, subtype="html")
        client = self.smtp_factory(e["host"], e["port"], e["port"] == 465)
        try:
            client.login(e["user"], e["password"])
            client.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            return "authentication failed"
        except (smtplib.SMTPException, OSError) as exc:
            return _scrub(type(exc).__name__, [e["password"]])
        finally:
            try:
                client.quit()
            except Exception:  # noqa: BLE001
                pass
        return ""

def telegram_discover_chat_id(token: str, *, transport: Any = None) -> dict[str, Any]:
    """``{ok, chat_id, name, error}`` from the bot's latest update. The user must write to the bot first."""
    if not token:
        return {"ok": False, "chat_id": "", "name": "", "error": "missing TANTALUS_TELEGRAM_TOKEN"}
    try:
        with httpx.Client(transport=transport, timeout=HTTP_TIMEOUT_S) as client:
            response = client.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 20, "timeout": 0})
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "chat_id": "", "name": "", "error": type(exc).__name__}
    if response.status_code != 200 or not data.get("ok"):
        return {"ok": False, "chat_id": "", "name": "", "error": _scrub(str(data.get("description") or f"http {response.status_code}"), [token])[:160]}
    for update in reversed(data.get("result") or []):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if isinstance(chat, dict) and chat.get("id") is not None:
                name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or ""
                return {"ok": True, "chat_id": str(chat["id"]), "name": name, "error": ""}
    return {"ok": False, "chat_id": "", "name": "", "error": "no messages yet: write to the bot first"}


__all__ = ["Notifier", "CHANNELS", "EMAIL_BACKENDS", "telegram_discover_chat_id", "build_toast_ps1", "compose", "label", "LABELS", "format_price", "xml_escape"]
