"""Local, non-secret audio endpoint preferences; indexes are never durable."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


PREFERENCES_PATH = Path("runtime/voice-agent/audio-devices.json")


def _valid(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"name", "hostapi"}:
        raise ValueError("invalid audio device preference")
    name, hostapi = value["name"], value["hostapi"]
    if not isinstance(name, str) or not name or not isinstance(hostapi, str) or not hostapi:
        raise ValueError("invalid audio device preference")
    return {"name": name, "hostapi": hostapi}


def load() -> dict[str, dict[str, str] | None]:
    if not PREFERENCES_PATH.exists():
        return {"input": None, "output": None}
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid audio preferences")
        return {"input": _valid(data.get("input")), "output": _valid(data.get("output"))}
    except (OSError, ValueError, TypeError):
        return {"input": None, "output": None}


def save(preferences: dict[str, dict[str, str] | None]) -> None:
    data = {"input": _valid(preferences.get("input")), "output": _valid(preferences.get("output"))}
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=PREFERENCES_PATH.parent, prefix="audio-", suffix=".tmp", delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, PREFERENCES_PATH)
    finally:
        temporary.unlink(missing_ok=True)
