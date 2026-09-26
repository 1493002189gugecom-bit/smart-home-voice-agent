"""Full voice interruption: the user may talk over the assistant.

The house has one microphone and it stays open. While a reply is playing, that
microphone hears the reply as well as the user, so "did a person start talking?"
cannot be answered by the VAD alone: it fires on the assistant's own audio. The
detector first aligns the known TTS waveform with each microphone block and
subtracts a matching copy. If that alignment is unreliable, it falls back to a
calibrated loudness gate:

* with a reliable waveform match, the residual is checked immediately and the
  reply's own leakage is kept out of the VAD;
* without one, the first ``echo_calibration_seconds`` of microphone audio are
  measured as leakage and cannot interrupt; later blocks need to be at least
  ``echo_ratio`` times louder than that leakage;
* an interruption additionally needs ``min_speech_seconds`` of sustained VAD
  speech, which is what stops a cough, a door or one loud click from cutting a
  sentence in half;
* while nothing is playing (the model is thinking) there is no echo, so the gate
  collapses to the absolute floor.

Reference subtraction only models one aligned copy of the reply, not a full
acoustic echo canceller. Room reflections and device effects can defeat the match;
on a machine whose speakers are far louder than the user, an interruption can
still be missed. Every fallback threshold is configurable; see ``config.py``.
"""
from __future__ import annotations

import queue
import statistics
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

BLOCK_SIZE = 512


def block_rms(block: Any) -> float:
    """Loudness of one microphone block, 0.0 for silence or an empty block."""

    values = np.asarray(block, dtype=np.float32).reshape(-1)
    if values.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(values))))


@dataclass(frozen=True)
class BargeInSettings:
    """Thresholds for one detector. Defaults are the shipped configuration."""

    min_speech_seconds: float = 0.30
    min_rms: float = 0.02
    echo_ratio: float = 1.5
    echo_calibration_seconds: float = 0.6
    pre_roll_blocks: int = 10
    block_size: int = BLOCK_SIZE


def take_queued_blocks(blocks: "queue.Queue[np.ndarray]") -> list[np.ndarray]:
    """Remove every block already queued, in order. Never blocks, never drops."""

    taken: list[np.ndarray] = []
    while True:
        try:
            taken.append(blocks.get_nowait())
        except queue.Empty:
            return taken


class SpeechBargeDetector:
    """Turn a stream of microphone blocks into "the user has started talking".

    One detector is reused across the two windows where it is needed — while the
    model is thinking (no echo guard) and while the reply is playing (echo guard
    active) — because only one of them can ever be running at a time. Call
    :meth:`reset` between windows.
    """

    def __init__(self, vad, sample_rate: int, settings: BargeInSettings | None = None):
        self.vad = vad
        self.sample_rate = int(sample_rate)
        self.settings = settings or BargeInSettings()
        self._block_size = max(1, int(self.settings.block_size))
        self._pre_roll: deque[np.ndarray] = deque(maxlen=max(1, int(self.settings.pre_roll_blocks)))
        # Kept apart on purpose: during playback the pre-roll is the tail of the
        # reply the microphone also heard, and only the caller knows whether
        # sending those blocks to ASR would mean answering our own words.
        self._lead: list[np.ndarray] = []
        self._voiced: list[np.ndarray] = []
        self._voiced_samples = 0
        self._triggered = False
        self._echo_guard = False
        self._echo_rms: float | None = None
        self._calibration: list[float] = []
        self._calibration_remaining = 0
        self._playback_reference: np.ndarray | None = None
        self._playback_samples_seen = 0

    # ------------------------------------------------------------------ control
    def reset(self) -> None:
        """Start a new window; the VAD stream is reset with it."""

        self._pre_roll.clear()
        self._lead = []
        self._voiced = []
        self._voiced_samples = 0
        self._triggered = False
        self._echo_guard = False
        self._echo_rms = None
        self._calibration = []
        self._calibration_remaining = 0
        self._playback_reference = None
        self._playback_samples_seen = 0
        reset = getattr(self.vad, "reset", None)
        if callable(reset):
            reset()

    def start_playback(
        self, reference: np.ndarray | None = None, reference_rate: int | None = None,
    ) -> None:
        """Arm the echo guard with the reply waveform when it is available."""

        # Capture state is cleared here as well as in `reset`, so arming a
        # detector that already fired cannot interrupt the new reply with the
        # previous sentence's audio.
        self._pre_roll.clear()
        self._lead = []
        self._voiced = []
        self._voiced_samples = 0
        self._triggered = False
        self._echo_guard = True
        self._echo_rms = None
        self._calibration = []
        self._calibration_remaining = max(
            1,
            int(round(self.settings.echo_calibration_seconds * self.sample_rate / self._block_size)),
        )
        self._playback_reference = None
        self._playback_samples_seen = 0
        if reference is not None and reference_rate:
            samples = np.asarray(reference, dtype=np.float32).reshape(-1)
            if reference_rate != self.sample_rate and samples.size:
                count = max(1, int(round(samples.size * self.sample_rate / reference_rate)))
                samples = np.interp(
                    np.linspace(0, samples.size - 1, count),
                    np.arange(samples.size), samples,
                ).astype(np.float32)
            self._playback_reference = samples

    def stop_playback(self) -> None:
        """Disarm the echo guard: nothing is coming out of the speaker any more."""

        self._echo_guard = False
        self._echo_rms = None
        self._calibration = []
        self._calibration_remaining = 0
        self._playback_reference = None

    def _without_playback_echo(self, block: np.ndarray) -> np.ndarray | None:
        """Remove a well-matched copy of the known reply from one mic block.

        Device latency is unknown, so find the best aligned reference within a
        bounded window near the amount of microphone audio already consumed.
        A poor match falls back to the original calibrated loudness guard.
        """

        reference = self._playback_reference
        if reference is None or len(reference) < len(block):
            return None
        start = max(0, self._playback_samples_seen - int(0.8 * self.sample_rate))
        end = min(len(reference), self._playback_samples_seen + int(0.25 * self.sample_rate) + len(block))
        window = reference[start:end]
        stride = max(1, self.sample_rate // 4000)
        mic_small = block[::stride]
        ref_small = window[::stride]
        if len(ref_small) < len(mic_small):
            return None
        correlation = np.correlate(ref_small, mic_small, mode="valid")
        squared = np.square(ref_small, dtype=np.float64)
        cumulative = np.concatenate(([0.0], np.cumsum(squared)))
        energy = cumulative[len(mic_small):] - cumulative[:-len(mic_small)]
        mic_energy = float(np.dot(mic_small, mic_small))
        if mic_energy <= 1e-8:
            return None
        scores = np.abs(correlation) / np.sqrt(np.maximum(energy * mic_energy, 1e-12))
        best = int(np.argmax(scores))
        if scores[best] < 0.55:
            return None

        approximate = start + best * stride
        best_residual: np.ndarray | None = None
        best_energy = float("inf")
        for offset in range(max(0, approximate - stride), min(len(reference) - len(block), approximate + stride) + 1):
            candidate = reference[offset:offset + len(block)]
            power = float(np.dot(candidate, candidate))
            if power <= 1e-8:
                continue
            gain = float(np.dot(block, candidate)) / power
            residual = block - gain * candidate
            residual_energy = float(np.dot(residual, residual))
            if residual_energy < best_energy:
                best_residual, best_energy = residual, residual_energy
        return best_residual

    # ---------------------------------------------------------------- read-only
    @property
    def triggered(self) -> bool:
        return self._triggered

    @property
    def echo_reference_rms(self) -> float | None:
        """The measured reply leakage, or None before calibration finished."""

        return self._echo_rms

    @property
    def captured(self) -> list[np.ndarray]:
        """The blocks that made up the interruption, oldest first."""

        return list(self._lead) + list(self._voiced)

    def take_captured(self, keep_pre_roll: bool = True) -> list[np.ndarray]:
        """Read the captured blocks and forget them, so they are used once.

        `keep_pre_roll=False` drops the blocks that preceded the confirmed speech.
        That is what the playback path needs: those blocks are the reply's own
        leakage, and transcribing them would let the assistant act on its own
        words.
        """

        captured = self.captured if keep_pre_roll else list(self._voiced)
        self._lead = []
        self._voiced = []
        self._voiced_samples = 0
        return captured

    def threshold_rms(self) -> float:
        """Loudness a block must exceed to count as the user rather than the reply."""

        floor = float(self.settings.min_rms)
        if self._echo_guard and self._echo_rms:
            return max(floor, float(self._echo_rms) * float(self.settings.echo_ratio))
        return floor

    # --------------------------------------------------------------------- feed
    def feed(self, block: np.ndarray) -> bool:
        """Absorb one block; return True once the user's speech is confirmed."""

        if self._triggered:
            return True
        raw = np.asarray(block, dtype=np.float32).reshape(-1)
        reduced = self._without_playback_echo(raw) if self._echo_guard else None
        self._playback_samples_seen += raw.size
        candidate = reduced if reduced is not None else raw
        loud = block_rms(candidate)
        speech = bool(self.vad.is_speech(candidate))

        if self._echo_guard:
            if self._calibration_remaining > 0:
                self._calibration_remaining -= 1
                self._calibration.append(block_rms(raw))
                if self._calibration_remaining == 0:
                    # The mean, not the peak: one loud syllable of the reply must
                    # not set a bar the user can never clear.
                    self._echo_rms = statistics.fmean(self._calibration) if self._calibration else 0.0
                if reduced is None:
                    return False
            if reduced is None and loud < self.threshold_rms():
                speech = False

        if speech and loud >= float(self.settings.min_rms):
            if not self._voiced:
                self._lead = list(self._pre_roll)
                self._pre_roll.clear()
            self._voiced.append(candidate)
            self._voiced_samples += candidate.size
            if self._voiced_samples >= self.settings.min_speech_seconds * self.sample_rate:
                self._triggered = True
                return True
        elif not self._voiced:
            self._pre_roll.append(block)
        return self._triggered


class BargeInMonitor:
    """Listen for an interruption on a worker thread while the loop is busy.

    The voice loop is single threaded: while it waits for the cloud model it cannot
    also read the microphone, so a user who starts a new sentence during that wait
    would simply not be heard. This thread reads the same barge queue for exactly
    that window and sets ``cancel`` the moment speech is confirmed, which is what
    lets the loop abandon a reply nobody wants any more.
    """

    def __init__(
        self,
        detector: SpeechBargeDetector,
        blocks: "queue.Queue[np.ndarray]",
        *,
        poll_seconds: float = 0.02,
        on_trigger=None,
    ):
        self.detector = detector
        self.blocks = blocks
        self.poll_seconds = float(poll_seconds)
        self.cancel = threading.Event()
        self.reason: str | None = None
        self._on_trigger = on_trigger
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("monitor already started")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="barge-in", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                block = self.blocks.get(timeout=self.poll_seconds)
            except queue.Empty:
                continue
            if self.detector.feed(block):
                self.reason = "speech"
                self.cancel.set()
                if self._on_trigger is not None:
                    self._on_trigger("speech")
                return

    def stop(self) -> None:
        """Stop listening and wait for the thread to leave the queue alone."""

        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    @property
    def interrupted(self) -> bool:
        return self.reason is not None

    def take_captured(self) -> list[np.ndarray]:
        return self.detector.take_captured()


class PlaybackBargeIn:
    """Poll both interruption sources on the thread that is playing audio.

    Playback writes in 50 ms chunks and asks between them, so no worker thread is
    needed here: the microphone queue is drained in the same call and a confirmed
    speech run, or a fresh wake word, stops the reply.
    """

    def __init__(self, *, speech: SpeechBargeDetector | None = None, wake=None):
        if speech is None and wake is None:
            raise ValueError("at least one barge-in source is required")
        self.speech = speech
        self.wake = wake
        self.reason: str | None = None

    def poll(self, blocks: "queue.Queue[np.ndarray]", sample_rate: int) -> str | None:
        """Feed every queued block to both detectors; return why playback stopped."""

        if self.reason is not None:
            return self.reason
        while True:
            try:
                block = blocks.get_nowait()
            except queue.Empty:
                return self.reason
            if self.speech is not None and self.speech.feed(block):
                self.reason = "speech"
            detect = getattr(self.wake, "detect", None)
            if detect is not None and detect(block, sample_rate):
                self.reason = "wake"
            if self.reason is not None:
                return self.reason


def voiced_seconds(blocks: Iterable[np.ndarray], sample_rate: int) -> float:
    """Total duration of the given blocks: how long the user's interruption was."""

    samples = sum(int(np.asarray(block).reshape(-1).size) for block in blocks)
    return samples / float(sample_rate) if sample_rate else 0.0
