"""Transport-safe contracts for the local vision service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


KNOWN_PERSON_IDS = frozenset({"dad", "mom", "child"})
KNOWN_ROOM_IDS = frozenset({"living_room", "bedroom", "kitchen"})


class ServiceMode(str, Enum):
    IDLE = "idle"
    MONITORING = "monitoring"
    REGISTERING = "registering"
    ERROR = "error"


class IdentityState(str, Enum):
    UNKNOWN = "unknown"
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    HELD = "held"
    CONFLICT = "conflict"


class PoseState(str, Enum):
    STANDING = "standing"
    SITTING = "sitting"
    LYING = "lying"
    SUSPECTED_FALL = "suspected_fall"
    HAND_RAISED = "hand_raised"
    UNKNOWN = "unknown"


class TrackState(str, Enum):
    ACTIVE = "active"
    LOST = "lost"


@dataclass(frozen=True)
class NormalizedPoint:
    x: float
    y: float
    confidence: float

    def to_list(self) -> list[float]:
        return [self.x, self.y, self.confidence]


@dataclass(frozen=True)
class TrackObservation:
    track_id: str
    bbox: tuple[float, float, float, float]
    keypoints: tuple[NormalizedPoint, ...]
    detection_confidence: float
    person_id: str | None
    identity_state: IdentityState
    face_similarity: float | None
    pose: PoseState
    pose_confidence: float
    observed_at_ms: int
    track_state: TrackState = TrackState.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "bbox": list(self.bbox),
            "keypoints": [point.to_list() for point in self.keypoints],
            "detection_confidence": self.detection_confidence,
            "person_id": self.person_id,
            "identity_state": self.identity_state.value,
            "face_similarity": self.face_similarity,
            "pose": self.pose.value,
            "pose_confidence": self.pose_confidence,
            "observed_at_ms": self.observed_at_ms,
            "track_state": self.track_state.value,
        }


@dataclass(frozen=True)
class VisionSnapshot:
    session_id: str | None
    mode: ServiceMode
    camera_id: str | None
    camera_room_id: str | None
    observed_at_ms: int
    sync_state: str
    tracks: tuple[TrackObservation, ...] = field(default_factory=tuple)
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.error_code is None,
            "session_id": self.session_id,
            "mode": self.mode.value,
            "camera_id": self.camera_id,
            "camera_room_id": self.camera_room_id,
            "observed_at_ms": self.observed_at_ms,
            "sync_state": self.sync_state,
            "tracks": [track.to_dict() for track in self.tracks],
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class PoseEstimate:
    pose: PoseState
    confidence: float


@dataclass(frozen=True)
class BodyTrack:
    track_id: str
    bbox: tuple[float, float, float, float]
    keypoints: tuple[NormalizedPoint, ...]
    confidence: float


@dataclass(frozen=True)
class FaceCandidate:
    person_id: str | None
    similarity: float | None
    margin: float | None


@dataclass(frozen=True)
class FaceQuality:
    """Outcome of the pre-recognition quality gates.

    ``reason`` is a stable snake_case code so the Unity panel can show a specific
    message instead of a generic failure. It is ``None`` only when ``ok`` is true.
    """

    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class FaceSample:
    """One detected face in normalized image coordinates.

    ``embedding`` is optional because quality-gated samples still carry geometry
    for association and registration progress while being unusable for identity.
    """

    bbox: tuple[float, float, float, float]
    landmarks: tuple[NormalizedPoint, ...]
    detection_confidence: float
    quality: FaceQuality
    embedding: tuple[float, ...] | None = None
    sharpness: float = 0.0
    brightness: float = 0.0


@dataclass(frozen=True)
class RegistrationStatus:
    """Progress of one registration session, shared by service and Unity panel."""

    person_id: str
    step: str
    step_index: int
    step_count: int
    accepted_samples: int
    required_samples: int
    state: str
    updated_at_ms: int
    quality_reason: str | None = None
    confirmation_remaining_ms: int = 0
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.state in {"collecting", "confirming"},
            "person_id": self.person_id,
            "step": self.step,
            "step_index": self.step_index,
            "step_count": self.step_count,
            "accepted_samples": self.accepted_samples,
            "required_samples": self.required_samples,
            "state": self.state,
            "updated_at_ms": self.updated_at_ms,
            "quality_reason": self.quality_reason,
            "confirmation_remaining_ms": self.confirmation_remaining_ms,
            "message": self.message,
        }
