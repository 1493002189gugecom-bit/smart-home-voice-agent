"""Bounded in-memory public event stream for the local voice console."""

from __future__ import annotations

import datetime as dt
import queue
import threading
from collections import deque
from copy import deepcopy
from typing import Any


_SECRET_KEYS = {
    "api_key", "authorization", "token", "secret", "password",
    "audio", "samples", "waveform", "model_path",
}


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _public(item)
            for key, item in value.items()
            if str(key).casefold() not in _SECRET_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class VoiceEventBus:
    """Keep recent public events and fan new ones out to SSE subscribers."""

    def __init__(self, max_events: int = 200, subscriber_queue_size: int = 64) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, max_events))
        self._subscribers: set[queue.Queue] = set()
        self._subscriber_queue_size = max(1, subscriber_queue_size)
        self._lock = threading.RLock()
        self._sequence = 0
        self.state = "starting"

    def publish(self, event_type: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            state = payload.get("state")
            if isinstance(state, str) and state:
                self.state = state
            event = {
                "id": self._sequence,
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
                "type": str(event_type),
                "payload": _public(payload),
            }
            self._events.append(event)
            for subscriber in tuple(self._subscribers):
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    try:
                        subscriber.get_nowait()
                        subscriber.put_nowait(event)
                    except (queue.Empty, queue.Full):
                        pass
            return deepcopy(event)

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._events))

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=self._subscriber_queue_size)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)
