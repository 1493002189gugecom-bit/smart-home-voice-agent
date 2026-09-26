import json
import hashlib
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from server import HomeServiceApp, LiveStateStream, create_server  # noqa: E402
from state import build_default_state  # noqa: E402
from diagnostics import DiagnosticWriter, read_records  # noqa: E402
import server as server_module  # noqa: E402


def read_event(response):
    result = {"data": ""}
    while True:
        line = response.readline().decode("utf-8").rstrip("\r\n")
        if not line:
            if result.get("event"):
                result["data"] = json.loads(result["data"])
                return result
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip()
        if field == "id":
            result["id"] = int(value)
        elif field == "event":
            result["event"] = value
        elif field == "data":
            result["data"] += value


def post_json(url, payload):
    request = Request(
        url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def test_events_sends_snapshot_immediately_and_memory_mutation_pushes():
    app = HomeServiceApp(build_default_state())
    server = create_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(url + "/events", timeout=2) as response:
            assert response.headers.get_content_type() == "text/event-stream"
            first = read_event(response)
            assert first["event"] == "snapshot"
            assert first["data"]["backend"] == "memory"

            result = post_json(
                url + "/tool/set_light",
                {
                    "device_id": "living_room_light",
                    "on": True,
                    "brightness": 61,
                    "operation_id": "sse-test-light-on",
                },
            )
            assert result["ok"] is True
            second = read_event(response)
            assert second["event"] == "snapshot"
            assert second["id"] > first["id"]
            device = next(
                item
                for item in second["data"]["devices"]
                if item["id"] == "living_room_light"
            )
            assert device["state"] == {"on": True, "brightness": 61}
            assert "sse-test-light-on" in second["data"]["trigger_ids"]
    finally:
        server.shutdown()
        server.server_close()
        server.stream.stop()
        thread.join(timeout=2)


def test_coalesced_observations_retain_both_trigger_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "DIAGNOSTICS", DiagnosticWriter(tmp_path, "home"))
    stream = LiveStateStream(HomeServiceApp(build_default_state()))
    stream.publish_after_vision_mutation = lambda: None
    try:
        stream.after_request("POST", 200, "/vision/observations",
                             {"session_id": "vision-abc", "observed_at_ms": 1001}, {})
        stream.after_request("POST", 200, "/vision/observations",
                             {"session_id": "vision-abc", "observed_at_ms": 1002}, {})
        item = stream.publish_snapshot()
        assert item is not None
        assert item.data["trigger_ids"] == ["vision-abc:1001", "vision-abc:1002"]
        records = [row for row in read_records(tmp_path) if row["stage"] == "sse_snapshot"]
        expected = [hashlib.sha256(value.encode()).hexdigest()[:24] for value in item.data["trigger_ids"]]
        assert records[-1]["trigger_ids"] == expected
    finally:
        stream.stop()


def test_health_summary_preserves_last_ha_connection_time():
    stream = LiveStateStream(HomeServiceApp(build_default_state()))
    try:
        stream._on_ha_status("upstream_connected")
        connected = stream.health_summary()
        assert connected["upstream_last_connected_at_ms"] > 0
        stream._on_ha_status("upstream_disconnected")
        disconnected = stream.health_summary()
        assert disconnected["upstream_state"] == "upstream_disconnected"
        assert disconnected["upstream_last_connected_at_ms"] == connected["upstream_last_connected_at_ms"]
    finally:
        stream.stop()


def test_http_health_exposes_stream_upstream_state():
    server = create_server(HomeServiceApp(build_default_state()), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/health", timeout=2) as response:
            health = json.load(response)
        assert health["ok"] is True
        assert health["upstream_state"] == "not_applicable"
    finally:
        server.shutdown()
        server.server_close()
        server.stream.stop()
        thread.join(timeout=2)


def test_failed_snapshot_keeps_trigger_for_retry(monkeypatch):
    stream = LiveStateStream(HomeServiceApp(build_default_state()))
    original = server_module.canonical_memory_snapshot
    try:
        monkeypatch.setattr(server_module, "canonical_memory_snapshot", lambda *args, **kwargs: 1 / 0)
        stream.after_request("POST", 200, "/tool/set_light", {"operation_id": "retry-op"}, {})
        monkeypatch.setattr(server_module, "canonical_memory_snapshot", original)
        item = stream.publish_snapshot()
        assert item is not None
        assert item.data["trigger_ids"] == ["retry-op"]
    finally:
        stream.stop()


def test_events_endpoint_is_read_only():
    app = HomeServiceApp(build_default_state())
    server = create_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        request = Request(url + "/events", method="POST", data=b"{}")
        try:
            urlopen(request, timeout=2)
        except Exception as exc:
            assert getattr(exc, "code", None) == 404
        else:
            raise AssertionError("POST /events must not be accepted")
    finally:
        server.shutdown()
        server.server_close()
        server.stream.stop()
        thread.join(timeout=2)


def test_idle_event_stream_sends_heartbeat_comments():
    app = HomeServiceApp(build_default_state())
    server = create_server(app, "127.0.0.1", 0, heartbeat_seconds=0.02)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(url + "/events", timeout=2) as response:
            read_event(response)
            assert response.readline().decode("utf-8").rstrip("\r\n") == ": heartbeat"
            assert response.readline().decode("utf-8").rstrip("\r\n") == ""
    finally:
        server.shutdown()
        server.server_close()
        server.stream.stop()
        thread.join(timeout=2)


def test_closing_sse_client_does_not_print_unhandled_socket_errors(capsys):
    app = HomeServiceApp(build_default_state())
    server = create_server(app, "127.0.0.1", 0, heartbeat_seconds=0.02)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        response = urlopen(url + "/events", timeout=2)
        read_event(response)
        response.close()
        time.sleep(0.1)

        assert "Exception occurred during processing of request" not in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()
        server.stream.stop()
        thread.join(timeout=2)


def test_consecutive_changes_are_debounced_to_one_snapshot():
    stream = LiveStateStream(HomeServiceApp(build_default_state()), debounce_seconds=0.02)
    first = stream.snapshot_for_connection()
    try:
        stream._on_ha_change("light.allowed")
        stream._on_ha_change("light.allowed")
        stream._on_ha_change("light.allowed")
        time.sleep(0.06)

        latest = stream.hub.wait_after(first.id, timeout=0)
        assert latest is not None
        assert latest.event == "snapshot"
        assert stream.hub.wait_after(latest.id, timeout=0) is None
    finally:
        stream.stop()


def test_upstream_recovery_reports_status_and_publishes_fresh_snapshot():
    stream = LiveStateStream(HomeServiceApp(build_default_state()), debounce_seconds=0.02)
    initial = stream.snapshot_for_connection()
    try:
        stream._on_ha_status("upstream_disconnected")
        disconnected = stream.hub.wait_after(initial.id, timeout=0)
        assert disconnected.event == "status"
        assert disconnected.data["state"] == "upstream_disconnected"

        stream._on_ha_status("upstream_connected")
        connected = stream.hub.wait_after(disconnected.id, timeout=0)
        assert connected.event == "status"
        assert connected.data["state"] == "upstream_connected"

        refreshed = stream.hub.wait_after(connected.id, timeout=0.5)
        assert refreshed.event == "snapshot"
        assert refreshed.data["version"] > initial.data["version"]
    finally:
        stream.stop()
