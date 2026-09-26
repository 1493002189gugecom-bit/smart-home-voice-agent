"""Backend-independent store for camera-sourced person observations.

The vision service is the only writer. It submits batches of confirmed
observations over a token-authenticated loopback route; this module validates a
whole batch before mutating anything, because a partially applied batch would
publish a person position that was never observed.

Memory and HA backends both read the same derived person view, so switching
backends can never resurrect a historical or simulated room.
"""
from __future__ import annotations

import os
import json
import secrets
import threading
import time
import uuid
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KNOWN_PERSON_IDS = frozenset({"dad", "mom", "child"})
PERSON_ID_PATTERN = re.compile(r"^person_[0-9a-f]{32}$")
KNOWN_ROOM_IDS = frozenset({"living_room", "bedroom", "kitchen"})
POSE_VALUES = frozenset(
    {"standing", "sitting", "lying", "suspected_fall", "hand_raised", "unknown"}
)
IDENTITY_VALUES = frozenset({"unknown", "candidate", "confirmed", "held", "conflict"})
TRACK_STATES = frozenset({"active", "lost"})
OFFLINE_REASONS = frozenset(
    {"camera_disconnected", "model_error", "service_stopping", "source_changed", "room_changed"}
)

# A device clock that runs slightly ahead must not be treated as a forged
# observation, but a far-future timestamp means the sender is broken.
MAX_FUTURE_MS = 5_000
TOKEN_BYTES = 32
# Mirrors vision.json's observation_ttl_ms. The vision service publishes roughly
# once per second, so a shorter window would flap while a much longer one would
# keep showing a person who already left the frame.
DEFAULT_OBSERVATION_TTL_MS = 3_500
DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime" / "vision"
DEFAULT_PERSON_DIRECTORY = DEFAULT_RUNTIME_DIR / "persons.json"
DEFAULT_TOKEN_PATH = DEFAULT_RUNTIME_DIR / "home-service.token"

_SUBMIT_KEYS = frozenset(
    {"camera_id", "camera_room_id", "session_id", "observed_at_ms", "observations"}
)
_WITHDRAW_KEYS = frozenset({"camera_id", "session_id", "track_ids"})
_OFFLINE_KEYS = frozenset({"camera_id", "session_id", "observed_at_ms", "reason"})
_OBSERVATION_KEYS = frozenset(
    {
        "track_id",
        "bbox",
        "keypoints",
        "detection_confidence",
        "person_id",
        "identity_state",
        "face_similarity",
        "pose",
        "pose_confidence",
        "observed_at_ms",
        "track_state",
    }
)


class VisualStateError(Exception):
    """Validation failure that must reject the whole request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_or_create_token(path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Return the shared ingress token, creating it once for this Windows user.

    The vision service must never create the token: whichever service starts
    second only reads the file, so a compromised or misconfigured vision process
    cannot rotate the secret the user relies on.
    """
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    except OSError as exc:
        raise VisualStateError("token_unreadable", f"cannot read {path.name}: {exc.strerror}") from exc
    if existing:
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    temporary = path.with_name(path.name + ".tmp")
    # Create with owner-only permissions before writing the secret so the value
    # is never briefly world-readable.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise VisualStateError("token_write_failed", f"cannot write {path.name}: {exc.strerror}") from exc
    return token


@dataclass
class _Track:
    """One camera track. Identity is absent unless the vision service confirmed it."""

    track_id: str
    camera_id: str
    camera_room_id: str
    session_id: str
    bbox: tuple[float, float, float, float]
    observed_at_ms: int
    detection_confidence: float
    person_id: str | None
    identity_state: str
    face_similarity: float | None
    pose: str
    pose_confidence: float
    track_state: str

    @property
    def confirmed_person(self) -> str | None:
        if self.identity_state == "confirmed" and self.person_id is not None:
            return self.person_id
        return None


@dataclass
class _Session:
    session_id: str
    camera_id: str
    camera_room_id: str
    started_at_ms: int
    offline_reason: str | None = None


@dataclass
class _PersonView:
    """Derived, published person state."""

    person_id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    room_id: str | None = None
    x: float | None = None
    y: float | None = None
    camera_id: str | None = None
    track_id: str | None = None
    pose: str = "unknown"
    pose_confidence: float = 0.0
    observed_at_ms: int | None = None
    version: int = 1


class VisualStateStore:
    """Thread-safe owner of live camera observations and the derived person view.

    Tracks are stored per camera session. Only a track whose identity the vision
    service confirmed may move a person; everything else stays unknown.
    """

    def __init__(
        self,
        person_catalog: dict[str, dict[str, Any]],
        *,
        observation_ttl_ms: int,
        token_path: Path = DEFAULT_TOKEN_PATH,
        directory_path: Path | None = None,
        clock=time.time,
    ) -> None:
        unknown = {item for item in person_catalog if item not in KNOWN_PERSON_IDS and not PERSON_ID_PATTERN.fullmatch(item)}
        if unknown:
            raise ValueError(f"unknown person ids in catalog: {sorted(unknown)}")
        self._ttl_ms = int(observation_ttl_ms)
        self._clock = clock
        self._token_path = token_path
        self._directory_path = directory_path
        self._token: str | None = None
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._tracks: dict[str, _Track] = {}
        self._persons: dict[str, _PersonView] = {
            person_id: _PersonView(
                person_id=person_id,
                display_name=str(record.get("display_name") or person_id),
                aliases=list(record.get("aliases") or []),
            )
            for person_id, record in person_catalog.items()
        }
        if directory_path is not None and directory_path.exists():
            try:
                # utf-8-sig accepts a byte-order mark: Notepad and Windows
                # PowerShell both add one, and a hand-edited directory file must
                # not be able to stop the service from starting.
                text = directory_path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise ValueError(f"person directory is unreadable: {exc.strerror}") from exc
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"person directory is not valid JSON: {directory_path.name} (line {exc.lineno})"
                ) from exc
            if not isinstance(payload, list):
                raise ValueError("person directory is invalid")
            for record in payload:
                if (not isinstance(record, dict) or not isinstance(record.get("id"), str)
                        or not PERSON_ID_PATTERN.fullmatch(record["id"])
                        or not isinstance(record.get("display_name"), str)
                        or not record["display_name"].strip()):
                    raise ValueError("person directory entry is invalid")
                person_id = record["id"]
                if person_id in self._persons:
                    raise ValueError("duplicate person in directory")
                self._persons[person_id] = _PersonView(person_id, record["display_name"].strip(), [])

    def persons_directory(self) -> list[dict[str, str]]:
        with self._lock:
            return [{"id": item.person_id, "display_name": item.display_name}
                    for item in self._persons.values()]

    def create_person(self, display_name: str) -> dict[str, str]:
        name = display_name.strip() if isinstance(display_name, str) else ""
        if not name or len(name) > 40 or any(ord(char) < 32 for char in name):
            raise ValueError("人物称呼须为 1 到 40 个可见字符")
        if self._directory_path is None:
            raise ValueError("person directory is unavailable")
        with self._lock:
            if any(view.display_name.casefold() == name.casefold() for view in self._persons.values()):
                raise ValueError("该人物称呼已存在")
            person_id = "person_" + uuid.uuid4().hex
            entries = [{"id": view.person_id, "display_name": view.display_name}
                       for view in self._persons.values() if PERSON_ID_PATTERN.fullmatch(view.person_id)]
            entries.append({"id": person_id, "display_name": name})
            path = self._directory_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self._persons[person_id] = _PersonView(person_id, name, [])
            return {"id": person_id, "display_name": name}

    # ------------------------------------------------------------- auth
    def authorize(self, token: str | None) -> bool:
        """Compare the ingress token without leaking length or content."""
        expected = self._expected_token()
        if not expected or not isinstance(token, str):
            return False
        return secrets.compare_digest(expected, token)

    def ensure_token(self) -> str:
        """Create/read the ingress token during real service construction."""
        with self._lock:
            if self._token is None:
                self._token = load_or_create_token(self._token_path)
            return self._token

    def _expected_token(self) -> str | None:
        with self._lock:
            if self._token is None:
                try:
                    self._token = load_or_create_token(self._token_path)
                except VisualStateError:
                    return None
            return self._token

    # ------------------------------------------------------------- writes
    def submit_batch(self, payload: dict[str, Any], now_ms: int) -> None:
        """Validate one observation batch, then apply it atomically."""
        self._require_exact_keys(payload, _SUBMIT_KEYS)
        camera_id = self._require_str(payload, "camera_id")
        session_id = self._require_str(payload, "session_id")
        camera_room_id = self._require_room(payload.get("camera_room_id"))
        observed_at_ms = self._require_timestamp(payload.get("observed_at_ms"), now_ms)
        raw_observations = payload.get("observations")
        if not isinstance(raw_observations, list):
            raise VisualStateError("invalid_batch", "observations must be a list")

        parsed = [
            self._parse_observation(item, camera_id, camera_room_id, session_id, now_ms)
            for item in raw_observations
        ]

        track_ids = [item.track_id for item in parsed]
        if len(set(track_ids)) != len(track_ids):
            raise VisualStateError("duplicate_track", "track ids must be unique inside a batch")
        confirmed = [item.person_id for item in parsed if item.confirmed_person]
        if len(set(confirmed)) != len(confirmed):
            raise VisualStateError(
                "duplicate_identity",
                "one confirmed person id may appear at most once per batch",
            )
        if not any(item.identity_state != "unknown" for item in parsed):
            # Unknown-only batches are still valid: they carry pose and geometry.
            pass

        with self._lock:
            self._expire_locked(now_ms)
            for item in parsed:
                existing = self._tracks.get(item.track_id)
                if existing is not None and existing.camera_id != camera_id:
                    raise VisualStateError(
                        "duplicate_track", "track id is already owned by another camera"
                    )
            # Each payload is a full latest-frame snapshot for this camera, not
            # an append operation. Replacing also makes an empty list immediately
            # clear people who have left that frame.
            for track_id in [
                key for key, item in self._tracks.items() if item.camera_id == camera_id
            ]:
                self._tracks.pop(track_id, None)
            self._sessions[camera_id] = _Session(
                session_id=session_id,
                camera_id=camera_id,
                camera_room_id=camera_room_id,
                started_at_ms=observed_at_ms,
            )
            for item in parsed:
                self._tracks[item.track_id] = item
            self._recompute_locked(now_ms)

    def withdraw(self, payload: dict[str, Any], now_ms: int) -> None:
        """Drop specific tracks; their persons must not keep the old room."""
        self._require_exact_keys(payload, _WITHDRAW_KEYS)
        camera_id = self._require_str(payload, "camera_id")
        session_id = self._require_str(payload, "session_id")
        track_ids = payload.get("track_ids")
        if not isinstance(track_ids, list) or not all(isinstance(item, str) for item in track_ids):
            raise VisualStateError("invalid_batch", "track_ids must be a list of strings")

        with self._lock:
            self._require_session_locked(camera_id, session_id)
            self._expire_locked(now_ms)
            for track_id in track_ids:
                track = self._tracks.get(track_id)
                if (
                    track is not None
                    and track.camera_id == camera_id
                    and track.session_id == session_id
                ):
                    self._tracks.pop(track_id, None)
            self._recompute_locked(now_ms)

    def offline(self, payload: dict[str, Any], now_ms: int) -> None:
        """Mark a camera session offline and withdraw everything it published."""
        self._require_exact_keys(payload, _OFFLINE_KEYS)
        camera_id = self._require_str(payload, "camera_id")
        session_id = self._require_str(payload, "session_id")
        self._require_timestamp(payload.get("observed_at_ms"), now_ms)
        reason = payload.get("reason")
        if reason not in OFFLINE_REASONS:
            raise VisualStateError("invalid_reason", "unknown offline reason")

        with self._lock:
            self._require_session_locked(camera_id, session_id)
            session = self._sessions[camera_id]
            session.offline_reason = str(reason)
            for track_id in [key for key, item in self._tracks.items() if item.camera_id == camera_id]:
                self._tracks.pop(track_id, None)
            self._recompute_locked(now_ms)

    # ------------------------------------------------------------- reads
    def snapshot(self, now_ms: int) -> list[dict[str, Any]]:
        """Return every catalogued person with camera provenance or explicit unknown."""
        with self._lock:
            self._expire_locked(now_ms)
            self._recompute_locked(now_ms)
            return [self._person_payload(self._persons[person_id]) for person_id in sorted(self._persons)]

    def active_session(self) -> tuple[str, str] | None:
        """Return the only session allowed to publish, if any camera is live."""
        with self._lock:
            for camera_id, session in self._sessions.items():
                if session.offline_reason is None:
                    return camera_id, session.session_id
            return None

    def published_person_ids(self) -> list[str]:
        """Ids that currently have a fresh confirmed camera location."""
        with self._lock:
            return sorted(
                view.person_id
                for view in self._persons.values()
                if view.room_id is not None
            )

    # ------------------------------------------------------------- validation
    @staticmethod
    def _require_exact_keys(payload: Any, allowed: frozenset[str]) -> None:
        if not isinstance(payload, dict):
            raise VisualStateError("invalid_batch", "body must be a JSON object")
        if set(payload) != allowed:
            raise VisualStateError(
                "invalid_batch",
                f"body keys must be exactly {sorted(allowed)}",
            )

    @staticmethod
    def _require_str(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise VisualStateError("invalid_batch", f"{key} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _require_room(value: Any) -> str:
        if value not in KNOWN_ROOM_IDS:
            raise VisualStateError("invalid_room", "camera_room_id must be a known room")
        return str(value)

    @staticmethod
    def _require_number(value: Any, name: str, low: float = 0.0, high: float = 1.0) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise VisualStateError("invalid_batch", f"{name} must be a number")
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise VisualStateError("invalid_batch", f"{name} must be finite")
        if not low <= number <= high:
            raise VisualStateError("invalid_range", f"{name} must be within [{low}, {high}]")
        return number

    @staticmethod
    def _require_optional_number(value: Any, name: str) -> float | None:
        if value is None:
            return None
        return VisualStateStore._require_number(value, name)

    @staticmethod
    def _require_timestamp(value: Any, now_ms: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise VisualStateError("invalid_batch", "observed_at_ms must be an integer")
        if value > now_ms + MAX_FUTURE_MS:
            raise VisualStateError("future_timestamp", "observed_at_ms is too far in the future")
        return int(value)

    def _parse_observation(
        self,
        item: Any,
        camera_id: str,
        camera_room_id: str,
        session_id: str,
        now_ms: int,
    ) -> _Track:
        self._require_exact_keys(item, _OBSERVATION_KEYS)
        track_id = self._require_str(item, "track_id")
        observed_at_ms = self._require_timestamp(item.get("observed_at_ms"), now_ms)
        if now_ms - observed_at_ms > self._ttl_ms:
            raise VisualStateError("stale_observation", "observed_at_ms is older than the TTL")

        bbox_raw = item.get("bbox")
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
            raise VisualStateError("invalid_bbox", "bbox must hold four normalized numbers")
        bbox = tuple(self._require_number(value, "bbox") for value in bbox_raw)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise VisualStateError("invalid_bbox", "bbox must have positive extent")

        keypoints_raw = item.get("keypoints")
        if not isinstance(keypoints_raw, (list, tuple)) or not keypoints_raw:
            raise VisualStateError("invalid_keypoints", "keypoints must be a non-empty list")
        for point in keypoints_raw:
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                raise VisualStateError("invalid_keypoints", "each keypoint must be [x, y, confidence]")
            self._require_number(point[0], "keypoint.x")
            self._require_number(point[1], "keypoint.y")
            self._require_number(point[2], "keypoint.confidence")

        detection_confidence = self._require_number(
            item.get("detection_confidence"), "detection_confidence"
        )
        pose_confidence = self._require_number(item.get("pose_confidence"), "pose_confidence")
        face_similarity = self._require_optional_number(item.get("face_similarity"), "face_similarity")

        identity_state = item.get("identity_state")
        if identity_state not in IDENTITY_VALUES:
            raise VisualStateError("invalid_identity_state", "unknown identity_state")
        pose = item.get("pose")
        if pose not in POSE_VALUES:
            raise VisualStateError("invalid_pose", "unknown pose value")
        track_state = item.get("track_state")
        if track_state not in TRACK_STATES:
            raise VisualStateError("invalid_track_state", "unknown track_state")

        person_id = item.get("person_id")
        if person_id is not None:
            if person_id not in self._persons:
                raise VisualStateError("unknown_person", "person_id is not in the person directory")
            if identity_state != "confirmed":
                # A named but unconfirmed identity must never publish a location,
                # so it is stored as unknown instead of being silently trusted.
                person_id = None
                identity_state = "unknown"

        return _Track(
            track_id=track_id,
            camera_id=camera_id,
            camera_room_id=camera_room_id,
            session_id=session_id,
            bbox=bbox,
            observed_at_ms=observed_at_ms,
            detection_confidence=detection_confidence,
            person_id=person_id,
            identity_state=str(identity_state),
            face_similarity=face_similarity,
            pose=str(pose),
            pose_confidence=pose_confidence,
            track_state=str(track_state),
        )

    def _require_session_locked(self, camera_id: str, session_id: str) -> None:
        session = self._sessions.get(camera_id)
        if session is None:
            raise VisualStateError("unknown_session", "camera has no recorded session")
        if session.session_id != session_id:
            # A late message from a replaced session must not touch live state.
            raise VisualStateError("stale_session", "session id does not match the live session")

    # ------------------------------------------------------------- internals
    def _expire_locked(self, now_ms: int) -> None:
        deadline = now_ms - self._ttl_ms
        expired = [key for key, item in self._tracks.items() if item.observed_at_ms < deadline]
        for track_id in expired:
            self._tracks.pop(track_id, None)

    def _recompute_locked(self, now_ms: int) -> None:
        """Derive the published person view from fresh confirmed tracks."""
        candidates: dict[str, _Track] = {}
        for track in self._tracks.values():
            person_id = track.confirmed_person
            if person_id is None or track.track_state != "active":
                continue
            current = candidates.get(person_id)
            if current is None or track.observed_at_ms >= current.observed_at_ms:
                candidates[person_id] = track

        for person_id, view in self._persons.items():
            track = candidates.get(person_id)
            if track is None:
                if view.room_id is not None:
                    view.version += 1
                view.room_id = None
                view.x = None
                view.y = None
                view.camera_id = None
                view.track_id = None
                view.pose = "unknown"
                view.pose_confidence = 0.0
                view.observed_at_ms = None
                continue

            bbox = track.bbox
            x = (bbox[0] + bbox[2]) / 2.0
            y = bbox[3]
            changed = (
                view.room_id != track.camera_room_id
                or view.track_id != track.track_id
                or view.pose != track.pose
            )
            if changed:
                view.version += 1
            view.room_id = track.camera_room_id
            view.x = round(x, 6)
            view.y = round(y, 6)
            view.camera_id = track.camera_id
            view.track_id = track.track_id
            view.pose = track.pose
            view.pose_confidence = track.pose_confidence
            view.observed_at_ms = track.observed_at_ms

    @staticmethod
    def _person_payload(view: _PersonView) -> dict[str, Any]:
        known = view.room_id is not None
        return {
            "id": view.person_id,
            "display_name": view.display_name,
            "aliases": list(view.aliases),
            "room_id": view.room_id,
            "x": view.x,
            "y": view.y,
            "location_known": known,
            # Always "camera": there is no manual position mode left, so an
            # unknown location must not look like a stale manual placement.
            "location_source": "camera",
            "camera_id": view.camera_id,
            "track_id": view.track_id,
            "pose": view.pose if known else "unknown",
            "pose_confidence": view.pose_confidence if known else 0.0,
            "observed_at_ms": view.observed_at_ms,
            "version": view.version,
        }


def route_vision_request(
    store: VisualStateStore,
    method: str,
    path: str,
    body: dict[str, Any],
    token: str | None,
    *,
    now_ms: int,
) -> tuple[int, dict[str, Any]] | None:
    """Serve the three restricted `/vision/*` routes.

    Returns ``None`` when the path is not a vision route, so both backends can
    call this first and keep their own routing untouched. Device tools and the HA
    gateway are never reachable from here.
    """
    if not path.startswith("/vision/"):
        return None
    if not store.authorize(token):
        # Do not reveal whether the token was absent, empty, or wrong.
        return 401, {"ok": False, "error_code": "unauthorized", "message": "invalid vision token"}
    try:
        if method == "POST" and path == "/vision/observations":
            store.submit_batch(body, now_ms)
            return 200, {"ok": True}
        if method == "POST" and path == "/vision/withdraw":
            store.withdraw(body, now_ms)
            return 200, {"ok": True}
        if method == "POST" and path == "/vision/offline":
            store.offline(body, now_ms)
            return 200, {"ok": True}
    except VisualStateError as exc:
        return 422, {"ok": False, "error_code": exc.code, "message": exc.message}
    return 404, {"ok": False, "error_code": "not_found", "message": f"no route for {method} {path}"}
