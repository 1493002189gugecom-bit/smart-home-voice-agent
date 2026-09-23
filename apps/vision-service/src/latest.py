"""Capacity-one handoff used to drop stale camera and upload work."""

from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar


T = TypeVar("T")


class LatestValue(Generic[T]):
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._value: T | None = None
        self._closed = False

    def put(self, value: T) -> None:
        with self._condition:
            if self._closed:
                return
            self._value = value
            self._condition.notify_all()

    def take(self, timeout: float | None = None) -> T | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._value is None and not self._closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._condition.wait(remaining)
            value = self._value
            self._value = None
            return value

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._value = None
            self._condition.notify_all()

    def discard(self) -> None:
        """Drop the currently buffered value without closing the handoff."""
        with self._condition:
            self._value = None

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed
