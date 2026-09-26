"""Playback helpers that resample to a verified output target."""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import sounddevice as sd

from audio_utils import PlaybackTarget


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear resample; adequate for 16k/24k/48k speech playback."""
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if source_rate == target_rate or len(samples) == 0:
        return samples
    duration = len(samples) / float(source_rate)
    target_count = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0.0, len(samples) - 1, num=target_count, dtype=np.float64)
    return np.interp(source_positions, np.arange(len(samples)), samples).astype(np.float32)


def to_channels(samples: np.ndarray, channels: int) -> np.ndarray:
    """Return an interleaved (frames, channels) array."""
    flat = np.asarray(samples, dtype=np.float32).reshape(-1)
    if channels == 1:
        return flat.reshape(-1, 1)
    return np.repeat(flat.reshape(-1, 1), channels, axis=1)


def play(
    samples: np.ndarray,
    source_rate: int,
    target: PlaybackTarget,
    drain_seconds: float = 0.35,
    *,
    chunk_seconds: float = 0.05,
    should_interrupt: Callable[[], bool] | None = None,
    on_stream_started: Callable[[float], None] | None = None,
) -> bool:
    """Play in short chunks; return False when a caller requests interruption."""
    audio = resample(samples, source_rate, target.sample_rate)
    frames = to_channels(audio, target.channels)
    chunk_frames = max(1, int(target.sample_rate * chunk_seconds))
    with sd.OutputStream(
        samplerate=target.sample_rate,
        channels=target.channels,
        dtype="float32",
        device=target.index,
    ) as stream:
        if on_stream_started is not None:
            on_stream_started(float(stream.latency))
        for offset in range(0, len(frames), chunk_frames):
            if should_interrupt is not None and should_interrupt():
                # OutputStream.__exit__ calls stop(), which waits for queued
                # speaker audio. Abort first so an interrupted reply cuts off now.
                stream.abort()
                return False
            stream.write(frames[offset:offset + chunk_frames])
        # A short drain lets the last chunk reach the device. Keep checking so
        # an interruption during that drain is not delayed by the whole tail.
        until = time.monotonic() + drain_seconds
        while time.monotonic() < until:
            if should_interrupt is not None and should_interrupt():
                stream.abort()
                return False
            time.sleep(max(0.0, min(0.02, until - time.monotonic())))
        return True
