"""Full barge-in: the user's own voice stops the assistant.

The VAD here is a scripted double so every branch is decided by this file rather
than by a model file: which blocks count as speech, which are the reply's own
leakage, and how long the user must keep talking.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass

import numpy as np

from barge_in import (
    BargeInMonitor,
    BargeInSettings,
    PlaybackBargeIn,
    SpeechBargeDetector,
    block_rms,
    take_queued_blocks,
    voiced_seconds,
)

SAMPLE_RATE = 16000
BLOCK = 512


@dataclass
class ScriptedVad:
    """Speech exactly where the caller says so; every block still feeds state."""

    speech_values: list[bool]

    def __post_init__(self):
        self.seen = 0
        self.resets = 0

    def is_speech(self, block) -> bool:  # noqa: ARG002 - scripted, not acoustic
        index = self.seen
        self.seen += 1
        return self.speech_values[index] if index < len(self.speech_values) else False

    def reset(self) -> None:
        self.resets += 1


def quiet(value: float = 0.001) -> np.ndarray:
    return np.full(BLOCK, value, dtype=np.float32)


def loud(value: float = 0.4) -> np.ndarray:
    return np.full(BLOCK, value, dtype=np.float32)


def settings(**overrides) -> BargeInSettings:
    base = {
        "min_speech_seconds": 0.30,
        "min_rms": 0.02,
        "echo_ratio": 1.5,
        "echo_calibration_seconds": 0.6,
        "pre_roll_blocks": 3,
    }
    base.update(overrides)
    return BargeInSettings(**base)


def test_block_rms_measures_loudness_not_length():
    assert block_rms(np.zeros(BLOCK, dtype=np.float32)) == 0.0
    assert block_rms(np.empty(0, dtype=np.float32)) == 0.0
    assert 0.39 < block_rms(loud(0.4)) < 0.41


def test_sustained_speech_is_required_before_a_reply_is_interrupted():
    # Ten speech blocks is 0.32 s of audio: enough for the 0.30 s gate.
    vad = ScriptedVad([True] * 10)
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())

    fired = [detector.feed(loud()) for _ in range(10)]

    assert fired.count(True) == 1
    assert fired[-1] is True
    assert detector.triggered


def test_a_short_burst_never_interrupts():
    vad = ScriptedVad([True] * 3)
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())

    assert [detector.feed(loud()) for _ in range(3)] == [False, False, False]
    assert not detector.triggered


def test_a_quiet_block_is_not_the_user_even_when_the_vad_says_speech():
    """A click the VAD likes but nobody could hear over the reply is not a barge."""
    vad = ScriptedVad([True] * 10)
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())

    assert [detector.feed(quiet(0.005)) for _ in range(10)] == [False] * 10
    assert not detector.triggered


def test_playback_calibration_absorbs_the_reply_and_raises_the_bar():
    # 0.6 s of calibration at 512/16000 is 19 blocks.
    calibration = 19
    vad = ScriptedVad([True] * (calibration + 40))
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())
    detector.start_playback()

    # The reply's own leakage: loud enough that an unguarded gate would fire.
    for _ in range(calibration):
        assert detector.feed(np.full(BLOCK, 0.1, dtype=np.float32)) is False
    assert detector.echo_reference_rms is not None
    assert detector.threshold_rms() > 0.1

    # The user at the same level as the reply is filtered out...
    assert detector.feed(np.full(BLOCK, 0.12, dtype=np.float32)) is False
    # ...and only something clearly louder gets through, for long enough.
    fired = [detector.feed(loud(0.4)) for _ in range(10)]
    assert fired[-1] is True


def test_quieter_user_speech_interrupts_when_the_reply_audio_is_known():
    """Speaker leakage alone stays quiet, but a user can speak below its RMS."""

    reference = np.random.default_rng(7).normal(0, 0.15, SAMPLE_RATE).astype(np.float32)
    detector = SpeechBargeDetector(ScriptedVad([True] * 31), SAMPLE_RATE, settings())
    detector.start_playback(reference=reference, reference_rate=SAMPLE_RATE)

    for index in range(20):
        echo = 0.5 * reference[index * BLOCK:(index + 1) * BLOCK]
        assert not detector.feed(echo)

    mixed_results = []
    for index in range(20, 30):
        echo = 0.5 * reference[index * BLOCK:(index + 1) * BLOCK]
        start = index * BLOCK
        voice = 0.04 * np.sin(2 * np.pi * 180 * np.arange(start, start + BLOCK) / SAMPLE_RATE)
        mixed_results.append(detector.feed((echo + voice).astype(np.float32)))

    assert mixed_results[-1] is True
    assert detector.triggered


def test_nothing_can_interrupt_during_the_calibration_window():
    """The head of every reply is unmeasurable, so it is not interruptible."""

    vad = ScriptedVad([True] * 40)
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())
    detector.start_playback()

    assert [detector.feed(loud()) for _ in range(18)] == [False] * 18
    assert not detector.triggered


def test_without_playback_there_is_no_echo_guard():
    """While the model thinks, only the absolute floor applies."""

    vad = ScriptedVad([True] * 10)
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())
    detector.start_playback()
    detector.stop_playback()

    assert detector.threshold_rms() == settings().min_rms
    assert detector.feed(loud(0.05)) is False  # not yet long enough
    fired = [detector.feed(loud(0.05)) for _ in range(9)]
    assert fired[-1] is True


def test_pre_roll_is_kept_for_a_thinking_barge_and_dropped_during_playback():
    """The pre-roll is either the user's first syllable or our own words."""

    vad = ScriptedVad([False, False, False] + [True] * 10)
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())
    for _ in range(3):
        detector.feed(quiet())
    for _ in range(10):
        detector.feed(loud())

    assert len(detector.captured) == 13
    assert len(detector.take_captured(keep_pre_roll=False)) == 10
    assert detector.captured == []


def test_playback_barge_reports_speech_and_wake_separately():
    class Wake:
        def __init__(self, hits):
            self.hits = list(hits)
            self.keyword = None

        def detect(self, block, sample_rate):
            if self.hits and self.hits.pop(0):
                self.keyword = "小屋小屋"
                return self.keyword
            return None

    vad = ScriptedVad([True] * 10)
    speech = SpeechBargeDetector(vad, SAMPLE_RATE, settings())
    detector = PlaybackBargeIn(speech=speech, wake=Wake([False] * 10))
    blocks: queue.Queue = queue.Queue()
    for _ in range(10):
        blocks.put(loud())

    assert detector.poll(blocks, SAMPLE_RATE) == "speech"
    assert detector.reason == "speech"


def test_a_wake_word_still_interrupts_playback_without_speech():
    class Wake:
        keyword = "你好小屋"

        def detect(self, block, sample_rate):
            return self.keyword

    blocks: queue.Queue = queue.Queue()
    blocks.put(loud())
    detector = PlaybackBargeIn(wake=Wake())

    assert detector.poll(blocks, SAMPLE_RATE) == "wake"


def test_a_playback_detector_needs_at_least_one_source():
    try:
        PlaybackBargeIn()
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:  # pragma: no cover - a detector with no sources can never interrupt
        raise AssertionError("PlaybackBargeIn accepted no source at all")


def test_take_queued_blocks_empties_the_queue_in_order():
    blocks: queue.Queue = queue.Queue()
    first, second = loud(0.3), loud(0.7)
    blocks.put(first)
    blocks.put(second)

    taken = take_queued_blocks(blocks)

    assert len(taken) == 2
    assert block_rms(taken[0]) < block_rms(taken[1])
    assert blocks.empty()


def test_voiced_seconds_reports_how_much_of_the_command_was_captured():
    assert voiced_seconds([loud(), loud()], SAMPLE_RATE) == 2 * BLOCK / SAMPLE_RATE


def test_the_monitor_thread_cancels_while_the_loop_is_busy():
    """The whole point: the caller is blocked, so the microphone is watched here."""

    vad = ScriptedVad([True] * 10)
    detector = SpeechBargeDetector(vad, SAMPLE_RATE, settings())
    blocks: queue.Queue = queue.Queue()
    monitor = BargeInMonitor(detector, blocks, poll_seconds=0.005)
    monitor.start()
    try:
        for _ in range(10):
            blocks.put(loud())
        assert monitor.cancel.wait(timeout=2.0), "the monitor never cancelled the turn"
    finally:
        monitor.stop()

    assert monitor.interrupted
    assert monitor.reason == "speech"
    assert len(monitor.take_captured()) == 10
    assert monitor.take_captured() == []


def test_a_stopped_monitor_leaves_the_queue_alone():
    """Playback reuses the same queue, so stop() must leave no reader behind."""

    detector = SpeechBargeDetector(ScriptedVad([]), SAMPLE_RATE, settings())
    blocks: queue.Queue = queue.Queue()
    monitor = BargeInMonitor(detector, blocks, poll_seconds=0.005)
    monitor.start()
    monitor.stop()
    blocks.put(loud())

    assert not monitor.interrupted
    assert take_queued_blocks(blocks) != []
