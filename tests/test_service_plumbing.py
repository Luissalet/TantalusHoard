"""The app shell on the shared service plumbing: error envelope, PWA, SPA fallback, agent router, entry point."""

import os

from tantalus_hoard.__main__ import main

LOCAL = {"host": "localhost:5197"}


def test_health_probe_keeps_its_fields(client):
    body = client.get("/api/health", headers=LOCAL).json()
    assert body["service"] == "tantalus-hoard"
    assert {"version", "dataDirConfigured", "offline", "counts", "scheduler", "hoard_link"} <= set(body)


def test_pwa_manifest_and_worker(client):
    manifest = client.get("/manifest.webmanifest", headers=LOCAL)
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["short_name"] == "Tantalus"
    worker = client.get("/sw.js", headers=LOCAL)
    assert "tantalus-hoard-" in worker.text and worker.headers["service-worker-allowed"] == "/"


def test_unknown_api_path_is_a_json_404(client):
    response = client.get("/api/nothing-here", headers=LOCAL)
    assert response.status_code == 404 and response.json()["error"] == "Not found."


def test_validation_errors_are_400_json(client):
    response = client.post("/api/ui/call", json={"arguments": {}}, headers=LOCAL)
    assert response.status_code == 400 and "name" in response.json()["error"]


def test_agent_call_needs_the_token_and_reports_app_errors(client):
    denied = client.post("/api/agent/call", json={"name": "watcher_list"}, headers=LOCAL)
    assert denied.status_code == 401
    token = client.app.state.services.token
    auth = {**LOCAL, "Authorization": f"Bearer {token}"}
    missing = client.post("/api/agent/call", json={"name": "target_get", "arguments": {"target_id": "t_none"}}, headers=auth)
    assert missing.status_code == 404 and missing.json()["code"] == "not_found" and missing.json()["hint"]
    unknown = client.post("/api/agent/call", json={"name": "no_such_tool"}, headers=auth)
    assert unknown.status_code == 404
    bad = client.post("/api/agent/call", json={"name": "target_get", "arguments": {}}, headers=auth)
    assert bad.status_code == 400


def test_entry_point_passes_the_app_identity(monkeypatch):
    seen = {}
    monkeypatch.setattr("tantalus_hoard.__main__.run_main", lambda **kw: seen.update(kw) or 0)
    monkeypatch.delenv("TANTALUS_PORT", raising=False)
    monkeypatch.setenv("PORT", "5300")
    assert main([]) == 0
    assert seen["service"] == "tantalus-hoard" and seen["default_port"] == 5197 and seen["open_browser_default"] is False
    assert seen["app_factory"] == "tantalus_hoard.main:create_app" and seen["data_dir_env"] == "TANTALUS_DATA_DIR"
    assert os.environ["TANTALUS_PORT"] == "5300"


def test_new_ids_are_ulids_and_old_ids_still_resolve(svc):
    from tantalus_hoard.store import new_id

    first, second = new_id("t"), new_id("t")
    assert first.startswith("t_") and first < second
    old = svc.store.create_watcher(name="old", mode="availability", config={}, watcher_id="w_mur1nyclbe4c77")
    new = svc.store.create_watcher(name="new", mode="availability", config={})
    assert svc.store.watcher("w_mur1nyclbe4c77")["name"] == "old" and svc.store.watcher(new["id"])["name"] == "new"
    assert old["id"] != new["id"]
