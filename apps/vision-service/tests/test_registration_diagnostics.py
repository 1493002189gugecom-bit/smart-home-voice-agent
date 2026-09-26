import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import pipeline  # noqa: E402
from contracts import ServiceMode  # noqa: E402


def test_registration_stages_are_measured_without_frame_contents(monkeypatch):
    events = []
    monkeypatch.setattr(pipeline, "DIAGNOSTICS", SimpleNamespace(
        emit=lambda stage, **fields: events.append((stage, fields))))
    runtime = pipeline.VisionRuntime.__new__(pipeline.VisionRuntime)
    runtime.mode = ServiceMode.REGISTERING
    runtime.capture = SimpleNamespace(session_id="vision-test")
    runtime.registration = SimpleNamespace(accept=Mock(), finished=lambda: False)
    runtime._render_preview = lambda frame, tracks: b"preview"
    runtime._published = SimpleNamespace(snapshot=None, preview_jpeg=None)
    runtime._lock = threading.RLock()
    runtime.faces = SimpleNamespace(analyze=lambda frame: [])
    frame = SimpleNamespace(frame_bgr=object(), captured_at_ms=123)

    runtime._process_registration(frame)

    assert {stage for stage, _ in events} == {
        "registration_preview", "registration_face", "registration_accept", "registration_frame"
    }
    assert all(fields["observation_id"] == "vision-test:123" for _, fields in events)
    assert all("frame_bgr" not in repr(fields) for _, fields in events)
