"""Loopback HTTP API for the vision service.

Binds to 127.0.0.1 only. Every JSON body must be an object with exactly the keys
the route expects, so a typo fails loudly instead of being silently ignored.
`/preview.jpg` and `/preview.mjpeg` are the non-JSON responses; raw frames are never written to
disk and never leave the machine.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from capture import CameraSource, enumerate_cameras
from contracts import KNOWN_PERSON_IDS, KNOWN_ROOM_IDS, ServiceMode
from pipeline import VisionRuntime

JSON_CACHE_CONTROL = "no-store"


class VisionRequestError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _require_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise VisionRequestError(422, "invalid_body", "请求体必须是 JSON 对象")
    return body


def _require_exact_keys(body: dict[str, Any], allowed: set[str]) -> None:
    if set(body) != allowed:
        raise VisionRequestError(422, "invalid_body", f"字段必须恰好是 {sorted(allowed)}")


def select_source(runtime: VisionRuntime, body: Any) -> CameraSource:
    payload = _require_object(body)
    kind = payload.get("kind")
    if kind == "device":
        _require_exact_keys(payload, {"kind", "device_id"})
        try:
            return CameraSource.device(payload.get("device_id"))
        except ValueError as exc:
            raise VisionRequestError(422, "invalid_source", str(exc)) from exc
    if kind == "url":
        _require_exact_keys(payload, {"kind", "url"})
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise VisionRequestError(422, "invalid_source", "url 不能为空")
        try:
            return CameraSource.url(url.strip())
        except ValueError as exc:
            raise VisionRequestError(422, "invalid_source", str(exc)) from exc
    raise VisionRequestError(422, "invalid_source", "kind 必须是 device 或 url")


def select_room(body: Any) -> str:
    payload = _require_object(body)
    _require_exact_keys(payload, {"room_id"})
    room_id = payload.get("room_id")
    if room_id not in KNOWN_ROOM_IDS:
        raise VisionRequestError(422, "invalid_room", "room_id 必须是客厅、卧室或厨房之一")
    return str(room_id)


def registration_person(body: Any) -> str:
    payload = _require_object(body)
    _require_exact_keys(payload, {"person_id"})
    person_id = payload.get("person_id")
    if person_id not in KNOWN_PERSON_IDS:
        raise VisionRequestError(422, "invalid_person", "person_id 必须是 dad、mom 或 child")
    return str(person_id)


class VisionApp:
    """Transport-independent routing so every rule is reviewable in one place."""

    def __init__(self, runtime: VisionRuntime) -> None:
        self.runtime = runtime

    def handle(self, method: str, path: str, body: Any) -> tuple[int, dict[str, Any]] | tuple[int, bytes, str]:
        try:
            return self._route(method, path, body)
        except VisionRequestError as exc:
            return exc.status, {"ok": False, "error_code": exc.code, "message": exc.message}

    def _route(self, method: str, path: str, body: Any):
        if method == "GET" and path == "/health":
            status = self.runtime.status()
            model_ready = bool(status["model_ready"])
            return 200, {
                "ok": True,
                "service": "vision",
                "model_ready": model_ready,
                "model_error": status["model_error"],
                "mode": status["mode"],
                "camera_room_id": status["camera_room_id"],
                "sync_state": status["sync_state"],
            }

        if method == "GET" and path == "/config":
            status = self.runtime.status()
            error_code = status["error_code"]
            if status["model_ready"]:
                model_state = "ready"
            else:
                model_state = status["model_error"] or "not_loaded"
            if error_code in {"camera_open_failed", "camera_disconnected"}:
                camera_state = error_code
            elif status["camera_id"] is not None:
                camera_state = "selected"
            else:
                camera_state = "not_selected"
            return 200, {
                "ok": True,
                "camera_id": status["camera_id"],
                "camera_room_id": status["camera_room_id"],
                "mode": status["mode"],
                "sync_state": status["sync_state"],
                "model_state": model_state,
                "camera_state": camera_state,
                "error_code": error_code,
                "message": status["message"],
                "data": self.runtime.config.public_view(),
            }

        if method == "GET" and path == "/cameras":
            try:
                cameras = enumerate_cameras()
            except ImportError:
                raise VisionRequestError(503, "opencv_missing", "未安装 opencv，无法枚举摄像头")
            return 200, {"ok": True, "data": {"cameras": cameras}}

        if method == "POST" and path == "/camera/select":
            source = select_source(self.runtime, body)
            self.runtime.select_source(source)
            return 200, {"ok": True, "camera_id": source.redacted_label, "kind": source.kind}

        if method == "POST" and path == "/room/select":
            room_id = select_room(body)
            self.runtime.select_room(room_id)
            return 200, {"ok": True, "camera_room_id": room_id}

        if method == "POST" and path == "/monitor/start":
            _require_exact_keys(_require_object(body), set())
            if self.runtime.mode == ServiceMode.MONITORING:
                return 409, {"ok": False, "error_code": "already_monitoring", "message": "当前已在监控"}
            if self.runtime.mode == ServiceMode.REGISTERING:
                return 409, {"ok": False, "error_code": "registration_active", "message": "请先结束人物注册"}
            self.runtime.start_monitoring()
            status = self.runtime.status()
            if self.runtime.mode == ServiceMode.ERROR:
                code = status["error_code"] or "monitor_unavailable"
                http_status = 503 if code in {"model_missing", "model_error", "camera_open_failed", "camera_disconnected"} else 409
                return http_status, {"ok": False, "error_code": code, "message": status["message"]}
            return 200, {"ok": True, "mode": self.runtime.mode.value}

        if method == "POST" and path == "/monitor/pause":
            _require_exact_keys(_require_object(body), set())
            if self.runtime.mode != ServiceMode.MONITORING:
                return 409, {"ok": False, "error_code": "not_monitoring", "message": "当前未在监控"}
            self.runtime.pause_monitoring()
            return 200, {"ok": True, "mode": self.runtime.mode.value}

        if method == "GET" and path == "/results":
            snapshot = self.runtime.snapshot()
            return 200, snapshot.to_dict()

        if method == "GET" and path == "/preview.jpg":
            preview = self.runtime.preview_jpeg()
            if preview is None:
                raise VisionRequestError(503, "preview_unavailable", "当前没有可用画面")
            return 200, preview, "image/jpeg"

        if method == "POST" and path == "/registration/start":
            person_id = registration_person(body)
            if self.runtime.mode == ServiceMode.REGISTERING:
                return 409, {"ok": False, "error_code": "registration_active", "message": "已有注册正在进行"}
            status = self.runtime.start_registration(person_id)
            if status is None:
                detail = self.runtime.status()
                return 503, {
                    "ok": False,
                    "error_code": detail["error_code"] or "camera_not_selected",
                    "message": detail["message"],
                }
            return 200, {"ok": True, "registration": status.to_dict()}

        if method == "GET" and path == "/registration":
            payload = self._registration_payload()
            if payload is None:
                raise VisionRequestError(404, "not_registered", "当前没有进行中的注册")
            return 200, {"ok": True, "registration": payload}

        if method == "POST" and path == "/registration/cancel":
            _require_exact_keys(_require_object(body), set())
            cancelled = self.runtime.cancel_registration()
            if cancelled is None:
                return 409, {"ok": False, "error_code": "no_registration", "message": "当前没有进行中的注册"}
            return 200, {"ok": True, "registration": cancelled.to_dict()}

        if method == "DELETE" and path.startswith("/registration/"):
            person_id = path.rsplit("/", 1)[-1]
            if person_id not in KNOWN_PERSON_IDS:
                raise VisionRequestError(422, "invalid_person", "person_id 必须是 dad、mom 或 child")
            removed = self.runtime.delete_registration(person_id)
            if not removed:
                return 404, {"ok": False, "error_code": "not_registered", "message": "该人物没有已保存的特征"}
            return 200, {"ok": True, "person_id": person_id}

        return 404, {"ok": False, "error_code": "not_found", "message": f"no route for {method} {path}"}

    def _registration_payload(self) -> dict[str, Any] | None:
        status = self.runtime.registration_status()
        return status.to_dict() if status is not None else None


class _Handler(BaseHTTPRequestHandler):
    app: VisionApp
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quiet console
        return

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise VisionRequestError(400, "invalid_json", f"请求体不是合法 JSON: {exc}") from exc

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        if method in {"POST", "DELETE"}:
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed_hosts or (origin and origin != f"http://{host}") or self.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"}:
                self._send_json(403, {"ok": False, "error_code": "origin_rejected", "message": "local origin required"})
                return
            if method == "POST" and self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._send_json(415, {"ok": False, "error_code": "unsupported_media_type", "message": "application/json required"})
                return
        try:
            body = self._read_body() if method in {"POST", "DELETE"} else {}
            result = self.app.handle(method, parsed.path, body)
        except VisionRequestError as exc:
            result = (exc.status, {"ok": False, "error_code": exc.code, "message": exc.message})
        if len(result) == 3:
            status, payload, content_type = result
            self._send_bytes(status, payload, content_type, cache_control=JSON_CACHE_CONTROL)
            return
        status, payload = result
        self._send_json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"}:
            self._send_json(403, {"ok": False, "error_code": "origin_rejected", "message": "local origin required"})
            return
        if urlparse(self.path).path == "/preview.mjpeg":
            self._stream_preview()
            return
        self._dispatch("GET")

    def _stream_preview(self) -> None:
        if self.app.runtime.preview_jpeg() is None:
            self._send_json(503, {"ok": False, "error_code": "preview_unavailable", "message": "当前没有可用画面"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        previous = None
        unavailable_since = None
        try:
            while True:
                frame = self.app.runtime.preview_jpeg()
                if frame is None:
                    unavailable_since = unavailable_since or time.monotonic()
                    if time.monotonic() - unavailable_since >= 2:
                        break
                else:
                    unavailable_since = None
                    if frame != previous:
                        header = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode("ascii") + b"\r\n\r\n"
                        self.wfile.write(header + frame + b"\r\n")
                        self.wfile.flush()
                        previous = frame
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.close_connection = True

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, data, "application/json; charset=utf-8", cache_control=JSON_CACHE_CONTROL)

    def _send_bytes(self, status: int, data: bytes, content_type: str, *, cache_control: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(data)


class _VisionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(runtime: VisionRuntime, host: str, port: int) -> _VisionHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("vision-service refuses to bind anywhere except 127.0.0.1")
    app = VisionApp(runtime)
    handler = type("BoundVisionHandler", (_Handler,), {"app": app})
    return _VisionHTTPServer((host, port), handler)
