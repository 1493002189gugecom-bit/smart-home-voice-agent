"""Loopback-only status, history and SSE API for the voice loop."""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import sounddevice as sd

import audio_utils
import config
import device_preferences
from event_bus import VoiceEventBus


def _device_view(device, kind: str, bus: VoiceEventBus) -> dict:
    try:
        if kind == "input":
            sd.check_input_settings(device=device.index, channels=1, samplerate=config.SAMPLE_RATE, dtype="float32")
        else:
            sd.check_output_settings(device=device.index, channels=2, samplerate=int(device.default_samplerate), dtype="float32")
        usable = True
    except (sd.PortAudioError, ValueError):
        usable = False
    return {
        "index": int(device.index),
        "name": str(device.name),
        "hostapi": str(device.hostapi),
        "channels": int(device.channels),
        "default_samplerate": int(device.default_samplerate),
        "is_system_default": audio_utils.system_default_index(kind) == device.index,
        "is_selected": bus.health_summary.get(f"{kind}_device_index") == device.index,
        "usable": usable,
    }


def _preference_warning(preference: dict | None, devices: list[dict]) -> str | None:
    if preference is None:
        return None
    preferred = next((item for item in devices if item["name"] == preference["name"] and item["hostapi"] == preference["hostapi"]), None)
    if preferred is None or not preferred["usable"]:
        return "preferred_device_unavailable"
    if any(item["is_selected"] for item in devices) and not preferred["is_selected"]:
        return "preferred_device_fallback"
    return None


# Voice enrolment routes. A *sample* is deliberately not one of them: the microphone
# belongs to the voice loop, so the console can start, watch and cancel, but it can
# never upload audio for a biometric it is not capturing.
IDENTITY_STATUS_PATH = "/identity/enroll/voice/status"
IDENTITY_POST_ROUTES = {
    "/identity/enroll/voice/start": ("start", {"person_id"}),
    "/identity/enroll/voice/cancel": ("cancel", set()),
}


def create_control_server(
    bus: VoiceEventBus,
    host: str = "127.0.0.1",
    port: int = 8767,
    on_select: Callable[[dict], dict] | None = None,
    on_identity: Callable[[str, dict], dict] | None = None,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("voice control API must bind to loopback")

    class Handler(BaseHTTPRequestHandler):
        server_version = "SmartHomeVoice/1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            path = self.path.split("?", 1)[0]
            if path == IDENTITY_STATUS_PATH and on_identity is not None:
                self._json(200, on_identity("status", {}))
                return
            if path == "/health":
                self._json(200, {
                    "ok": bus.state != "degraded",
                    "service": "voice",
                    "state": bus.state,
                    "history_size": len(bus.history()),
                    **bus.health_summary,
                })
                return
            if path == "/history":
                self._json(200, {"ok": True, "events": bus.history()})
                return
            if path == "/devices":
                try:
                    inputs = [_device_view(item, "input", bus) for item in audio_utils.list_input_devices()]
                    outputs = [_device_view(item, "output", bus) for item in audio_utils.list_output_devices()]
                except Exception as exc:  # device APIs can disappear during hot-plug
                    self._json(503, {
                        "ok": False,
                        "error_code": "audio_device_query_failed",
                        "message": type(exc).__name__,
                    })
                    return
                preferences = device_preferences.load()
                self._json(200, {
                    "ok": True, "inputs": inputs, "outputs": outputs,
                    "preferences": preferences,
                    "warnings": {
                        "input": _preference_warning(preferences["input"], inputs),
                        "output": _preference_warning(preferences["output"], outputs),
                    },
                    "selection": bus.selection_status(),
                })
                return
            if path == "/events":
                self._events()
                return
            self._json(404, {"ok": False, "error_code": "not_found", "message": "unknown endpoint"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            path = self.path.split("?", 1)[0]
            identity_action = IDENTITY_POST_ROUTES.get(path)
            if path != "/devices/select" and identity_action is None:
                self._json(404, {"ok": False, "error_code": "not_found", "message": "unknown endpoint"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._json(415, {"ok": False, "error_code": "unsupported_media_type", "message": "application/json required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 4096:
                    raise ValueError("invalid length")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("invalid body")
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(422, {"ok": False, "error_code": "invalid_body", "message": "body must be a JSON object"})
                return

            if identity_action is not None:
                if on_identity is None:
                    self._json(404, {"ok": False, "error_code": "not_found", "message": "unknown endpoint"})
                    return
                action, allowed = identity_action
                # Exact keys, so a typo in a client fails loudly instead of being
                # silently ignored while the request appears to succeed.
                if set(body) != allowed:
                    self._json(422, {"ok": False, "error_code": "invalid_body", "message": f"fields must be exactly {sorted(allowed)}"})
                    return
                payload = on_identity(action, body)
                self._json(200 if payload.get("ok") else 422, payload)
                return

            try:
                if set(body) != {"input", "output"}:
                    raise ValueError("invalid selection")
                payload = on_select(body)
            except (ValueError, TypeError):
                self._json(422, {"ok": False, "error_code": "invalid_selection", "message": "choose an available input and output"})
                return
            self._json(200 if payload.get("ok") else 422, payload)

        def _events(self) -> None:
            subscriber = bus.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        event = subscriber.get(timeout=15.0)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                        block = f"id: {event['id']}\nevent: {event['type']}\ndata: {encoded}\n\n"
                        self.wfile.write(block.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bus.unsubscribe(subscriber)

        def _json(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def start_control_server(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="voice-control-api", daemon=True)
    thread.start()
    return thread
