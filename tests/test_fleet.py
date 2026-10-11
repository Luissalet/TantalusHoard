from tantalus_hoard.fleet import fleet_status
from tantalus_hoard.agent_tools import call_tool
from conftest import T0


def add(svc):
    w = svc.store.create_watcher(name="QA watcher", mode="availability", config={"private_marker": "keep private"}, interval_min=15)
    svc.store.create_target(w["id"], "https://shop.example/qa")   # a watcher with nothing to check is not published
    return w


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


def watcher(svc, mode="availability", **fields):
    return svc.store.create_watcher(name="QA " + mode, mode=mode, config=fields.pop("config", {}), **fields)


def published(svc):
    return {item["id"].split(":")[-1]: item for item in fleet_status(svc)["items"]}


def test_a_target_with_its_own_slower_interval_sets_the_cadence_not_the_watcher_default(svc):
    w = watcher(svc, interval_min=15)
    svc.store.create_target(w["id"], "https://shop.example/a", interval_min=240)
    assert published(svc)[w["id"]]["every"] == "240m"
    svc.store.create_target(w["id"], "https://shop.example/b", interval_min=45)
    assert published(svc)[w["id"]]["every"] == "45m"       # something runs at least that often
    svc.store.create_target(w["id"], "https://shop.example/c")   # no own interval: the watcher's
    assert published(svc)[w["id"]]["every"] == "15m"


def test_targets_waiting_for_the_person_are_revisited_every_three_hours_at_most(svc):
    w = watcher(svc, interval_min=15)
    target = svc.store.create_target(w["id"], "https://shop.example/a")
    svc.store.update_target(target["id"], status="needs_human")
    assert published(svc)[w["id"]]["every"] == "180m"
    svc.store.update_target(target["id"], status="needs_human", interval_min=400)
    assert published(svc)[w["id"]]["every"] == "400m"
    svc.store.update_target(target["id"], status="active", interval_min=None)
    other = svc.store.create_target(w["id"], "https://shop.example/b")
    svc.store.update_target(other["id"], status="needs_human")
    assert published(svc)[w["id"]]["every"] == "15m"       # the active one still runs every 15


def test_failing_targets_back_off_and_inactive_ones_are_ignored(svc):
    w = watcher(svc, interval_min=15)
    target = svc.store.create_target(w["id"], "https://shop.example/a")
    svc.store.update_target(target["id"], status="error", fail_count=3)
    assert published(svc)[w["id"]]["every"] == "120m"
    svc.store.update_target(target["id"], status="error", fail_count=9)
    assert published(svc)[w["id"]]["every"] == "360m"
    svc.store.update_target(target["id"], status="paused")
    assert w["id"] not in published(svc)                   # nothing live to check


def test_sweeps_respect_the_scheduler_floor(svc):
    low = watcher(svc, mode="secondhand", interval_min=5)
    info = watcher(svc, mode="information", interval_min=240)
    items = published(svc)
    assert items[low["id"]]["every"] == "10m" and items[info["id"]]["every"] == "240m"


def test_a_watcher_with_nothing_to_check_is_not_expected_to_run(svc):
    empty = watcher(svc, interval_min=15)
    assert empty["id"] not in published(svc)
    discovering = watcher(svc, interval_min=15, config={"discovery": {"queries": ["switch 2"], "enabled": True}})
    assert published(svc)[discovering["id"]]["every"] == "1440m"
    off = svc.store.create_watcher(name="QA off", mode="availability", config={}, interval_min=30, enabled=False)
    row = published(svc)[off["id"]]
    assert row["enabled"] is False and row["every"] == "30m"      # still listed, as paused
