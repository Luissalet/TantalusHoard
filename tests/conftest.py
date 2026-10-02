from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from tantalus_hoard.agent_tools import call_tool  # noqa: E402
from tantalus_hoard.config import Config  # noqa: E402
from tantalus_hoard.main import create_app  # noqa: E402
from tantalus_hoard.services import Services  # noqa: E402

T0 = 1_790_000_000.0


@dataclass
class FakeReply:
    text: str
    model: str = "fake-model"


class FakeLink:
    """Sync-shaped model double: hands back scripted replies in order, then repeats the last one."""

    def __init__(self, replies: list[Any] | None = None, fail: bool = False):
        self.replies = list(replies or [])
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.fail:
            raise RuntimeError("no model")
        if not self.replies:
            raise RuntimeError("no scripted reply")
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return FakeReply(item if isinstance(item, str) else json.dumps(item))

    def status(self):
        return {"fake": True}

    def close(self):
        pass


def _no_network():
    import httpx

    def handler(request):
        raise httpx.ConnectError(f"network disabled in tests: {request.url}")
    return httpx.MockTransport(handler)


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[dict, list[str]]] = []

    def send(self, event, channels):
        self.sent.append((event, list(channels)))
        return [{"channel": c, "ok": True, "error": ""} for c in channels if c in ("hub", "toast")]

    def test(self, channel):
        return {"channel": channel, "ok": True, "error": ""}

    def channels_status(self):
        return {"toast": {"configured": True, "enabled": True}, "hub": {"configured": True, "enabled": True}}

    def telegram_discover_chat_id(self):
        return {"ok": False}


@pytest.fixture(autouse=True)
def no_family_hub(monkeypatch):
    """No test talks to a real hub: the notification centre, the mail gateway and the reference graph answer "away" unless a test fakes them."""
    from tantalus_hoard.hoard_link import fam_mail, fam_notify, fam_refs

    monkeypatch.setattr(fam_mail, "_sibling_candidates", lambda: [])
    monkeypatch.setattr(fam_mail, "COMMON_FAUSTUS_PATHS", ())
    for key in fam_mail.FAUSTUS_ENV:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(fam_notify, "hub_available", lambda timeout=1.0: False)
    monkeypatch.setattr(fam_notify, "notify", lambda *a, **k: {"ok": False, "error": "hub unreachable"})
    monkeypatch.setattr(fam_mail, "available", lambda timeout=1.0: False)
    monkeypatch.setattr(fam_mail, "register_interest", lambda *a, **k: {"ok": False, "error": "hub unreachable"})
    monkeypatch.setattr(fam_mail, "claim", lambda *a, **k: {"ok": False, "error": "hub unreachable"})
    monkeypatch.setattr(fam_refs, "link", lambda *a, **k: {"ok": False, "error": "hub unreachable"})


def make_config(tmp_path: Path, **overrides) -> Config:
    base = dict(data_dir=tmp_path / "data", port=0, port_strict=False, data_dir_configured=True, scheduler=False,
                browser=False, offline=False, secrets={})
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
def link():
    return FakeLink(fail=True)


@pytest.fixture
def svc(config, link):
    s = Services(config, link=link, clock_fn=lambda: T0, sleep_fn=lambda _s: None, http_transport=_no_network(),
                 notifier=FakeNotifier(), install_presets=False)
    yield s
    s.stop()


@pytest.fixture
def client(config, link):
    services = Services(config, link=link, clock_fn=lambda: T0, sleep_fn=lambda _s: None, http_transport=_no_network(),
                        notifier=FakeNotifier(), install_presets=False)
    app = create_app(config, services=services)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.svc = services
        c.bearer = {"Authorization": f"Bearer {services.token}"}
        yield c


def tool(svc: Services, tool_name: str, /, **arguments) -> Any:
    return call_tool(svc, tool_name, arguments)


