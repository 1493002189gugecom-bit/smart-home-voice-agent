"""Audio device selection helpers. Device indexes are never persisted."""
from __future__ import annotations

import os
from dataclasses import dataclass

import sounddevice as sd

import config


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    hostapi: str
    channels: int
    default_samplerate: float


@dataclass(frozen=True)
class PlaybackTarget:
    """A concrete, verified way to play audio on one device."""

    device: AudioDevice
    sample_rate: int
    channels: int

    @property
    def index(self) -> int:
        return self.device.index

    @property
    def name(self) -> str:
        return self.device.name

    @property
    def hostapi(self) -> str:
        return self.device.hostapi

    def describe(self) -> str:
        return (
            f"#{self.device.index} {self.device.name} [{self.device.hostapi}] "
            f"{self.sample_rate} Hz x{self.channels}"
        )


# WASAPI is the modern path straight to the hardware endpoint and is preferred
# for playback. Legacy MME and DirectSound are ordered next for input, where
# WDM-KS is avoided: PortAudio reports "Blocking API not supported yet" for
# WDM-KS streams, which is fragile for the capture loop.
_HOST_PRIORITY = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "MME": 2,
    "Windows WDM-KS": 3,
}

# Endpoints that are not a microphone or a speaker: loopbacks, virtual cables and
# the legacy mapper aliases. They frequently report themselves as usable and are
# even the *system default* (this machine's was once a virtual device, which
# silently produced garbage audio), so "follow the system device" must skip them
# unless the user pinned one deliberately.
#
# Deliberately narrow: a bare "virtual" would flag "HyperX Virtual Surround Sound",
# which is a real headset whose name merely contains the word.
_VIRTUAL_ENDPOINT_HINTS = (
    "虚拟音频", "virtual audio", "virtual cable", "virtual device", "virtual microphone",
    "loopback", "回环", "映射器", "mapper", "主声音捕获", "primary sound",
    "steam streaming", "立体声混音", "stereo mix", "vb-audio", "voicemeeter",
)


def is_virtual_endpoint(device: AudioDevice) -> bool:
    """True for loopbacks, virtual cables and mapper aliases."""

    name = device.name.casefold()
    return any(hint in name for hint in _VIRTUAL_ENDPOINT_HINTS)


def list_input_devices() -> list[AudioDevice]:
    hostapis = sd.query_hostapis()
    result = []
    for index, raw in enumerate(sd.query_devices()):
        if int(raw["max_input_channels"]) <= 0:
            continue
        host_name = str(hostapis[int(raw["hostapi"])]["name"])
        result.append(
            AudioDevice(
                index=index,
                name=str(raw["name"]),
                hostapi=host_name,
                channels=int(raw["max_input_channels"]),
                default_samplerate=float(raw["default_samplerate"]),
            )
        )
    return result


def list_output_devices() -> list[AudioDevice]:
    hostapis = sd.query_hostapis()
    result = []
    for index, raw in enumerate(sd.query_devices()):
        if int(raw["max_output_channels"]) <= 0:
            continue
        host_name = str(hostapis[int(raw["hostapi"])]["name"])
        result.append(
            AudioDevice(
                index=index,
                name=str(raw["name"]),
                hostapi=host_name,
                channels=int(raw["max_output_channels"]),
                default_samplerate=float(raw["default_samplerate"]),
            )
        )
    return result


def _match_candidates(devices: list[AudioDevice], patterns: list[str]) -> list[AudioDevice]:
    """Return devices matching the patterns, preserving the pattern order."""
    ordered: list[AudioDevice] = []
    seen: set[int] = set()
    for pattern in patterns:
        needle = pattern.casefold()
        matched = sorted(
            (d for d in devices if needle in d.name.casefold()),
            key=lambda d: (_HOST_PRIORITY.get(d.hostapi, 99), d.index),
        )
        for device in matched:
            if device.index not in seen:
                seen.add(device.index)
                ordered.append(device)
    return ordered


def system_default_index(kind: str) -> int | None:
    """Return the current PortAudio default for one direction, if present."""
    try:
        pair = sd.default.device
        index = int(pair[0 if kind == "input" else 1])
    except (TypeError, ValueError, IndexError, sd.PortAudioError):
        return None
    return index if index >= 0 else None


def order_candidates(devices: list[AudioDevice], default_index: int | None, patterns: list[str]) -> list[AudioDevice]:
    """Order devices for automatic selection: the system's current one first.

    Real endpoints come before virtual ones at every level, so a virtual endpoint
    that happens to be the system default cannot take over the microphone or the
    speaker. It stays in the list, last: if nothing real exists the service should
    still start and report honestly, not refuse to run.
    """

    matched = _match_candidates(devices, patterns)
    default = next((item for item in devices if item.index == default_index), None)

    ordered: list[AudioDevice] = []
    for allow_virtual in (False, True):
        for candidate in ([default] if default else []) + matched:
            if candidate is None or candidate in ordered:
                continue
            if is_virtual_endpoint(candidate) and not allow_virtual:
                continue
            ordered.append(candidate)
    return ordered


def _automatic_candidates(devices: list[AudioDevice], patterns: list[str], kind: str) -> list[AudioDevice]:
    return order_candidates(devices, system_default_index(kind), patterns)


def _selected_candidates(
    devices: list[AudioDevice], patterns: list[str], kind: str, preferred: dict[str, str] | None,
) -> list[AudioDevice]:
    automatic = _automatic_candidates(devices, patterns, kind)
    if preferred is None:
        return automatic
    selected = [item for item in devices if item.name == preferred.get("name") and item.hostapi == preferred.get("hostapi")]
    return [*selected, *(item for item in automatic if item not in selected)]


def _select(
    devices: list[AudioDevice],
    patterns: list[str],
    sample_rate: int,
    check,
    kind: str,
    *,
    automatic: bool = False,
) -> AudioDevice:
    candidates = _automatic_candidates(devices, patterns, kind) if automatic else _match_candidates(devices, patterns)
    errors = []
    for device in candidates:
        try:
            check(
                device=device.index,
                channels=1,
                samplerate=sample_rate,
                dtype="float32",
            )
            return device
        except sd.PortAudioError as exc:
            errors.append(f"{device.index} {device.name} ({device.hostapi}): {exc}")

    available = "\n".join(f"  {d.index}: {d.name} [{d.hostapi}]" for d in devices)
    detail = "\n".join(errors) if errors else "no device matched the preferred names"
    raise RuntimeError(
        f"No usable {kind} for {patterns!r} at {sample_rate} Hz.\n"
        f"Candidate errors:\n{detail}\n\nAvailable {kind} devices:\n{available}\n\n"
        f"Set SMART_HOME_INPUT_DEVICE / SMART_HOME_OUTPUT_DEVICE to override the name list."
    )


def select_playback_target(
    name_contains: str | None = None,
    sample_rate: int = config.TTS_SAMPLE_RATE,
    patterns: list[str] | None = None,
    preferred: dict[str, str] | None = None,
) -> PlaybackTarget:
    """Select an output device *and* a verified channel/rate combination.

    Some virtual-surround endpoints reject mono or the 24 kHz speech rate on
    WASAPI, so stereo and the device-native rate are tried as well. Returning the
    working combination avoids the failure mode where a legacy host API accepts
    writes but produces no audible output.
    """
    automatic = patterns is None and name_contains is None and not os.environ.get("SMART_HOME_OUTPUT_DEVICE")
    if patterns is None:
        patterns = [name_contains] if name_contains else config.output_device_candidates()

    devices = list_output_devices()
    candidates = _selected_candidates(devices, patterns, "output", preferred) if automatic else _match_candidates(devices, patterns)
    errors: list[str] = []

    for device in candidates:
        attempts: list[tuple[int, int]] = [
            (sample_rate, 1),
            (sample_rate, 2),
            (int(device.default_samplerate), 2),
            (config.OUTPUT_FALLBACK_SAMPLE_RATE, 2),
            (44100, 2),
        ]
        seen: set[tuple[int, int]] = set()
        for rate, channels in attempts:
            if rate <= 0 or (rate, channels) in seen:
                continue
            seen.add((rate, channels))
            try:
                sd.check_output_settings(
                    device=device.index,
                    channels=channels,
                    samplerate=rate,
                    dtype="float32",
                )
                return PlaybackTarget(device=device, sample_rate=rate, channels=channels)
            except sd.PortAudioError as exc:
                errors.append(
                    f"{device.index} {device.name} ({device.hostapi}) {rate}Hz x{channels}: {exc}"
                )

    available = "\n".join(f"  {d.index}: {d.name} [{d.hostapi}]" for d in devices)
    detail = "\n".join(errors) if errors else "no device matched the preferred names"
    raise RuntimeError(
        f"No usable playback target for {patterns!r}.\n"
        f"Attempts:\n{detail}\n\nAvailable output devices:\n{available}"
    )


def select_input_device(
    name_contains: str | None = None,
    sample_rate: int = 16000,
    patterns: list[str] | None = None,
    preferred: dict[str, str] | None = None,
) -> AudioDevice:
    """Use the working system default, then configured physical fallbacks."""
    automatic = patterns is None and name_contains is None and not os.environ.get("SMART_HOME_INPUT_DEVICE")
    if patterns is None:
        patterns = [name_contains] if name_contains else config.input_device_candidates()
    devices = list_input_devices()
    if automatic and preferred is not None:
        candidates = _selected_candidates(devices, patterns, "input", preferred)
        errors = []
        for item in candidates:
            try:
                sd.check_input_settings(device=item.index, channels=1, samplerate=sample_rate, dtype="float32")
                return item
            except sd.PortAudioError as exc:
                errors.append(f"{item.index} {item.name}: {exc}")
        raise RuntimeError("No usable input. " + "; ".join(errors))
    return _select(devices, patterns, sample_rate, sd.check_input_settings, "input", automatic=automatic)


def select_output_device(
    name_contains: str | None = None,
    sample_rate: int = 24000,
    patterns: list[str] | None = None,
) -> AudioDevice:
    """Select a physical output device by ordered name preference."""
    if patterns is None:
        patterns = [name_contains] if name_contains else config.output_device_candidates()
    return _select(list_output_devices(), patterns, sample_rate, sd.check_output_settings, "output")
