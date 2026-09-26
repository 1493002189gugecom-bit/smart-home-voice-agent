from __future__ import annotations

import json
import importlib.util
import threading
from io import BytesIO
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
from urllib.request import urlopen

_MAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "main.py"
_SPEC = importlib.util.spec_from_file_location("perception_console_main_for_tests", _MAIN_PATH)
assert _SPEC is not None and _SPEC.loader is not None
main = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(main)


class HealthResponse:
    def __init__(self, payload):
        self.body = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.body.close()

    def read(self):
        return self.body.read()


def test_monitoring_without_a_recent_frame_is_not_reported_as_usable(monkeypatch):
    monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: 10.0), raising=False)
    monkeypatch.setattr(main, "open_upstream", lambda *args, **kwargs: HealthResponse({
        "ok": True, "model_ready": True, "mode": "monitoring",
        "last_frame_at_ms": None, "error_code": None,
    }))

    status = main._service_status("vision")

    assert status["state"] == "degraded"
    assert status["error_code"] == "camera_no_frames"
    assert "画面" in status["message"]


def test_old_frame_is_degraded_but_intentionally_idle_camera_is_not(monkeypatch):
    monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: 10.0), raising=False)
    payload = {"ok": True, "model_ready": True, "mode": "monitoring", "last_frame_at_ms": 4000}
    monkeypatch.setattr(main, "open_upstream", lambda *args, **kwargs: HealthResponse(payload))

    stale = main._service_status("vision")
    assert stale["state"] == "degraded"
    assert stale["error_code"] == "camera_frame_stale"

    payload["mode"] = "idle"
    payload["last_frame_at_ms"] = None
    assert main._service_status("vision")["state"] == "up"


def test_unreachable_service_keeps_the_last_success_time(monkeypatch):
    clock = SimpleNamespace(time=lambda: 10.0)
    monkeypatch.setattr(main, "time", clock, raising=False)
    monkeypatch.setattr(main, "open_upstream", lambda *args, **kwargs: HealthResponse({"ok": True}))
    assert main._service_status("home")["last_success_at_ms"] == 10000

    def unavailable(*args, **kwargs):
        raise OSError("private system path")

    monkeypatch.setattr(main, "open_upstream", unavailable)
    clock.time = lambda: 12.0
    status = main._service_status("home")
    assert status["state"] == "down"
    assert status["last_success_at_ms"] == 10000
    assert "private system path" not in repr(status)


def test_unreachable_voice_has_actionable_message(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("private system path")

    monkeypatch.setattr(main, "open_upstream", unavailable)
    status = main._service_status("voice")
    assert status["state"] == "down"
    assert "private system path" not in status["message"]
    assert "未运行" in status["message"]


def test_ha_process_online_but_upstream_disconnected_is_degraded(monkeypatch):
    monkeypatch.setattr(main, "open_upstream", lambda *args, **kwargs: HealthResponse({
        "ok": True, "backend": "ha", "upstream_state": "upstream_disconnected",
        "upstream_last_connected_at_ms": 123,
    }))
    status = main._service_status("home")
    assert status["state"] == "degraded"
    assert status["error_code"] == "ha_upstream_unavailable"
    assert status["data"]["upstream_last_connected_at_ms"] == 123


def test_health_checks_console_without_waiting_for_other_services(monkeypatch):
    def backend_probe(_name):
        raise AssertionError("console health must not probe backends")

    monkeypatch.setattr(main, "_service_status", backend_probe)
    server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=2) as response:
            assert response.status == 200
            assert json.load(response) == {"ok": True, "service": "perception-console"}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
