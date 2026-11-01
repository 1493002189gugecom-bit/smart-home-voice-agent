"""Unit tests for the recording helpers used by the speaker-evaluation corpus.

These exist because the corpus depends on two numbers that are easy to get wrong:
how long the *voiced* part of a clip is, and whether trimming left the waveform
intact. A corpus that misreports its own durations would quietly invalidate the
threshold chosen from it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "voice-check"
sys.path.insert(0, str(TOOLS))

import record_cases  # noqa: E402

RATE = 16000


def tone(seconds: float, amplitude: float = 0.4) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (amplitude * np.sin(2 * np.pi * 180 * t)).astype("float32")


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), dtype="float32")


class TestSpeechSeconds:
    def test_pure_silence_has_no_speech(self):
        assert record_cases.speech_seconds(silence(2.0)) == 0.0

    def test_a_tone_is_measured_at_its_full_length(self):
        measured = record_cases.speech_seconds(tone(2.0))
        assert measured == pytest.approx(2.0, abs=0.1)

    def test_surrounding_silence_does_not_inflate_the_measured_length(self):
        clip = np.concatenate([silence(3.0), tone(1.0), silence(3.0)])
        assert record_cases.speech_seconds(clip) == pytest.approx(1.0, abs=0.15)

    def test_a_clip_shorter_than_one_frame_reports_zero(self):
        assert record_cases.speech_seconds(silence(0.005)) == 0.0

    def test_low_level_noise_alone_is_not_counted_as_speech(self):
        rng = np.random.default_rng(1)
        floor = (1e-5 * rng.standard_normal(RATE * 2)).astype("float32")
        assert record_cases.speech_seconds(floor) == 0.0


class TestTrimSilence:
    def test_leading_and_trailing_silence_is_removed(self):
        clip = np.concatenate([silence(2.0), tone(1.5), silence(2.0)])
        trimmed = record_cases.trim_silence(clip)
        assert len(trimmed) < len(clip)
        # The pad keeps a little margin on each side, so the result is longer than
        # the bare tone but nowhere near the original 5.5 s.
        assert 1.5 * RATE <= len(trimmed) <= 2.2 * RATE

    def test_the_speech_itself_survives_trimming(self):
        clip = np.concatenate([silence(1.0), tone(1.2), silence(1.0)])
        trimmed = record_cases.trim_silence(clip)
        assert record_cases.speech_seconds(trimmed) == pytest.approx(1.2, abs=0.15)

    def test_an_all_silent_clip_is_returned_unchanged(self):
        clip = silence(1.0)
        assert len(record_cases.trim_silence(clip)) == len(clip)

    def test_trimming_never_returns_an_empty_array(self):
        assert len(record_cases.trim_silence(tone(0.2))) > 0
