"""Home state domain models.

Field definitions follow design section 5. All identifiers are stable strings;
the LLM never invents them. Every mutable object carries a version so conflicts
can be detected instead of overwritten.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class DeviceType(str, Enum):
    LIGHT = "light"
    AC = "ac"
    SWITCH = "switch"
    SENSOR = "sensor"
    SPEAKER = "speaker"


class AcMode(str, Enum):
    COOL = "cool"
    HEAT = "heat"
    FAN = "fan"
    DRY = "dry"
    AUTO = "auto"


class OperationStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class BroadcastState(str, Enum):
    QUEUED = "queued"
    PLAYING = "playing"
    PLAYED = "played"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Device:
    id: str
    room_id: str
    type: DeviceType
    name: str
    online: bool = True
    # light: on, brightness (0-100)
    # ac: on, mode, target_temp
    state: dict[str, Any] = field(default_factory=dict)
    version: int = 1


@dataclass
class Room:
    id: str
    name: str
    simulated_temp: float
    devices: list[str] = field(default_factory=list)
    version: int = 1


@dataclass
class Person:
    id: str
    display_name: str
    # Family-relationship aliases so "爸爸" resolves locally, not by the LLM.
    aliases: list[str] = field(default_factory=list)
    room_id: str | None = None
    x: float | None = None
    y: float | None = None
    version: int = 1

    @property
    def location_known(self) -> bool:
        return self.room_id is not None


@dataclass
class VisualObservation:
    """Camera detection. Deliberately not an identity claim."""

    camera_id: str
    track_id: str
    bbox: tuple[float, float, float, float]
    confidence: float
    observed_at_ms: int
    # Manual binding only; never inferred from appearance or voice.
    bound_person_id: str | None = None
    bound_manually: bool = False


@dataclass
class Conversation:
    id: str
    test_speaker: str | None
    source_room_id: str | None
    recent_targets: list[str] = field(default_factory=list)
    pending_clarification: str | None = None
    last_activity_ms: int = field(default_factory=now_ms)
    version: int = 1


@dataclass
class BroadcastTask:
    id: str
    room_id: str
    text: str
    target_person_ids: list[str]
    state: BroadcastState = BroadcastState.QUEUED
    created_at_ms: int = field(default_factory=now_ms)
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    error: str | None = None
    # Unity must echo this id when reporting playback completion.
    receipt_id: str | None = None
    version: int = 1


@dataclass
class Operation:
    """One write request. The id is generated locally and reused on retry."""

    id: str
    kind: str
    target_id: str | None
    params: dict[str, Any]
    status: OperationStatus = OperationStatus.PENDING
    error: str | None = None
    result: dict[str, Any] | None = None
    precondition_version: int | None = None
    created_at_ms: int = field(default_factory=now_ms)
    finished_at_ms: int | None = None
    # Set for broadcast operations so the task can be traced back.
    broadcast_task_id: str | None = None
