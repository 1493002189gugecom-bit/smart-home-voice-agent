"""Speaker embedding gates and transport, with no model and no microphone.

Everything here uses numpy on synthetic audio and a fake ONNX session, so the
whole embedding path is exercised without onnxruntime installed and without
running inference — which is the point: the gates must be provable on their own.
"""

from __future__ import annotations

import math
import sys

import numpy as np
import pytest

from speaker import (
    DEFAULT_MIN_SPEECH_SECONDS,
    MAX_RESAMPLE_RATIO,
    SpeakerEmbedder,
    SpeakerModelError,
    VoiceQuality,
    describe_quality,
    estimate_snr_db,
    normalize_embedding,
    resample,
)

RATE = 16000


def tone(seconds: float, amplitude: float = 0.45, rate: int = RATE, frequency: float = 220.0) -> np.ndarray:
    """Syllable-like bursts over a quiet floor.

    A steady sine would be a poor stand-in for speech: it has no dynamic range, so
    the SNR gate correctly refuses it and the test would prove nothing about audio
    that a person actually produced.
    """
    rng = np.random.default_rng(7)
    t = np.arange(int(seconds * rate)) / rate
    carrier = np.sin(2 * math.pi * frequency * t)
    gate = (np.sin(2 * math.pi * 3.0 * t) > 0).astype("float64")
    floor = rng.normal(0.0, 0.002, carrier.size)
    return (amplitude * carrier * gate + floor).astype("float32")


def silence(seconds: float, rate: int = RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype="float32")


# ------------------------------------------------------------------ quality


def test_a_long_clear_utterance_passes():
    quality = describe_quality(tone(2.0), RATE)

    assert isinstance(quality, VoiceQuality)
    assert quality.ok is True
    assert quality.reason is None
    assert quality.seconds == pytest.approx(2.0, abs=0.01)
    assert quality.snr_db is not None


def test_a_short_utterance_is_refused_even_when_it_is_also_silent():
    """Length is reported first: "say more" and "your mic is dead" are different
    problems, and the user can only act on the right one."""
    quality = describe_quality(silence(DEFAULT_MIN_SPEECH_SECONDS / 3), RATE)

    assert quality.ok is False
    assert quality.reason == "too_short"


def test_a_long_silence_is_reported_as_silent():
    quality = describe_quality(silence(3.0), RATE)

    assert quality.reason == "silent"


def test_a_clipped_recording_is_refused():
    clipped = np.clip(tone(2.0, amplitude=3.0), -1.0, 1.0).astype("float32")

    assert describe_quality(clipped, RATE).reason == "clipping"


def test_a_quiet_room_is_reported_as_low_snr():
    """Speech only slightly above the noise floor must not be embedded."""
    rng = np.random.default_rng(11)
    noisy = (rng.normal(0.0, 0.05, RATE * 2) + 0.051 * np.sin(np.linspace(0, 40, RATE * 2))).astype("float32")

    quality = describe_quality(noisy, RATE)

    assert quality.ok is False
    assert quality.reason == "low_snr"
    assert quality.snr_db is not None


def test_the_snr_estimate_needs_enough_frames_to_mean_anything():
    assert estimate_snr_db(tone(0.02), RATE) is None


def test_a_zero_sample_rate_is_a_programming_error():
    with pytest.raises(ValueError):
        describe_quality(tone(2.0), 0)


def test_quality_serialises_without_biometric_detail():
    payload = describe_quality(tone(2.0), RATE).to_dict()

    assert set(payload) == {"ok", "reason", "seconds", "snr_db"}


# ----------------------------------------------------------------- resampling


def test_matching_rates_are_left_alone():
    audio = tone(0.5)

    assert resample(audio, RATE, RATE).shape == audio.shape


def test_upsampling_preserves_duration():
    audio = tone(1.0, rate=8000)

    converted = resample(audio, 8000, 16000)

    assert converted.size == pytest.approx(16000, abs=2)
    assert converted.dtype == np.float32


def test_an_absurd_rate_conversion_is_refused():
    with pytest.raises(ValueError):
        resample(tone(1.0, rate=int(RATE / (MAX_RESAMPLE_RATIO + 1))), int(RATE / (MAX_RESAMPLE_RATIO + 1)), RATE)


def test_empty_audio_resamples_to_empty():
    assert resample(np.zeros(0, dtype="float32"), RATE, RATE).size == 0


# -------------------------------------------------------------- normalisation


def test_a_model_output_becomes_a_unit_vector():
    vector = normalize_embedding([3.0, 4.0])

    assert vector == pytest.approx((0.6, 0.8))
    assert math.isclose(sum(value * value for value in vector), 1.0)


def test_a_flat_model_output_is_rejected():
    with pytest.raises(ValueError):
        normalize_embedding([0.0, 0.0])


def test_a_non_finite_model_output_is_rejected():
    with pytest.raises(ValueError):
        normalize_embedding([1.0, float("nan")])


def test_an_oversized_model_output_is_rejected():
    with pytest.raises(ValueError):
        normalize_embedding([1.0] * 4096)


# ------------------------------------------------------------------- embedder


class FakeStream:
    """Mirrors sherpa_onnx's embedding stream: accept waveform, then compute."""

    def __init__(self):
        self.accepted = []
        self.finished = False

    def accept_waveform(self, sample_rate=None, waveform=None):
        self.accepted.append((sample_rate, waveform))

    def input_finished(self):
        self.finished = True


class FakeExtractor:
    """Returns a fixed vector, so the wiring is provable without a model."""

    def __init__(self, output=None, error=None, dim=3, ready=True):
        self.output = [1.0, 0.0, 0.0] if output is None else output
        self.error = error
        self.dim = dim
        self.ready = ready
        self.streams = []

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, _stream):
        return self.ready

    def compute(self, _stream):
        if self.error is not None:
            raise self.error
        return self.output


class FakeSherpaConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def validate(self):
        # Real sherpa-onnx reports a missing or unusable model here rather than
        # failing inside native code; the fake mirrors the "missing" case.
        return kwargs_model_exists(self.kwargs)


def kwargs_model_exists(kwargs) -> bool:
    from pathlib import Path as _Path

    return _Path(str(kwargs.get("model", ""))).is_file()


class FakeSherpa:
    """Stands in for the sherpa_onnx module, so the native path is testable."""

    def __init__(self, dim=192, valid=None):
        self.calls = []
        self.dim = dim
        self.valid = valid

    def SpeakerEmbeddingExtractorConfig(self, **kwargs):  # noqa: N802 - mirrors the library
        self.calls.append(kwargs)
        config = FakeSherpaConfig(**kwargs)
        if self.valid is not None:
            config.validate = lambda: self.valid
        return config

    def SpeakerEmbeddingExtractor(self, _config):  # noqa: N802 - mirrors the library
        return FakeExtractor(dim=self.dim)


def embedder(tmp_path, extractor=None, **kwargs):
    model = tmp_path / "speaker.onnx"
    model.write_bytes(b"not a real model")
    created = []

    def factory(path):
        created.append(path)
        return extractor if extractor is not None else FakeExtractor()

    instance = SpeakerEmbedder(model, extractor_factory=factory, **kwargs)
    return instance, created


def test_a_good_utterance_returns_a_normalised_vector(tmp_path):
    instance, created = embedder(tmp_path)

    vector, quality = instance.embed(tone(2.0), RATE)

    assert quality.ok is True
    assert vector == pytest.approx((1.0, 0.0, 0.0))
    assert len(created) == 1


def test_bad_audio_never_loads_the_model(tmp_path):
    """A cough must cost nothing and must not touch the model."""
    instance, created = embedder(tmp_path)

    vector, quality = instance.embed(tone(0.2), RATE)

    assert vector is None
    assert quality.reason == "too_short"
    assert created == []


def test_the_extractor_is_loaded_once_and_reused(tmp_path):
    instance, created = embedder(tmp_path)

    instance.embed(tone(2.0), RATE)
    instance.embed(tone(2.0), RATE)

    assert len(created) == 1


def test_input_audio_is_resampled_and_declared_at_the_model_rate(tmp_path):
    extractor = FakeExtractor()
    instance, _ = embedder(tmp_path, extractor)

    # Two seconds, because anything shorter is refused before the model is opened.
    instance.embed(tone(2.0, rate=8000), 8000)

    # Two seconds of 8 kHz audio becomes two seconds of 16 kHz audio, and sherpa is
    # told the rate rather than left to assume it.
    sample_rate, waveform = extractor.streams[0].accepted[0]
    assert sample_rate == 16000
    assert waveform.shape == (32000,)
    assert extractor.streams[0].finished is True


def test_a_missing_model_file_is_reported_with_a_closed_code(tmp_path):
    instance = SpeakerEmbedder(tmp_path / "absent.onnx")

    with pytest.raises(SpeakerModelError) as caught:
        instance.embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_model_missing"


def test_no_configured_model_is_reported_as_missing_not_as_a_crash():
    instance = SpeakerEmbedder(None)

    assert instance.configured is False
    with pytest.raises(SpeakerModelError) as caught:
        instance.embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_model_missing"


def test_a_failing_extractor_becomes_a_closed_code(tmp_path):
    instance, _ = embedder(tmp_path, FakeExtractor(error=RuntimeError("bad tensor")))

    with pytest.raises(SpeakerModelError) as caught:
        instance.embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_model_failed"


def test_a_useless_model_output_becomes_a_closed_code(tmp_path):
    instance, _ = embedder(tmp_path, FakeExtractor(output=[0.0, 0.0, 0.0]))

    with pytest.raises(SpeakerModelError) as caught:
        instance.embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_model_failed"


def test_a_stream_that_is_not_ready_becomes_a_closed_code(tmp_path):
    """sherpa reports an unusable buffer through `is_ready`, not an exception."""
    instance, _ = embedder(tmp_path, FakeExtractor(ready=False))

    with pytest.raises(SpeakerModelError) as caught:
        instance.embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_model_failed"


# ------------------------------------------------------------- the native loader


def test_the_native_loader_passes_model_provider_and_threads(monkeypatch, tmp_path):
    fake = FakeSherpa()
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    model = tmp_path / "speaker.onnx"
    model.write_bytes(b"pretend model")

    SpeakerEmbedder(model, provider="cpu", num_threads=4).embed(tone(2.0), RATE)

    assert fake.calls == [{"model": str(model), "num_threads": 4, "debug": False, "provider": "cpu"}]


def test_a_model_sherpa_refuses_to_validate_is_a_closed_code(monkeypatch, tmp_path):
    fake = FakeSherpa(valid=False)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    model = tmp_path / "speaker.onnx"
    model.write_bytes(b"pretend model")

    with pytest.raises(SpeakerModelError) as caught:
        SpeakerEmbedder(model).embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_model_failed"


def test_an_unusable_embedding_dimension_fails_at_load_not_later(monkeypatch, tmp_path):
    """A zero dimension would otherwise surface at the registry, far from the cause."""
    monkeypatch.setitem(sys.modules, "sherpa_onnx", FakeSherpa(dim=0))
    model = tmp_path / "speaker.onnx"
    model.write_bytes(b"pretend model")

    with pytest.raises(SpeakerModelError) as caught:
        SpeakerEmbedder(model).embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_model_failed"


def test_a_missing_sherpa_runtime_is_reported_as_such(monkeypatch, tmp_path):
    # `None` in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    model = tmp_path / "speaker.onnx"
    model.write_bytes(b"pretend model")

    with pytest.raises(SpeakerModelError) as caught:
        SpeakerEmbedder(model).embed(tone(2.0), RATE)

    assert caught.value.code == "speaker_runtime_missing"


def test_an_unknown_error_code_is_a_programming_error():
    with pytest.raises(ValueError):
        SpeakerModelError("something_else")
