from tantalus_hoard.fleet import fleet_status
from tantalus_hoard.agent_tools import call_tool
from conftest import T0


def add(svc):
    return svc.store.create_watcher(name="QA watcher", mode="availability", config={"private_marker": "keep private"}, interval_min=15)


def test_only_confirmed_finished_runs_are_success_not_latest_attempt(svc):
    watcher = add(svc)
    old = svc.store.start_run("check", watcher_id=watcher["id"])
    svc.store.finish_run(old, True, {"private_marker": "keep private"})
    svc.store.start_run("check", watcher_id=watcher["id"])
    result = fleet_status(svc)
    item = result["items"][0]
    assert item["last_success_ts"] == T0
    assert item["last_status"] == "ok"
    assert item["every"] == "15m"
    assert "keep private" not in str(result)
    failed = svc.store.start_run("check", watcher_id=watcher["id"])
    svc.store.finish_run(failed, False, {"private_error": "keep private"})
    assert fleet_status(svc)["items"][0]["last_status"] == "error"


def test_native_http_agent_and_read_only_catalogue_share_metadata(client):
    add(client.svc)
    assert client.get("/api/family/fleet").status_code == 401
    result = client.get("/api/family/fleet", headers=client.bearer)
    assert result.status_code == 200
    agent = client.post("/api/agent/call", headers=client.bearer, json={"name": "fleet_status", "arguments": {}})
    assert agent.status_code == 200
    assert agent.json() == result.json()
    catalog = client.get("/api/agent/tools").json()
    tool = next(t for t in catalog["tools"] if t["name"] == "fleet_status")
    assert tool["annotations"]["readOnlyHint"] is True
    assert call_tool(client.svc, "fleet_status", {}) == result.json()
