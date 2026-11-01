"""Pure-Python SSE parsing and Smart Home display formatting."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    id: str
    event: str
    data: str


class SSEDecoder:
    def __init__(self):
        self._utf8 = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""

    def feed(self, chunk: bytes) -> list[StreamEvent]:
        if not chunk:
            return []
        self._buffer += self._utf8.decode(chunk, final=False)
        normalized = self._buffer.replace("\r\n", "\n").replace("\r", "\n")
        events: list[StreamEvent] = []
        while "\n\n" in normalized:
            frame, normalized = normalized.split("\n\n", 1)
            event = self._parse_frame(frame)
            if event is not None:
                events.append(event)
        self._buffer = normalized
        return events

    @staticmethod
    def _parse_frame(frame: str) -> StreamEvent | None:
        event_id = ""
        event_name = "message"
        data: list[str] = []
        for line in frame.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if not separator:
                value = ""
            elif value.startswith(" "):
                value = value[1:]
            if field == "id":
                event_id = value
            elif field == "event":
                event_name = value
            elif field == "data":
                data.append(value)
        if not data:
            return None
        return StreamEvent(event_id, event_name, "\n".join(data))


def devices_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    devices = snapshot.get("devices") if isinstance(snapshot, dict) else None
    if not isinstance(devices, list):
        return {}
    result = {}
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_id = device.get("id")
        if isinstance(device_id, str) and device_id:
            result[device_id] = device
    return result


def _number(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"{value:g}"


def format_device_state(
    device: dict[str, Any] | None,
    *,
    fallback_name: str = "设备",
) -> str:
    if not isinstance(device, dict):
        return f"{fallback_name}  暂无数据"
    name = device.get("name") if isinstance(device.get("name"), str) else fallback_name
    if device.get("online") is False:
        return f"{name}  离线"
    state = device.get("state") if isinstance(device.get("state"), dict) else {}
    kind = device.get("type")

    if kind == "sensor":
        temperature = _number(state.get("temperature"))
        return f"{name}  {temperature}°C" if temperature is not None else f"{name}  暂无数据"

    on = state.get("on")
    if on is False:
        return f"{name}  关"
    if on is not True:
        return f"{name}  暂无数据"

    if kind == "light":
        brightness = _number(state.get("brightness"))
        return f"{name}  开" + (f" {brightness}%" if brightness is not None else "")
    if kind == "ac":
        mode_names = {"cool": "制冷", "fan_only": "送风", "heat": "制热", "auto": "自动"}
        mode = mode_names.get(state.get("mode"), "开")
        target = _number(state.get("target_temp"))
        return f"{name}  {mode}" + (f" {target}°C" if target is not None else "")
    if kind == "switch":
        power = _number(state.get("power"))
        return f"{name}  开" + (f" {power}W" if power is not None and power != "0" else "")
    return f"{name}  开"
