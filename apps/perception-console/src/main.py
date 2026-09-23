"""Serve the built React console and proxy approved local service routes."""

from __future__ import annotations

import argparse
import json
import mimetypes
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from proxy import MAX_REQUEST_BODY, SERVICES, ProxyRejected, open_upstream


APP_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = APP_ROOT / "web" / "dist"


def _service_status(name: str) -> dict:
    try:
        with open_upstream(name, "GET", "/health", None, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"state": "up" if payload.get("ok", True) else "degraded", "data": payload}
    except Exception as exc:
        return {"state": "down", "error_code": "service_unavailable", "message": type(exc).__name__}


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
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST_BODY:
            self._json(413, {"ok": False, "error_code": "body_too_large", "message": "request body exceeds 1 MiB"})
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
        if content_type.startswith("text/event-stream"):
            self.send_response(response.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    block = response.read(4096)
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

