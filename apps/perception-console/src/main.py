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


APP_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = APP_ROOT / "web" / "dist"
_last_success_at_ms: dict[str, int] = {}
_status_lock = threading.Lock()
FRAME_STALE_AFTER_MS = 5000


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
        if parsed.path.startswith("/api/"):
            self._proxy(method, parsed)
            return
        if method != "GET":
            self._json(405, {"ok": False, "error_code": "method_not_allowed", "message": "GET required"})
            return
        self._static(parsed.path)

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
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("perception console must bind to loopback")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"perception-console listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
