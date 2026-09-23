"""Contract tests for the JSON payloads Unity and the Agent consume.

These exist because the C# side silently lost two fields: `location_known` is a
derived property that `asdict()` drops, and `room_name` was missing from the
snapshot's broadcasts. Both are asserted here so the Unity parser cannot break
again unnoticed.
"""
from __future__ import annotations

import pytest

from service_paths import CONFIG
from state import build_default_state


@pytest.fixture()
def state():
    return build_default_state(CONFIG)


def test_person_payload_includes_location_known(state):
    payload = state.snapshot()["persons"]
    for person in payload:
        assert "location_known" in person, f"{person['id']} is missing location_known"
        assert isinstance(person["location_known"], bool)


def test_person_payload_marks_unknown_location_explicitly(state):
    # No camera has observed anyone, so every person must read as unknown rather
    # than falling back to a configured or remembered room.
    payload = state.snapshot()["persons"]
    dad = next(p for p in payload if p["id"] == "dad")
    assert dad["location_known"] is False
    assert dad["room_id"] is None
    assert dad["location_source"] == "camera"


def test_broadcast_payload_includes_room_name(state):
    state.enqueue_broadcast("bedroom", "吃饭啦", ["dad"])
    payload = state.snapshot()["broadcasts"]
    assert payload, "expected one broadcast"
    for task in payload:
        assert task["room_name"] == "卧室"
        assert task["state"] == "queued"


def test_snapshot_and_broadcast_endpoint_agree(state):
    """The same task must serialize identically wherever it is exposed."""
    from server import _broadcast_payload

    task = state.enqueue_broadcast("kitchen", "吃饭啦", ["mom"])
    from_snapshot = next(t for t in state.snapshot()["broadcasts"] if t["id"] == task.id)
    from_endpoint = _broadcast_payload(task, state)
    assert from_snapshot == from_endpoint
    assert set(from_snapshot) >= {"id", "room_id", "room_name", "text", "state"}


def test_broadcast_payload_room_name_matches_room_id(state):
    state.enqueue_broadcast("living_room", "测试", [])
    task = state.snapshot()["broadcasts"][0]
    room = next(r for r in state.snapshot()["rooms"] if r["id"] == task["room_id"])
    assert task["room_name"] == room["name"]


def test_every_snapshot_field_unity_reads_is_present(state):
    """Guard the exact field set StateParser.cs reads."""
    state.set_light("living_room_light", on=True, brightness=50)
    state.enqueue_broadcast("bedroom", "吃饭啦", ["dad"])
    snapshot = state.snapshot()

    for room in snapshot["rooms"]:
        assert {"id", "name", "simulated_temp", "version"} <= set(room)

    for device in snapshot["devices"]:
        assert {"id", "room_id", "type", "name", "online", "state", "version"} <= set(device)
        if device["type"] == "sensor":
            assert {"temperature"} <= set(device["state"])
        else:
            assert {"on"} <= set(device["state"])

    for person in snapshot["persons"]:
        assert {"id", "display_name", "room_id", "location_known", "version"} <= set(person)

    for task in snapshot["broadcasts"]:
        assert {"id", "room_id", "room_name", "text", "state", "receipt_id"} <= set(task)

    assert isinstance(snapshot["broadcast_queue"], list)
    assert isinstance(snapshot["version"], int)
