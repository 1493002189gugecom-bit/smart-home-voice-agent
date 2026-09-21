import json
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from server import HomeServiceApp, LiveStateStream, create_server  # noqa: E402
from state import build_default_state  # noqa: E402


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
    finally:
        server.shutdown()
        server.server_close()
        server.stream.stop()
        thread.join(timeout=2)


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

        time.sleep(0.06)
        refreshed = stream.hub.wait_after(connected.id, timeout=0)
        assert refreshed.event == "snapshot"
        assert refreshed.data["version"] > initial.data["version"]
    finally:
        stream.stop()
