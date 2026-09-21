"""Bounded live-state events shared by SSE transports and state backends."""

from __future__ import annotations

import json
import threading
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    id: int
    event: str
    data: dict[str, Any]


class EventHub:
    """Thread-safe bounded event history with snapshot coalescing."""

    def __init__(self, history_size: int = 32):
        if history_size < 1:
            raise ValueError("history_size must be positive")
        self._condition = threading.Condition()
        self._events: deque[StreamEvent] = deque(maxlen=history_size)
        self._next_id = 1

    def publish(self, event: str, data: dict[str, Any]) -> StreamEvent:
        if not event:
            raise ValueError("event name is required")
        with self._condition:
            item = StreamEvent(self._next_id, event, deepcopy(data))
            self._next_id += 1
            if event == "snapshot":
                self._events = deque(
                    (existing for existing in self._events if existing.event != "snapshot"),
                    maxlen=self._events.maxlen,
                )
            self._events.append(item)
            self._condition.notify_all()
            return item

    def wait_after(self, last_id: int, timeout: float) -> StreamEvent | None:
        with self._condition:
            item = self._find_after(last_id)
            if item is None and timeout > 0:
                self._condition.wait(timeout)
                item = self._find_after(last_id)
            return item

    def _find_after(self, last_id: int) -> StreamEvent | None:
        for item in self._events:
            if item.id > last_id:
                return item
        return None


def encode_sse(item: StreamEvent) -> bytes:
    data = json.dumps(item.data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {item.id}\nevent: {item.event}\ndata: {data}\n\n".encode("utf-8")


def canonical_memory_snapshot(
    payload: dict[str, Any], *, sequence: int, generated_at_ms: int
) -> dict[str, Any]:
    return {
        "version": int(sequence),
        "backend": "memory",
        "generated_at_ms": int(generated_at_ms),
        "rooms": deepcopy(payload.get("rooms") or []),
        "devices": deepcopy(payload.get("devices") or []),
        "persons": deepcopy(payload.get("persons") or []),
        "broadcasts": deepcopy(payload.get("broadcasts") or []),
        "broadcast_queue": deepcopy(payload.get("broadcast_queue") or []),
    }


def canonical_ha_snapshot(
    payload: dict[str, Any], *, sequence: int, generated_at_ms: int
) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    rooms_source = data.get("rooms") if isinstance(data, dict) else None
    if payload.get("ok") is not True or not isinstance(rooms_source, list):
        raise ValueError("HA room status did not contain successful room data")

    rooms: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    for source_room in rooms_source:
        if not isinstance(source_room, dict):
            continue
        room = deepcopy(source_room)
        room_devices = room.pop("devices", None)
        room["devices"] = None
        room["version"] = int(sequence)
        rooms.append(room)
        if not isinstance(room_devices, list):
            continue
        for source_device in room_devices:
            if not isinstance(source_device, dict):
                continue
            device = deepcopy(source_device)
            device.setdefault("room_id", room.get("id"))
            device.setdefault("room_name", room.get("name"))
            device["version"] = int(sequence)
            if not isinstance(device.get("state"), dict):
                device["state"] = {}
            devices.append(device)

    return {
        "version": int(sequence),
        "backend": "ha",
        "generated_at_ms": int(generated_at_ms),
        "rooms": rooms,
        "devices": devices,
        "persons": [],
        "broadcasts": [],
        "broadcast_queue": [],
    }
