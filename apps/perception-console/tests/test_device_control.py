"""Manual writes must target one catalogued device and require a matching confirmation."""

import pytest

from device_control import ControlRejected, normalize_control, project_control_result, validate_command


OPERATION = "ui-test-operation-1234"


def snapshot(*, online=True, device_type="ac"):
    return {"devices": [{"id": "bedroom_ac", "type": device_type, "online": online}]}


def command(changes):
    return {"device_id": "bedroom_ac", "operation_id": OPERATION, "changes": changes}


def test_one_device_command_maps_to_existing_confirmed_tool():
    route, body = normalize_control(command({"on": True, "mode": "cool", "target_temp": 26.5}), snapshot())
    assert route == "/tool/set_ac"
    assert body == {"device_id": "bedroom_ac", "operation_id": OPERATION,
                    "on": True, "mode": "cool", "target_temp": 26.5}
    assert normalize_control(command({"on": False}), snapshot(device_type="switch"))[0] == "/tool/set_switch"


@pytest.mark.parametrize("changes", [
    {}, {"target_temp": 31}, {"target_temp": float("nan")}, {"target_temp": True},
    {"mode": "heat"}, {"mode": []}, {"on": "false"}, {"on": False, "mode": "cool"},
    {"entity_id": "outside_device"}, {"on": False, "target_temp": 26},
])
def test_invalid_or_conflicting_values_are_rejected(changes):
    with pytest.raises(ControlRejected):
        normalize_control(command(changes), snapshot())


@pytest.mark.parametrize("payload", [
    {**command({"on": True}), "room": "kitchen"},
    {**command({"on": True}), "operation_id": ""},
    {**command({"on": True}), "changes": []},
    {**command({"on": True}), "device_id": "../secret"},
])
def test_command_schema_rejects_extra_fields_and_invalid_ids(payload):
    with pytest.raises(ControlRejected):
        validate_command(payload)


def test_unknown_offline_and_sensor_devices_cannot_be_written():
    for payload, view in [(command({"on": True}), {"devices": []}),
                          (command({"on": True}), snapshot(online=False)),
                          (command({"on": True}), snapshot(device_type="sensor"))]:
        with pytest.raises(ControlRejected):
            normalize_control(payload, view)


def test_light_brightness_and_switch_fields_are_validated():
    assert normalize_control(command({"on": True, "brightness": 65}), snapshot(device_type="light"))[0] == "/tool/set_light"
    for device_type, changes in [("light", {"on": False, "brightness": 60}),
                                  ("light", {"brightness": 0}), ("light", {"brightness": 101}),
                                  ("switch", {"on": True, "brightness": 60})]:
        with pytest.raises(ControlRejected):
            normalize_control(command(changes), snapshot(device_type=device_type))


def test_success_requires_matching_id_device_and_observed_state():
    request = command({"on": False})
    result = {"ok": True, "status": "confirmed", "operation_id": OPERATION,
              "phrase": "卧室空调已关闭", "data": {"device": "bedroom_ac", "state": {"on": False}, "token": "secret"}}
    projected = project_control_result(result, request, 200)
    assert projected["status"] == "confirmed"
    assert projected["message"] == "卧室空调已关闭"
    assert "secret" not in str(projected)
    for invalid in [{**result, "operation_id": "other-operation"},
                    {**result, "data": {"device": "living_room_ac", "state": {"on": False}}},
                    {**result, "data": {"device": "bedroom_ac", "state": {"on": True}}},
                    {**result, "data": {"device": "bedroom_ac", "state": {"on": 0}}},
                    {"ok": True, "phrase": "操作完成"}]:
        assert project_control_result(invalid, request, 200)["status"] == "unconfirmed"


def test_unconfirmed_is_not_reported_as_success():
    result = {"ok": False, "status": "unconfirmed", "operation_id": OPERATION,
              "error_code": "confirmation_timeout", "error": "backend secret"}
    projected = project_control_result(result, command({"on": False}), 202)
    assert projected["ok"] is False
    assert projected["status"] == "unconfirmed"
    assert "secret" not in str(projected)
