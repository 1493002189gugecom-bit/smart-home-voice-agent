"""Serve the built React console and proxy approved local service routes."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from proxy import MAX_REQUEST_BODY, SERVICES, ProxyRejected, open_upstream
from device_control import ControlRejected, normalize_control, project_control_result, validate_command


APP_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = APP_ROOT / "web" / "dist"
TABLET_APK_PATH = APP_ROOT / "web" / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
_last_success_at_ms: dict[str, int] = {}
_status_lock = threading.Lock()
FRAME_STALE_AFTER_MS = 5000
TABLET_SNAPSHOT_MAX_AGE_MS = 5000
TABLET_SNAPSHOT_MAX_BYTES = 1024 * 1024
DEVICE_CONTROL_PATH = "/api/home/device/control"
DEVICE_CONTROL_MAX_BYTES = 4096


def _tablet_snapshot(payload: dict, now_ms: int) -> dict:
    """Expose only the fields used by the read-only house view."""
    if not isinstance(payload, dict):
        raise ValueError("invalid snapshot")
    generated = payload.get("generated_at_ms")
    if not isinstance(generated, int) or isinstance(generated, bool) or not -1000 <= now_ms - generated <= TABLET_SNAPSHOT_MAX_AGE_MS:
        raise ValueError("stale snapshot")
    if not all(isinstance(payload.get(key), list) for key in ("rooms", "devices", "persons")):
        raise ValueError("incomplete snapshot")

    def only(item: object, keys: tuple[str, ...]) -> dict:
        return {key: item[key] for key in keys if isinstance(item, dict) and key in item}

    rooms = [only(room, ("id", "name", "simulated_temp")) for room in payload["rooms"] if isinstance(room, dict)]
    devices = []
    for device in payload["devices"]:
        if not isinstance(device, dict):
            continue
        public = only(device, ("id", "room_id", "name", "type", "online"))
        public["state"] = only(device.get("state"), ("on", "brightness", "mode", "target_temp", "temperature", "power"))
        devices.append(public)
    persons = []
    for person in payload["persons"]:
        if not isinstance(person, dict):
            continue
        known = person.get("location_known") is True
        public = only(person, ("id", "display_name"))
        public["location_known"] = known
        public["room_id"] = person.get("room_id") if known else None
        persons.append(public)
    return {"generated_at_ms": generated, "rooms": rooms, "devices": devices, "persons": persons}


def _service_status(name: str) -> dict:
    try:
        with open_upstream(name, "GET", "/health", None, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        now_ms = int(time.time() * 1000)
        with _status_lock:
            _last_success_at_ms[name] = now_ms
        result = {"state": "up", "data": payload, "last_success_at_ms": now_ms}
        if payload.get("ok") is False:
            result.update(state="degraded", error_code=payload.get("error_code") or "service_unhealthy", message="服务需要处理")
        if name == "home" and payload.get("backend") == "ha":
            upstream = payload.get("upstream_state")
            if upstream != "upstream_connected":
                result.update(state="degraded", error_code="ha_upstream_unavailable",
                              message="家庭服务在线，但 Home Assistant 尚未连接")
        if name == "vision":
            if payload.get("model_ready") is False:
                result.update(state="degraded", error_code=payload.get("model_error") or "model_not_ready", message="视觉模型未就绪")
            elif payload.get("mode") == "error":
                result.update(state="degraded", error_code=payload.get("error_code") or "vision_error", message="视觉服务发生故障")
            elif payload.get("mode") in {"monitoring", "registering"}:
                last_frame = payload.get("last_frame_at_ms")
                if not isinstance(last_frame, (int, float)) or isinstance(last_frame, bool):
                    result.update(state="degraded", error_code="camera_no_frames", message="摄像头已启动，但尚未收到画面")
                elif now_ms - last_frame > FRAME_STALE_AFTER_MS:
                    result.update(state="degraded", error_code="camera_frame_stale", message="摄像头画面已中断，请检查连接")
        return result
    except Exception:
        with _status_lock:
            last_success = _last_success_at_ms.get(name)
        return {"state": "down", "error_code": "service_unavailable", "message": "服务未运行或尚未就绪，请检查启动器与设备连接", "last_success_at_ms": last_success}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SmartHomePerception/1"

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        if getattr(self.server, "tablet_mode", False):
            # This listener is forwarded through an authenticated HTTPS tunnel.
            # Its route boundary is independent of Host headers supplied by clients.
            if parsed.path == DEVICE_CONTROL_PATH and method == "POST":
                host = self.headers.get("Host", "")
                origin = self.headers.get("Origin")
                allowed_origins = {f"https://{host}", f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
                if (origin is not None and origin not in allowed_origins) or self.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"}:
                    self.close_connection = True
                    self._json(403, {"ok": False, "error_code": "origin_rejected", "message": "请从家庭应用中提交设备设置"})
                    return
                if parsed.query:
                    self.close_connection = True
                    self._json(400, {"ok": False, "error_code": "invalid_request", "message": "设备控制不接受地址参数"})
                    return
                self._manual_device_control()
                return
            allowed = parsed.path in {"/tablet", "/health", "/api/home/snapshot", "/tablet/app-debug.apk"} or parsed.path.startswith("/assets/")
            if method != "GET" or not allowed or self.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"}:
                self.close_connection = True
                self._json(403, {"ok": False, "error_code": "tablet_read_only", "message": "此入口仅允许家庭快照和所选设备控制"})
                return
            if parsed.path == "/api/home/snapshot":
                self._tablet_home_snapshot()
                return
            if parsed.path == "/health":
                self._json(200, {"ok": True, "service": "perception-tablet-view"})
                return
            if parsed.path == "/tablet/app-debug.apk":
                self._tablet_apk()
                return
            self._static(parsed.path)
            return
        if parsed.path.startswith("/api/"):
            host = self.headers.get("Host", "")
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://{host}", "http://127.0.0.1:5173", "http://localhost:5173"}
            fetch_site = self.headers.get("Sec-Fetch-Site")
            if host not in allowed_hosts or (origin and origin not in allowed_origins) or fetch_site not in {None, "none", "same-origin"}:
                self._json(403, {"ok": False, "error_code": "origin_rejected", "message": "local origin required"})
                return
        if parsed.path == "/health" and method == "GET":
            self._json(200, {"ok": True, "service": "perception-console"})
            return
        if parsed.path == "/api/status" and method == "GET":
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {name: pool.submit(_service_status, name) for name in SERVICES}
                services = {name: item.result() for name, item in futures.items()}
            overall = "up" if all(item["state"] == "up" for item in services.values()) else "degraded"
            self._json(200, {"ok": True, "state": overall, "services": services})
            return
        if parsed.path == DEVICE_CONTROL_PATH and method == "POST":
            if parsed.query:
                self.close_connection = True
                self._json(400, {"ok": False, "error_code": "invalid_request", "message": "设备控制不接受地址参数"})
                return
            self._manual_device_control()
            return
        if parsed.path.startswith("/api/"):
            self._proxy(method, parsed)
            return
        if method != "GET":
            self._json(405, {"ok": False, "error_code": "method_not_allowed", "message": "GET required"})
            return
        self._static(parsed.path)

    def _tablet_home_snapshot(self) -> None:
        try:
            response = open_upstream("home", "GET", "/snapshot", None, timeout=4.0)
            try:
                if response.status != 200:
                    raise ValueError("upstream unavailable")
                raw = response.read(TABLET_SNAPSHOT_MAX_BYTES + 1)
                if len(raw) > TABLET_SNAPSHOT_MAX_BYTES:
                    raise ValueError("snapshot too large")
            finally:
                response.close()
            projected = _tablet_snapshot(json.loads(raw.decode("utf-8")), int(time.time() * 1000))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError, json.JSONDecodeError):
            self._json(503, {"ok": False, "error_code": "snapshot_unavailable", "message": "家庭数据暂不可用"})
            return
        self._json(200, projected)

    def _home_json(self, path: str, timeout: float) -> dict:
        response = open_upstream("home", "GET", path, None, timeout=timeout)
        try:
            raw = response.read(TABLET_SNAPSHOT_MAX_BYTES + 1)
            if response.status != 200 or len(raw) > TABLET_SNAPSHOT_MAX_BYTES:
                raise ValueError("home unavailable")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid home response")
            return payload
        finally:
            response.close()

    def _manual_device_control(self) -> None:
        command = None
        write_attempted = False
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ControlRejected("invalid_request", "请提交完整的设备设置")
            length = int(self.headers.get("Content-Length") or 0)
            if length > DEVICE_CONTROL_MAX_BYTES:
                raise ControlRejected("body_too_large", "设备设置内容过大", 413)
            if length <= 0:
                raise ControlRejected("invalid_request", "设备设置内容为空")
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ControlRejected("unsupported_media_type", "设备设置必须使用 JSON", 415)
            self.connection.settimeout(5)
            command = validate_command(json.loads(self.rfile.read(length).decode("utf-8")))
            health = self._home_json("/health", 2.0)
            if health.get("backend") != "ha":
                raise ControlRejected("control_backend_unavailable", "请以 Home Assistant 模式启动服务后控制设备", 409)
            snapshot = _tablet_snapshot(self._home_json("/snapshot", 4.0), int(time.time() * 1000))
            route, body = normalize_control(command, snapshot)
            write_attempted = True
            try:
                response = open_upstream("home", "POST", route, json.dumps(body).encode("utf-8"), timeout=20.0)
            except urllib.error.HTTPError as exc:
                response = exc
            try:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("control response too large")
                status = response.status
                result = project_control_result(json.loads(raw.decode("utf-8")), command, status)
            finally:
                response.close()
            self._json(200 if result["status"] == "confirmed" else status if status >= 400 else 202, result)
        except ControlRejected as exc:
            self.close_connection = True
            self._json(exc.status, {"ok": False, "status": "rejected", "error_code": exc.code,
                                    "message": exc.message, "operation_id": command["operation_id"] if command else None})
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, UnicodeError):
            self.close_connection = True
            self._json(503 if command else 400, {"ok": False,
                "status": "unconfirmed" if write_attempted else "rejected",
                "error_code": "operation_unconfirmed" if write_attempted else "snapshot_unavailable" if command else "invalid_request",
                "message": "操作结果未确认，请查看最新设备状态" if write_attempted else "家庭数据暂不可用，请检查服务连接" if command else "设备设置内容无效",
                "operation_id": command["operation_id"] if command else None})

    def _tablet_apk(self) -> None:
        try:
            with TABLET_APK_PATH.open("rb") as apk:
                size = TABLET_APK_PATH.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", 'attachment; filename="app-debug.apk"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                while block := apk.read(64 * 1024):
                    self.wfile.write(block)
        except FileNotFoundError:
            self._json(404, {"ok": False, "error_code": "apk_not_built", "message": "Android APK 尚未构建"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _proxy(self, method: str, parsed) -> None:
        parts = parsed.path.split("/", 3)
        if len(parts) != 4 or not parts[2] or not parts[3]:
            self._json(404, {"ok": False, "error_code": "not_found", "message": "invalid proxy path"})
            return
        service_name = parts[2]
        suffix = "/" + parts[3]
        if parsed.query:
            suffix += "?" + parsed.query
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"ok": False, "service": service_name, "error_code": "invalid_length", "message": "invalid request length"})
            return
        if length < 0:
            self._json(400, {"ok": False, "service": service_name, "error_code": "invalid_length", "message": "invalid request length"})
            return
        if length > MAX_REQUEST_BODY:
            self._json(413, {"ok": False, "error_code": "body_too_large", "message": "request body exceeds 1 MiB"})
            return
        if method == "POST" and self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"ok": False, "service": service_name, "error_code": "unsupported_media_type", "message": "application/json required"})
            return
        body = self.rfile.read(length) if length else None
        try:
            response = open_upstream(service_name, method, suffix, body)
        except ProxyRejected as exc:
            self._json(403, {"ok": False, "service": service_name, "error_code": "proxy_rejected", "message": str(exc)})
            return
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self._bytes(exc.code, payload, exc.headers.get_content_type() or "application/json")
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._json(503, {"ok": False, "service": service_name, "error_code": "service_unavailable", "message": type(exc).__name__})
            return

        content_type = response.headers.get("Content-Type", "application/octet-stream")
        if content_type.startswith(("text/event-stream", "multipart/x-mixed-replace")):
            self.send_response(response.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    block = response.read1(4096)
                    if not block:
                        break
                    self.wfile.write(block)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                response.close()
                self.close_connection = True
            return

        try:
            payload = response.read()
            status = response.status
        finally:
            response.close()
        self._bytes(status, payload, content_type)

    def _static(self, request_path: str) -> None:
        if not DIST_ROOT.is_dir():
            self._json(503, {
                "ok": False,
                "error_code": "frontend_not_built",
                "message": "React source exists but web/dist is absent; run the documented Vite build first.",
            })
            return
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (DIST_ROOT / relative).resolve()
        if DIST_ROOT.resolve() not in candidate.parents and candidate != DIST_ROOT.resolve():
            self._json(403, {"ok": False, "error_code": "invalid_path", "message": "path rejected"})
            return
        if not candidate.is_file():
            candidate = DIST_ROOT / "index.html"
        self._bytes(200, candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")

    def _json(self, status: int, payload: dict) -> None:
        self._bytes(status, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")

    def _bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self.server, "tablet_mode", False):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--tablet-port", type=int, default=None,
                        help="optional loopback-only home view with scoped device controls for a private HTTPS tunnel")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("perception console must bind to loopback")
    if args.tablet_port is not None and (args.tablet_port == args.port or not 1 <= args.tablet_port <= 65535):
        raise SystemExit("tablet port must differ from console port and be within 1..65535")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    tablet_server = None
    tablet_worker = None
    try:
        if args.tablet_port is not None:
            tablet_server = ThreadingHTTPServer(("127.0.0.1", args.tablet_port), Handler)
            tablet_server.daemon_threads = True
            tablet_server.tablet_mode = True
            tablet_worker = threading.Thread(target=tablet_server.serve_forever, daemon=True)
            tablet_worker.start()
            print(f"perception-tablet-view listening on http://127.0.0.1:{args.tablet_port}/tablet")
    except Exception:
        server.server_close()
        raise
    print(f"perception-console listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if tablet_server is not None:
            tablet_server.shutdown()
            tablet_server.server_close()
        if tablet_worker is not None:
            tablet_worker.join(timeout=2)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
