"""Camera mode is measured from received frames; no device is opened here."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capture
from capture import CameraSource, CaptureSession


def test_actual_mode_uses_frame_dimensions_and_measured_rate(monkeypatch):
    ticks = iter((10.0, 10.04, 10.08))
    monkeypatch.setattr(capture.time, "monotonic", lambda: next(ticks))
    session = CaptureSession(lambda *args: None)
    frame = SimpleNamespace(shape=(720, 1280, 3))

    session.note_frame(frame, 10000)
    assert session.actual_mode == {"width": 1280, "height": 720, "fps": None}
    session.note_frame(frame, 10040)
    session.note_frame(frame, 10080)
    assert session.actual_mode == {"width": 1280, "height": 720, "fps": 25.0}


def test_new_camera_session_does_not_inherit_old_mode(monkeypatch):
    session = CaptureSession(lambda *args: None)
    session.actual_mode = {"width": 640, "height": 480, "fps": 30.0}
    monkeypatch.setattr(session, "stop", lambda *args: None)
    monkeypatch.setattr(capture.threading.Thread, "start", lambda self: None)

    session.start(CameraSource.device(0))

    assert session.actual_mode is None


def test_changing_source_hides_previous_camera_mode():
    session = CaptureSession(lambda *args: None)
    session.actual_mode = {"width": 640, "height": 480, "fps": 30.0}
    session.last_frame_at_ms = 10000

    session.stop("source_changed")

    assert session.actual_mode is None
    assert session.last_frame_at_ms is None
