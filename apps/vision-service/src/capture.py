"""Single-owner camera capture with URL redaction and latest-frame delivery."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit

from latest import LatestValue


_PREFERRED_DEVICE_WIDTH = 1280
_PREFERRED_DEVICE_HEIGHT = 720
_PREFERRED_DEVICE_FPS = 30
_STOP_JOIN_TIMEOUT_SECONDS = 2.0


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


# Enumerating opens every camera index in turn. A Windows access violation was
# observed inside OpenCV/DSHOW when two scans ran at once (the crash dump showed
# several threads inside this function), so scans are serialized here and the
# caller can exclude an index that the capture thread already owns.
_ENUMERATION_LOCK = threading.Lock()


def enumerate_cameras(
    max_index: int = 8, skip_indices: Iterable[int] = ()
) -> list[dict[str, int | str]]:
    import cv2

    try:
        skipped = {int(item) for item in skip_indices}
    except (TypeError, ValueError):
        skipped = set()
    found: list[dict[str, int | str]] = []
    with _ENUMERATION_LOCK:
        for index in range(max(0, max_index)):
            if index in skipped:
                continue
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
        self._control_lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture = None
        self.source: CameraSource | None = None
        self.session_id: str | None = None
        self.latest_frame: LatestValue[CapturedFrame] = LatestValue()
        self.last_frame_at_ms: int | None = None
        self.actual_mode: dict[str, int | float | None] | None = None
        self._frame_times: deque[float] = deque(maxlen=30)

    def note_frame(self, frame, captured_at_ms: int) -> None:
        """Record actual received dimensions and a rolling observed frame rate."""
        height, width = frame.shape[:2]
        self._frame_times.append(time.monotonic())
        span = self._frame_times[-1] - self._frame_times[0]
        fps = round((len(self._frame_times) - 1) / span, 1) if span > 0 else None
        self.actual_mode = {"width": int(width), "height": int(height), "fps": fps}
        self.last_frame_at_ms = captured_at_ms

    def list_cameras(self) -> tuple[list[dict[str, int | str]], bool]:
        """Scan only while no native capture owner can be reading or opening."""
        with self._control_lock:
            with self._lock:
                active = self._thread is not None and self._thread.is_alive()
                source = self.source
            if active:
                if source is not None and source.kind == "device":
                    index = str(source.source_id)
                    return ([{"kind": "device", "device_id": index,
                              "label": f"摄像头 {index}（使用中）",
                              "width": 0, "height": 0, "fps": 0}], True)
                return ([], True)
            return (enumerate_cameras(), False)

    def start(self, source: CameraSource) -> str | None:
        with self._control_lock:
            self.stop("source_changed")
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    # A driver can block in read(). Never reopen the camera or
                    # replace its stop token while its old owner is still alive.
                    self.status_callback("camera_open_failed", source.redacted_label)
                    return None
                self.source = source
                self.session_id = f"vision-{uuid.uuid4().hex}"
                self._stop = threading.Event()
                self.latest_frame = LatestValue()
                self.last_frame_at_ms = None
                self.actual_mode = None
                self._frame_times.clear()
                session_id = self.session_id
                self._thread = threading.Thread(
                    target=self._run,
                    args=(source, session_id, self._stop, self.latest_frame),
                    name="vision-capture", daemon=True,
                )
                self._thread.start()
                return session_id

    def stop(self, reason: str = "stopped") -> None:
        with self._control_lock:
            with self._lock:
                thread = self._thread
                self._stop.set()
                self.latest_frame.close()
            # Only the capture owner may release the native object. Releasing
            # from an HTTP/pipeline thread can corrupt memory during read().
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
            if reason == "source_changed":
                # Clear after the old worker has had a chance to finish a frame.
                # A late native read checks the stop token before recording it.
                self.actual_mode = None
                self.last_frame_at_ms = None
                self._frame_times.clear()
            if thread is not None:
                self.status_callback(reason, None)

    def _run(
        self, source: CameraSource, session_id: str,
        stop: threading.Event, latest_frame: LatestValue[CapturedFrame],
    ) -> None:
        import cv2

        capture = None
        try:
            capture = cv2.VideoCapture(source.source_id)
            if source.kind == "device" and capture.isOpened():
                _configure_device_capture(capture, cv2)
            with self._lock:
                if self.session_id != session_id or stop.is_set():
                    return
                self._capture = capture
            if not capture.isOpened():
                self.status_callback("camera_open_failed", source.redacted_label)
                return
            self.status_callback("camera_connected", source.redacted_label)
            failures = 0
            while not stop.is_set():
                ok, frame = capture.read()
                if stop.is_set():
                    break
                if not ok or frame is None:
                    failures += 1
                    if failures >= 5:
                        self.status_callback("camera_disconnected", source.redacted_label)
                        break
                    time.sleep(0.05)
                    continue
                failures = 0
                height, width = frame.shape[:2]
                captured_at_ms = int(time.time() * 1000)
                self.note_frame(frame, captured_at_ms)
                latest_frame.put(CapturedFrame(
                    frame_bgr=frame, captured_at_ms=captured_at_ms,
                    width=int(width), height=int(height),
                ))
        finally:
            if capture is not None:
                capture.release()
            latest_frame.close()
            with self._lock:
                if self.session_id == session_id:
                    self._capture = None
                    self._thread = None
