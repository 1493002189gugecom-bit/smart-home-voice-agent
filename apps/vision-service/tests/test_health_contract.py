"""Health checks describe model and frame readiness without opening a camera."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import ServiceMode
from pipeline import VisionRuntime
from server import VisionApp


def test_health_exposes_last_camera_frame_and_failure_reason():
    runtime = SimpleNamespace(status=lambda: {
        "model_ready": True, "model_error": None, "mode": "monitoring",
        "camera_room_id": "bedroom", "sync_state": "synced",
        "camera_id": "device:0", "last_frame_at_ms": 12345,
        "actual_mode": {"width": 1280, "height": 720, "fps": 25.0},
        "error_code": None,
    })

    code, payload = VisionApp(runtime).handle("GET", "/health", None)

    assert code == 200
    assert payload["last_frame_at_ms"] == 12345
    assert payload["camera_id"] == "device:0"
    assert payload["actual_mode"]["fps"] == 25.0
    assert payload["error_code"] is None


def test_runtime_status_uses_capture_frame_time_without_claiming_a_person():
    runtime = VisionRuntime.__new__(VisionRuntime)
    runtime._lock = threading.RLock()
    runtime.mode = ServiceMode.MONITORING
    runtime._model_ready = True
    runtime._model_error = None
    runtime._registry_error = None
    runtime.source = None
    runtime.camera_room_id = "bedroom"
    runtime.capture = SimpleNamespace(session_id="session", last_frame_at_ms=12345, actual_mode=None)
    runtime.sync = SimpleNamespace(sync_state="synced")
    runtime.error_code = None
    runtime.message = None
    runtime._last_failure = None

    status = runtime.status()

    assert status["last_frame_at_ms"] == 12345
    assert "person_id" not in status
