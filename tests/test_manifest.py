"""faustus-plugin.json, the README and the docs stay in sync with the code."""

import json
import re
from pathlib import Path

from tantalus_hoard import SERVICE
from tantalus_hoard.agent_tools import TOOLS
from tantalus_hoard.config import DEFAULT_PORT

ROOT = Path(__file__).resolve().parent.parent
BANNED = ("chatgpt", "claude", "openai", "anthropic", "lm studio", "odysseus", "gemini", "copilot", "keepa", "camelcamelcamel", "distill.io", "visualping", "changedetection")
FINAL_TOOLS = {
    "tantalus_overview", "tantalus_status", "watcher_list", "watcher_get", "watcher_create", "watcher_update", "watcher_delete",
    "watcher_run", "target_add", "target_list", "target_get", "target_update", "target_delete", "target_check", "target_resolve",
    "inspect_url", "events_list", "events_mark_seen", "event_dismiss", "event_notify", "listings_list", "listing_set",
    "info_items_list", "info_item_set", "candidates_list", "candidate_accept", "candidate_reject", "discovery_run", "web_search",
    "secondhand_search", "secondhand_facebook_login", "packs_list", "presets_list", "presets_install", "notify_status", "notify_test",
    "telegram_find_chat_id", "settings_set", "secret_set", "scheduler_status", "runs_list", "config_export", "config_import",
}


def test_manifest_matches_code():
    manifest = json.loads((ROOT / "faustus-plugin.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "tantalus" and manifest["name"] == "Tantalus's Hoard"
    assert manifest["app"]["health"]["expect"]["service"] == SERVICE == "tantalus-hoard"
    assert manifest["app"]["url_default"].endswith(f":{DEFAULT_PORT}") and DEFAULT_PORT == 5197
    assert manifest["app"]["launch_hint"]["env"]["PORT_STRICT"] == "1"


def test_manifest_has_only_allowed_top_level_keys():
    manifest = json.loads((ROOT / "faustus-plugin.json").read_text(encoding="utf-8"))
    assert set(manifest) <= {"schema", "id", "name", "purpose", "capabilities", "placeholders", "defaults", "app", "mcp", "notes"}
    assert manifest["mcp"]["env"].keys() >= {"TANTALUS_URL", "TANTALUS_TOKEN_FILE"}


def test_tool_names_are_the_contract():
    assert {t.name for t in TOOLS} == FINAL_TOOLS and len(TOOLS) == len(FINAL_TOOLS)


def test_readmes_and_api_doc_list_every_tool():
    for name in ("README.md", "README.es.md", "docs/API.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for tool in TOOLS:
            assert f"`{tool.name}`" in text, (name, tool.name)


def test_no_other_products_in_docs_manifest_or_code():
    files = [ROOT / "faustus-plugin.json", ROOT / "README.md", ROOT / "README.es.md", ROOT / "docs" / "API.md", ROOT / "pyproject.toml"]
    files += [p for p in (ROOT / "tantalus_hoard").rglob("*.py") if "hoard_link" not in p.parts]
    files += [p for p in (ROOT / "client" / "src").rglob("*") if p.is_file()]
    files += [ROOT / "mcp_server.py", *(ROOT / "scripts").glob("*.py")]
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for word in BANNED:
            assert not re.search(rf"\b{re.escape(word)}\b", text), (path, word)


def test_license_readme_and_first_lines():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Luis María Salete Cuartero" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "CAPTCHA" in readme
    for tool in TOOLS:  # the first line is what tool retrieval indexes: short, with EN + ES keywords
        assert len(tool.description.split("\n", 1)[0]) <= 140, tool.name
