"""Authenticated publisher that pushes vision state into home-service.

home-service owns the shared token; this client only reads it. A missing token
means the vision service started before home-service, and that is reported as
``disconnected`` rather than inventing a token the user cannot rotate.

Only the newest observation batch is ever sent: an older batch describes people
who have already moved, so queueing it would publish a stale position.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from latest import LatestValue

REQUEST_TIMEOUT_SECONDS = 2.0
TOKEN_FILENAME = "home-service.token"

SYNC_SYNCED = "synced"
SYNC_PENDING = "pending"
SYNC_DISCONNECTED = "disconnected"
SYNC_NOT_MONITORING = "not_monitoring"


class HomeSyncClient:
    def __init__(
        self,
        base_url: str,
        runtime_dir: Path,
        *,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.runtime_dir = Path(runtime_dir)
        self.token_path = self.runtime_dir / TOKEN_FILENAME
        self.timeout_seconds = float(timeout_seconds)
        self._pending: LatestValue[tuple[int, dict[str, Any]]] = LatestValue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._generation = 0
        self._sync_state = SYNC_NOT_MONITORING
        self._last_error: str | None = None

    # ------------------------------------------------------------- state
    @property
    def sync_state(self) -> str:
        with self._lock:
            return self._sync_state

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def _set_state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self._sync_state = state
            self._last_error = error

    def token(self) -> str | None:
        """Read the shared token. Never logged and never created here."""
        try:
            value = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def available(self) -> bool:
        return self.token() is not None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._stop = threading.Event()
            self._pending = LatestValue()
            self._thread = threading.Thread(target=self._run, name="vision-home-sync", daemon=True)
            self._thread.start()

    def stop(self, *, final_payload: dict[str, Any] | None = None) -> None:
        """Stop the worker, attempting at most one bounded final message."""
        with self._lock:
            thread = self._thread
            self._thread = None
        self._stop.set()
        self._pending.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.timeout_seconds + 1.0)
        if final_payload is not None:
            # Best effort only: an unreachable home-service must not block exit.
            self._post("/vision/offline", final_payload)

    # ------------------------------------------------------------- commands
    def submit(self, payload: dict[str, Any]) -> None:
        """Queue one batch, replacing any unconsumed batch."""
        with self._lock:
            self._sync_state = SYNC_PENDING
            generation = self._generation
        self._pending.put((generation, payload))

    def withdraw(self, payload: dict[str, Any]) -> bool:
        """Synchronous withdrawal: the caller must not switch sessions first."""
        return self._barrier_post("/vision/withdraw", payload)

    def offline(self, payload: dict[str, Any]) -> bool:
        return self._barrier_post("/vision/offline", payload)

    def _barrier_post(self, path: str, payload: dict[str, Any]) -> bool:
        """Order a transition after older sends and invalidate queued work."""
        with self._lock:
            self._generation += 1
        self._pending.discard()
        with self._send_lock:
            return self._post_locked(path, payload)

    def mark_not_monitoring(self) -> None:
        with self._lock:
            self._sync_state = SYNC_NOT_MONITORING
            self._last_error = None

    # ------------------------------------------------------------- worker
    def _run(self) -> None:
        while not self._stop.is_set():
            pending = self._pending.take(timeout=0.5)
            if pending is None:
                continue
            generation, payload = pending
            with self._send_lock:
                with self._lock:
                    if generation != self._generation:
                        continue
                self._post_locked("/vision/observations", payload, expect_confirmation=True)

    def _post(self, path: str, payload: dict[str, Any], *, expect_confirmation: bool = False) -> bool:
        with self._send_lock:
            return self._post_locked(path, payload, expect_confirmation=expect_confirmation)

    def _post_locked(self, path: str, payload: dict[str, Any], *, expect_confirmation: bool = False) -> bool:
        token = self.token()
        if token is None:
            self._set_state(SYNC_DISCONNECTED, "home_service_token_missing")
            return False
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Vision-Token": token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    self._set_state(SYNC_DISCONNECTED, f"http_{response.status}")
                    return False
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            # The error text may embed the URL, so only the exception class is kept.
            self._set_state(SYNC_DISCONNECTED, type(exc).__name__)
            return False
        if expect_confirmation:
            self._set_state(SYNC_SYNCED, None)
        return True
