"""Notification channels: Windows toast, family hub, ntfy, Telegram and e-mail.

The channel code is the family's (``hoard_link.notify_channels``: toast, ntfy, Telegram, SMTP and the Faustus mail helper) and the
``auto | hub | own`` switch is ``hoard_link.fam_notify.Router``; this module keeps what is Tantalus's own: which settings
switch a channel on, the severity floors, the labels and texts (``labels.py``) and the per-channel result list.

E-mail has two backends: plain SMTP with Tantalus's own credentials, or the account configured in Faustus (the vendored mail
helper runs under Faustus's Python, so that password never reaches Tantalus). ``auto`` uses SMTP when Tantalus has its own user and
password and Faustus otherwise.

``Notifier.send(event, channels)`` returns one ``{channel, ok, error}`` per channel and never raises.
Secrets come only from ``config.secret(...)`` (environment / .env); the database holds only the
``notify.<channel>.enabled`` / ``min_severity`` / non-secret settings. Nothing secret is logged or
put in an error string.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import REPO_ROOT
from ..hoard_link import notify_channels as nc
from ..hoard_link.fam_mail import FaustusHelper, faustus_python as _faustus_python
from ..hoard_link.fam_notify import VIA_MODES, Router
from .labels import LABELS, TYPE_TAGS, WORDS, compose, format_price, label

log = logging.getLogger("tantalus.notify")

CHANNELS = ("toast", "hub", "ntfy", "telegram", "email")
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
APP_ID = "Tantalus's Hoard"
DEFAULT_ENABLED = {"toast": True, "hub": True, "ntfy": False, "telegram": False, "email": False}
EMAIL_BACKENDS = ("auto", "faustus", "smtp")
PUSH_CHANNELS = ("toast", "ntfy", "telegram", "email")   # the channels the hub's notification facet replaces ("hub" is the bus event)
SEVERITY_PRIORITY = {"low": "low", "medium": "normal", "high": "high"}
FAUSTUS_STATUS_TTL_S = 300.0     # a good status is reused for five minutes, a failure for thirty seconds

xml_escape = nc.xml_escape


def build_toast_ps1(title: str, body: str, url: str = "") -> str:
    """PowerShell that shows one toast through Windows.UI.Notifications (no extra module needed)."""
    return nc.build_toast_ps1(title, body, url)


class Notifier:
    def __init__(self, config: Any, db_settings_get: Callable[[str, Optional[str]], Optional[str]], *, http: Any = None,
                 clock: Callable[[], float] = time.time, smtp_factory: Optional[Callable[[dict[str, Any]], Any]] = None,
                 toast_backend: Optional[Callable[[str, str, str, bool], None]] = None,
                 powershell_runner: Optional[Callable[..., Any]] = None, platform: Optional[str] = None, icon_path: Optional[Path] = None,
                 faustus_runner: Optional[Callable[..., Any]] = None, hub_notify: Any = None):
        self.config = config
        self.get = db_settings_get
        self.http = http                       # ``http(url, payload, headers=, timeout=) -> (status, json)``; default: the commons' client
        self.clock = clock
        self.smtp_factory = smtp_factory
        self.toast_backend = toast_backend
        self.powershell_runner = powershell_runner
        self.platform = platform or sys.platform
        self.icon_path = icon_path if icon_path is not None else REPO_ROOT / "app-icon.png"
        self._hub_notify = hub_notify          # an object with notify() and hub_available(); default: hoard_link.fam_notify
        self.helper = FaustusHelper(self._faustus_setting, owner=lambda: self._setting("notify.email.faustus_owner"),
                                    runner=faustus_runner, env_drop_prefixes=("TANTALUS_",), ask_hub=False, clock=clock)
        self._faustus_status: Optional[tuple[float, str, dict[str, Any]]] = None
        self.router = self._router(None)

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
                "from": self._secret("SMTP_FROM") or user, "to": to, "tls": True}

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

    # ------------------------------------------------------------------ delivery through the family hub (fam_notify.Router)
    def _router(self, own_send: Optional[Callable[..., Any]], via: Optional[Callable[[], Any]] = None) -> Router:
        return Router(via or self.via_setting, own_send, app_name="Tantalus", hub=self._hub_notify)

    def via_setting(self) -> str:
        """``notify.via``: ``auto`` (hub when it answers, own channels otherwise), ``hub`` (only the hub) or ``own``."""
        value = self._setting("notify.via", "auto").lower()
        return value if value in VIA_MODES else "auto"

    def via_status(self) -> dict[str, Any]:
        """What the next alert would use: ``{setting, effective: hub|own, hub_available}``."""
        return self.router.via_status()

    @staticmethod
    def _hub_fields(event: dict[str, Any], title: str) -> dict[str, Any]:
        return {"priority": SEVERITY_PRIORITY.get(str(event.get("severity") or "medium").lower(), "normal"), "url": nc.http_url(event.get("url")),
                "group": str(event.get("type") or "alert").lower(), "dedupe_key": str(event.get("dedupe_key") or "") or ("tantalus:" + str(event.get("id") or title))}

    def test_hub(self) -> dict[str, Any]:
        """A sample notification through the hub, whatever ``notify.via`` says."""
        words = WORDS[self._lang()]
        sample = {"id": "test", "type": "INFO_CHANGE", "severity": "medium", "title": words["test_title"], "summary": words["test_body"],
                  "url": "", "price": None, "currency": None, "confidence": None, "watcher_name": "", "image": "",
                  "dedupe_key": f"tantalus:test:{int(self.clock())}"}
        title, body = compose(sample, self._lang())
        res = self._router(None, via=lambda: "hub").send(title, body, **self._hub_fields(sample, title))
        return {"channel": "hub_notify", "ok": res["ok"], "error": self._hub_error(res), "held": res.get("held", ""), "via": "hub"}

    @staticmethod
    def _hub_error(res: dict[str, Any]) -> str:
        if res.get("ok"):
            return f"held by the hub ({res['held']})" if res.get("held") else ""
        return str(res.get("why") or "")

    # ------------------------------------------------------------------ e-mail through Faustus (the commons' mail helper)
    def _faustus_setting(self) -> str:
        return self._setting("notify.email.faustus_dir") or self._secret("FAUSTUS_DIR")

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
        """The Faustus folder: setting, TANTALUS_FAUSTUS_DIR, FAUSTUS_DIR, then the usual places (see ``fam_mail.faustus_dir``)."""
        return self.helper.faustus_dir()

    @staticmethod
    def faustus_python(root: Path) -> Optional[str]:
        return _faustus_python(root)

    def faustus_status(self, refresh: bool = False) -> dict[str, Any]:
        key = str(self.faustus_dir() or "") + "|" + self._setting("notify.email.faustus_owner")
        cached = self._faustus_status
        if cached and not refresh and cached[1] == key:
            ttl = FAUSTUS_STATUS_TTL_S if cached[2].get("ok") else 30.0
            if self.clock() - cached[0] < ttl:
                return cached[2]
        result = self.helper.run("status", {"to": self._secret("SMTP_TO")}, 60.0)
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
        """Deliver ``event`` through ``channels``. With ``notify.via`` = ``auto``/``hub`` the push channels (toast, ntfy, telegram, email)
        become ONE notification to the family hub, which decides channels, quiet hours and sphere; ``auto`` falls back to the own
        channels when the hub does not answer, ``hub`` reports the failure instead, ``own`` never calls the hub. The ``hub`` channel
        (the bus event) is independent of this."""
        results = []
        pushes = [c for c in channels if c in PUSH_CHANNELS]
        own: dict[str, dict[str, Any]] = {}
        routed: Optional[dict[str, Any]] = None

        def own_send(title: str, body: str, **_kw: Any) -> dict[str, Any]:
            for channel in pushes:
                own[channel] = self._checked(channel, event)
            return {"ok": True}

        if pushes:
            title, body = compose(event, self._lang())
            routed = self._router(own_send).send(title, body, **self._hub_fields(event, title))
        for channel in channels:
            if channel not in CHANNELS:
                results.append({"channel": channel, "ok": False, "error": "unknown channel"})
            elif channel in own:
                results.append(own[channel])
            elif routed is not None and channel in PUSH_CHANNELS:
                results.append({"channel": channel, "ok": routed["ok"], "error": self._hub_error(routed), "via": "hub", "ts": self.clock()})
            else:
                results.append(self._checked(channel, event))
        return results

    def _checked(self, channel: str, event: dict[str, Any]) -> dict[str, Any]:
        """One own delivery, after the enabled flag and the severity floor."""
        if not self._enabled(channel):
            return {"channel": channel, "ok": False, "error": "disabled", "skipped": True}
        if SEVERITY_RANK.get(str(event.get("severity") or "medium").lower(), 1) < SEVERITY_RANK[self._min_severity(channel)]:
            return {"channel": channel, "ok": False, "error": "below minimum severity", "skipped": True}
        return self._deliver(channel, event)

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

    @staticmethod
    def _error(result: dict[str, Any]) -> str:
        return "" if result.get("ok") else str(result.get("error") or "failed")

    # ------------------------------------------------------------------ channels (each returns '' on success or an error)
    def _send_toast(self, event: dict[str, Any], title: str, body: str) -> str:
        if not self._is_windows():
            return "not windows"
        url = nc.http_url(event.get("url"))
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
        return self._error(nc.send_toast(title, body, url, runner=self.powershell_runner, platform=self.platform))

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

    def _send_ntfy(self, event: dict[str, Any], title: str, body: str) -> str:
        severity = str(event.get("severity") or "medium")
        try:
            conf = float(event.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0
        priority = (5 if conf >= 90 else 4) if severity == "high" else 3 if severity == "medium" else 2
        return self._error(nc.send_ntfy(self._ntfy_server(), self._ntfy_topic(), title, body, priority=priority, url=event.get("url"),
                                        token=self._secret("NTFY_TOKEN") or None, tags=[TYPE_TAGS.get(str(event.get("type")), "bell")],
                                        attach=event.get("image"), http=self.http))

    def _send_telegram(self, event: dict[str, Any], title: str, body: str) -> str:
        text = nc.telegram_text(title, body, event.get("url"), WORDS[self._lang()]["open"])
        return self._error(nc.send_telegram(self._secret("TELEGRAM_TOKEN"), self._secret("TELEGRAM_CHAT_ID"), text, http=self.http))

    def telegram_discover_chat_id(self) -> dict[str, Any]:
        """Find the chat id after the user has written to the bot (getUpdates). Nothing is stored here."""
        return telegram_discover_chat_id(self._secret("TELEGRAM_TOKEN"), http=self.http)

    def _send_email(self, event: dict[str, Any], title: str, body: str) -> str:
        subject, text, html_body = nc.email_parts(title, body, event.get("url"))
        if self.email_backend() == "faustus":
            to = [a.strip() for a in self._secret("SMTP_TO").split(",") if a.strip()]
            answer = nc.send_via_helper(self.helper.run, subject, text, html=html_body, to=to)
            if not answer.get("ok"):
                self._faustus_status = None      # re-check the account on the next status
            return self._error(answer)
        e = self._email_settings()
        return self._error(nc.send_smtp(e, e["to"], subject, text, html=html_body, smtp_factory=self.smtp_factory))


def telegram_discover_chat_id(token: str, *, http: Any = None) -> dict[str, Any]:
    """``{ok, chat_id, name, error}`` from the bot's latest update. The user must write to the bot first."""
    if not token:
        return {"ok": False, "chat_id": "", "name": "", "error": "missing TANTALUS_TELEGRAM_TOKEN"}
    return nc.telegram_discover_chat_id(token, http=http)


__all__ = ["Notifier", "CHANNELS", "EMAIL_BACKENDS", "VIA_MODES", "PUSH_CHANNELS", "telegram_discover_chat_id", "build_toast_ps1", "compose", "label",
           "LABELS", "format_price", "xml_escape"]
