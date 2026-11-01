"""Thin CPU-only wrappers around sherpa-onnx voice models."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import sherpa_onnx

import config


def create_asr() -> sherpa_onnx.OfflineRecognizer:
    paths = config.asr_paths()
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(paths["model"]),
        tokens=str(paths["tokens"]),
        num_threads=2,
        use_itn=True,
        language="zh",
        provider="cpu",
        debug=False,
    )


class Transcript(str):
    def __new__(cls, text, *, emotion="", event="", lang=""):
        value = super().__new__(cls, text)
        value.emotion, value.event, value.lang = emotion, event, lang
        return value


def transcribe(recognizer: sherpa_onnx.OfflineRecognizer, samples: np.ndarray, sample_rate: int = 16000) -> str:
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, np.asarray(samples, dtype=np.float32))
    recognizer.decode_stream(stream)
    result = stream.result
    return Transcript(result.text.strip(), emotion=getattr(result, "emotion", ""),
                      event=getattr(result, "event", ""), lang=getattr(result, "lang", ""))


class TtsEngine:
    """Text-to-speech facade with fixed backends.

    ``edge`` uses Microsoft Edge neural voices over the network and is the
    default because it sounds far more natural than the local model. There is no
    automatic local substitution: if the network call fails, the caller must
    treat the announcement as failed.
    """

    def __init__(self, provider: str | None = None, voice: str | None = None) -> None:
        self.provider = (provider or config.tts_provider()).lower()
        self.voice = voice or config.edge_voice()
        if self.provider not in ("edge", "kokoro", "cosyvoice"):
            raise ValueError(f"unknown TTS provider: {self.provider!r}")
        self._kokoro: sherpa_onnx.OfflineTts | None = None

    # -- kokoro (local) ---------------------------------------------------
    def _kokoro_engine(self) -> sherpa_onnx.OfflineTts:
        if self._kokoro is None:
            paths = config.tts_paths()
            kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(paths["model"]),
                voices=str(paths["voices"]),
                tokens=str(paths["tokens"]),
                data_dir=str(paths["data_dir"]),
                lexicon=paths["lexicon"],
            )
            model = sherpa_onnx.OfflineTtsModelConfig(
                kokoro=kokoro,
                num_threads=2,
                provider="cpu",
                debug=False,
            )
            tts_config = sherpa_onnx.OfflineTtsConfig(model=model, max_num_sentences=1)
            if not tts_config.validate():
                raise RuntimeError("invalid Kokoro TTS configuration")
            self._kokoro = sherpa_onnx.OfflineTts(tts_config)
        return self._kokoro

    # -- playback helpers -------------------------------------------------
    def render_to(self, text: str, wav: Path) -> tuple[np.ndarray, int]:
        """Synthesize into a 16-bit WAV file and return the samples."""
        import soundfile as sf

        samples, rate = self.synthesize(text)
        wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(wav), samples, rate, subtype="PCM_16")
        return samples, rate

    def synthesize(self, text: str, *, cancel=None) -> tuple[np.ndarray, int]:
        """Return (mono float32 samples, sample rate) or raise on failure."""
        if self.provider == "kokoro":
            tts = self._kokoro_engine()
            audio = tts.generate(text, sid=config.tts_speaker_id(), speed=config.tts_speed())
            if audio is None or len(audio.samples) == 0:
                raise RuntimeError("Kokoro generated no samples")
            return np.asarray(audio.samples, dtype=np.float32), int(audio.sample_rate)
        if self.provider == "cosyvoice":
            chunks = [samples for samples, _ in self.stream(text, cancel=cancel)]
            if not chunks:
                raise RuntimeError("speech_empty_audio")
            return np.concatenate(chunks), 24000
        if cancel is None:
            return _edge_synthesize(text, self.voice)
        return _edge_synthesize(text, self.voice, cancel=cancel)

    @property
    def supports_streaming(self):
        return self.provider in {"cosyvoice", "edge"}

    def stream(self, text, *, cancel=None):
        from cloud_speech import stream_tts
        if self.provider == "cosyvoice":
            yield from stream_tts(text, cancel=cancel)
        elif self.provider == "edge":
            import re
            import time
            remaining = config.speech_timeout()
            # Edge returns compressed audio per sentence. Keep each sentence
            # bounded so long replies can start playing before the rest is ready.
            parts = re.findall(r"[^。！？!?；;\n]{1,100}[。！？!?；;\n]?|[。！？!?；;\n]", text)
            for part in parts:
                if part.strip():
                    if remaining <= 0:
                        raise TimeoutError("speech_timeout")
                    started = time.monotonic()
                    try:
                        chunk = _edge_synthesize(part, self.voice, cancel=cancel, timeout=remaining)
                    except Exception as error:
                        error.voice_stage = "tts_synthesis"
                        raise
                    remaining -= time.monotonic()-started
                    yield chunk
        else:
            yield self.synthesize(text, cancel=cancel)


def _decode_edge_audio(payload: bytes) -> tuple[np.ndarray, int]:
    """Decode Edge MP3 bytes into mono float32 samples."""
    import io

    import soundfile as sf

    samples, rate = sf.read(io.BytesIO(payload), dtype="float32", always_2d=False)
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if len(samples) == 0:
        raise RuntimeError("edge-tts audio decoded to zero samples")
    return samples, int(rate)


def _edge_synthesize(text: str, voice: str, *, cancel=None, timeout=None) -> tuple[np.ndarray, int]:
    """Fetch MP3 audio from the Edge read-aloud service and decode it.

    Retries transient failures, and also retries a suspiciously slow response
    because the service occasionally stalls instead of erroring.
    """
    import asyncio
    import edge_tts
    from voice_runtime import bounded, run, VoiceCancelled

    async def fetch() -> bytes:
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=config.edge_rate(),
            volume=config.edge_volume(),
            pitch=config.edge_pitch(),
        )
        payload = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                payload.extend(chunk["data"])
        return bytes(payload)

    async def attempts():
        for attempt in range(1, config.EDGE_MAX_ATTEMPTS + 1):
            try:
                payload = await fetch()
                if not payload:
                    raise RuntimeError("speech_empty_audio")
                return payload
            except VoiceCancelled:
                raise
            except Exception as error:
                status = getattr(error, "status", None)
                if status in {400, 401, 403, 404} or attempt == config.EDGE_MAX_ATTEMPTS:
                    raise RuntimeError(f"edge-tts failed after {attempt} attempts") from error
                await asyncio.sleep(config.EDGE_RETRY_DELAY_SECONDS)
    return _decode_edge_audio(run(bounded(attempts(), timeout if timeout is not None else config.speech_timeout(), cancel)))


def create_tts(provider: str | None = None, voice: str | None = None) -> TtsEngine:
    return TtsEngine(provider=provider, voice=voice)


def synthesize(
    tts: TtsEngine,
    text: str,
    speaker_id: int | None = None,
    speed: float | None = None,
) -> tuple[np.ndarray, int]:
    """Backwards-compatible wrapper around ``TtsEngine.synthesize``.

    ``speaker_id``/``speed`` are accepted for the local Kokoro backend; the Edge
    backend encodes speed in ``SMART_HOME_TTS_RATE`` instead.
    """
    if speaker_id is not None or speed is not None:
        if tts.provider != "kokoro":
            raise ValueError("speaker_id/speed only apply to the kokoro provider")
        audio = tts._kokoro_engine().generate(
            text,
            sid=config.tts_speaker_id() if speaker_id is None else speaker_id,
            speed=config.tts_speed() if speed is None else speed,
        )
        if audio is None or len(audio.samples) == 0:
            raise RuntimeError("Kokoro generated no samples")
        return np.asarray(audio.samples, dtype=np.float32), int(audio.sample_rate)
    return tts.synthesize(text)


def create_vad() -> sherpa_onnx.VadModel:
    silero = sherpa_onnx.SileroVadModelConfig(
        model=str(config.vad_model()),
        threshold=0.5,
        min_silence_duration=0.6,
        min_speech_duration=0.25,
        window_size=512,
        max_speech_duration=20,
    )
    vad_config = sherpa_onnx.VadModelConfig(
        silero_vad=silero,
        sample_rate=config.SAMPLE_RATE,
        num_threads=1,
        provider="cpu",
        debug=False,
    )
    if not vad_config.validate():
        raise RuntimeError("invalid Silero VAD configuration")
    return sherpa_onnx.VadModel.create(vad_config)


def create_kws(keywords_file: Path) -> sherpa_onnx.KeywordSpotter:
    paths = config.kws_paths()
    return sherpa_onnx.KeywordSpotter(
        tokens=str(paths["tokens"]),
        encoder=str(paths["encoder"]),
        decoder=str(paths["decoder"]),
        joiner=str(paths["joiner"]),
        keywords_file=str(keywords_file),
        num_threads=2,
        provider="cpu",
        keywords_score=2.0,
        keywords_threshold=0.25,
        num_trailing_blanks=1,
    )
