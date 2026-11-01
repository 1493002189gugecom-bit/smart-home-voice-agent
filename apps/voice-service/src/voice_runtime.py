"""Cancellation/deadlines shared by network speech operations."""
from __future__ import annotations

import asyncio
import threading
import time


class VoiceCancelled(RuntimeError):
    pass


class CombinedCancel:
    def __init__(self, *tokens):
        self.tokens = tokens

    def is_set(self):
        return any(token is not None and token.is_set() for token in self.tokens)

    def wait(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else float("inf")
        while not self.is_set() and time.monotonic() < deadline:
            time.sleep(min(0.01, max(0, deadline-time.monotonic())))
        return self.is_set()


class StopSignal:
    def __init__(self):
        self._lock = threading.Lock()
        self._event = threading.Event()

    def token(self):
        with self._lock:
            return self._event

    def stop(self):
        with self._lock:
            self._event.set()
            self._event = threading.Event()


async def bounded(awaitable, timeout, cancel=None):
    task = asyncio.ensure_future(awaitable)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise VoiceCancelled("speech_cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("speech_timeout")
            done, _ = await asyncio.wait({task}, timeout=min(0.05, remaining))
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def run(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    # Used by synchronous tooling invoked inside an asyncio application.
    result, errors = [], []
    def worker():
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]
