"""Single-owner camera capture with URL redaction and latest-frame delivery."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from latest import LatestValue


_PREFERRED_DEVICE_WIDTH = 1280
_PREFERRED_DEVICE_HEIGHT = 720
_PREFERRED_DEVICE_FPS = 30


def _configure_device_capture(capture, cv2) -> None:
    """Request a high-quality low-latency profile and accept device clamping.

    OpenCV cannot enumerate every Windows camera mode portably.  Asking for a
    common 720p/30 profile lets the backend negotiate the best supported mode
    instead of silently keeping its usual 640x480 default.
    """

    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, _PREFERRED_DEVICE_WIDTH)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, _PREFERRED_DEVICE_HEIGHT)
    capture.set(cv2.CAP_PROP_FPS, _PREFERRED_DEVICE_FPS)


@dataclass(frozen=True)
class CameraSource:
    kind: str
    source_id: int | str
    redacted_label: str

    @classmethod
    def device(cls, device_id: str | int) -> "CameraSource":
        try:
            index = int(device_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("device_id must be an integer") from exc
        if index < 0:
            raise ValueError("device_id must be non-negative")
        return cls("device", index, f"device:{index}")

    @classmethod
    def url(cls, value: str) -> "CameraSource":
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "rtsp"} or not parsed.hostname:
            raise ValueError("camera URL must use http, https, or rtsp")
        fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        label = f"{parsed.scheme}://{parsed.hostname}/…#{fingerprint}"
        return cls("url", value, label)

    @property
    def camera_id(self) -> str:
        return self.redacted_label


@dataclass(frozen=True)
class CapturedFrame:
    frame_bgr: object
    captured_at_ms: int
    width: int
    height: int


def enumerate_cameras(max_index: int = 8) -> list[dict[str, int | str]]:
    import cv2

    found: list[dict[str, int | str]] = []
    for index in range(max(0, max_index)):
        capture = cv2.VideoCapture(index)
        try:
            if capture.isOpened():
                _configure_device_capture(capture, cv2)
                found.append({
                    "kind": "device", "device_id": str(index),
                    "label": f"摄像头 {index}",
                    "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                    "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                    "fps": round(float(capture.get(cv2.CAP_PROP_FPS) or 0), 2),
                })
        finally:
            capture.release()
    return found


class CaptureSession:
    def __init__(self, status_callback: Callable[[str, str | None], None]) -> None:
        self.status_callback = status_callback
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture = None
        self.source: CameraSource | None = None
        self.session_id: str | None = None
        self.latest_frame: LatestValue[CapturedFrame] = LatestValue()

    def start(self, source: CameraSource) -> str:
        self.stop("source_changed")
        with self._lock:
            self.source = source
            self.session_id = f"vision-{uuid.uuid4().hex}"
            self._stop = threading.Event()
            self.latest_frame = LatestValue()
            session_id = self.session_id
            self._thread = threading.Thread(
                target=self._run, args=(source, session_id),
                name="vision-capture", daemon=True,
            )
            self._thread.start()
            return session_id

    def stop(self, reason: str = "stopped") -> None:
        with self._lock:
            thread = self._thread
            capture = self._capture
            self._stop.set()
            self._thread = None
            self._capture = None
        if capture is not None:
            capture.release()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.latest_frame.close()
        if thread is not None:
            self.status_callback(reason, None)

    def _run(self, source: CameraSource, session_id: str) -> None:
        import cv2

        capture = cv2.VideoCapture(source.source_id)
        if source.kind == "device" and capture.isOpened():
            _configure_device_capture(capture, cv2)
        with self._lock:
            if self.session_id != session_id or self._stop.is_set():
                capture.release()
                return
            self._capture = capture
        if not capture.isOpened():
            capture.release()
            self.latest_frame.close()
            self.status_callback("camera_open_failed", source.redacted_label)
            return
        self.status_callback("camera_connected", source.redacted_label)
        failures = 0
        try:
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    failures += 1
                    if failures >= 5:
                        self.status_callback("camera_disconnected", source.redacted_label)
                        break
                    time.sleep(0.05)
                    continue
                failures = 0
                height, width = frame.shape[:2]
                self.latest_frame.put(CapturedFrame(
                    frame_bgr=frame, captured_at_ms=int(time.time() * 1000),
                    width=int(width), height=int(height),
                ))
        finally:
            capture.release()
            self.latest_frame.close()
            with self._lock:
                if self.session_id == session_id:
                    self._capture = None
