"""Local HTTP interface for the home state service.

Binds to 127.0.0.1 only: this service is never exposed to the LAN. It speaks
plain JSON over HTTP; the design forbids richer payloads because broadcast text
is data, not instructions.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ha_gateway import HAGateway
from ha_event_subscriber import HAEventSubscriber
from ha_service import HAServiceApp, load_catalog, load_scenes
from event_stream import (
    EventHub,
    StreamEvent,
    canonical_ha_snapshot,
    canonical_memory_snapshot,
    encode_sse,
)
from models import VisualObservation, now_ms
from notify import plan_notification
from state import HomeState, StateError, build_default_state
from tools import TOOL_NAMES, ToolService

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Only these two variables may be read from the local env file. The file is
# git-ignored and is never logged or echoed back.
ENV_FILE_KEYS = frozenset({"HOME_ASSISTANT_URL", "HOME_ASSISTANT_TOKEN"})


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "rooms.json"


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "ha_entities.json"


def default_scenes_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "scenes.json"


def _runtime_environment() -> dict[str, str]:
    """Process environment, optionally completed from the local HA env file.

    Real values already present in the environment always win, so a caller can
    override the file without editing it.
    """
    values = dict(os.environ)
    env_file = values.get("HA_ENV_FILE")
    if env_file:
        path = Path(env_file)
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise SystemExit(f"HA_ENV_FILE is not readable: {path.name}") from exc
        for line in lines:
            key, separator, value = line.partition("=")
            if separator and key.strip() in ENV_FILE_KEYS:
                values.setdefault(key.strip(), value.strip())
    return values


def required_value(values: dict[str, str], name: str) -> str:
    value = values.get(name, "")
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{name} is required")
    return value


def build_app_from_environment(config: Path | None = None):
    """Select exactly one backend. There is no silent fallback.

    An incomplete or misspelled HA configuration must stop the service rather
    than quietly serving stale in-memory state that the Agent would treat as
    real device state.
    """
    values = _runtime_environment()
    backend = values.get("HOME_SERVICE_BACKEND", "memory")
    if backend == "memory":
        path = default_config_path() if config is None else config
        return HomeServiceApp(build_default_state(path if path.exists() else None))
    if backend != "ha":
        raise SystemExit("HOME_SERVICE_BACKEND must be memory or ha")

    gateway = HAGateway(
        required_value(values, "HOME_ASSISTANT_URL"),
        required_value(values, "HOME_ASSISTANT_TOKEN"),
    )
    catalog_path = Path(values.get("HA_ENTITY_CATALOG", str(default_catalog_path())))
    database = Path(required_value(values, "HA_OPERATION_DB"))
    catalog = load_catalog(catalog_path)
    scenes_path = Path(values.get("HA_SCENES", str(default_scenes_path())))
    # Scenes are validated against the catalog at startup, so a broken scene file
    # cannot wait until a user says "我出门了" to fail.
    scenes = load_scenes(scenes_path, catalog) if scenes_path.exists() else {}
    app = HAServiceApp(gateway, catalog, database, scenes=scenes)
    # Crash recovery must finish before the service accepts requests, so no
    # caller can observe a half-resolved operation.
    app.reconcile_unfinished()
    return app


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class HomeServiceApp:
    """Transport-independent request handling, so it can be unit tested."""

    def __init__(self, state: HomeState):
        self.state = state
        self.tools = ToolService(state)

    # ------------------------------------------------------------- routing
    def handle(self, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        def first(name: str) -> str | None:
            values = query.get(name)
            return values[0] if values else None

        if method == "GET" and path == "/health":
            return 200, {"ok": True, "version": self.state.version, "tools": list(TOOL_NAMES)}

        if method == "GET" and path == "/state":
            since = first("since")
            return 200, self.state.sync(_int_or_none(since))

        if method == "GET" and path == "/snapshot":
            return 200, self.state.snapshot()

        if method == "GET" and path == "/broadcast/head":
            task = self.state.head_broadcast()
            return 200, {"task": None if task is None else _broadcast_payload(task, self.state)}

        if method == "POST" and path == "/broadcast/start":
            task_id = str(body.get("task_id", ""))
            try:
                task = self.state.start_broadcast(task_id)
            except StateError as exc:
                return 409, {"ok": False, "error": exc.message, "code": exc.code}
            return 200, {"ok": True, "task": _broadcast_payload(task, self.state)}

        if method == "POST" and path == "/broadcast/receipt":
            try:
                task = self.state.complete_broadcast(
                    str(body.get("task_id", "")),
                    str(body.get("receipt_id", "")),
                    bool(body.get("success", False)),
                    body.get("error"),
                )
            except StateError as exc:
                return 409, {"ok": False, "error": exc.message, "code": exc.code}
            return 200, {"ok": True, "task": _broadcast_payload(task, self.state), "phrase": self.state.broadcast_phrase(task.id)}

        if method == "POST" and path == "/tool/broadcast":
            result = self.tools.broadcast_to_room(
                str(body.get("room", "")),
                str(body.get("text", "")),
                body.get("person_ids"),
                body.get("operation_id"),
            )
            return (200 if result.ok else 400), result.to_dict()

        if method == "POST" and path == "/tool/notify":
            result = plan_notification(
                self.tools,
                list(body.get("targets", [])),
                str(body.get("text", "")),
                body.get("operation_id"),
            )
            return (200 if result.ok else 400), result.to_dict()

        if method == "POST" and path == "/tool/set_light":
            result = self.tools.set_light(
                room=body.get("room"),
                device_id=body.get("device_id"),
                on=body.get("on"),
                brightness=body.get("brightness"),
                precondition_version=body.get("precondition_version"),
                operation_id=body.get("operation_id"),
            )
            return (200 if result.ok else 400), result.to_dict()

        if method == "POST" and path == "/tool/set_ac":
            result = self.tools.set_ac(
                room=body.get("room"),
                device_id=body.get("device_id"),
                on=body.get("on"),
                mode=body.get("mode"),
                target_temp=body.get("target_temp"),
                precondition_version=body.get("precondition_version"),
                operation_id=body.get("operation_id"),
            )
            return (200 if result.ok else 400), result.to_dict()

        if method == "GET" and path == "/tool/room_status":
            result = self.tools.query_room_status(first("room"))
            return (200 if result.ok else 400), result.to_dict()

        if method == "GET" and path == "/tool/person_location":
            result = self.tools.query_person_location(first("person"))
            return (200 if result.ok else 400), result.to_dict()

        if method == "GET" and path == "/tool/device_status":
            result = self.tools.query_device_status(first("device"), first("room"))
            return (200 if result.ok else 400), result.to_dict()

        # Test-panel style mutations used by the dashboard and tests.
        if method == "POST" and path == "/test/move_person":
            try:
                person = self.state.move_person(
                    str(body.get("person_id", "")),
                    str(body.get("room_id", "")),
                    body.get("x"),
                    body.get("y"),
                )
            except StateError as exc:
                return 400, {"ok": False, "error": exc.message, "code": exc.code}
            return 200, {"ok": True, "person_id": person.id, "room_id": person.room_id}

        if method == "POST" and path == "/test/set_room_temp":
            try:
                room = self.state.set_room_temp(str(body.get("room_id", "")), float(body.get("temp", 0)))
            except (StateError, TypeError, ValueError) as exc:
                message = getattr(exc, "message", str(exc))
                return 400, {"ok": False, "error": message}
            return 200, {"ok": True, "room_id": room.id, "simulated_temp": room.simulated_temp}

        if method == "POST" and path == "/test/device_online":
            try:
                device = self.state.set_device_online(str(body.get("device_id", "")), bool(body.get("online", True)))
            except StateError as exc:
                return 400, {"ok": False, "error": exc.message, "code": exc.code}
            return 200, {"ok": True, "device_id": device.id, "online": device.online}

        if method == "POST" and path == "/test/observe":
            observation = VisualObservation(
                camera_id=str(body.get("camera_id", "cam0")),
                track_id=str(body.get("track_id", "")),
                bbox=tuple(body.get("bbox", (0, 0, 0, 0))),
                confidence=float(body.get("confidence", 0.5)),
                observed_at_ms=now_ms(),
            )
            self.state.observe(observation)
            return 200, {"ok": True, "track_id": observation.track_id}

        if method == "POST" and path == "/test/bind_track":
            try:
                observation = self.state.bind_track(str(body.get("track_id", "")), str(body.get("person_id", "")))
            except StateError as exc:
                return 400, {"ok": False, "error": exc.message, "code": exc.code}
            return 200, {"ok": True, "track_id": observation.track_id, "person_id": observation.bound_person_id}

        if method == "POST" and path == "/test/lose_track":
            self.state.lose_track(str(body.get("track_id", "")))
            return 200, {"ok": True}

        if method == "POST" and path == "/test/person_location_unknown":
            try:
                person = self.state.require_person(str(body.get("person_id", "")))
            except StateError as exc:
                return 400, {"ok": False, "error": exc.message, "code": exc.code}
            person.room_id = None
            person.x = None
            person.y = None
            person.version += 1
            return 200, {"ok": True, "person_id": person.id, "location_known": person.location_known}

        return 404, {"ok": False, "error": f"no route for {method} {path}"}


def _broadcast_payload(task, state: HomeState) -> dict[str, Any]:
    """Single source of truth for how a broadcast task is serialized."""
    return state._broadcast_view(task)


class LiveStateStream:
    """Connect an app backend to a bounded, read-only stream of snapshots."""

    def __init__(self, app: Any, *, debounce_seconds: float = 0.1):
        self.app = app
        self.backend = "ha" if isinstance(app, HAServiceApp) else "memory"
        self.hub = EventHub(history_size=32)
        self._lock = threading.RLock()
        self._sequence = 0
        self._latest_snapshot: StreamEvent | None = None
        self._debounce_seconds = debounce_seconds
        self._debounce_timer: threading.Timer | None = None
        self._subscriber: HAEventSubscriber | None = None
        self._stopped = False

        self.publish_snapshot()
        if self.backend == "ha":
            entity_ids = {
                record["entity_id"] for record in app.catalog.values()
                if isinstance(record.get("entity_id"), str)
            }
            self._subscriber = HAEventSubscriber(
                app.gateway.url,
                app.gateway.token,
                entity_ids,
                self._on_ha_change,
                self._on_ha_status,
            )

    def start(self) -> None:
        if self._subscriber is not None:
            self._subscriber.start()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        if self._subscriber is not None:
            self._subscriber.stop()

    def snapshot_for_connection(self) -> StreamEvent:
        with self._lock:
            if self._latest_snapshot is None:
                return self._publish_empty_snapshot_locked()
            return self._latest_snapshot

    def publish_snapshot(self) -> StreamEvent | None:
        with self._lock:
            if self._stopped:
                return None
            self._sequence += 1
            sequence = self._sequence
            generated = now_ms()
            try:
                if self.backend == "memory":
                    payload = canonical_memory_snapshot(
                        self.app.state.snapshot(),
                        sequence=sequence,
                        generated_at_ms=generated,
                    )
                else:
                    status, response = self.app.handle("GET", "/tool/room_status", {}, {})
                    if status != 200:
                        raise RuntimeError("ha_snapshot_unavailable")
                    payload = canonical_ha_snapshot(
                        response,
                        sequence=sequence,
                        generated_at_ms=generated,
                    )
            except Exception:
                self._sequence -= 1
                self.hub.publish(
                    "status",
                    {"state": "snapshot_unavailable", "generated_at_ms": generated},
                )
                return None
            self._latest_snapshot = self.hub.publish("snapshot", payload)
            return self._latest_snapshot

    def after_request(self, method: str, status: int) -> None:
        if self.backend == "memory" and method == "POST" and 200 <= status < 300:
            self.publish_snapshot()

    def _publish_empty_snapshot_locked(self) -> StreamEvent:
        self._sequence += 1
        payload = {
            "version": self._sequence,
            "backend": self.backend,
            "generated_at_ms": now_ms(),
            "rooms": [],
            "devices": [],
            "persons": [],
            "broadcasts": [],
            "broadcast_queue": [],
        }
        self._latest_snapshot = self.hub.publish("snapshot", payload)
        return self._latest_snapshot

    def _on_ha_change(self, _entity_id: str) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self._debounce_seconds,
                self.publish_snapshot,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _on_ha_status(self, state: str) -> None:
        self.hub.publish("status", {"state": state, "generated_at_ms": now_ms()})
        if state == "upstream_connected":
            self._on_ha_change("")


class _SSEThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        handler,
        stream: LiveStateStream,
        heartbeat_seconds: float = 15.0,
    ):
        self.stream = stream
        self.heartbeat_seconds = float(heartbeat_seconds)
        super().__init__(server_address, handler)


class _Handler(BaseHTTPRequestHandler):
    app: Any
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quiet console
        return

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/events":
            self._respond_sse()
            return
        status, payload = self.app.handle("GET", parsed.path, parse_qs(parsed.query), {})
        self._respond(status, payload)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._read_body()
        except json.JSONDecodeError as exc:
            self._respond(400, {"ok": False, "error": f"invalid json: {exc}"})
            return
        status, payload = self.app.handle("POST", parsed.path, parse_qs(parsed.query), body)
        self._respond(status, payload)
        self.server.stream.after_request("POST", status)

    def _respond_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        initial = self.server.stream.snapshot_for_connection()
        last_id = initial.id
        try:
            self.wfile.write(encode_sse(initial))
            self.wfile.flush()
            while True:
                item = self.server.stream.hub.wait_after(
                    last_id,
                    self.server.heartbeat_seconds,
                )
                if item is None:
                    payload = b": heartbeat\n\n"
                else:
                    payload = encode_sse(item)
                    last_id = item.id
                self.wfile.write(payload)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            self.close_connection = True


def create_server(
    app: Any,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    heartbeat_seconds: float = 15.0,
) -> _SSEThreadingHTTPServer:
    stream = LiveStateStream(app)
    handler = type("BoundHandler", (_Handler,), {"app": app})
    server = _SSEThreadingHTTPServer(
        (host, port),
        handler,
        stream,
        heartbeat_seconds,
    )
    stream.start()
    return server


def serve(app: Any, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    server = create_server(app, host, port)
    print(f"home-service listening on http://{host}:{port} (loopback only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.stream.stop()
        server.server_close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--print-snapshot", action="store_true")
    args = parser.parse_args()

    if args.host != DEFAULT_HOST:
        print("refusing to bind anywhere except 127.0.0.1")
        return 2

    if args.print_snapshot:
        backend = _runtime_environment().get("HOME_SERVICE_BACKEND", "memory")
        if backend != "memory":
            # Printing an in-memory snapshot under an HA configuration would
            # present invented device state as if it were authoritative.
            print(
                "--print-snapshot only supports the memory backend; "
                f"HOME_SERVICE_BACKEND is {backend}"
            )
            return 2
        state = build_default_state(args.config if args.config.exists() else None)
        print(json.dumps(state.snapshot(), ensure_ascii=False, indent=2))
        return 0

    serve(build_app_from_environment(args.config), args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
