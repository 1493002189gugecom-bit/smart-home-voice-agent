"""Fault handling tests use fake queues and never open a camera or model."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capture  # noqa: E402
from capture import CameraSource, CaptureSession  # noqa: E402
from contracts import ServiceMode  # noqa: E402
from pipeline import VisionRuntime, _failure_location  # noqa: E402


class FakeFrameQueue:
    def __init__(self, values):
        self.values = list(values)

    def take(self, timeout=None):
        if self.values:
            return self.values.pop(0)
        return None


class RuntimeFailureHandlingTest(unittest.TestCase):
    def test_failure_diagnostic_contains_location_but_not_exception_text(self):
        try:
            raise RuntimeError("private frame payload and embedding")
        except RuntimeError as error:
            diagnostic = _failure_location(error)

        self.assertEqual(diagnostic["type"], "RuntimeError")
        self.assertEqual(diagnostic["module"], "external")
        self.assertNotIn("private", repr(diagnostic))
        self.assertNotIn("embedding", repr(diagnostic))
        self.assertNotIn("filename", diagnostic)

    def test_only_three_consecutive_frame_failures_stop_capture(self):
        runtime = VisionRuntime.__new__(VisionRuntime)
        runtime._stop = threading.Event()
        runtime._lock = threading.RLock()
        runtime._last_failure = None
        runtime.mode = ServiceMode.MONITORING
        runtime.error_code = None
        runtime.message = None
        runtime.capture = SimpleNamespace(
            session_id="session-1",
            latest_frame=FakeFrameQueue([object() for _ in range(6)]),
        )
        runtime.sync = SimpleNamespace(mark_not_monitoring=Mock())
        runtime._offline_current = Mock()
        runtime._abort_registration = Mock()
        stop = Mock(side_effect=lambda reason: runtime._stop.set())
        runtime.capture.stop = stop

        def set_mode(mode, error_code=None):
            runtime.mode = mode
            runtime.error_code = error_code

        runtime._set_mode = set_mode
        failures = [
            RuntimeError("first"), RuntimeError("second"), None,
            RuntimeError("fourth"), RuntimeError("fifth"), RuntimeError("sixth"),
        ]
        process = Mock(side_effect=failures)

        with patch.object(runtime, "_process", process):
            runtime._run()

        self.assertEqual(process.call_count, 6)
        stop.assert_called_once_with("frame_processing_error")
        runtime._offline_current.assert_called_once_with("frame_processing_error")
        runtime._abort_registration.assert_called_once_with("frame_processing_error")
        runtime.sync.mark_not_monitoring.assert_called_once_with()
        self.assertEqual(runtime.mode, ServiceMode.ERROR)
        self.assertEqual(runtime.error_code, "frame_processing_error")
        self.assertNotIn("first", repr(runtime._last_failure))


class CameraEnumerationSafetyTest(unittest.TestCase):
    def test_active_capture_does_not_probe_native_camera_indices(self):
        session = CaptureSession(lambda *_: None)
        session.source = CameraSource.device(0)
        session._thread = SimpleNamespace(is_alive=lambda: True)

        with patch.object(capture, "enumerate_cameras") as enumerate_mock:
            cameras, paused = session.list_cameras()

        enumerate_mock.assert_not_called()
        self.assertTrue(paused)
        self.assertEqual(cameras[0]["device_id"], "0")
        self.assertIn("使用中", cameras[0]["label"])

    def test_idle_capture_delegates_to_camera_enumerator(self):
        session = CaptureSession(lambda *_: None)
        expected = [{"kind": "device", "device_id": "2", "label": "摄像头 2"}]

        with patch.object(capture, "enumerate_cameras", return_value=expected) as enumerate_mock:
            cameras, paused = session.list_cameras()

        enumerate_mock.assert_called_once_with()
        self.assertFalse(paused)
        self.assertEqual(cameras, expected)


if __name__ == "__main__":
    unittest.main()
