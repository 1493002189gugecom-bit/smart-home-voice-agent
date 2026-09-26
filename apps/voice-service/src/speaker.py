"""Local speaker embeddings for the voice service.

Top-level imports are standard library only. `sherpa_onnx` is imported inside the
loader so that importing the voice loop never depends on the speaker model being
present. No model is ever downloaded: a missing file is reported as
`speaker_model_missing` instead.

The model is a sherpa-onnx speaker-embedding ONNX file — the 3D-Speaker ERes2Net or
CAM++ family from that project's `speaker-recongition-models` release. sherpa-onnx
runs the feature frontend itself, so this module hands it a raw waveform at the
model's rate and receives one embedding; that is why no mel-filterbank code lives
here. `sherpa_onnx` is already a dependency of this service, so voiceprints need no
extra install — only the model file.

Nothing here decides *who* the speaker is. This module turns one utterance into a
single normalised vector plus a quality verdict; matching it against the enrolled
gallery happens in vision-service, which is the only place holding voiceprints.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SAMPLE_RATE = 16000
# The single most important gate: below this the vector is not worth comparing.
DEFAULT_MIN_SPEECH_SECONDS = 1.5
DEFAULT_MIN_SNR_DB = 8.0
# Fraction of samples pinned to full scale that counts as a clipped recording.
DEFAULT_MAX_CLIPPED_RATIO = 0.01
# Refuse an implausible rate conversion instead of emitting a mangled vector.
MAX_RESAMPLE_RATIO = 4.0
# Mirrors the ceiling enforced by the registry that stores these vectors.
MAX_EMBEDDING_DIM = 2048
# Every reason the console is allowed to explain to the user.
QUALITY_REASONS = ("too_short", "silent", "clipping", "low_snr")
ERROR_CODES = ("speaker_model_missing", "speaker_runtime_missing", "speaker_model_failed")


class SpeakerModelError(RuntimeError):
    """A closed set of codes; never carries audio, paths or model internals."""

    def __init__(self, code: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError("unknown speaker error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VoiceQuality:
    """Outcome of the pre-embedding audio gates.

    `reason` is a stable snake_case code so the enrolment UI can say what to fix
    rather than showing a generic failure. It is `None` only when `ok` is true.
    """

    ok: bool
    reason: str | None = None
    seconds: float = 0.0
    snr_db: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "seconds": round(self.seconds, 3),
            "snr_db": None if self.snr_db is None else round(self.snr_db, 1),
        }


def estimate_snr_db(samples, sample_rate, frame_ms: float = 25.0) -> float | None:
    """Speech-to-noise ratio from frame energies: loud frames over quiet ones.

    Deliberately crude. It only has to separate "somebody spoke clearly" from "the
    microphone mostly heard a room", so it is never presented as a calibrated dB
    figure and returns `None` when there are too few frames to compare.
    """

    import numpy as np

    values = np.asarray(samples, dtype="float64").reshape(-1)
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    usable = (values.size // frame) * frame
    if usable < frame * 4:
        return None
    energies = (values[:usable].reshape(-1, frame) ** 2).mean(axis=1)
    speech = float(np.percentile(energies, 90))
    noise = float(np.percentile(energies, 10))
    if noise <= 0.0:
        # A digital-silence floor would make the ratio infinite; use the smallest
        # observed frame instead so the number stays finite and comparable.
        noise = max(float(energies.min()), 1e-12)
    return 10.0 * math.log10(max(speech, 1e-12) / noise)


def describe_quality(
    samples,
    sample_rate: int,
    *,
    min_seconds: float = DEFAULT_MIN_SPEECH_SECONDS,
    min_snr_db: float = DEFAULT_MIN_SNR_DB,
    max_clipped_ratio: float = DEFAULT_MAX_CLIPPED_RATIO,
) -> VoiceQuality:
    """Gate one utterance before it is embedded.

    Order matters and is deliberate: length first, because a too-short clip must be
    reported as `too_short` even when it is also silent — that is the difference
    between "say a longer sentence" and "your microphone is not working".
    """

    import numpy as np

    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    values = np.asarray(samples, dtype="float64").reshape(-1)
    seconds = float(values.size) / float(sample_rate)
    if seconds < min_seconds:
        return VoiceQuality(False, "too_short", seconds)
    if float(np.max(np.abs(values))) < 1e-4:
        return VoiceQuality(False, "silent", seconds)
    if float(np.mean(np.abs(values) >= 0.999)) > max_clipped_ratio:
        return VoiceQuality(False, "clipping", seconds)
    snr = estimate_snr_db(values, sample_rate)
    if snr is not None and snr < min_snr_db:
        return VoiceQuality(False, "low_snr", seconds, snr)
    return VoiceQuality(True, None, seconds, snr)


def resample(samples, source_rate: int, target_rate: int):
    """Linear-interpolation resample, adequate for speaker embeddings.

    Speaker models tolerate far larger perturbations than the aliasing a linear
    resampler introduces, and the voice environment has no scipy. The ratio is
    bounded: a wrong device rate must surface as a configuration error rather than
    as a silently mangled vector.
    """

    import numpy as np

    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    values = np.asarray(samples, dtype="float64").reshape(-1)
    if source_rate == target_rate or values.size == 0:
        return values.astype("float32")
    ratio = float(source_rate) / float(target_rate)
    if not (1.0 / MAX_RESAMPLE_RATIO <= ratio <= MAX_RESAMPLE_RATIO):
        raise ValueError("unsupported sample rate conversion")
    target_count = max(1, int(round((values.size / source_rate) * target_rate)))
    positions = np.linspace(0.0, values.size - 1, target_count)
    return np.interp(positions, np.arange(values.size), values).astype("float32")


def normalize_embedding(values) -> tuple[float, ...]:
    """Flatten a model output into one unit vector, refusing anything unusable."""

    import numpy as np

    array = np.asarray(values, dtype="float64").reshape(-1)
    if array.size == 0 or array.size > MAX_EMBEDDING_DIM:
        raise ValueError("embedding has an unusable size")
    if not bool(np.all(np.isfinite(array))):
        raise ValueError("embedding contains a non-finite value")
    norm = float(np.linalg.norm(array))
    if norm <= 0.0:
        raise ValueError("embedding has zero norm")
    return tuple(float(value / norm) for value in array)


class SpeakerEmbedder:
    """Runs the speaker model over one utterance.

    `extractor_factory` exists so tests — and any alternative runtime — can supply
    their own extractor object. The default loads sherpa-onnx lazily, which keeps the
    model genuinely optional for everything that is not speaker identification.
    """

    def __init__(
        self,
        model_path,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        min_seconds: float = DEFAULT_MIN_SPEECH_SECONDS,
        provider: str = "cpu",
        num_threads: int = 1,
        extractor_factory=None,
    ) -> None:
        self.model_path = Path(model_path) if model_path is not None else None
        self.sample_rate = int(sample_rate)
        self.min_seconds = float(min_seconds)
        self.provider = str(provider)
        self.num_threads = max(1, int(num_threads))
        self._extractor_factory = extractor_factory
        self._extractor = None

    @property
    def configured(self) -> bool:
        """True when a model path was supplied at all; says nothing about the file."""
        return self.model_path is not None

    def _load(self):
        if self._extractor is not None:
            return self._extractor
        if self.model_path is None or not self.model_path.is_file():
            raise SpeakerModelError("speaker_model_missing")
        if self._extractor_factory is not None:
            self._extractor = self._extractor_factory(self.model_path)
            return self._extractor
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise SpeakerModelError("speaker_runtime_missing") from exc
        try:  # pragma: no cover - requires a real model file
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.model_path),
                num_threads=self.num_threads,
                debug=False,
                provider=self.provider,
            )
            # `validate()` reports a missing or unusable file as False instead of
            # failing inside native code, so it is checked before the extractor is
            # built: a clean closed code rather than a crash.
            if not config.validate():
                raise SpeakerModelError("speaker_model_failed")
            extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        except SpeakerModelError:
            raise
        except Exception as exc:
            raise SpeakerModelError("speaker_model_failed") from exc
        dimension = int(getattr(extractor, "dim", 0) or 0)
        if dimension <= 0 or dimension > MAX_EMBEDDING_DIM:
            # An unusable dimension would otherwise surface much later, at the
            # registry, where the cause is far harder to see.
            raise SpeakerModelError("speaker_model_failed")
        self._extractor = extractor
        return self._extractor

    def embed(self, samples, sample_rate: int, min_seconds: float | None = None):
        """Return ``(embedding | None, quality)``; bad audio is never an exception.

        A raising path here would take down the voice loop over a cough, so every
        audio problem is a verdict instead. Only a model problem raises, because
        that is a configuration fault the user has to fix.

        `min_seconds` overrides the instance default, which enrolment uses to demand
        a longer sample than an ordinary question needs.
        """

        quality = describe_quality(
            samples, sample_rate,
            min_seconds=self.min_seconds if min_seconds is None else float(min_seconds),
        )
        if not quality.ok:
            return None, quality
        try:
            audio = resample(samples, sample_rate, self.sample_rate)
            extractor = self._load()
            return self._run(extractor, audio), quality
        except SpeakerModelError:
            raise
        except ValueError as exc:
            raise SpeakerModelError("speaker_model_failed") from exc

    def _run(self, extractor, audio) -> tuple[float, ...]:
        import numpy as np

        try:
            # sherpa-onnx owns the feature frontend, so it is handed the raw waveform
            # at the model's rate; `resample` has already made the rate match.
            stream = extractor.create_stream()
            stream.accept_waveform(
                sample_rate=self.sample_rate,
                waveform=np.asarray(audio, dtype="float32").reshape(-1),
            )
            stream.input_finished()
            if not extractor.is_ready(stream):
                raise SpeakerModelError("speaker_model_failed")
            return normalize_embedding(extractor.compute(stream))
        except SpeakerModelError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise SpeakerModelError("speaker_model_failed") from exc
