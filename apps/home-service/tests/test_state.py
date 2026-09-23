from __future__ import annotations

import pytest

from service_paths import CONFIG
from models import BroadcastState, DeviceType
from state import (
    HomeState,
    NotFound,
    Offline,
    OutOfRange,
    VersionConflict,
    build_default_state,
)


@pytest.fixture()
def state() -> HomeState:
    return build_default_state(CONFIG)


def test_default_house_has_three_rooms_and_eight_devices(state):
    assert set(state.rooms) == {"living_room", "bedroom", "kitchen"}
    assert len(state.devices) == 8
    assert len(state.persons) == 3


def test_comfort_limits_come_from_config(state):
    assert state.comfort_temp == 26.0
    assert (state.temp_min, state.temp_max) == (16.0, 30.0)


def test_invalid_limit_configuration_is_rejected():
    with pytest.raises(ValueError, match="temp_min must be lower"):
        HomeState({"temp_min": 30, "temp_max": 16})


def test_snapshot_reports_version_and_queue(state):
    before = state.version
    state.set_light("living_room_light", on=True)
    snapshot = state.snapshot()
    assert snapshot["version"] > before
    assert snapshot["broadcast_queue"] == []
    assert any(device["id"] == "living_room_light" for device in snapshot["devices"])


def test_increments_track_every_commit(state):
    start = state.version
    state.set_light("living_room_light", on=True)
    state.set_ac("bedroom_ac", on=True, target_temp=25)
    increments = state.increments_since(start)
    assert [inc["kind"] for inc in increments] == ["device_changed", "device_changed"]


def test_unknown_version_falls_back_to_snapshot(state):
    result = state.sync(9999)
    assert result["mode"] == "snapshot"
    assert result["reason"] == "unknown_version"


def test_sync_without_version_returns_snapshot(state):
    assert state.sync(None)["mode"] == "snapshot"


def test_sync_with_known_version_returns_increments(state):
    start = state.version
    state.set_light("kitchen_light", on=True)
    result = state.sync(start)
    assert result["mode"] == "increments"
    assert result["to"] == state.version


def test_version_conflict_is_raised_for_stale_write(state):
    device = state.require_device("living_room_light")
    stale = device.version
    state.set_light("living_room_light", on=True)
    with pytest.raises(VersionConflict):
        state.set_light("living_room_light", brightness=10, precondition_version=stale)


def test_offline_device_cannot_be_written(state):
    state.set_device_online("bedroom_light", False)
    with pytest.raises(Offline):
        state.set_light("bedroom_light", on=True)


def test_brightness_range_is_enforced(state):
    with pytest.raises(OutOfRange):
        state.set_light("living_room_light", brightness=101)
    with pytest.raises(OutOfRange):
        state.set_light("living_room_light", brightness=-1)


def test_temperature_range_is_enforced(state):
    with pytest.raises(OutOfRange):
        state.set_ac("bedroom_ac", target_temp=35)
    with pytest.raises(OutOfRange):
        state.set_ac("bedroom_ac", target_temp=5)


def test_unknown_ids_raise_not_found(state):
    with pytest.raises(NotFound):
        state.require_device("no_such_device")
    with pytest.raises(NotFound):
        state.require_room("no_such_room")
    with pytest.raises(NotFound):
        state.require_person("nobody")


def test_device_type_mismatch_is_rejected(state):
    with pytest.raises(OutOfRange):
        state.set_ac("living_room_light", on=True)
    with pytest.raises(OutOfRange):
        state.set_light("living_room_ac", on=True)


def test_room_temperature_is_independent_of_ac(state):
    room = state.require_room("living_room")
    original = room.simulated_temp
    state.set_ac("living_room_ac", on=True, target_temp=18)
    assert state.require_room("living_room").simulated_temp == original


def test_resolve_person_uses_aliases(state):
    assert state.resolve_person("爸爸").id == "dad"
    assert state.resolve_person("老爸").id == "dad"
    assert state.resolve_person("不存在的人") is None


def test_resolve_room_by_name_and_id(state):
    assert state.resolve_room("客厅").id == "living_room"
    assert state.resolve_room("living_room").id == "living_room"


def test_light_brightness_implies_on(state):
    device = state.set_light("bedroom_light", brightness=30)
    assert device.state["on"] is True
    assert device.state["brightness"] == 30


def test_ac_mode_validation(state):
    device = state.set_ac("bedroom_ac", mode="dry")
    assert device.state["mode"] == "dry"
    with pytest.raises(OutOfRange):
        state.set_ac("bedroom_ac", mode="turbo")


# --------------------------------------------------------------- broadcasts
def test_same_room_targets_merge_into_one_task(state):
    task = state.enqueue_broadcast("bedroom", "吃饭了", ["dad"])
    assert state.broadcast_queue == [task.id]
    assert task.state == BroadcastState.QUEUED


def test_cross_room_tasks_play_serially(state):
    first = state.enqueue_broadcast("bedroom", "吃饭了", ["dad"])
    second = state.enqueue_broadcast("kitchen", "吃饭了", ["mom"])
    assert state.head_broadcast().id == first.id
    state.start_broadcast(first.id)
    state.complete_broadcast(first.id, first.receipt_id, success=True)
    assert state.head_broadcast().id == second.id


def test_only_queue_head_may_start(state):
    first = state.enqueue_broadcast("bedroom", "A", [])
    second = state.enqueue_broadcast("kitchen", "B", [])
    with pytest.raises(VersionConflict):
        state.start_broadcast(second.id)
    state.start_broadcast(first.id)


def test_receipt_mismatch_counts_as_failure(state):
    task = state.enqueue_broadcast("bedroom", "A", [])
    state.start_broadcast(task.id)
    result = state.complete_broadcast(task.id, "wrong-receipt", success=True)
    assert result.state == BroadcastState.FAILED
    assert result.error == "receipt mismatch"


def test_failed_broadcast_never_claims_played(state):
    task = state.enqueue_broadcast("bedroom", "A", [])
    state.start_broadcast(task.id)
    state.fail_broadcast(task.id, "tts unavailable")
    phrase = state.broadcast_phrase(task.id)
    assert "失败" in phrase
    assert "已" not in phrase


def test_queued_phrase_says_scheduled_not_played(state):
    task = state.enqueue_broadcast("bedroom", "A", [])
    phrase = state.broadcast_phrase(task.id)
    assert phrase == "已安排在卧室播报"
    assert "已播报" not in phrase


def test_played_phrase_says_played(state):
    task = state.enqueue_broadcast("bedroom", "A", [])
    state.start_broadcast(task.id)
    state.complete_broadcast(task.id, task.receipt_id, success=True)
    assert state.broadcast_phrase(task.id) == "已在卧室播报"


def test_empty_broadcast_text_is_rejected(state):
    with pytest.raises(OutOfRange):
        state.enqueue_broadcast("bedroom", "   ", [])


def test_conversation_exit_clears_pending_state(state):
    conversation = state.create_conversation("测试说话者", "living_room")
    state.touch_conversation(conversation.id, recent_targets=["bedroom_ac"], pending_clarification="哪个房间")
    state.end_conversation(conversation.id)
    assert conversation.recent_targets == []
    assert conversation.pending_clarification is None


def test_device_type_enum_values_are_stable():
    assert DeviceType.LIGHT.value == "light"
    assert DeviceType.AC.value == "ac"
