import json

import pytest

from main import apply_fault_control, graceful_shutdown
from model import initial_state
from runtime import SimulatorRuntime


class FakePublisher:
    def __init__(self):
        self.items = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.items.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )

    def last_raw(self, topic):
        return [item["payload"] for item in self.items if item["topic"] == topic][-1]

    def last(self, topic):
        return json.loads(self.last_raw(topic))

    def has_topic(self, topic, retain=None):
        return any(
            item["topic"] == topic
            and (retain is None or item["retain"] is retain)
            for item in self.items
        )


def test_success_saves_atomically_before_publishing_state(tmp_path):
    state_path = tmp_path / "state.json"

    class InspectingPublisher(FakePublisher):
        def publish(self, topic, payload, qos=0, retain=False):
            if topic == "shv/living_room_light/state":
                assert json.loads(state_path.read_text(encoding="utf-8"))["living_room_light"][
                    "state"
                ] == "ON"
            super().publish(topic, payload, qos, retain)

    publisher = InspectingPublisher()
    runtime = SimulatorRuntime(state_path, publisher, sleep=lambda _: None)
    assert runtime.handle("shv/living_room_light/set", '{"state":"ON"}') is True
    assert runtime.state["living_room_light"]["state"] == "ON"
    assert publisher.has_topic("shv/living_room_light/state", retain=True)
    assert not (tmp_path / "state.json.tmp").exists()


def test_fixed_delay_happens_before_transition_or_publication(tmp_path):
    publisher = FakePublisher()
    observations = []
    runtime = None

    def sleep(seconds):
        observations.append((seconds, runtime.state, list(publisher.items)))

    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=sleep)
    runtime.set_delay(1.25)
    runtime.handle("shv/desk_plug/set", "ON")
    assert observations == [(1.25, initial_state(), [])]
    assert runtime.state["desk_plug"] == {"state": "ON", "power": 60}


def test_one_shot_failure_preserves_state_and_publishes_sanitized_fault(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    before = runtime.state
    runtime.fail_next("living_room_light")
    runtime.handle("shv/living_room_light/set", '{"state":"ON","password":"secret"}')
    assert runtime.state == before
    fault = publisher.last("shv/events/fault")
    assert fault == {"device_id": "living_room_light", "error": "injected_failure"}
    assert "secret" not in publisher.last_raw("shv/events/fault")
    runtime.handle("shv/living_room_light/set", '{"state":"ON"}')
    assert runtime.state["living_room_light"]["state"] == "ON"


def test_fail_next_is_scoped_to_named_device(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    runtime.fail_next("living_room_light")
    assert runtime.handle("shv/desk_plug/set", "ON") is True
    assert runtime.handle("shv/living_room_light/set", '{"state":"ON"}') is False


def test_device_offline_rejects_only_that_device_without_delay_or_state_change(tmp_path):
    publisher = FakePublisher()
    sleeps = []
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=sleeps.append)
    runtime.set_delay(10)
    runtime.set_online("living_room_light", False)
    before = runtime.state
    assert runtime.handle("shv/living_room_light/set", '{"state":"ON"}') is False
    assert runtime.state == before
    assert sleeps == []
    assert publisher.last_raw("shv/living_room_light/availability") == "offline"
    assert publisher.last("shv/events/fault") == {
        "device_id": "living_room_light",
        "error": "offline",
    }
    assert runtime.handle("shv/desk_plug/set", "ON") is True
    assert runtime.state["desk_plug"]["state"] == "ON"


def test_invalid_command_preserves_state_and_does_not_echo_payload(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    before = runtime.state
    assert runtime.handle("shv/desk_plug/set", "credential=secret") is False
    assert runtime.state == before
    assert publisher.last("shv/events/fault") == {
        "device_id": "desk_plug",
        "error": "invalid_command",
    }
    assert "secret" not in publisher.last_raw("shv/events/fault")


def test_reconnect_republishes_discovery_availability_and_complete_state(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    runtime.on_connected()
    assert publisher.has_topic(
        "homeassistant/light/shv_living_room_light/config", retain=True
    )
    assert publisher.last_raw("shv/status") == "online"
    for device in (
        "living_room_light",
        "bedroom_light",
        "kitchen_light",
        "living_room_ac",
        "bedroom_ac",
        "kitchen_ac",
        "desk_plug",
        "indoor_temperature",
    ):
        assert publisher.last_raw(f"shv/{device}/availability") == "online"
    for topic in (
        "shv/living_room_light/state",
        "shv/bedroom_light/state",
        "shv/kitchen_light/state",
        "shv/living_room_ac/mode/state",
        "shv/living_room_ac/temperature/state",
        "shv/bedroom_ac/mode/state",
        "shv/bedroom_ac/temperature/state",
        "shv/kitchen_ac/mode/state",
        "shv/kitchen_ac/temperature/state",
        "shv/desk_plug/state",
        "shv/indoor_temperature/state",
    ):
        assert publisher.has_topic(topic, retain=True)
    first_count = len(publisher.items)
    runtime.on_connected()
    assert len(publisher.items) == first_count * 2


def test_reconnect_republishes_each_devices_fault_availability(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    runtime.set_online("living_room_light", False)
    runtime.on_connected()
    assert publisher.last_raw("shv/status") == "online"
    assert publisher.last_raw("shv/living_room_light/availability") == "offline"
    assert publisher.last_raw("shv/desk_plug/availability") == "online"


def test_new_runtime_restores_persisted_state(tmp_path):
    path = tmp_path / "state.json"
    first = SimulatorRuntime(path, FakePublisher(), sleep=lambda _: None)
    first.handle("shv/bedroom_ac/mode/set", "cool")
    restored = SimulatorRuntime(path, FakePublisher(), sleep=lambda _: None)
    assert restored.state["bedroom_ac"]["mode"] == "cool"


def test_local_fault_control_applies_offline_delay_and_one_shot_failure(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    apply_fault_control(
        runtime,
        {
            "device_id": "living_room_light",
            "online": False,
            "delay_seconds": 2.5,
            "fail_next": "living_room_light",
        },
    )
    assert runtime.is_online("living_room_light") is False
    assert runtime.is_online("desk_plug") is True
    assert runtime.delay_seconds == 2.5
    runtime.set_online("living_room_light", True)
    assert runtime.handle("shv/living_room_light/set", '{"state":"ON"}') is False


def test_graceful_shutdown_publishes_waits_then_disconnects():
    calls = []

    class MessageInfo:
        def wait_for_publish(self, timeout=None):
            calls.append(("wait", timeout))

    class FakeClient:
        def publish(self, topic, payload, qos=0, retain=False):
            calls.append(("publish", topic, payload, qos, retain))
            return MessageInfo()

        def disconnect(self):
            calls.append(("disconnect",))

    graceful_shutdown(FakeClient(), "house", wait_timeout=0.75)
    assert calls == [
        ("publish", "house/status", "offline", 1, True),
        ("wait", 0.75),
        ("disconnect",),
    ]


def test_graceful_shutdown_does_not_raise_when_the_client_never_connected():
    """A broker that was never reachable must not crash the shutdown path."""
    calls = []

    class MessageInfo:
        def wait_for_publish(self, timeout=None):
            raise RuntimeError("Message publish failed: The client is not currently connected.")

    class FakeClient:
        def publish(self, topic, payload, qos=0, retain=False):
            calls.append(("publish", topic))
            return MessageInfo()

        def disconnect(self):
            calls.append(("disconnect",))

    graceful_shutdown(FakeClient(), "house")

    assert calls == [("publish", "house/status"), ("disconnect",)]


def test_fault_controls_reject_invalid_values_without_mqtt_management_topics(tmp_path):
    runtime = SimulatorRuntime(
        tmp_path / "state.json", FakePublisher(), sleep=lambda _: None
    )
    with pytest.raises(ValueError, match="device_id"):
        apply_fault_control(runtime, {"online": False})
    with pytest.raises(ValueError, match="online"):
        apply_fault_control(runtime, {"device_id": "desk_plug", "online": "false"})
    with pytest.raises(ValueError, match="delay"):
        apply_fault_control(runtime, {"delay_seconds": float("inf")})
    with pytest.raises(ValueError, match="device"):
        apply_fault_control(runtime, {"fail_next": "indoor_temperature"})
