"""The six restricted tools exposed to the Agent.

Every write generates its operation id locally and reuses it on retry. The model
never supplies ids, paths, or arbitrary device names — only natural-language
targets that are resolved against authoritative state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models import (
    BroadcastState,
    DeviceType,
    Operation,
    OperationStatus,
    new_id,
    now_ms,
)
from state import HomeState, NotFound, Offline, OutOfRange, StateError, VersionConflict

TOOL_NAMES = (
    "query_room_status",
    "query_person_location",
    "query_device_status",
    "set_light",
    "set_ac",
    "broadcast_to_room",
)


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    operation_id: str | None = None
    # Honest wording the Agent may repeat; never claims delivery or hearing.
    phrase: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "operation_id": self.operation_id,
            "phrase": self.phrase,
        }


class ToolService:
    def __init__(self, state: HomeState, visual_state: Any | None = None):
        self.state = state
        # Camera-owned person locations. None means no visual store is attached,
        # so every person must read as location-unknown rather than simulated.
        self.visual_state = visual_state
        # Counts of device state changes, exposed so tests can prove zero
        # misoperations. Misoperations are writes that should not have happened.
        self.device_writes = 0
        self.misoperations = 0

    # ------------------------------------------------------------ queries
    def query_room_status(self, room: str | None = None) -> ToolResult:
        if room is None:
            rooms = list(self.state.rooms.values())
        else:
            resolved = self.state.resolve_room(room)
            if resolved is None:
                return ToolResult(False, error=f"无法确定房间：{room}", error_code="not_found")
            rooms = [resolved]

        payload = []
        for item in rooms:
            devices = [
                {
                    "id": d.id,
                    "type": d.type.value,
                    "name": d.name,
                    "online": d.online,
                    "state": dict(d.state),
                    "version": d.version,
                }
                for d in self.state.devices_in_room(item.id)
            ]
            payload.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "simulated_temp": item.simulated_temp,
                    "version": observed.get("version", item.version),
                    "devices": devices,
                }
            )
        return ToolResult(True, data={"rooms": payload, "state_version": self.state.version})

    def query_person_location(self, person: str | None = None) -> ToolResult:
        if person is None:
            people = list(self.state.persons.values())
        else:
            resolved = self.state.resolve_person(person)
            if resolved is None:
                return ToolResult(False, error=f"无法确定人物：{person}", error_code="not_found")
            people = [resolved]

        # Location is only ever a fresh confirmed camera observation. Without one
        # the honest answer is "unknown" — never the person's previous room.
        visual = self._visual_person_index()
        payload = []
        for item in people:
            observed = visual.get(item.id) or {}
            room_id = observed.get("room_id")
            room = self.state.rooms.get(room_id) if room_id else None
            payload.append(
                {
                    "id": item.id,
                    "display_name": item.display_name,
                    "aliases": list(item.aliases),
                    "room_id": room_id,
                    "room_name": room.name if room else None,
                    "location_known": bool(room_id),
                    "location_source": "camera",
                    "camera_id": observed.get("camera_id"),
                    "track_id": observed.get("track_id"),
                    "pose": observed.get("pose") or "unknown",
                    "pose_confidence": observed.get("pose_confidence") or 0.0,
                    "observed_at_ms": observed.get("observed_at_ms"),
                    "x": observed.get("x"),
                    "y": observed.get("y"),
                    "version": item.version,
                }
            )
        return ToolResult(True, data={"persons": payload, "state_version": self.state.version})

    def _visual_person_index(self) -> dict[str, dict[str, Any]]:
        """Camera-owned person states keyed by person id; empty means unknown."""
        if self.visual_state is None:
            return {}
        return {item["id"]: item for item in self.visual_state.snapshot(now_ms())}

    def query_device_status(self, device: str | None = None, room: str | None = None) -> ToolResult:
        if device is not None:
            target = self.state.devices.get(device)
            if target is None:
                room_obj = self.state.resolve_room(device)
                if room_obj is not None:
                    return self.query_room_status(room_obj.name)
                return ToolResult(False, error=f"未知设备：{device}", error_code="not_found")
            devices = [target]
        elif room is not None:
            resolved = self.state.resolve_room(room)
            if resolved is None:
                return ToolResult(False, error=f"无法确定房间：{room}", error_code="not_found")
            devices = self.state.devices_in_room(resolved.id)
        else:
            devices = list(self.state.devices.values())

        payload = [
            {
                "id": d.id,
                "room_id": d.room_id,
                "room_name": self.state.rooms[d.room_id].name,
                "type": d.type.value,
                "name": d.name,
                "online": d.online,
                "state": dict(d.state),
                "version": d.version,
            }
            for d in devices
        ]
        return ToolResult(True, data={"devices": payload, "state_version": self.state.version})

    # ------------------------------------------------------------- writes
    def set_light(
        self,
        room: str | None = None,
        device_id: str | None = None,
        on: bool | None = None,
        brightness: int | None = None,
        precondition_version: int | None = None,
        operation_id: str | None = None,
    ) -> ToolResult:
        op, replayed = self._begin("set_light", device_id or room, {
            "on": on,
            "brightness": brightness,
        }, operation_id)
        if replayed is not None:
            return replayed
        assert op is not None

        device, error = self._resolve_device(device_id, room, DeviceType.LIGHT)
        if error is not None:
            return self._fail(op, error, log_misoperation=False)
        if on is None and brightness is None:
            return self._fail(op, ToolResult(False, error="必须指定 on 或 brightness", error_code="invalid_params"))

        # An offline device must not change state; that would be a misoperation.
        if not device.online:
            return self._fail(op, ToolResult(False, error=f"{device.name}现在离线", error_code="offline"))
        # An explicit "turn off" is already satisfied; do not rewrite state.
        if on is False and device.state.get("on") is False and brightness is None:
            result = ToolResult(
                True,
                data={"device": device.id, "state": dict(device.state), "noop": True},
                operation_id=op.id,
                phrase=f"{device.name}本来就是关着的",
            )
            self.state.finish_operation(op, OperationStatus.SUCCEEDED, result=result.data)
            self.state.remember_effect(op.id, result.to_dict())
            return result

        try:
            updated = self.state.set_light(
                device.id, on=on, brightness=brightness, precondition_version=precondition_version
            )
        except StateError as exc:
            return self._fail(op, ToolResult(False, error=exc.message, error_code=exc.code))
        except (TypeError, ValueError) as exc:
            return self._fail(op, ToolResult(False, error=str(exc), error_code="invalid_params"))

        self.device_writes += 1
        result = ToolResult(
            True,
            data={"device": updated.id, "state": dict(updated.state), "version": updated.version},
            operation_id=op.id,
            phrase=self._describe_light(updated),
        )
        self.state.finish_operation(op, OperationStatus.SUCCEEDED, result=result.data)
        self.state.remember_effect(op.id, result.to_dict())
        return result

    def set_ac(
        self,
        room: str | None = None,
        device_id: str | None = None,
        on: bool | None = None,
        mode: str | None = None,
        target_temp: float | None = None,
        precondition_version: int | None = None,
        operation_id: str | None = None,
    ) -> ToolResult:
        op, replayed = self._begin("set_ac", device_id or room, {
            "on": on,
            "mode": mode,
            "target_temp": target_temp,
        }, operation_id)
        if replayed is not None:
            return replayed
        assert op is not None

        device, error = self._resolve_device(device_id, room, DeviceType.AC)
        if error is not None:
            return self._fail(op, error, log_misoperation=False)
        if on is None and mode is None and target_temp is None:
            return self._fail(op, ToolResult(False, error="必须指定 on、mode 或 target_temp", error_code="invalid_params"))

        if not device.online:
            return self._fail(op, ToolResult(False, error=f"{device.name}现在离线", error_code="offline"))

        # Reject out-of-range temperatures before touching state.
        if target_temp is not None:
            if not isinstance(target_temp, (int, float)) or isinstance(target_temp, bool):
                return self._fail(op, ToolResult(False, error="target_temp 必须是数字", error_code="invalid_params"))
            if not self.state.temp_min <= float(target_temp) <= self.state.temp_max:
                return self._fail(
                    op,
                    ToolResult(
                        False,
                        error=(
                            f"目标温度必须在 {self.state.temp_min:g} 到 "
                            f"{self.state.temp_max:g} 度之间"
                        ),
                        error_code="out_of_range",
                    ),
                )
        if on is False and device.state.get("on") is False and mode is None and target_temp is None:
            result = ToolResult(
                True,
                data={"device": device.id, "state": dict(device.state), "noop": True},
                operation_id=op.id,
                phrase=f"{device.name}本来就是关着的",
            )
            self.state.finish_operation(op, OperationStatus.SUCCEEDED, result=result.data)
            self.state.remember_effect(op.id, result.to_dict())
            return result

        try:
            updated = self.state.set_ac(
                device.id,
                on=on,
                mode=mode,
                target_temp=target_temp,
                precondition_version=precondition_version,
            )
        except StateError as exc:
            return self._fail(op, ToolResult(False, error=exc.message, error_code=exc.code))
        except (TypeError, ValueError) as exc:
            return self._fail(op, ToolResult(False, error=str(exc), error_code="invalid_params"))

        self.device_writes += 1
        result = ToolResult(
            True,
            data={"device": updated.id, "state": dict(updated.state), "version": updated.version},
            operation_id=op.id,
            phrase=self._describe_ac(updated),
        )
        self.state.finish_operation(op, OperationStatus.SUCCEEDED, result=result.data)
        self.state.remember_effect(op.id, result.to_dict())
        return result

    def broadcast_to_room(
        self,
        room: str,
        text: str,
        person_ids: list[str] | None = None,
        operation_id: str | None = None,
    ) -> ToolResult:
        op, replayed = self._begin("broadcast_to_room", room, {"text": text}, operation_id)
        if replayed is not None:
            return replayed
        assert op is not None

        resolved = self.state.resolve_room(room)
        if resolved is None:
            return self._fail(op, ToolResult(False, error=f"无法确定房间：{room}", error_code="not_found"))
        if not text or not text.strip():
            return self._fail(op, ToolResult(False, error="播报内容不能为空", error_code="invalid_params"))
        if person_ids:
            for person_id in person_ids:
                if person_id not in self.state.persons:
                    return self._fail(
                        op,
                        ToolResult(False, error=f"未知人物：{person_id}", error_code="not_found"),
                    )

        task = self.state.enqueue_broadcast(resolved.id, text, person_ids)
        op.broadcast_task_id = task.id
        result = ToolResult(
            True,
            data={
                "task_id": task.id,
                "room_id": resolved.id,
                "room_name": resolved.name,
                "state": task.state.value,
            },
            operation_id=op.id,
            # Queued is not played: only "已安排" is honest here.
            phrase=self.state.broadcast_phrase(task.id),
        )
        self.state.finish_operation(op, OperationStatus.SUCCEEDED, result=result.data)
        self.state.remember_effect(op.id, result.to_dict())
        return result

    # ------------------------------------------------------------ internal
    def _begin(
        self,
        kind: str,
        target: str | None,
        params: dict[str, Any],
        operation_id: str | None,
    ) -> tuple[Operation | None, ToolResult | None]:
        """Return (operation, replayed_result).

        Reusing an operation id returns the stored outcome instead of applying
        the change twice, which is what design section 8 requires for retries.
        """
        if operation_id is not None:
            prior = self.state.find_operation(operation_id)
            if prior is not None:
                # A failed operation must replay as a failure. Checking the
                # stored effect first would re-report the original error code
                # and hide the fact that nothing was retried.
                if prior.status in (OperationStatus.FAILED, OperationStatus.REJECTED, OperationStatus.CONFLICT):
                    return None, ToolResult(
                        False,
                        error=prior.error,
                        error_code="replayed_failure",
                        operation_id=operation_id,
                    )
                stored = self.state.replay_effect(operation_id)
                if stored is not None:
                    return None, ToolResult(**stored)
        op = Operation(
            id=operation_id or new_id("op"),
            kind=kind,
            target_id=target,
            params=params,
        )
        self.state.record_operation(op)
        return op, None

    def _fail(
        self,
        op: Operation,
        result: ToolResult,
        log_misoperation: bool = False,
    ) -> ToolResult:
        status = OperationStatus.REJECTED
        if result.error_code == "offline":
            status = OperationStatus.FAILED
        elif result.error_code in ("version_conflict",):
            status = OperationStatus.CONFLICT
        self.state.finish_operation(op, status, error=result.error)
        if log_misoperation:
            self.misoperations += 1
        result.operation_id = op.id
        self.state.remember_effect(op.id, result.to_dict())
        return result

    def _resolve_device(
        self, device_id: str | None, room: str | None, type_: DeviceType
    ) -> tuple[Any, ToolResult | None]:
        if device_id is not None:
            device = self.state.devices.get(device_id)
            if device is None:
                return None, ToolResult(False, error=f"未知设备：{device_id}", error_code="not_found")
            if device.type != type_:
                return None, ToolResult(
                    False,
                    error=f"{device.name}不是{'灯' if type_ == DeviceType.LIGHT else '空调'}",
                    error_code="wrong_type",
                )
            return device, None
        if room is None:
            return None, ToolResult(False, error="必须指定房间或设备", error_code="invalid_params")
        resolved = self.state.resolve_room(room)
        if resolved is None:
            return None, ToolResult(False, error=f"无法确定房间：{room}", error_code="not_found")
        candidates = self.state.devices_in_room(resolved.id, type_)
        if not candidates:
            return None, ToolResult(
                False, error=f"{resolved.name}没有可控制的设备", error_code="not_found"
            )
        if len(candidates) > 1:
            return None, ToolResult(
                False,
                error=f"{resolved.name}有多个候选设备，需要明确指定",
                error_code="ambiguous",
            )
        return candidates[0], None

    @staticmethod
    def _describe_light(device) -> str:
        if not device.state.get("on"):
            return f"已关闭{device.name}"
        brightness = device.state.get("brightness")
        if brightness is None:
            return f"已打开{device.name}"
        return f"已打开{device.name}，亮度 {brightness}"

    @staticmethod
    def _describe_ac(device) -> str:
        if not device.state.get("on"):
            return f"已关闭{device.name}"
        parts = [f"已打开{device.name}"]
        mode = device.state.get("mode")
        if mode:
            mode_names = {
                "cool": "制冷",
                "heat": "制热",
                "fan": "送风",
                "dry": "除湿",
                "auto": "自动",
            }
            parts.append(f"模式{mode_names.get(mode, mode)}")
        temp = device.state.get("target_temp")
        if temp is not None:
            parts.append(f"设定 {temp:g} 度")
        return "，".join(parts)
