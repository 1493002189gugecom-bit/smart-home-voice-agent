"""Read-only Home Assistant WebSocket event subscription.

Only fixed status codes leave this module. Credentials and upstream response
bodies are intentionally never included in exceptions or callbacks.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid_home_assistant_url")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    prefix = parsed.path.rstrip("/")
    return urlunsplit((scheme, parsed.netloc, f"{prefix}/api/websocket", "", ""))


class HAEventSubscriber:
    """Subscribe to HA state changes on a private daemon thread."""

    def __init__(
        self,
        base_url: str,
        token: str,
        entity_ids: Iterable[str],
        on_change: Callable[[str], None],
        on_status: Callable[[str], None],
        *,
        session_factory: Callable[[], Any] | None = None,
    ):
        self.ws_url = websocket_url(base_url)
        self._token = token
        self._entity_ids = frozenset(entity_ids)
        self._on_change = on_change
        self._on_status = on_status
        self._session_factory = session_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="home-assistant-events",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _thread_main(self) -> None:
        asyncio.run(self.run_forever())

    async def run_forever(self) -> None:
        delays = (1, 2, 4, 8, 16, 30)
        attempt = 0
        while not self._stop.is_set():
            try:
                await self.run_once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self._emit_status("upstream_error")
            if self._stop.is_set():
                break
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            await asyncio.to_thread(self._stop.wait, delay)

    async def run_once(self) -> None:
        session = self._new_session()
        connected = False
        try:
            async with session.ws_connect(self.ws_url, heartbeat=30) as websocket:
                required = await websocket.receive_json()
                if required.get("type") != "auth_required":
                    raise RuntimeError("ha_protocol_error")
                await websocket.send_json({"type": "auth", "access_token": self._token})

                auth = await websocket.receive_json()
                if auth.get("type") != "auth_ok":
                    raise RuntimeError("ha_auth_failed")

                await websocket.send_json(
                    {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
                )
                result = await websocket.receive_json()
                if (
                    result.get("type") != "result"
                    or result.get("id") != 1
                    or result.get("success") is not True
                ):
                    raise RuntimeError("ha_subscribe_failed")

                connected = True
                self._emit_status("upstream_connected")
                async for message in websocket:
                    if self._stop.is_set():
                        break
                    payload = self._message_payload(message)
                    entity_id = self._state_changed_entity(payload)
                    if entity_id in self._entity_ids:
                        self._on_change(entity_id)
        finally:
            if connected:
                self._emit_status("upstream_disconnected")
            await session.close()

    def _new_session(self):
        if self._session_factory is not None:
            return self._session_factory()
        import aiohttp

        return aiohttp.ClientSession()

    @staticmethod
    def _message_payload(message: Any) -> dict[str, Any]:
        data = getattr(message, "data", None)
        if not isinstance(data, str):
            return {}
        try:
            value = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _state_changed_entity(payload: dict[str, Any]) -> str | None:
        if payload.get("type") != "event":
            return None
        event = payload.get("event")
        if not isinstance(event, dict) or event.get("event_type") != "state_changed":
            return None
        data = event.get("data")
        entity_id = data.get("entity_id") if isinstance(data, dict) else None
        return entity_id if isinstance(entity_id, str) else None

    def _emit_status(self, code: str) -> None:
        try:
            self._on_status(code)
        except Exception:
            # A consumer callback must never expose credentials or kill reconnects.
            return
