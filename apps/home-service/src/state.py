"""Authoritative home state: snapshot, versioned increments, and transitions.

The state service is the single source of truth. Unity and the Agent only read
snapshots and request changes through validated operations; they never mutate
state directly.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from models import (
    AcMode,
    BroadcastState,
    BroadcastTask,
    Conversation,
    Device,
    DeviceType,
    Operation,
    OperationStatus,
    Person,
    Room,
    new_id,
    now_ms,
)

DEFAULT_CONFORT_TEMP = 26.0
DEFAULT_TEMP_MIN = 16.0
DEFAULT_TEMP_MAX = 30.0


class StateError(Exception):
    """Base class for state violations that map to a tool error."""

    code = "state_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(StateError):
    code = "not_found"


class Offline(StateError):
    code = "offline"


class OutOfRange(StateError):
    code = "out_of_range"


class VersionConflict(StateError):
    code = "version_conflict"


class InvalidRoom(StateError):
    code = "invalid_room"


def _device_to_dict(device: Device) -> dict[str, Any]:
    data = asdict(device)
    data["type"] = device.type.value
    return data


def _room_to_dict(room: Room) -> dict[str, Any]:
    return asdict(room)


def _person_to_dict(person: Person) -> dict[str, Any]:
    data = asdict(person)
    # location_known is a derived property, so asdict() drops it. Unity and the
    # Agent both need it explicitly: "no position" must be visible, never
    # inferred from a missing field.
    data["location_known"] = person.location_known
    # The person directory no longer owns a position: the camera store does.
    # These keys keep the payload shape stable for existing readers, and they
    # always describe a camera-sourced state so the source can never be misread.
    data["location_source"] = "camera"
    data["camera_id"] = None
    data["track_id"] = None
    data["pose"] = "unknown"
    data["pose_confidence"] = 0.0
    data["observed_at_ms"] = None
    return data


def _broadcast_to_dict(task: BroadcastTask) -> dict[str, Any]:
    data = asdict(task)
    data["state"] = task.state.value
    return data


def _operation_to_dict(op: Operation) -> dict[str, Any]:
    data = asdict(op)
    data["status"] = op.status.value
    return data


class HomeState:
    """In-memory authoritative state with a monotonic version counter."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.temp_min = float(cfg.get("temp_min", DEFAULT_TEMP_MIN))
        self.temp_max = float(cfg.get("temp_max", DEFAULT_TEMP_MAX))
        self.comfort_temp = float(cfg.get("comfort_temp", DEFAULT_CONFORT_TEMP))
        if self.temp_min >= self.temp_max:
            raise ValueError("temp_min must be lower than temp_max")

        self.rooms: dict[str, Room] = {}
        self.devices: dict[str, Device] = {}
        self.persons: dict[str, Person] = {}
        self.conversations: dict[str, Conversation] = {}
        self.operations: dict[str, Operation] = {}
        self.broadcast_queue: list[str] = []
        self.broadcasts: dict[str, BroadcastTask] = {}

        # Bumped on every committed change; dropped increments are replayable.
        self.version = 0
        self._increments: list[dict[str, Any]] = []
        self._operation_effects: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # ------------------------------------------------------------------ setup
    def add_room(self, room_id: str, name: str, simulated_temp: float) -> Room:
        if room_id in self.rooms:
            raise ValueError(f"duplicate room id: {room_id}")
        room = Room(id=room_id, name=name, simulated_temp=float(simulated_temp))
        self.rooms[room_id] = room
        return room

    def add_device(
        self,
        device_id: str,
        room_id: str,
        type_: DeviceType,
        name: str,
        state: dict[str, Any] | None = None,
        online: bool = True,
    ) -> Device:
        if room_id not in self.rooms:
            raise InvalidRoom(f"unknown room: {room_id}")
        if device_id in self.devices:
            raise ValueError(f"duplicate device id: {device_id}")
        device = Device(
            id=device_id,
            room_id=room_id,
            type=type_,
            name=name,
            online=online,
            state=dict(state or {}),
        )
        self.devices[device_id] = device
        self.rooms[room_id].devices.append(device_id)
        return device

    def add_person(
        self,
        person_id: str,
        display_name: str,
        aliases: list[str] | None = None,
        room_id: str | None = None,
        x: float | None = None,
        y: float | None = None,
    ) -> Person:
        if person_id in self.persons:
            raise ValueError(f"duplicate person id: {person_id}")
        if room_id is not None and room_id not in self.rooms:
            raise InvalidRoom(f"unknown room: {room_id}")
        person = Person(
            id=person_id,
            display_name=display_name,
            aliases=list(aliases or []),
            room_id=room_id,
            x=x,
            y=y,
        )
        self.persons[person_id] = person
        return person

    # ------------------------------------------------------------- versioning
    def _commit(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.version += 1
        increment = {"version": self.version, "kind": kind, "at_ms": now_ms(), **payload}
        self._increments.append(increment)
        for listener in list(self._listeners):
            listener(copy.deepcopy(increment))
        return increment

    def subscribe(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def increments_since(self, version: int) -> list[dict[str, Any]]:
        """Return increments newer than ``version``.

        Callers that request a version that has been compacted away, or a
        future/unknown version, must fall back to a full snapshot.
        """
        if version > self.version:
            raise VersionConflict(f"requested version {version} is ahead of {self.version}")
        return [inc for inc in self._increments if inc["version"] > version]

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "comfort_temp": self.comfort_temp,
            "temp_min": self.temp_min,
            "temp_max": self.temp_max,
            "rooms": [_room_to_dict(r) for r in self.rooms.values()],
            "devices": [_device_to_dict(d) for d in self.devices.values()],
            "persons": [_person_to_dict(p) for p in self.persons.values()],
            "broadcasts": [self._broadcast_view(t) for t in self.broadcasts.values()],
            "broadcast_queue": list(self.broadcast_queue),
        }

    def _broadcast_view(self, task: BroadcastTask) -> dict[str, Any]:
        """Broadcast payload with the room name resolved.

        Consumers must not have to join rooms themselves, and the room name has
        to travel with the task so a highlight can never point at the wrong room.
        """
        data = _broadcast_to_dict(task)
        room = self.rooms.get(task.room_id)
        data["room_name"] = room.name if room else None
        return data

    def sync(self, since_version: int | None) -> dict[str, Any]:
        """Return either a snapshot or increments, matching design section 8."""
        if since_version is None:
            return {"mode": "snapshot", **self.snapshot()}
        try:
            increments = self.increments_since(since_version)
        except VersionConflict:
            return {"mode": "snapshot", "reason": "unknown_version", **self.snapshot()}
        return {"mode": "increments", "from": since_version, "to": self.version, "increments": increments}

    # ------------------------------------------------------------- lookups
    def require_room(self, room_id: str) -> Room:
        room = self.rooms.get(room_id)
        if room is None:
            raise NotFound(f"unknown room: {room_id}")
        return room

    def require_device(self, device_id: str) -> Device:
        device = self.devices.get(device_id)
        if device is None:
            raise NotFound(f"unknown device: {device_id}")
        return device

    def require_person(self, person_id: str) -> Person:
        person = self.persons.get(person_id)
        if person is None:
            raise NotFound(f"unknown person: {person_id}")
        return person

    def resolve_person(self, text: str) -> Person | None:
        """Resolve a family alias or display name to exactly one person."""
        needle = text.strip()
        if not needle:
            return None
        exact = [p for p in self.persons.values() if p.display_name == needle or needle in p.aliases]
        if len(exact) == 1:
            return exact[0]
        partial = [
            p
            for p in self.persons.values()
            if needle in p.display_name or any(needle in a for a in p.aliases)
        ]
        return partial[0] if len(partial) == 1 else None

    def resolve_room(self, text: str) -> Room | None:
        needle = text.strip()
        if not needle:
            return None
        exact = [r for r in self.rooms.values() if r.name == needle or r.id == needle]
        if len(exact) == 1:
            return exact[0]
        partial = [r for r in self.rooms.values() if needle in r.name]
        return partial[0] if len(partial) == 1 else None

    def devices_in_room(self, room_id: str, type_: DeviceType | None = None) -> list[Device]:
        room = self.require_room(room_id)
        devices = [self.devices[d] for d in room.devices]
        if type_ is not None:
            devices = [d for d in devices if d.type == type_]
        return devices

    # ------------------------------------------------------- operations
    def find_operation(self, operation_id: str) -> Operation | None:
        return self.operations.get(operation_id)

    def record_operation(self, operation: Operation) -> Operation:
        if operation.id in self.operations:
            raise ValueError(f"duplicate operation id: {operation.id}")
        self.operations[operation.id] = operation
        return operation

    def finish_operation(
        self,
        operation: Operation,
        status: OperationStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Operation:
        operation.status = status
        operation.result = result
        operation.error = error
        operation.finished_at_ms = now_ms()
        return operation

    def remember_effect(self, operation_id: str, effect: dict[str, Any]) -> None:
        self._operation_effects[operation_id] = effect

    def replay_effect(self, operation_id: str) -> dict[str, Any] | None:
        """Return the stored outcome so retries never apply twice."""
        return self._operation_effects.get(operation_id)

    # ------------------------------------------------------- device writes
    def set_light(
        self,
        device_id: str,
        on: bool | None = None,
        brightness: int | None = None,
        precondition_version: int | None = None,
    ) -> Device:
        device = self.require_device(device_id)
        if device.type != DeviceType.LIGHT:
            raise OutOfRange(f"device {device_id} is not a light")
        if not device.online:
            raise Offline(f"device {device_id} is offline")
        if precondition_version is not None and device.version != precondition_version:
            raise VersionConflict(
                f"device {device_id} version is {device.version}, expected {precondition_version}"
            )
        if brightness is not None:
            if not isinstance(brightness, int) or isinstance(brightness, bool):
                raise OutOfRange("brightness must be an integer 0-100")
            if not 0 <= brightness <= 100:
                raise OutOfRange(f"brightness {brightness} outside 0-100")
            device.state["brightness"] = brightness
            # A brightness change implies the light is on.
            device.state["on"] = True
        if on is not None:
            device.state["on"] = bool(on)
        device.version += 1
        self._commit("device_changed", {"device_id": device_id, "state": dict(device.state), "version": device.version})
        return device

    def set_ac(
        self,
        device_id: str,
        on: bool | None = None,
        mode: str | None = None,
        target_temp: float | None = None,
        precondition_version: int | None = None,
    ) -> Device:
        device = self.require_device(device_id)
        if device.type != DeviceType.AC:
            raise OutOfRange(f"device {device_id} is not an air conditioner")
        if not device.online:
            raise Offline(f"device {device_id} is offline")
        if precondition_version is not None and device.version != precondition_version:
            raise VersionConflict(
                f"device {device_id} version is {device.version}, expected {precondition_version}"
            )
        if mode is not None:
            try:
                device.state["mode"] = AcMode(mode).value
            except ValueError as exc:
                allowed = ", ".join(m.value for m in AcMode)
                raise OutOfRange(f"unsupported ac mode {mode!r}; allowed: {allowed}") from exc
        if target_temp is not None:
            if not isinstance(target_temp, (int, float)) or isinstance(target_temp, bool):
                raise OutOfRange("target_temp must be a number")
            value = float(target_temp)
            if not self.temp_min <= value <= self.temp_max:
                raise OutOfRange(
                    f"target_temp {value} outside allowed {self.temp_min}-{self.temp_max}"
                )
            device.state["target_temp"] = value
        if on is not None:
            device.state["on"] = bool(on)
        device.version += 1
        self._commit("device_changed", {"device_id": device_id, "state": dict(device.state), "version": device.version})
        return device

    def set_device_online(self, device_id: str, online: bool) -> Device:
        device = self.require_device(device_id)
        device.online = bool(online)
        device.version += 1
        self._commit(
            "device_changed",
            {"device_id": device_id, "online": device.online, "version": device.version},
        )
        return device

    def set_room_temp(self, room_id: str, temp: float) -> Room:
        """Test panel only: simulated room temperature, independent of the AC."""
        room = self.require_room(room_id)
        room.simulated_temp = float(temp)
        room.version += 1
        self._commit(
            "room_changed",
            {"room_id": room_id, "simulated_temp": room.simulated_temp, "version": room.version},
        )
        return room

    # ------------------------------------------------------ conversations
    def create_conversation(
        self, test_speaker: str | None, source_room_id: str | None
    ) -> Conversation:
        if source_room_id is not None and source_room_id not in self.rooms:
            raise InvalidRoom(f"unknown room: {source_room_id}")
        conversation = Conversation(
            id=new_id("conv"),
            test_speaker=test_speaker,
            source_room_id=source_room_id,
        )
        self.conversations[conversation.id] = conversation
        return conversation

    def touch_conversation(
        self,
        conversation_id: str,
        recent_targets: list[str] | None = None,
        pending_clarification: str | None = None,
    ) -> Conversation:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise NotFound(f"unknown conversation: {conversation_id}")
        if recent_targets is not None:
            conversation.recent_targets = list(recent_targets)
        conversation.pending_clarification = pending_clarification
        conversation.last_activity_ms = now_ms()
        conversation.version += 1
        return conversation

    def end_conversation(self, conversation_id: str) -> Conversation:
        """Exit clears pending clarification and recent targets."""
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise NotFound(f"unknown conversation: {conversation_id}")
        conversation.recent_targets = []
        conversation.pending_clarification = None
        conversation.last_activity_ms = now_ms()
        conversation.version += 1
        return conversation

    # --------------------------------------------------------- broadcasts
    def enqueue_broadcast(
        self,
        room_id: str,
        text: str,
        target_person_ids: list[str] | None = None,
    ) -> BroadcastTask:
        self.require_room(room_id)
        if not text or not text.strip():
            raise OutOfRange("broadcast text must not be empty")
        task = BroadcastTask(
            id=new_id("bc"),
            room_id=room_id,
            text=text.strip(),
            target_person_ids=list(target_person_ids or []),
        )
        self.broadcasts[task.id] = task
        self.broadcast_queue.append(task.id)
        self._commit("broadcast_queued", {"task_id": task.id, "room_id": room_id})
        return task

    def head_broadcast(self) -> BroadcastTask | None:
        """Unity consumes only the queue head; there is never parallel playback."""
        while self.broadcast_queue:
            task = self.broadcasts.get(self.broadcast_queue[0])
            if task is None or task.state in (
                BroadcastState.PLAYED,
                BroadcastState.FAILED,
                BroadcastState.CANCELLED,
            ):
                self.broadcast_queue.pop(0)
                continue
            return task
        return None

    def start_broadcast(self, task_id: str) -> BroadcastTask:
        task = self._require_broadcast(task_id)
        head = self.head_broadcast()
        if head is None or head.id != task_id:
            raise VersionConflict(
                f"broadcast {task_id} is not at the queue head; only one task may play at a time"
            )
        if task.state != BroadcastState.QUEUED:
            raise VersionConflict(f"broadcast {task_id} is {task.state.value}, expected queued")
        task.state = BroadcastState.PLAYING
        task.started_at_ms = now_ms()
        task.receipt_id = new_id("receipt")
        task.version += 1
        self._commit("broadcast_started", {"task_id": task_id, "receipt_id": task.receipt_id})
        return task

    def complete_broadcast(self, task_id: str, receipt_id: str, success: bool, error: str | None = None) -> BroadcastTask:
        """Record the playback receipt. Only ``played`` may be reported as done."""
        task = self._require_broadcast(task_id)
        if task.state != BroadcastState.PLAYING:
            raise VersionConflict(f"broadcast {task_id} is {task.state.value}, expected playing")
        if not receipt_id or receipt_id != task.receipt_id:
            # A mismatched or missing receipt counts as failure; no auto replay.
            task.state = BroadcastState.FAILED
            task.error = "receipt mismatch"
            task.finished_at_ms = now_ms()
            task.version += 1
            self._commit("broadcast_finished", {"task_id": task_id, "state": task.state.value})
            return task
        task.state = BroadcastState.PLAYED if success else BroadcastState.FAILED
        task.error = error
        task.finished_at_ms = now_ms()
        task.version += 1
        self._commit("broadcast_finished", {"task_id": task_id, "state": task.state.value})
        return task

    def fail_broadcast(self, task_id: str, error: str) -> BroadcastTask:
        """TTS or playback failure: fail the task, do not play, do not retry."""
        task = self._require_broadcast(task_id)
        task.state = BroadcastState.FAILED
        task.error = error
        task.finished_at_ms = now_ms()
        task.version += 1
        self._commit("broadcast_finished", {"task_id": task_id, "state": task.state.value})
        return task

    def _require_broadcast(self, task_id: str) -> BroadcastTask:
        task = self.broadcasts.get(task_id)
        if task is None:
            raise NotFound(f"unknown broadcast task: {task_id}")
        return task

    def broadcast_phrase(self, task_id: str) -> str:
        """Return the only honest wording for a task's current state."""
        task = self._require_broadcast(task_id)
        room = self.require_room(task.room_id)
        if task.state == BroadcastState.PLAYED:
            return f"已在{room.name}播报"
        if task.state == BroadcastState.PLAYING:
            return f"正在{room.name}播报"
        if task.state == BroadcastState.QUEUED:
            return f"已安排在{room.name}播报"
        if task.state == BroadcastState.FAILED:
            return f"{room.name}播报失败"
        return f"{room.name}播报已取消"


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_default_state(config_path: Path | None = None) -> HomeState:
    """Build the three-room virtual house described in the design."""
    cfg = load_config(config_path)
    state = HomeState(cfg.get("comfort", {}))
    rooms = cfg.get("rooms")
    if not rooms:
        rooms = [
            {"id": "living_room", "name": "客厅", "simulated_temp": 28.0},
            {"id": "bedroom", "name": "卧室", "simulated_temp": 27.0},
            {"id": "kitchen", "name": "厨房", "simulated_temp": 29.0},
        ]
    for room in rooms:
        state.add_room(room["id"], room["name"], room.get("simulated_temp", 27.0))

    devices = cfg.get("devices")
    if not devices:
        devices = []
        for room in rooms:
            devices.append(
                {
                    "id": f"{room['id']}_light",
                    "room_id": room["id"],
                    "type": "light",
                    "name": f"{room['name']}的灯",
                    "state": {"on": False, "brightness": 50},
                }
            )
            devices.append(
                {
                    "id": f"{room['id']}_ac",
                    "room_id": room["id"],
                    "type": "ac",
                    "name": f"{room['name']}的空调",
                    "state": {
                        "on": False,
                        "mode": AcMode.COOL.value,
                        "target_temp": state.comfort_temp,
                    },
                }
            )
    for device in devices:
        state.add_device(
            device["id"],
            device["room_id"],
            DeviceType(device["type"]),
            device["name"],
            state=device.get("state"),
            online=device.get("online", True),
        )

    persons = cfg.get("persons")
    if not persons:
        persons = [
            # No default room: a person's location is only ever what the camera
            # observes, so a stopped vision service must read as unknown.
            {"id": "dad", "display_name": "爸爸", "aliases": ["父亲", "老爸"], "room_id": None},
            {"id": "mom", "display_name": "妈妈", "aliases": ["母亲", "老妈"], "room_id": None},
            {"id": "child", "display_name": "孩子", "aliases": ["小朋友", "宝宝"], "room_id": None},
        ]
    for person in persons:
        state.add_person(
            person["id"],
            person["display_name"],
            aliases=person.get("aliases"),
            room_id=person.get("room_id"),
            x=person.get("x"),
            y=person.get("y"),
        )
    return state
