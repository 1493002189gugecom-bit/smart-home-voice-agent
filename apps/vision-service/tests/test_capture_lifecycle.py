"""The capture owner must finish read() before releasing the native camera."""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from capture import CameraSource, CaptureSession


class BlockingCamera:
    def __init__(self):
        self.read_entered = threading.Event()
        self.finish_read = threading.Event()
        self.released = threading.Event()
        self.release_during_read = False
        self.reading = False
        self.read_thread = None
        self.release_threads = []

    def isOpened(self):
        return True

    def read(self):
        self.read_thread = threading.get_ident()
        self.reading = True
        self.read_entered.set()
        self.finish_read.wait(5)
        self.reading = False
        return False, None

    def release(self):
        self.release_during_read |= self.reading
        self.release_threads.append(threading.get_ident())
        self.released.set()


class CaptureLifecycleTest(unittest.TestCase):
    def test_blocked_old_reader_prevents_reopening_and_keeps_its_stop_token(self):
        camera = BlockingCamera()
        events = []
        module = SimpleNamespace(VideoCapture=lambda source: camera)
        session = CaptureSession(lambda state, source: events.append(state))
        with patch.dict(sys.modules, {"cv2": module}), patch("capture._STOP_JOIN_TIMEOUT_SECONDS", .02):
            first_id = session.start(CameraSource.url("http://127.0.0.1/first"))
            self.assertTrue(camera.read_entered.wait(1))
            first_stop = session._stop
            try:
                self.assertIsNone(session.start(CameraSource.url("http://127.0.0.1/second")))
                self.assertEqual(session.session_id, first_id)
                self.assertIs(session._stop, first_stop)
                self.assertTrue(first_stop.is_set())
                self.assertFalse(camera.released.is_set())
                self.assertEqual(events[-1], "camera_open_failed")
            finally:
                camera.finish_read.set()
                self.assertTrue(camera.released.wait(1))
                session.stop()

    def test_stop_does_not_release_native_capture_while_read_is_running(self):
        camera = BlockingCamera()
        module = SimpleNamespace(VideoCapture=lambda source: camera)
        session = CaptureSession(lambda *args: None)
        with patch.dict(sys.modules, {"cv2": module}):
            session.start(CameraSource.url("http://127.0.0.1/test-camera"))
            self.assertTrue(camera.read_entered.wait(1))
            stopper = threading.Thread(target=session.stop)
            stopper.start()
            try:
                self.assertTrue(session._stop.wait(1))
                self.assertFalse(camera.released.wait(.05), "release raced with native read")
            finally:
                camera.finish_read.set()
                stopper.join(3)
                session.stop()
            self.assertFalse(camera.release_during_read)
            self.assertEqual(camera.release_threads, [camera.read_thread])
            self.assertTrue(session.latest_frame.closed)


if __name__ == "__main__":
    unittest.main()
