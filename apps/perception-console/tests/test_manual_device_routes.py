import importlib.util
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


spec = importlib.util.spec_from_file_location("manual_device_console_test", Path(__file__).resolve().parents[1] / "src/main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


class Response:
    def __init__(self, value, status=200):
        self.status, self.value = status, json.dumps(value).encode()

    def read(self, _limit=-1):
        return self.value

    def close(self):
        pass


class Home:
    backend = "ha"
    online = True
    age = 0
    fail_write = False
    status = "confirmed"

    def __init__(self):
        self.writes = []

    def open(self, _service, method, route, body, **_kwargs):
        if route == "/health":
            return Response({"ok": True, "backend": self.backend})
        if route == "/snapshot":
            return Response({"generated_at_ms": int(time.time() * 1000) - self.age, "rooms": [], "persons": [],
                             "devices": [{"id": "bedroom_ac", "type": "ac", "online": self.online}]})
        assert method == "POST" and route == "/tool/set_ac"
        payload = json.loads(body)
        self.writes.append(payload)
        if self.fail_write:
            raise TimeoutError("private backend details")
        return Response({"ok": self.status == "confirmed", "status": self.status,
                         "operation_id": payload["operation_id"], "phrase": "卧室空调已关闭",
                         "error_code": "confirmation_timeout" if self.status == "unconfirmed" else None,
                         "data": {"device": "bedroom_ac", "state": {"on": False}}},
                        200 if self.status == "confirmed" else 202)


@pytest.fixture(params=[False, True], ids=["desktop", "tablet"])
def endpoint(request, monkeypatch):
    home = Home()
    monkeypatch.setattr(main, "open_upstream", home.open)
    server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
    server.daemon_threads = True
    server.tablet_mode = request.param
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", home
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def send(base, payload=None, *, path="/api/home/device/control", method="POST", headers=None):
    payload = payload if payload is not None else {"device_id": "bedroom_ac", "operation_id": "ui-test-operation-5678", "changes": {"on": False}}
    request = Request(base + path, data=json.dumps(payload).encode() if method == "POST" else None,
                      method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def test_desktop_and_tablet_forward_one_device_and_preserve_retry_id(endpoint):
    base, home = endpoint
    for _ in range(2):
        status, result = send(base, headers={"Origin": base, "Sec-Fetch-Site": "same-origin"})
        assert status == 200 and result["status"] == "confirmed"
    assert len(home.writes) == 2
    assert home.writes[0] == home.writes[1] == {"device_id": "bedroom_ac", "operation_id": "ui-test-operation-5678", "on": False}


def test_invalid_body_stale_offline_and_wrong_backend_have_zero_writes(endpoint):
    base, home = endpoint
    assert send(base, {"device_id": "bedroom_ac", "operation_id": "ui-test-operation-5678", "changes": {"target_temp": 99}})[0] == 400
    home.age = 10000
    assert send(base)[0] == 503
    home.age, home.online = 0, False
    assert send(base)[0] == 409
    home.online, home.backend = True, "memory"
    assert send(base)[0] == 409
    assert home.writes == []


def test_foreign_origin_and_cross_site_requests_are_rejected(endpoint):
    base, home = endpoint
    assert send(base, headers={"Origin": "https://outside.example"})[0] == 403
    assert send(base, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert home.writes == []


def test_body_size_and_content_type_are_enforced(endpoint):
    base, home = endpoint
    assert send(base, {"padding": "x" * 5000})[0] == 413
    assert send(base, headers={"Content-Type": "text/plain"})[0] == 415
    assert home.writes == []


def test_timeout_and_unconfirmed_never_claim_success(endpoint):
    base, home = endpoint
    home.fail_write = True
    status, result = send(base)
    assert status == 503 and result["status"] == "unconfirmed" and result["ok"] is False
    assert "private" not in json.dumps(result)
    home.fail_write, home.status = False, "unconfirmed"
    status, result = send(base)
    assert status == 202 and result["status"] == "unconfirmed" and result["ok"] is False


def test_tablet_still_refuses_other_writes_and_raw_control_tools(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
    server.tablet_mode = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        for path in ("/api/home/persons", "/api/home/tool/set_ac", "/api/vision/registration/start", "/api/voice/devices/select"):
            assert send(base, path=path)[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
