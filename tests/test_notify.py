from __future__ import annotations

import json
import smtplib
from types import SimpleNamespace

from tantalus_hoard import model
from tantalus_hoard.hoard_link import family
from tantalus_hoard.notify import CHANNELS, Notifier, build_toast_ps1, compose, label, telegram_discover_chat_id, xml_escape
from tantalus_hoard.notify.labels import LABELS, format_price

EVENT = {"id": "e1", "type": "PRICE_DROP", "severity": "high", "title": "RTX Spark <128GB> & más", "summary": "Baja a 3.499 €",
         "url": "https://www.pccomponentes.com/rtx-spark?a=1&b=2", "price": 3499.0, "currency": "EUR", "confidence": 92,
         "watcher_name": "RTX Spark", "image": "https://img.example/a.png"}


class Cfg:
    def __init__(self, **secrets):
        self.secrets = {f"{k}": v for k, v in secrets.items()}

    def secret(self, name):
        return self.secrets.get(name, "")


def settings(**values):
    return lambda key, default=None: values.get(key.replace(".", "__"), default)


NO_FAUSTUS = {"notify__email__faustus_dir": "/nonexistent/faustus"}   # keeps tests independent of a Faustus next to the repo


def make(cfg=None, sett=None, **kw):
    return Notifier(cfg or Cfg(), settings(**{**NO_FAUSTUS, **(sett or {})}), clock=lambda: 42.0, platform=kw.pop("platform", "win32"), **kw)


def test_hub_delivery_records_only_channels_actually_sent():
    hub = SimpleNamespace(hub_available=lambda: True, notify=lambda *a, **kw: {
        "ok": True, "channels": [{"channel": "windows", "ok": True}], "delivered": ["windows"]})
    results = make(hub_notify=hub).send(EVENT, ["toast", "email", "telegram", "ntfy"])
    assert results[0]["channel"] == "toast" and results[0]["ok"]
    assert all(not r["ok"] and r["skipped"] for r in results[1:])


def test_hub_email_failure_is_reported_even_when_hub_accepted_the_alert():
    hub = SimpleNamespace(hub_available=lambda: True, notify=lambda *a, **kw: {
        "ok": True, "channels": [{"channel": "email", "ok": False, "error": "SMTP unavailable"}], "delivered": []})
    result = make(hub_notify=hub).send(EVENT, ["email"])[0]
    assert not result["ok"] and result["error"] == "SMTP unavailable"


def test_hub_hold_preserves_quiet_hours_without_sending_own_email():
    hub = SimpleNamespace(hub_available=lambda: True, notify=lambda *a, **kw: {"ok": True, "held": "quiet", "channels": []})
    result = make(hub_notify=hub).send(EVENT, ["email"])[0]
    assert result["ok"] and result["error"] == "held by the hub (quiet)"


# ------------------------------------------------------------------ labels
def test_every_event_type_has_es_and_en_labels():
    for t in model.EVENT_TYPES:
        assert LABELS["es"][t] and LABELS["en"][t]
    assert label("RESTOCK") == "Restock" and label("PRICE_DROP") == "Bajada de precio" and label("PREORDER_OPEN") == "Preventa abierta"
    assert label("PRICE_DROP", "en") == "Price drop" and label("UNKNOWN_X") == "Unknown x"


def test_compose_spanish_and_english():
    title, body = compose(EVENT, "es")
    assert title == "Bajada de precio: RTX Spark <128GB> & más"
    assert "Baja a 3.499 €" in body and "3.499 €" in body and "Confianza: 92%" in body and "Vigía: RTX Spark" in body
    title_en, body_en = compose(EVENT, "en")
    assert title_en.startswith("Price drop:") and "€3,499" in body_en and "Confidence: 92%" in body_en
    assert format_price(19.9, "EUR", "es") == "19,90 €" and format_price(None, "EUR") == ""


# ------------------------------------------------------------------ status, enabled flags, severity
def test_channels_status():
    n = make(Cfg(TELEGRAM_TOKEN="t", NTFY_TOPIC="mytopic"), {"notify__telegram__enabled": "1"}, platform="linux")
    st = n.channels_status()
    assert set(st) == set(CHANNELS)
    assert st["toast"] == {"configured": False, "enabled": True, "detail": "not windows", "min_severity": "low"}
    assert st["ntfy"]["configured"] and not st["ntfy"]["enabled"] and "ntfy.sh" in st["ntfy"]["detail"]
    assert not st["telegram"]["configured"] and st["telegram"]["enabled"] and "TANTALUS_TELEGRAM_CHAT_ID" in st["telegram"]["detail"]
    assert "TANTALUS_SMTP_PASSWORD" in st["email"]["detail"] and st["hub"]["configured"]


def test_send_skips_disabled_and_low_severity_and_unknown():
    calls = []
    n = make(Cfg(), {"notify__toast__enabled": "1", "notify__toast__min_severity": "high", "notify__hub__enabled": "0"},
             toast_backend=lambda *a: calls.append(a))
    res = n.send({**EVENT, "severity": "medium"}, ["toast", "hub", "carrier_pigeon"])
    assert [(r["channel"], r["ok"], r["error"]) for r in res] == [("toast", False, "below minimum severity"), ("hub", False, "disabled"),
                                                                    ("carrier_pigeon", False, "unknown channel")]
    assert res[0]["skipped"] and calls == []
    res = n.send(EVENT, ["toast"])
    assert res[0]["ok"] and calls[0][0].startswith("Bajada de precio") and calls[0][2] == EVENT["url"] and calls[0][3] is True


def test_unconfigured_channel_reports_reason():
    res = make(Cfg(), {"notify__ntfy__enabled": "1"}).send(EVENT, ["ntfy"])
    assert res == [{"channel": "ntfy", "ok": False, "error": "missing topic (TANTALUS_NTFY_TOPIC or notify.ntfy.topic)"}]


# ------------------------------------------------------------------ toast
def test_toast_not_windows():
    res = make(Cfg(), {"notify__toast__enabled": "1"}, platform="linux").send(EVENT, ["toast"])
    assert res[0]["ok"] is False and res[0]["error"] == "not windows"


def test_toast_powershell_fallback_script_and_flags(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def no_winotify(name, *a, **k):
        if name == "winotify":
            raise ImportError("no winotify")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_winotify)
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        from pathlib import Path

        seen["script"] = Path(cmd[-1]).read_bytes()
        return SimpleNamespace(returncode=0)

    n = make(Cfg(), {"notify__toast__enabled": "1"}, powershell_runner=runner, icon_path=tmp_path / "none.png")
    assert n.channels_status()["toast"]["detail"] == "PowerShell fallback"
    res = n.send(EVENT, ["toast"])
    assert res[0]["ok"] and seen["cmd"][1:6] == ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", seen["cmd"][5]]
    assert seen["script"].startswith(b"\xef\xbb\xbf")  # BOM for Windows PowerShell 5.1
    script = seen["script"].decode("utf-8-sig")
    assert "ContentType = WindowsRuntime" in script and "&lt;128GB&gt; &amp; más" in script and 'launch="https://www.pccomponentes.com/rtx-spark?a=1&amp;b=2"' in script
    assert "<128GB>" not in script and seen["kw"]["timeout"] == 20
    assert not __import__("os").path.exists(seen["cmd"][-1])  # temp script removed

    failing = make(Cfg(), {"notify__toast__enabled": "1"}, powershell_runner=lambda *a, **k: SimpleNamespace(returncode=1))
    assert failing.send(EVENT, ["toast"])[0]["error"] == "powershell exit 1"


def test_toast_uses_winotify_when_present(monkeypatch, tmp_path):
    shown = []

    class FakeNotification:
        def __init__(self, **kw):
            shown.append(kw)

        def set_audio(self, sound, loop=False):
            shown.append(("audio", loop))

        def show(self):
            shown.append("show")

    monkeypatch.setitem(__import__("sys").modules, "winotify", SimpleNamespace(Notification=FakeNotification, audio=SimpleNamespace(Default="d")))
    icon = tmp_path / "app-icon.png"
    icon.write_bytes(b"png")
    res = make(Cfg(), {"notify__toast__enabled": "1"}, icon_path=icon).send(EVENT, ["toast"])
    assert res[0]["ok"] and shown[0]["app_id"] == "Tantalus's Hoard" and shown[0]["icon"] == str(icon) and shown[0]["launch"] == EVENT["url"]
    assert ("audio", False) in shown and shown[-1] == "show"
    shown.clear()
    make(Cfg(), {"notify__toast__enabled": "1"}, icon_path=tmp_path / "missing.png").send({**EVENT, "severity": "low"}, ["toast"])
    assert "icon" not in shown[0] and ("audio", False) not in shown


def test_xml_escape_and_script_builder():
    assert xml_escape('a<b>&"c\'\x01') == "a&lt;b&gt;&amp;&quot;c&#x27;"
    assert "launch=" not in build_toast_ps1("t", "b", "javascript:alert(1)")


# ------------------------------------------------------------------ hub
def test_hub_emits_family_event(monkeypatch):
    got = []
    monkeypatch.setattr(family, "emit", lambda type, data=None, **kw: got.append((type, data)) or True)
    res = make(Cfg(), {"notify__hub__enabled": "1"}).send(EVENT, ["hub"])
    assert res[0]["ok"] and got[0][0] == "tantalus.alert" and got[0][1]["event_id"] == "e1" and got[0][1]["price"] == 3499.0
    monkeypatch.setattr(family, "emit", lambda *a, **k: False)
    assert make(Cfg()).send(EVENT, ["hub"])[0]["error"] == "hub not configured"
    monkeypatch.setattr(family, "emit", lambda *a, **k: 1 / 0)
    assert make(Cfg()).send(EVENT, ["hub"])[0]["error"] == "hub not configured"


# ------------------------------------------------------------------ ntfy
class Http:
    """Stands in for the commons' ``http_json``: records every call and answers ``(status, json)`` (or ``(None, "<ErrorName>")``)."""

    def __init__(self, status=200, data=None, error=None, fn=None):
        self.status, self.data, self.error, self.fn, self.calls = status, data, error, fn, []

    def __call__(self, url, payload=None, headers=None, timeout=10.0):
        self.calls.append({"url": url, "payload": payload, "headers": headers or {}})
        if self.fn is not None:
            return self.fn(url, payload)
        return (None, self.error) if self.error else (self.status, self.data)


def test_ntfy_json_publish():
    http = Http(data={"id": "x"})
    n = make(Cfg(NTFY_TOPIC="topic-1", NTFY_TOKEN="tk_secret"), {"notify__ntfy__enabled": "1", "notify__ntfy__server": "https://ntfy.example/"}, http=http)
    res = n.send(EVENT, ["ntfy"])
    assert res[0]["ok"]
    call = http.calls[0]
    body = call["payload"]
    assert call["url"] == "https://ntfy.example/" and call["headers"]["Authorization"] == "Bearer tk_secret"
    assert body["topic"] == "topic-1" and body["priority"] == 5 and body["click"] == EVENT["url"] and body["title"].startswith("Bajada de precio")
    assert body["tags"] == ["chart_with_downwards_trend"] and "más" in body["title"] and body["attach"] == EVENT["image"]
    medium = Http()
    make(Cfg(NTFY_TOPIC="t"), {"notify__ntfy__enabled": "1"}, http=medium).send({**EVENT, "severity": "medium", "confidence": 50}, ["ntfy"])
    assert medium.calls[0]["payload"]["priority"] == 3


def test_ntfy_topic_from_settings_default_server_and_failures():
    http = Http(status=403)
    res = make(Cfg(), {"notify__ntfy__enabled": "1", "notify__ntfy__topic": "from-db"}, http=http).send(EVENT, ["ntfy"])
    assert http.calls[0]["url"] == "https://ntfy.sh/" and http.calls[0]["payload"]["topic"] == "from-db" and res[0]["error"] == "http 403"
    down = Http(error="ConnectError")
    res = make(Cfg(NTFY_TOPIC="from-secret"), {"notify__ntfy__enabled": "1"}, http=down).send(EVENT, ["ntfy"])
    assert res[0]["error"] == "ConnectError" and "from-secret" not in json.dumps(res)


# ------------------------------------------------------------------ telegram
def test_telegram_send_html_escaped():
    http = Http(data={"ok": True})
    n = make(Cfg(TELEGRAM_TOKEN="123:ABC", TELEGRAM_CHAT_ID="-1001"), {"notify__telegram__enabled": "1"}, http=http)
    res = n.send(EVENT, ["telegram"])
    assert res[0]["ok"]
    call, body = http.calls[0], http.calls[0]["payload"]
    assert call["url"] == "https://api.telegram.org/bot123:ABC/sendMessage" and body["chat_id"] == "-1001"
    assert body["parse_mode"] == "HTML"
    assert "<b>Bajada de precio: RTX Spark &lt;128GB&gt; &amp; más</b>" in body["text"] and 'href="https://www.pccomponentes.com/rtx-spark?a=1&amp;b=2"' in body["text"]


def test_telegram_errors_never_leak_the_token():
    http = Http(status=401, data={"ok": False, "description": "Unauthorized bot123:ABC"})
    res = make(Cfg(TELEGRAM_TOKEN="123:ABC", TELEGRAM_CHAT_ID="99"), {"notify__telegram__enabled": "1"}, http=http).send(EVENT, ["telegram"])
    assert res[0]["ok"] is False and "123:ABC" not in json.dumps(res) and "***" in res[0]["error"]
    down = Http(error="ConnectError for bot123:ABC")
    res = make(Cfg(TELEGRAM_TOKEN="123:ABC", TELEGRAM_CHAT_ID="99"), {"notify__telegram__enabled": "1"}, http=down).send(EVENT, ["telegram"])
    assert "123:ABC" not in json.dumps(res) and res[0]["error"].startswith("ConnectError")


def test_telegram_discover_chat_id():
    def answer(url, payload):
        assert "/bot123:ABC/getUpdates" in url
        return 200, {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 555, "first_name": "Ana", "type": "private"}}},
            {"update_id": 2, "message": {"chat": {"id": 777, "first_name": "Ana", "last_name": "M", "type": "private"}}}]}

    out = telegram_discover_chat_id("123:ABC", http=Http(fn=answer))
    assert out == {"ok": True, "chat_id": "777", "name": "Ana M", "error": ""}
    empty = telegram_discover_chat_id("123:ABC", http=Http(data={"ok": True, "result": []}))
    assert not empty["ok"] and "write to the bot first" in empty["error"]
    assert telegram_discover_chat_id("")["error"] == "missing TANTALUS_TELEGRAM_TOKEN"
    via_notifier = make(Cfg(TELEGRAM_TOKEN="123:ABC"), http=Http(fn=answer)).telegram_discover_chat_id()
    assert via_notifier["chat_id"] == "777"


# ------------------------------------------------------------------ email
class FakeSMTP:
    instances: list = []

    def __init__(self, cfg, fail=None):
        self.host, self.port, self.use_ssl, self.fail = cfg["host"], cfg["port"], cfg["port"] == 465, fail
        self.sent, self.logged, self.quit_called = [], None, False
        FakeSMTP.instances.append(self)

    def login(self, user, password):
        self.logged = (user, password)
        if self.fail == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad password hunter2")

    def send_message(self, msg):
        if self.fail == "send":
            raise smtplib.SMTPRecipientsRefused({})
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


EMAIL_SECRETS = {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "hunter2"}


def test_email_defaults_gmail_ssl_and_multipart():
    FakeSMTP.instances = []
    n = make(Cfg(**EMAIL_SECRETS), {"notify__email__enabled": "1"}, smtp_factory=FakeSMTP)
    assert n.channels_status()["email"]["detail"] == "SMTP smtp.gmail.com:465"
    res = n.send(EVENT, ["email"])
    assert res[0]["ok"]
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.use_ssl, smtp.logged, smtp.quit_called) == ("smtp.gmail.com", 465, True, ("me@gmail.com", "hunter2"), True)
    msg = smtp.sent[0]
    assert msg["To"] == "me@gmail.com" and msg["From"] == "me@gmail.com" and msg["Subject"].startswith("Bajada de precio")
    plain, html_part = msg.get_body(("plain",)).get_content(), msg.get_body(("html",)).get_content()
    assert "https://www.pccomponentes.com/rtx-spark?a=1&b=2" in plain and "&lt;128GB&gt;" in html_part and "<128GB>" not in html_part


def test_email_starttls_port_custom_recipients_and_errors():
    FakeSMTP.instances = []
    secrets = {**EMAIL_SECRETS, "SMTP_HOST": "smtp.example.org", "SMTP_PORT": "587", "SMTP_FROM": "alerts@example.org", "SMTP_TO": "a@x.com, b@y.com"}
    n = make(Cfg(**secrets), {"notify__email__enabled": "1"}, smtp_factory=FakeSMTP)
    assert n.send(EVENT, ["email"])[0]["ok"]
    smtp = FakeSMTP.instances[0]
    assert smtp.use_ssl is False and smtp.port == 587 and smtp.sent[0]["To"] == "a@x.com, b@y.com" and smtp.sent[0]["From"] == "alerts@example.org"

    bad = make(Cfg(**EMAIL_SECRETS), {"notify__email__enabled": "1"}, smtp_factory=lambda cfg: FakeSMTP(cfg, fail="auth"))
    res = bad.send(EVENT, ["email"])
    assert res[0]["error"] == "authentication failed" and "hunter2" not in json.dumps(res)
    worse = make(Cfg(**EMAIL_SECRETS), {"notify__email__enabled": "1"}, smtp_factory=lambda cfg: FakeSMTP(cfg, fail="send"))
    assert worse.send(EVENT, ["email"])[0]["error"] == "SMTPRecipientsRefused"

    def unreachable(cfg):
        raise OSError("network down")

    assert make(Cfg(**EMAIL_SECRETS), {"notify__email__enabled": "1"}, smtp_factory=unreachable).send(EVENT, ["email"])[0]["error"] == "OSError"


# ------------------------------------------------------------------ test()
def test_test_ignores_enabled_flag_but_needs_configuration():
    http = Http()
    n = make(Cfg(NTFY_TOPIC="t"), {"notify__ntfy__enabled": "0", "notify__ntfy__min_severity": "high"}, http=http)
    res = n.test("ntfy")
    assert res["channel"] == "ntfy" and res["ok"] and http.calls[0]["payload"]["title"].startswith("Novedad: Prueba de notificación")
    assert n.test("telegram") == {"channel": "telegram", "ok": False, "error": "missing TANTALUS_TELEGRAM_TOKEN, TANTALUS_TELEGRAM_CHAT_ID"}
    assert n.test("nope")["error"] == "unknown channel"


def test_language_setting_switches_text():
    http = Http()
    n = make(Cfg(NTFY_TOPIC="t"), {"notify__ntfy__enabled": "1", "notify__language": "en"}, http=http)
    n.send(EVENT, ["ntfy"])
    assert http.calls[0]["payload"]["title"].startswith("Price drop:")


# ------------------------------------------------------------------ e-mail through Faustus
def fake_faustus(tmp_path, venv=True):
    root = tmp_path / "faustus"
    (root / "mcp_servers").mkdir(parents=True)
    (root / "mcp_servers" / "email_server.py").write_text("# stub\n")
    if venv:
        (root / "venv" / "bin").mkdir(parents=True)
        (root / "venv" / "bin" / "python").write_text("")
    return root


class Runner:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, argv, **kw):
        self.calls.append((argv, json.loads(kw["input"]), kw))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return SimpleNamespace(returncode=0, stdout="noise from an import\n" + (json.dumps(answer) if answer is not None else "boom"), stderr="")


def test_email_auto_backend_picks_faustus_without_own_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TANTALUS_TELEGRAM_TOKEN", "leak-me-not")
    root = fake_faustus(tmp_path)
    ok = {"ok": True, "error": "", "account": "Main", "from": "abc***@example.org", "to": ["abc***@example.org"], "server": "smtp.example.org:587"}
    runner = Runner([ok])
    n = make(Cfg(), {"notify__email__enabled": "1", "notify__email__faustus_dir": str(root)}, faustus_runner=runner)
    assert n.email_backend() == "faustus" and n.faustus_dir() == root.resolve()
    st = n.channels_status()["email"]
    assert st["configured"] and st["backend"] == "faustus" and st["backend_setting"] == "auto"
    assert st["detail"] == "Faustus account Main <abc***@example.org> → abc***@example.org"
    argv, request, kw = runner.calls[0]
    assert argv[0].endswith("python") and argv[1].endswith("mail_helper.py") and argv[2] == str(root.resolve())
    assert request["action"] == "status" and kw["cwd"] == str(root.resolve()) and not any(k.startswith("TANTALUS_") for k in kw["env"])
    res = n.send(EVENT, ["email"])
    assert res[0]["ok"] and len(runner.calls) == 2          # the good status was cached: one status call, one send
    _, sent, _ = runner.calls[1]
    assert sent["action"] == "send" and sent["subject"].startswith("Bajada de precio") and "to" not in sent
    assert "https://www.pccomponentes.com/rtx-spark?a=1&b=2" in sent["text"] and "&lt;128GB&gt;" in sent["html"]


def test_email_own_credentials_win_in_auto_and_backend_can_be_forced(tmp_path):
    root = fake_faustus(tmp_path)
    runner = Runner([{"ok": True, "from": "x***@y.z", "to": ["q***@y.z"]}])
    n = make(Cfg(**EMAIL_SECRETS), {"notify__email__faustus_dir": str(root)}, faustus_runner=runner)
    assert n.email_backend() == "smtp"
    forced = make(Cfg(**EMAIL_SECRETS, SMTP_TO="a@x.com, b@y.com"),
                  {"notify__email__enabled": "1", "notify__email__backend": "faustus", "notify__email__faustus_dir": str(root),
                   "notify__email__faustus_owner": "admin"}, faustus_runner=runner)
    assert forced.email_backend() == "faustus" and forced.send(EVENT, ["email"])[0]["ok"]
    _, sent, _ = runner.calls[-1]
    assert sent["to"] == ["a@x.com", "b@y.com"] and sent["owner"] == "admin"
    smtp_only = make(Cfg(), {"notify__email__backend": "smtp", "notify__email__faustus_dir": str(root)})
    assert smtp_only.email_backend() == "smtp" and "TANTALUS_SMTP_PASSWORD" in smtp_only.channels_status()["email"]["detail"]


def test_email_faustus_errors_are_reported_not_raised(tmp_path):
    missing = make(Cfg(), {"notify__email__backend": "faustus"})
    assert missing.channels_status()["email"]["configured"] is False and "folder not found" in missing.channels_status()["email"]["detail"]
    no_venv = make(Cfg(), {"notify__email__backend": "faustus", "notify__email__faustus_dir": str(fake_faustus(tmp_path, venv=False))})
    assert no_venv.test("email")["error"] == "Faustus: Faustus has no venv with Python"
    root = tmp_path / "f2"
    root.mkdir()
    fake = fake_faustus(root)
    garbage = make(Cfg(), {"notify__email__backend": "faustus", "notify__email__faustus_dir": str(fake)}, faustus_runner=Runner([None]))
    assert garbage.test("email")["error"] == "Faustus: mail helper exit 0"
    runner = Runner([{"ok": True, "from": "a***@b.c", "to": ["a***@b.c"]}, {"ok": False, "error": "authentication failed"}])
    failing = make(Cfg(), {"notify__email__enabled": "1", "notify__email__backend": "faustus", "notify__email__faustus_dir": str(fake)},
                   faustus_runner=runner)
    assert failing.send(EVENT, ["email"])[0]["error"] == "authentication failed" and failing._faustus_status is None

    def broken(*a, **k):
        raise OSError("no such file")

    crashed = make(Cfg(), {"notify__email__backend": "faustus", "notify__email__faustus_dir": str(fake)}, faustus_runner=broken)
    assert crashed.test("email")["error"] == "Faustus: mail helper: OSError"


def test_faustus_mail_helper_against_a_stub_email_server(tmp_path, monkeypatch):
    """The helper runs the stub's own config resolution, fixes a port/security mismatch and never prints secrets."""
    import sys as _sys
    from tantalus_hoard.hoard_link import mail_helper as faustus_mail

    root = tmp_path / "faustus"
    (root / "mcp_servers").mkdir(parents=True)
    (root / "mcp_servers" / "__init__.py").write_text("")
    (root / "mcp_servers" / "email_server.py").write_text(
        "import os\n"
        "print('import noise')\n"
        "def _read_accounts_from_db():\n"
        "    return [{'owner': 'admin', 'enabled': 1}]\n"
        "def _resolve_send_config(account=None):\n"
        "    if os.environ.get('ODYSSEUS_MCP_EMAIL_OWNER') != 'admin':\n"
        "        raise ValueError('owner required')\n"
        "    return account, {'smtp_host': 'smtp.example.org', 'smtp_port': 587, 'smtp_security': 'ssl', 'smtp_user': 'me@example.org',\n"
        "                     'smtp_password': 'hunter2', 'from_address': 'me@example.org', 'account_name': 'Main'}\n")
    monkeypatch.delenv("ODYSSEUS_MCP_EMAIL_OWNER", raising=False)
    monkeypatch.delenv("ODYSSEUS_EMAIL_OWNER", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_sys, "path", list(_sys.path))
    for name in [m for m in _sys.modules if m == "mcp_servers" or m.startswith("mcp_servers.")]:
        monkeypatch.delitem(_sys.modules, name)
    made = []

    class SMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.kind, self.host, self.port, self.sent = "plain", host, port, []
            made.append(self)

        def starttls(self, context=None):
            self.kind = "starttls"

        def login(self, user, password):
            assert (user, password) == ("me@example.org", "hunter2")

        def send_message(self, msg):
            self.sent.append(msg)

        def quit(self):
            pass

    class SMTP_SSL(SMTP):
        def __init__(self, *a, **k):
            raise AssertionError("implicit TLS must not be used on 587")

    monkeypatch.setattr(faustus_mail.smtplib, "SMTP", SMTP)
    monkeypatch.setattr(faustus_mail.smtplib, "SMTP_SSL", SMTP_SSL)
    status = faustus_mail.handle({"action": "status"}, str(root))
    assert status["ok"] is True and not status["error"] and not made
    assert {k: status[k] for k in ("account", "from", "to", "server")} == {"account": "Main", "from": "me***@example.org",
                                                                          "to": ["me***@example.org"], "server": "smtp.example.org:587"}
    sent = faustus_mail.handle({"action": "send", "subject": "Hi\r\nBcc: x@evil.com", "text": "body", "html": "<p>b</p>",
                                "to": ["other@example.net", "bad address"]}, str(root))
    assert sent["ok"] and made[0].kind == "starttls"
    msg = made[0].sent[0]
    assert msg["To"] == "other@example.net" and msg["Subject"] == "Hi Bcc: x@evil.com" and "me@example.org" in msg["From"]
    assert "hunter2" not in json.dumps(sent)
