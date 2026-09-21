from __future__ import annotations

import json

import pytest

from service_paths import CONFIG
from models import BroadcastState
from notify import plan_notification
from server import HomeServiceApp
from state import build_default_state
from tools import ToolService


@pytest.fixture()
def app() -> HomeServiceApp:
    return HomeServiceApp(build_default_state(CONFIG))


def get(app: HomeServiceApp, path: str, **query):
    return app.handle("GET", path, {k: [str(v)] for k, v in query.items()}, {})


def post(app: HomeServiceApp, path: str, body: dict):
    return app.handle("POST", path, {}, body)


# ------------------------------------------------------------------- routes
def test_health_lists_the_six_tools(app):
    status, payload = get(app, "/health")
    assert status == 200
    assert payload["ok"] is True
    assert len(payload["tools"]) == 6


def test_unknown_route_returns_404(app):
    status, payload = get(app, "/nope")
    assert status == 404
    assert payload["ok"] is False


def test_snapshot_route_returns_full_state(app):
    status, payload = get(app, "/snapshot")
    assert status == 200
    assert len(payload["rooms"]) == 3


def test_memory_snapshot_uses_all_eight_stable_device_ids(app):
    status, payload = get(app, "/snapshot")

    assert status == 200
    assert {device["id"] for device in payload["devices"]} == {
        "living_room_light",
        "living_room_ac",
        "desk_plug",
        "indoor_temperature",
        "bedroom_light",
        "bedroom_ac",
        "kitchen_light",
        "kitchen_ac",
    }


def test_state_route_supports_since(app):
    _, health = get(app, "/health")
    version = health["version"]
    post(app, "/tool/set_light", {"room": "客厅", "on": True})
    status, payload = get(app, "/state", since=version)
    assert status == 200
    assert payload["mode"] == "increments"
    assert payload["increments"]


def test_state_route_falls_back_to_snapshot_for_unknown_version(app):
    status, payload = get(app, "/state", since=99999)
    assert status == 200
    assert payload["mode"] == "snapshot"
    assert payload["reason"] == "unknown_version"


# --------------------------------------------------------------- tool routes
def test_set_light_route(app):
    status, payload = post(app, "/tool/set_light", {"room": "客厅", "brightness": 40})
    assert status == 200
    assert payload["ok"] is True
    assert payload["data"]["state"]["brightness"] == 40


def test_set_ac_route_rejects_bad_temperature(app):
    status, payload = post(app, "/tool/set_ac", {"room": "卧室", "target_temp": 50})
    assert status == 400
    assert payload["error_code"] == "out_of_range"


def test_person_location_route(app):
    status, payload = get(app, "/tool/person_location", person="爸爸")
    assert status == 200
    assert payload["data"]["persons"][0]["room_name"] == "卧室"


# ------------------------------------------------------- fault behaviours
def test_offline_device_returns_offline_and_keeps_state(app):
    post(app, "/test/device_online", {"device_id": "bedroom_light", "online": False})
    status, payload = post(app, "/tool/set_light", {"room": "卧室", "on": True})
    assert status == 400
    assert payload["error_code"] == "offline"
    snapshot = get(app, "/snapshot")[1]
    device = next(d for d in snapshot["devices"] if d["id"] == "bedroom_light")
    assert device["state"]["on"] is False


def test_offline_device_back_online_can_be_controlled(app):
    post(app, "/test/device_online", {"device_id": "bedroom_light", "online": False})
    post(app, "/tool/set_light", {"room": "卧室", "on": True})
    post(app, "/test/device_online", {"device_id": "bedroom_light", "online": True})
    status, payload = post(app, "/tool/set_light", {"room": "卧室", "on": True})
    assert status == 200
    assert payload["ok"] is True


def test_duplicate_request_via_http_is_applied_once(app):
    body = {"room": "客厅", "brightness": 65, "operation_id": "http-op-1"}
    first = post(app, "/tool/set_light", body)
    version_after_first = app.state.require_device("living_room_light").version
    second = post(app, "/tool/set_light", body)
    assert first[1]["ok"] and second[1]["ok"]
    assert second[1]["data"] == first[1]["data"]
    assert app.state.require_device("living_room_light").version == version_after_first


def test_partial_success_when_one_target_location_unknown(app):
    post(app, "/test/person_location_unknown", {"person_id": "dad"})
    status, payload = post(app, "/tool/notify", {"targets": ["爸爸", "孩子"], "text": "吃饭啦"})
    assert status == 200
    plan = payload["data"]
    assert len(plan["tasks"]) == 1
    assert len(plan["unknown"]) == 1
    assert payload["ok"] is True


def test_notify_with_player_still_reports_scheduled(app):
    status, payload = post(app, "/tool/notify", {"targets": ["爸爸"], "text": "吃饭啦"})
    assert status == 200
    assert "已安排" in payload["phrase"]
    assert "已播报" not in payload["phrase"]


# ------------------------------------------------------ broadcast lifecycle
def test_broadcast_playback_requires_head_and_receipt(app):
    post(app, "/tool/broadcast", {"room": "卧室", "text": "吃饭啦"})
    status, payload = get(app, "/broadcast/head")
    assert status == 200
    task = payload["task"]
    assert task["state"] == BroadcastState.QUEUED.value

    status, started = post(app, "/broadcast/start", {"task_id": task["id"]})
    assert status == 200
    receipt = started["task"]["receipt_id"]

    status, finished = post(
        app,
        "/broadcast/receipt",
        {"task_id": task["id"], "receipt_id": receipt, "success": True},
    )
    assert status == 200
    assert finished["task"]["state"] == BroadcastState.PLAYED.value
    assert finished["phrase"] == "已在卧室播报"


def test_broadcast_start_with_wrong_id_is_rejected(app):
    post(app, "/tool/broadcast", {"room": "卧室", "text": "A"})
    post(app, "/tool/broadcast", {"room": "厨房", "text": "B"})
    _, head = get(app, "/broadcast/head")
    second = [t for t in app.state.broadcasts.values() if t.id != head["task"]["id"]][0]
    status, payload = post(app, "/broadcast/start", {"task_id": second.id})
    assert status == 409
    assert payload["code"] == "version_conflict"


def test_bad_receipt_marks_failed_and_no_replay(app):
    post(app, "/tool/broadcast", {"room": "卧室", "text": "A"})
    _, head = get(app, "/broadcast/head")
    task = head["task"]
    post(app, "/broadcast/start", {"task_id": task["id"]})
    status, payload = post(
        app,
        "/broadcast/receipt",
        {"task_id": task["id"], "receipt_id": "bogus", "success": True},
    )
    assert status == 200
    assert payload["task"]["state"] == BroadcastState.FAILED.value
    assert "失败" in payload["phrase"]


def test_failed_broadcast_does_not_block_the_next_room(app):
    post(app, "/tool/broadcast", {"room": "卧室", "text": "A"})
    post(app, "/tool/broadcast", {"room": "厨房", "text": "B"})
    first = app.state.head_broadcast()
    app.state.start_broadcast(first.id)
    app.state.fail_broadcast(first.id, "tts failed")
    second = app.state.head_broadcast()
    assert second is not None
    assert second.room_id == "kitchen"


# --------------------------------------------------- restart / consistency
def test_state_restores_from_config_after_restart(app):
    """A fresh service must rebuild the same authoritative baseline."""
    post(app, "/tool/set_light", {"room": "客厅", "brightness": 15})
    restarted = HomeServiceApp(build_default_state(CONFIG))
    snapshot = restarted.handle("GET", "/snapshot", {}, {})[1]
    device = next(d for d in snapshot["devices"] if d["id"] == "living_room_light")
    # Config is the baseline; runtime changes are intentionally not persisted yet.
    assert device["state"]["brightness"] == 50
    assert len(snapshot["rooms"]) == 3


def test_version_conflict_via_http_returns_conflict_code(app):
    _, before = get(app, "/tool/device_status", room="卧室")
    ac = next(d for d in before["data"]["devices"] if d["type"] == "ac")
    stale = ac["version"]
    post(app, "/tool/set_ac", {"room": "卧室", "on": True})
    status, payload = post(
        app,
        "/tool/set_ac",
        {"room": "卧室", "target_temp": 24, "precondition_version": stale},
    )
    assert status == 400
    assert payload["error_code"] == "version_conflict"


def test_misoperations_stay_zero_across_rejections(app):
    """Rejections must never mutate device state; that count is the gate."""
    post(app, "/test/device_online", {"device_id": "living_room_light", "online": False})
    post(app, "/tool/set_light", {"room": "客厅", "on": True})
    post(app, "/tool/set_light", {"room": "客厅", "brightness": 500})
    post(app, "/tool/set_ac", {"room": "卧室", "target_temp": 99})
    post(app, "/tool/set_ac", {"room": "卧室", "mode": "turbo"})
    post(app, "/tool/set_light", {"room": "车库", "on": True})

    snapshot = get(app, "/snapshot")[1]
    on_lights = [d["id"] for d in snapshot["devices"] if d["state"].get("on")]
    assert on_lights == []
    assert app.tools.device_writes == 0
    assert app.tools.misoperations == 0
