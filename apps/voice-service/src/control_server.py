"""Loopback-only status, history and SSE API for the voice loop."""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import audio_utils
from event_bus import VoiceEventBus


def _device_view(device) -> dict:
    return {
        "index": int(device.index),
        "name": str(device.name),
        "hostapi": str(device.hostapi),
        "channels": int(device.channels),
        "default_samplerate": int(device.default_samplerate),
    }


def create_control_server(
    bus: VoiceEventBus,
    host: str = "127.0.0.1",
    port: int = 8767,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("voice control API must bind to loopback")

    class Handler(BaseHTTPRequestHandler):
        server_version = "SmartHomeVoice/1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._json(200, {
                    "ok": True,
                    "service": "voice",
                    "state": bus.state,
                    "history_size": len(bus.history()),
                })
                return
            if path == "/history":
                self._json(200, {"ok": True, "events": bus.history()})
                return
            if path == "/devices":
                try:
                    inputs = [_device_view(item) for item in audio_utils.list_input_devices()]
                    outputs = [_device_view(item) for item in audio_utils.list_output_devices()]
                except Exception as exc:  # device APIs can disappear during hot-plug
                    self._json(503, {
                        "ok": False,
                        "error_code": "audio_device_query_failed",
                        "message": type(exc).__name__,
                    })
                    return
                self._json(200, {"ok": True, "inputs": inputs, "outputs": outputs})
                return
            if path == "/events":
                self._events()
                return
            self._json(404, {"ok": False, "error_code": "not_found", "message": "unknown endpoint"})

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
