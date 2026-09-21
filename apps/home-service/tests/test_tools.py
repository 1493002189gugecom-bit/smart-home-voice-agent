from __future__ import annotations

import pytest

from service_paths import CONFIG
from models import BroadcastState, OperationStatus
from notify import plan_notification
from state import build_default_state
from tools import TOOL_NAMES, ToolService


@pytest.fixture()
def service() -> ToolService:
    return ToolService(build_default_state(CONFIG))


def test_tool_surface_is_exactly_six_tools():
    assert len(TOOL_NAMES) == 6
    assert set(TOOL_NAMES) == {
        "query_room_status",
        "query_person_location",
        "query_device_status",
        "set_light",
        "set_ac",
        "broadcast_to_room",
    }


# ------------------------------------------------------------------ queries
def test_query_room_status_by_name(service):
    result = service.query_room_status("客厅")
    assert result.ok
    assert result.data["rooms"][0]["name"] == "客厅"
    assert {device["id"] for device in result.data["rooms"][0]["devices"]} == {
        "living_room_light",
        "living_room_ac",
        "desk_plug",
        "indoor_temperature",
    }


def test_query_unknown_room_fails(service):
    result = service.query_room_status("车库")
    assert not result.ok
    assert result.error_code == "not_found"


def test_query_person_location_reports_known(service):
    result = service.query_person_location("爸爸")
    assert result.ok
    person = result.data["persons"][0]
    assert person["room_name"] == "卧室"
    assert person["location_known"] is True


def test_query_person_location_reports_unknown(service):
    service.state.require_person("dad").room_id = None
    result = service.query_person_location("爸爸")
    assert result.ok
    assert result.data["persons"][0]["location_known"] is False


def test_query_device_status_by_room(service):
    result = service.query_device_status(room="卧室")
    assert result.ok
    assert {d["type"] for d in result.data["devices"]} == {"light", "ac"}


# ------------------------------------------------------------------- writes
def test_turn_on_light_by_room(service):
    result = service.set_light(room="客厅", on=True)
    assert result.ok
    assert result.data["state"]["on"] is True
    # Brightness is reported because it is what actually took effect; the user
    # otherwise cannot tell how bright the light became.
    assert result.phrase == "已打开客厅的灯，亮度 50"


def test_set_light_on_with_explicit_brightness(service):
    result = service.set_light(room="客厅", on=True, brightness=20)
    assert result.ok
    assert result.phrase == "已打开客厅的灯，亮度 20"


def test_set_light_brightness_reports_value(service):
    result = service.set_light(room="卧室", brightness=30)
    assert result.ok
    assert result.data["state"]["brightness"] == 30
    assert "30" in result.phrase


def test_turning_off_an_off_light_is_a_noop(service):
    result = service.set_light(room="客厅", on=False)
    assert result.ok
    assert result.data["noop"] is True
    assert service.device_writes == 0


def test_offline_device_is_rejected_and_counts_no_write(service):
    service.state.set_device_online("living_room_light", False)
    result = service.set_light(room="客厅", brightness=10)
    assert not result.ok
    assert result.error_code == "offline"
    assert service.device_writes == 0
    assert service.state.require_device("living_room_light").state["on"] is False


def test_out_of_range_brightness_is_rejected(service):
    result = service.set_light(room="客厅", brightness=200)
    assert not result.ok
    assert result.error_code == "out_of_range"
    assert service.device_writes == 0


def test_out_of_range_temperature_is_rejected(service):
    result = service.set_ac(room="卧室", target_temp=40)
    assert not result.ok
    assert result.error_code == "out_of_range"
    assert service.device_writes == 0


def test_unknown_mode_is_rejected(service):
    result = service.set_ac(room="卧室", mode="turbo")
    assert not result.ok
    assert result.error_code == "out_of_range"


def test_stale_precondition_returns_conflict(service):
    device = service.state.require_device("bedroom_ac")
    stale = device.version
    service.set_ac(room="卧室", on=True)
    result = service.set_ac(room="卧室", target_temp=24, precondition_version=stale)
    assert not result.ok
    assert result.error_code == "version_conflict"


def test_set_ac_before_writing_never_touches_state(service):
    device = service.state.require_device("bedroom_ac")
    before = dict(device.state)
    service.set_ac(room="卧室", target_temp=99)
    assert device.state == before


def test_wrong_device_type_is_rejected(service):
    result = service.set_ac(device_id="bedroom_light", on=True)
    assert not result.ok
    assert result.error_code == "wrong_type"


# --------------------------------------------------------- operation ids
def test_repeated_operation_id_is_not_applied_twice(service):
    first = service.set_light(room="客厅", brightness=70, operation_id="op-fixed-1")
    assert first.ok
    version_after_first = service.state.require_device("living_room_light").version
    second = service.set_light(room="客厅", brightness=70, operation_id="op-fixed-1")
    assert second.ok
    assert second.data == first.data
    # No second write happened.
    assert service.state.require_device("living_room_light").version == version_after_first
    assert service.device_writes == 1


def test_repeated_operation_id_does_not_double_queue_broadcast(service):
    service.broadcast_to_room("卧室", "吃饭了", operation_id="op-bc-1")
    service.broadcast_to_room("卧室", "吃饭了", operation_id="op-bc-1")
    assert len(service.state.broadcast_queue) == 1


def test_failed_operation_is_replayed_as_failure(service):
    service.state.set_device_online("living_room_light", False)
    first = service.set_light(room="客厅", brightness=10, operation_id="op-fail-1")
    assert not first.ok
    again = service.set_light(room="客厅", brightness=10, operation_id="op-fail-1")
    assert not again.ok
    assert again.error_code == "replayed_failure"


def test_operation_records_are_persisted(service):
    service.set_light(room="客厅", on=True, operation_id="op-record")
    record = service.state.find_operation("op-record")
    assert record is not None
    assert record.status == OperationStatus.SUCCEEDED
    assert record.result["state"]["on"] is True


# ------------------------------------------------------------ broadcast
def test_broadcast_queues_and_reports_scheduled(service):
    result = service.broadcast_to_room("卧室", "吃饭了")
    assert result.ok
    assert result.data["state"] == BroadcastState.QUEUED.value
    assert "已安排" in result.phrase
    assert "已播报" not in result.phrase


def test_broadcast_to_unknown_room_fails(service):
    result = service.broadcast_to_room("车库", "吃饭了")
    assert not result.ok
    assert result.error_code == "not_found"


def test_empty_broadcast_text_fails(service):
    result = service.broadcast_to_room("卧室", "  ")
    assert not result.ok


# ------------------------------------------------- notification planning
def test_two_aliases_of_the_same_person_merge_into_one_task(service):
    # 爸爸 and 老爸 are aliases for the same person, so only one task is queued.
    result = plan_notification(service, ["爸爸", "老爸"], "吃饭啦")
    assert result.ok
    plan = result.data
    assert len(plan["tasks"]) == 1
    assert plan["tasks"][0]["room_name"] == "卧室"
    assert len(service.state.broadcast_queue) == 1
    assert plan["merged"]


def test_cross_room_targets_create_serial_tasks(service):
    result = plan_notification(service, ["爸爸", "孩子"], "吃饭啦")
    assert result.ok
    plan = result.data
    assert len(plan["tasks"]) == 2
    assert [t["room_name"] for t in plan["tasks"]] == ["卧室", "客厅"]
    assert len(service.state.broadcast_queue) == 2


def test_unknown_location_is_reported_and_not_broadcast(service):
    service.state.require_person("dad").room_id = None
    result = plan_notification(service, ["爸爸"], "吃饭啦")
    assert result.ok
    plan = result.data
    assert plan["tasks"] == []
    assert plan["unknown"][0]["display_name"] == "爸爸"
    assert plan["needs_clarification"] is True
    # No whole-house fallback: the queue stays empty.
    assert service.state.broadcast_queue == []


def test_partial_unknown_still_notifies_known_targets(service):
    service.state.require_person("dad").room_id = None
    result = plan_notification(service, ["爸爸", "孩子"], "吃饭啦")
    assert result.ok
    plan = result.data
    assert len(plan["tasks"]) == 1
    assert plan["tasks"][0]["room_name"] == "客厅"
    assert len(plan["unknown"]) == 1
    assert plan["needs_clarification"] is True


def test_phrase_never_claims_hearing_or_arrival(service):
    result = plan_notification(service, ["爸爸", "孩子"], "吃饭啦")
    phrase = result.phrase
    for forbidden in ("听到了", "来了", "已播报"):
        assert forbidden not in phrase
    assert "已安排" in phrase


def test_unknown_phrase_asks_instead_of_broadcasting(service):
    service.state.require_person("dad").room_id = None
    result = plan_notification(service, ["爸爸"], "吃饭啦")
    assert "未通知" in result.phrase
    assert "全屋播报" in result.phrase


def test_duplicate_target_names_merge(service):
    result = plan_notification(service, ["孩子", "小朋友"], "吃饭啦")
    assert result.ok
    plan = result.data
    assert len(plan["tasks"]) == 1
    assert len(plan["tasks"][0]["display_names"]) == 1
    assert plan["merged"]


def test_same_room_different_people_merge(service):
    """Two distinct people in one room share a single announcement."""
    service.state.move_person("dad", "kitchen")
    result = plan_notification(service, ["爸爸", "妈妈"], "吃饭啦")
    assert result.ok
    plan = result.data
    assert len(plan["tasks"]) == 1
    assert plan["tasks"][0]["room_name"] == "厨房"
    assert sorted(plan["tasks"][0]["display_names"]) == ["妈妈", "爸爸"]


def test_notification_with_no_targets_fails(service):
    result = plan_notification(service, [], "吃饭啦")
    assert not result.ok
