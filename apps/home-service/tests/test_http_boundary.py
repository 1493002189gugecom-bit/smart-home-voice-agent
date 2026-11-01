"""Real HTTP validation against isolated in-memory devices; no live services."""
import http.client
import json
import socket
import threading
from pathlib import Path

import pytest

from server import HomeServiceApp, create_server
from state import build_default_state


TOKEN = "a" * 64
PAYLOAD = json.dumps({"device_id": "living_room_light", "on": True,
                      "brightness": 61, "operation_id": "boundary-test"}).encode()


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    token_path = tmp_path / "write.token"
    token_path.write_text(TOKEN, encoding="ascii")
    monkeypatch.setenv("HOME_SERVICE_WRITE_TOKEN_FILE", str(token_path))
    app = HomeServiceApp(build_default_state())
    writes = []
    handle = app.handle

    def observe(method, *args, **kwargs):
        if method == "POST":
            writes.append(args)
        return handle(method, *args, **kwargs)

    app.handle = observe
    server = create_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], writes
    finally:
        server.shutdown()
        server.server_close()
        server.stream.stop()
        thread.join(timeout=2)


def post(endpoint, *, headers=None, body=PAYLOAD):
    port, _ = endpoint
    actual = {"Content-Type": "application/json", "X-Home-Token": TOKEN}
    actual.update(headers or {})
    actual = {key: value for key, value in actual.items() if value is not None}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("POST", "/tool/set_light", body, actual)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "https://example.invalid"}, 403),
    ({"Origin": "null"}, 403),
    ({"Origin": "http://localhost:8770"}, 403),
    ({"Host": "example.invalid"}, 403),
    ({"Host": "127.0.0.1:1"}, 403),
    ({"Content-Type": "text/plain"}, 415),
    ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
    ({"Content-Type": None}, 415),
    ({"X-Home-Token": None}, 401),
    ({"X-Home-Token": "wrong-token"}, 401),
    ({"Content-Length": "-1"}, 400),
    ({"Content-Length": "invalid"}, 400),
    ({"Content-Length": "1048577"}, 413),
    ({"Transfer-Encoding": "chunked"}, 400),
])
def test_untrusted_or_malformed_request_never_reaches_devices(endpoint, headers, status):
    actual, response = post(endpoint, headers=headers)
    assert actual == status
    assert response["ok"] is False
    assert endpoint[1] == []


@pytest.mark.parametrize("body", [b"[]", b"null", b"123", b'"text"', b"{", b"\xff", b"",
    PAYLOAD[:-1] + b',"ignored":NaN}', PAYLOAD[:-1] + b',"ignored":Infinity}',
    PAYLOAD[:-1] + b',"ignored":-Infinity}', PAYLOAD[:-1] + b',"ignored":1e999}'])
def test_only_utf8_json_objects_reach_devices(endpoint, body):
    status, response = post(endpoint, body=body)
    assert status == 400 and response["ok"] is False
    assert endpoint[1] == []


@pytest.mark.parametrize("origin", [None, "same_origin"])
def test_authorized_local_write_updates_memory_state(endpoint, origin):
    headers = {} if origin is None else {"Origin": f"http://127.0.0.1:{endpoint[0]}"}
    status, response = post(endpoint, headers=headers)
    assert status == 200 and response["ok"] is True
    assert len(endpoint[1]) == 1
    connection = http.client.HTTPConnection("127.0.0.1", endpoint[0], timeout=2)
    try:
        connection.request("GET", "/snapshot")
        state = json.loads(connection.getresponse().read())
        light = next(d for d in state["devices"] if d["id"] == "living_room_light")
        assert light["state"] == {"on": True, "brightness": 61}
    finally:
        connection.close()


def test_duplicate_lengths_are_rejected_before_dispatch(endpoint):
    with socket.create_connection(("127.0.0.1", endpoint[0]), timeout=2) as connection:
        connection.sendall((f"POST /tool/set_light HTTP/1.1\r\nHost: 127.0.0.1:{endpoint[0]}\r\n"
                            f"Content-Type: application/json\r\nX-Home-Token: {TOKEN}\r\n"
                            "Content-Length: 2\r\nContent-Length: 3\r\n\r\n{}").encode())
        response = connection.recv(2048)
    assert b" 400 " in response
    assert endpoint[1] == []


@pytest.mark.parametrize("client", ["voice", "console", "acceptance"])
def test_local_clients_send_write_credential_without_exposing_it(endpoint, monkeypatch, client):
    root = Path(__file__).resolve().parents[3]
    base = f"http://127.0.0.1:{endpoint[0]}"
    if client == "voice":
        monkeypatch.syspath_prepend(str(root / "apps/voice-service/src"))
        from agent_tools import HomeToolExecutor
        executor = HomeToolExecutor(base)
        status, response = executor._request("POST", "/tool/set_light", None, json.loads(PAYLOAD))
    elif client == "console":
        monkeypatch.syspath_prepend(str(root / "apps/perception-console/src"))
        import proxy
        monkeypatch.setitem(proxy.SERVICES, "home", proxy.Service("home", base, proxy.SERVICES["home"].allowed))
        with proxy.open_upstream("home", "POST", "/tool/set_light", PAYLOAD) as upstream:
            status, response = upstream.status, json.load(upstream)
    else:
        monkeypatch.syspath_prepend(str(root / "tools/ha-check"))
        from acceptance import service_request
        status, response = service_request(base, "POST", "/tool/set_light", json.loads(PAYLOAD))
    assert status == 200 and response["ok"] is True
    assert TOKEN not in json.dumps(response)
    assert len(endpoint[1]) == 1


def test_incomplete_body_times_out_without_dispatch(endpoint, monkeypatch):
    import server
    monkeypatch.setattr(server, "BODY_TIMEOUT_SECONDS", 0.05)
    with socket.create_connection(("127.0.0.1", endpoint[0]), timeout=2) as connection:
        connection.sendall((f"POST /tool/set_light HTTP/1.1\r\nHost: 127.0.0.1:{endpoint[0]}\r\n"
                            f"Content-Type: application/json\r\nX-Home-Token: {TOKEN}\r\n"
                            "Content-Length: 100\r\n\r\n{").encode())
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status == 408
        assert json.loads(response.read())["error_code"] == "body_timeout"
    assert endpoint[1] == []


def test_vision_token_does_not_authorize_device_control(endpoint):
    status, _ = post(endpoint, headers={"X-Home-Token": None, "X-Vision-Token": TOKEN})
    assert status == 401
    assert endpoint[1] == []


def test_health_exposes_actual_memory_backend_for_startup_validation(endpoint):
    connection = http.client.HTTPConnection("127.0.0.1", endpoint[0], timeout=2)
    try:
        connection.request("GET", "/health")
        response = json.loads(connection.getresponse().read())
        assert response["backend"] == "memory"
        assert response["write_auth"] == "required"
        assert TOKEN not in json.dumps(response)
    finally:
        connection.close()
