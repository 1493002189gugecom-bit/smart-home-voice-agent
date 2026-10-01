"""The optional tablet listener only exposes the home view and its snapshot."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


_MAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "main.py"
_SPEC = importlib.util.spec_from_file_location("perception_console_tablet_for_tests", _MAIN_PATH)
assert _SPEC is not None and _SPEC.loader is not None
main = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(main)


class SnapshotResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self, _limit=-1):
        return self.payload

    def close(self):
        pass


def test_tablet_listener_is_read_only_even_with_spoofed_local_host(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("tablet view", encoding="utf-8")
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    monkeypatch.setattr(main, "DIST_ROOT", dist)
    apk = tmp_path / "app-debug.apk"
    monkeypatch.setattr(main, "TABLET_APK_PATH", apk)
    upstream_calls = []
    upstream_payload = {
        "generated_at_ms": int(time.time() * 1000),
        "rooms": [{"id": "bedroom", "name": "卧室", "simulated_temp": 25.5, "secret": "room-private"}],
        "devices": [{"id": "bedroom_ac", "room_id": "bedroom", "name": "空调", "type": "ac",
                     "online": True, "state": {"on": True, "mode": "cool", "target_temp": 26, "token": "private"},
                     "entity_id": "private-id"}],
        "persons": [{"id": "dad", "display_name": "爸爸", "room_id": "bedroom", "location_known": True,
                     "aliases": ["private-alias"], "camera_id": "private-camera", "track_id": "private-track", "x": 0.3}],
        "broadcasts": [{"message": "private-message"}],
    }

    def upstream(*args, **kwargs):
        upstream_calls.append(args)
        return SnapshotResponse(upstream_payload)

    monkeypatch.setattr(main, "open_upstream", upstream)
    server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
    server.daemon_threads = True
    server.tablet_mode = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/tablet", timeout=2) as response:
            assert response.read() == b"tablet view"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        with urlopen(base + "/assets/app.js", timeout=2) as response:
            assert response.read() == b"export {};"
        with urlopen(base + "/api/home/snapshot", timeout=2) as response:
            projected = json.load(response)
            assert projected["rooms"] == [{"id": "bedroom", "name": "卧室", "simulated_temp": 25.5}]
            assert projected["devices"][0]["state"] == {"on": True, "mode": "cool", "target_temp": 26}
            assert projected["persons"] == [{"id": "dad", "display_name": "爸爸", "room_id": "bedroom", "location_known": True}]
            assert "private" not in json.dumps(projected)
        assert upstream_calls == [("home", "GET", "/snapshot", None)]

        try:
            urlopen(base + "/tablet/app-debug.apk", timeout=2)
            assert False, "missing APK was accepted"
        except HTTPError as exc:
            assert exc.code == 404
        apk.write_bytes(b"test-apk-data")
        with urlopen(base + "/tablet/app-debug.apk", timeout=2) as response:
            assert response.read() == b"test-apk-data"
            assert response.headers["Content-Type"] == "application/vnd.android.package-archive"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Content-Disposition"] == 'attachment; filename="app-debug.apk"'

        for path, method in [
            ("/", "GET"), ("/api/status", "GET"), ("/api/home/persons", "GET"),
            ("/api/vision/preview.jpg", "GET"), ("/api/home/persons", "POST"),
            ("/tablet/app-debug.apk", "POST"),
            ("/tablet/%2e%2e/app-debug.apk", "GET"),
        ]:
            request = Request(base + path, method=method, headers={"Host": "localhost:8770"})
            try:
                urlopen(request, timeout=2)
                assert False, f"tablet route unexpectedly allowed: {method} {path}"
            except HTTPError as exc:
                assert exc.code == 403
                assert json.load(exc)["error_code"] == "tablet_read_only"
        assert len(upstream_calls) == 1

        upstream_payload["generated_at_ms"] = int(time.time() * 1000) - main.TABLET_SNAPSHOT_MAX_AGE_MS - 1000
        try:
            urlopen(base + "/api/home/snapshot", timeout=2)
            assert False, "stale snapshot was accepted"
        except HTTPError as exc:
            assert exc.code == 503
            assert json.load(exc)["error_code"] == "snapshot_unavailable"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
