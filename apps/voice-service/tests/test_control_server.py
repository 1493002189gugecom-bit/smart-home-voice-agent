from __future__ import annotations

import json
import urllib.request

import audio_utils
import control_server
from control_server import create_control_server, start_control_server
from event_bus import VoiceEventBus


def test_degraded_health_is_visible_without_secret_details():
    bus = VoiceEventBus()
    bus.state = "degraded"
    bus.health_summary = {"error_code": "audio_input_unavailable"}
    server = create_control_server(bus, port=0)
    start_control_server(server)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health") as response:
            payload = json.load(response)
        assert payload["ok"] is False
        assert payload["state"] == "degraded"
        assert payload["error_code"] == "audio_input_unavailable"
    finally:
        server.shutdown()
        server.server_close()


def test_device_selection_is_sent_to_controller():
    bus = VoiceEventBus()
    selections = []
    server = create_control_server(bus, port=0, on_select=lambda body: selections.append(body) or {"ok": True, "state": "applying"})
    start_control_server(server)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/devices/select",
            data=b'{"input":"auto","output":"auto"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
        assert payload["state"] == "applying"
        assert selections == [{"input": "auto", "output": "auto"}]
    finally:
        server.shutdown()
        server.server_close()


def test_device_list_marks_default_selected_and_usable(monkeypatch):
    device = audio_utils.AudioDevice(4, "Realtek microphone", "MME", 2, 48000)
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: [device])
    monkeypatch.setattr(audio_utils, "list_output_devices", lambda: [])
    monkeypatch.setattr(audio_utils, "system_default_index", lambda kind: 4 if kind == "input" else None)
    monkeypatch.setattr(control_server.sd, "check_input_settings", lambda **kwargs: None)
    bus = VoiceEventBus()
    bus.health_summary = {"input_device_index": 4}
    server = create_control_server(bus, port=0)
    start_control_server(server)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/devices") as response:
            payload = json.load(response)
        assert payload["inputs"][0]["is_system_default"] is True
        assert payload["inputs"][0]["is_selected"] is True
        assert payload["inputs"][0]["usable"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_device_list_exposes_selection_result(monkeypatch):
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: [])
    monkeypatch.setattr(audio_utils, "list_output_devices", lambda: [])
    bus = VoiceEventBus()
    bus.set_selection_status("request-1", "failed", "audio_input_unavailable")
    server = create_control_server(bus, port=0)
    start_control_server(server)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/devices") as response:
            payload = json.load(response)
        assert payload["selection"] == {
            "request_id": "request-1", "state": "failed", "error_code": "audio_input_unavailable",
        }
    finally:
        server.shutdown()
        server.server_close()


def test_missing_manual_device_is_reported_as_fallback(monkeypatch):
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: [audio_utils.AudioDevice(4, "Realtek", "MME", 1, 48000)])
    monkeypatch.setattr(audio_utils, "list_output_devices", lambda: [])
    monkeypatch.setattr(control_server.sd, "check_input_settings", lambda **kwargs: None)
    monkeypatch.setattr(control_server.device_preferences, "load", lambda: {
        "input": {"name": "HyperX", "hostapi": "WASAPI"}, "output": None,
    })
    bus = VoiceEventBus()
    bus.health_summary = {"input_device_index": 4}
    server = create_control_server(bus, port=0)
    start_control_server(server)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/devices") as response:
            payload = json.load(response)
        assert payload["warnings"]["input"] == "preferred_device_unavailable"
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------ transient events
def test_transient_events_reach_subscribers_without_entering_history():
    """Streaming deltas arrive per token; a single reply would otherwise evict
    the whole conversation from the bounded history the console displays."""
    bus = VoiceEventBus(max_events=4)
    subscriber = bus.subscribe()

    bus.publish("transcript", text="我在哪")
    for index in range(50):
        bus.publish_transient("agent_delta", text="x" * index)

    assert [event["type"] for event in bus.history()] == ["transcript"]
    assert subscriber.get_nowait()["type"] == "transcript"


def test_transient_events_do_not_change_the_machine_state():
    bus = VoiceEventBus()
    bus.state = "listening"

    bus.publish_transient("agent_delta", state="idle", text="半句话")

    assert bus.state == "listening"


def test_history_stays_bounded_while_transient_events_stream():
    bus = VoiceEventBus(max_events=3)

    for index in range(10):
        bus.publish("transcript", text=str(index))
        bus.publish_transient("agent_delta", text="…")

    assert [event["payload"]["text"] for event in bus.history()] == ["7", "8", "9"]


# ------------------------------------------------------------ voice enrolment


def identity_server(handler):
    server = create_control_server(VoiceEventBus(), port=0, on_identity=handler)
    start_control_server(server)
    return server


def post_json(server, path, payload):
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_enrolment_status_is_asked_of_the_loop():
    calls = []
    server = identity_server(lambda action, body: calls.append((action, body)) or {"ok": True, "enrollment": {"state": "idle"}})
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/identity/enroll/voice/status"
        ) as response:
            payload = json.load(response)
        assert payload["enrollment"]["state"] == "idle"
        assert calls == [("status", {})]
    finally:
        server.shutdown()
        server.server_close()


def test_starting_an_enrolment_forwards_the_person():
    calls = []
    server = identity_server(lambda action, body: calls.append((action, body)) or {"ok": True})
    try:
        status, _payload = post_json(server, "/identity/enroll/voice/start", {"person_id": "dad"})

        assert status == 200
        assert calls == [("start", {"person_id": "dad"})]
    finally:
        server.shutdown()
        server.server_close()


def test_an_enrolment_start_with_a_typo_is_refused_rather_than_ignored():
    calls = []
    server = identity_server(lambda action, body: calls.append((action, body)) or {"ok": True})
    try:
        status, payload = post_json(server, "/identity/enroll/voice/start", {"person_id": "dad", "extra": 1})

        assert status == 422
        assert payload["error_code"] == "invalid_body"
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()


def test_a_refused_enrolment_returns_its_reason():
    server = identity_server(lambda action, body: {"ok": False, "error_code": "unknown_person", "message": "请先在身份页添加该人物"})
    try:
        status, payload = post_json(server, "/identity/enroll/voice/start", {"person_id": "nobody"})

        assert status == 422
        assert payload["error_code"] == "unknown_person"
    finally:
        server.shutdown()
        server.server_close()


def test_cancelling_takes_no_fields():
    calls = []
    server = identity_server(lambda action, body: calls.append((action, body)) or {"ok": True})
    try:
        assert post_json(server, "/identity/enroll/voice/cancel", {})[0] == 200
        assert post_json(server, "/identity/enroll/voice/cancel", {"person_id": "dad"})[0] == 422
        assert calls == [("cancel", {})]
    finally:
        server.shutdown()
        server.server_close()


def test_an_unknown_post_path_is_still_not_found():
    server = identity_server(lambda action, body: {"ok": True})
    try:
        assert post_json(server, "/nope", {})[0] == 404
    finally:
        server.shutdown()
        server.server_close()
