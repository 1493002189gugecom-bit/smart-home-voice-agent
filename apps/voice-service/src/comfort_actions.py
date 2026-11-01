"""Deterministic comfort policy over the existing catalog and confirmed tools."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import config
from agent_client import AgentCancelled
from agent_tools import ToolArgumentError, ToolResult, new_operation_id


@dataclass
class ComfortOutcome:
    text: str
    ok: bool
    error_code: str | None = None
    results: list[ToolResult] = field(default_factory=list)
    ignored: bool = False


class ComfortActions:
    def __init__(self, executor, *, clock=time.monotonic):
        self.executor = executor
        self.clock = clock
        self.last_attempt = {}
        self.pending = None

    def reset(self):
        # Conversation expiry removes pending targets, but not the short cooldown.
        self.pending = None

    def pending_need(self, speaker_id):
        if self.pending is not None:
            need, actor, deadline = self.pending
            # This is used only with a newly spoken explicit room. A short room
            # answer need not match a voiceprint; another known speaker must not
            # inherit the previous person's pending need.
            if self.clock() < deadline and (actor == speaker_id or speaker_id is None or actor is None):
                return need
        return None

    @staticmethod
    def _check_cancel(cancel):
        if cancel is not None and cancel.is_set():
            raise AgentCancelled()

    def _resolve(self, need, room, speaker_id):
        tool = "adjust_ac" if need in {"cooler", "warmer"} else "set_light"
        if room is None:
            return self.executor.resolve_self_device(tool, speaker_id)
        kind = "ac" if tool == "adjust_ac" else "light"
        matches = [d for d in self.executor.catalog()
                   if d.get("type") == kind and d.get("controllable")
                   and (d.get("room_name") == room or d.get("room_id") == room)]
        if len(matches) != 1:
            raise ToolArgumentError("device_ambiguous", f"{room}没有唯一可选的设备，请明确说出设备")
        return str(matches[0]["id"]), str(matches[0].get("room_name") or room)

    def execute(self, need: str, room: str | None, speaker_id: str | None,
                cancel: threading.Event | None = None) -> ComfortOutcome:
        self._check_cancel(cancel)
        if need not in {"cooler", "warmer", "brighter", "dimmer"}:
            return ComfortOutcome("没有识别到可执行的舒适需求", False, "invalid_comfort_need")
        try:
            device_id, room_name = self._resolve(need, room, speaker_id)
        except ToolArgumentError as exc:
            if room is None:
                self.pending = (need, speaker_id, self.clock() + 60.0)
            return ComfortOutcome(exc.message, False, exc.code)
        self._check_cancel(cancel)
        self.pending = None
        now = self.clock()
        cooldown = config.comfort_cooldown_seconds()
        # One device must not be nudged twice just because a short utterance
        # failed voiceprint matching, or a second person repeated the complaint.
        key = (device_id, need)
        self.last_attempt = {k: at for k, at in self.last_attempt.items() if now - at < cooldown}
        if key in self.last_attempt:
            return ComfortOutcome("", True, "comfort_cooldown", ignored=True)
        # Bound the map even if the catalog or speaker registry changes rapidly.
        if len(self.last_attempt) >= 256:
            self.last_attempt.pop(next(iter(self.last_attempt)))
        if need in {"cooler", "warmer"}:
            # The backend reads state, fills the configured comfort temperature,
            # applies its step and bounds, and confirms the operation.
            self._check_cancel(cancel)
            self.last_attempt[key] = now
            result = self.executor.execute("adjust_ac", {
                "device_id": device_id, "direction": need,
            }, new_operation_id())
            return self._outcome(result, room_name)

        observed = self.executor.execute("query_device_status", {"device_id": device_id})
        self._check_cancel(cancel)
        if not observed.ok:
            return self._outcome(observed, room_name)
        data = observed.data if isinstance(observed.data, dict) else {}
        devices = data.get("devices")
        devices = devices if isinstance(devices, list) else []
        matches = [d for d in devices if isinstance(d, dict) and d.get("id") == device_id]
        if len(matches) != 1 or matches[0].get("online") is not True:
            return ComfortOutcome(f"暂时无法确认{room_name}灯光的在线状态", False, "state_unavailable", [observed])
        state = matches[0].get("state")
        state = state if isinstance(state, dict) else {}
        on, brightness = state.get("on"), state.get("brightness")
        if on is False and need == "dimmer":
            self.last_attempt[key] = now
            return ComfortOutcome(f"{room_name}灯已经关闭了", True, results=[observed])
        if on is False:
            target = config.comfort_light_brightness()
        elif on is True and isinstance(brightness, (int, float)) and not isinstance(brightness, bool) and 0 <= brightness <= 100:
            change = config.comfort_light_step() * (1 if need == "brighter" else -1)
            target = max(1, min(100, round(brightness + change)))
        else:
            return ComfortOutcome(f"暂时查不到{room_name}灯光亮度，请明确说出要设置的亮度", False, "state_unavailable", [observed])
        if on is True and target == brightness:
            self.last_attempt[key] = now
            return ComfortOutcome(f"{room_name}灯已经达到可调亮度边界了", True, results=[observed])
        self._check_cancel(cancel)
        self.last_attempt[key] = now
        result = self.executor.execute("set_light", {
            "device_id": device_id, "on": True, "brightness": target,
        }, new_operation_id())
        outcome = self._outcome(result, room_name)
        outcome.results.insert(0, observed)
        return outcome

    @staticmethod
    def _outcome(result, room_name):
        if result.ok:
            text = result.phrase or "设备操作已确认，但未返回具体状态"
        elif result.error_code == "confirmation_timeout":
            text = "请求已提交，但没有确认到设备状态"
        else:
            text = result.message or "操作失败"
        if room_name not in text:
            text = f"{room_name}：{text}"
        result.resolved_room_name = room_name
        return ComfortOutcome(text, result.ok, result.error_code, [result])
