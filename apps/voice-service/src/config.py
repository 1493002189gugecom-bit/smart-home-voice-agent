"""Shared configuration for the voice service and offline check tools.

Model files are never committed. They live outside the repository by default,
on the D drive as requested for this machine, and can be overridden with the
SMART_HOME_MODELS_DIR environment variable.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MODELS_DIR = Path(r"D:\smart-home-models")


def models_dir() -> Path:
    """Return the model root directory (env override wins)."""
    raw = os.environ.get("SMART_HOME_MODELS_DIR")
    return Path(raw) if raw else DEFAULT_MODELS_DIR


# Directory names inside the model root, matching the extracted tarballs.
ASR_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
ASR_MODEL = "model.int8.onnx"
ASR_TOKENS = "tokens.txt"

TTS_DIR = "kokoro-int8-multi-lang-v1_1"
TTS_MODEL = "model.int8.onnx"
TTS_VOICES = "voices.bin"
TTS_TOKENS = "tokens.txt"
TTS_DATA_DIR = "espeak-ng-data"
TTS_LEXICON = "lexicon-us-en.txt,lexicon-zh.txt"
TTS_DICT_DIR = "dict"

KWS_DIR = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
KWS_CHUNK16 = True  # chunk-16 => ~320 ms latency, higher accuracy
KWS_TOKEN_TYPE = "phone+ppinyin"
KWS_LEXICON = "en.phone"

VAD_MODEL = "silero_vad.onnx"

# Playback sample rate for generated speech (Kokoro emits 24 kHz).
TTS_SAMPLE_RATE = 24000
# Virtual surround endpoints often expose only 48 kHz; resample when needed.
OUTPUT_FALLBACK_SAMPLE_RATE = 48000

# Automatic selection checks the current Windows default first. If it cannot
# use that endpoint, these names provide ordered fallbacks. Override the whole
# fallback list with SMART_HOME_INPUT_DEVICE (comma-separated substrings).
DEFAULT_INPUT_DEVICE_CANDIDATES = (
    "HyperX",
    "Realtek",
)
DEFAULT_OUTPUT_DEVICE_CANDIDATES = (
    "HyperX",
    "Realtek",
)


def input_device_candidates() -> list[str]:
    """Return ordered name substrings used to pick the physical microphone."""
    raw = os.environ.get("SMART_HOME_INPUT_DEVICE")
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(DEFAULT_INPUT_DEVICE_CANDIDATES)


def output_device_candidates() -> list[str]:
    raw = os.environ.get("SMART_HOME_OUTPUT_DEVICE")
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(DEFAULT_OUTPUT_DEVICE_CANDIDATES)


# Kept for backwards compatibility with earlier scripts/README wording.
DEFAULT_INPUT_DEVICE = DEFAULT_INPUT_DEVICE_CANDIDATES[0]
DEFAULT_OUTPUT_DEVICE = DEFAULT_OUTPUT_DEVICE_CANDIDATES[0]

WAKE_WORD_CANDIDATES = ("小屋小屋", "你好小屋")
SAMPLE_RATE = 16000


# Kokoro v1.1 Chinese voices: 45-48 female (zf_*), 49-52 male (zm_*).
# Audition them with tools/voice-check/audition_voices.py, then set
# SMART_HOME_TTS_SPEAKER to the chosen id.
DEFAULT_TTS_SPEAKER_ID = 47
DEFAULT_TTS_SPEED = 1.0


def tts_speaker_id() -> int:
    """Return the configured Kokoro speaker id."""
    raw = os.environ.get("SMART_HOME_TTS_SPEAKER")
    if raw and raw.strip().isdigit():
        return int(raw.strip())
    return DEFAULT_TTS_SPEAKER_ID


def tts_speed() -> float:
    """Return the configured speech rate."""
    raw = os.environ.get("SMART_HOME_TTS_SPEED")
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_TTS_SPEED
        if 0.5 <= value <= 2.0:
            return value
    return DEFAULT_TTS_SPEED


# Text-to-speech. Edge neural voices are used for announcements because they are
# far more natural than the local 82M Kokoro model. This requires network access:
# when it fails the announcement is reported as failed rather than silently
# substituted.
TTS_PROVIDER = "edge"
EDGE_VOICE = "zh-CN-XiaoxiaoNeural"
EDGE_VOICE_CANDIDATES = (
    "zh-CN-XiaoxiaoNeural",  # female, warm
    "zh-CN-XiaoyiNeural",  # female, lively
    "zh-CN-YunxiNeural",  # male, lively
    "zh-CN-YunyangNeural",  # male, professional
    "zh-CN-YunjianNeural",  # male, passionate
)
EDGE_RATE = "+0%"
EDGE_VOLUME = "+0%"
EDGE_PITCH = "+0Hz"
EDGE_TIMEOUT_SECONDS = 30.0
# Online synthesis needs retries: transient network hiccups otherwise surface as
# announcement failures. A slow request is retried rather than accepted.
EDGE_MAX_ATTEMPTS = 3
EDGE_RETRY_DELAY_SECONDS = 1.5
EDGE_SLOW_SECONDS = 6.0
# Fallback sample rate used only if the returned MP3 reports no rate.
TTS_FALLBACK_SAMPLE_RATE = 24000


def tts_provider() -> str:
    return os.environ.get("SMART_HOME_TTS_PROVIDER", TTS_PROVIDER).strip().lower()


def edge_voice() -> str:
    return os.environ.get("SMART_HOME_TTS_VOICE", EDGE_VOICE).strip()


def edge_rate() -> str:
    return os.environ.get("SMART_HOME_TTS_RATE", EDGE_RATE).strip()


def edge_volume() -> str:
    return os.environ.get("SMART_HOME_TTS_VOLUME", EDGE_VOLUME).strip()


def edge_pitch() -> str:
    return os.environ.get("SMART_HOME_TTS_PITCH", EDGE_PITCH).strip()


def require(path: Path) -> Path:
    """Fail loudly with an actionable message when a model path is missing."""
    if not path.exists():
        raise FileNotFoundError(
            f"missing model artifact: {path}\n"
            f"model root = {models_dir()} (override with SMART_HOME_MODELS_DIR)"
        )
    return path


def asr_paths() -> dict:
    root = models_dir() / ASR_DIR
    return {
        "model": require(root / ASR_MODEL),
        "tokens": require(root / ASR_TOKENS),
    }


def tts_paths() -> dict:
    root = models_dir() / TTS_DIR
    return {
        "model": require(root / TTS_MODEL),
        "voices": require(root / TTS_VOICES),
        "tokens": require(root / TTS_TOKENS),
        "data_dir": require(root / TTS_DATA_DIR),
        "lexicon": ",".join(str(root / n) for n in TTS_LEXICON.split(",")),
        "dict_dir": root / TTS_DICT_DIR,
    }


def kws_paths() -> dict:
    root = models_dir() / KWS_DIR
    suffix = "chunk-16" if KWS_CHUNK16 else "chunk-8"
    return {
        "encoder": require(root / f"encoder-epoch-13-avg-2-{suffix}-left-64.int8.onnx"),
        "decoder": require(root / f"decoder-epoch-13-avg-2-{suffix}-left-64.onnx"),
        "joiner": require(root / f"joiner-epoch-13-avg-2-{suffix}-left-64.int8.onnx"),
        "tokens": require(root / "tokens.txt"),
        "lexicon": require(root / KWS_LEXICON),
    }


def vad_model() -> Path:
    return require(models_dir() / VAD_MODEL)


def wake_words(keywords_file: Path) -> list[str]:
    """Read the human-readable wake phrases out of a sherpa KWS keyword file.

    A line looks like ``x iǎo w ū :2.0 #0.25 @小屋小屋``; the ``@`` field is the
    phrase the user actually says. The loop reports these instead of a hardcoded
    string, so changing the file cannot make the prompt lie.
    """
    path = Path(keywords_file)
    if not path.exists():
        return []
    words: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        _, marker, phrase = line.partition("@")
        if marker and phrase.strip() and phrase.strip() not in words:
            words.append(phrase.strip())
    return words


# ---------------------------------------------------------------- voice agent
# The agent's LLM key lives in a local git-ignored file (or the environment).
# It is never written to logs, exceptions, the repository, or a command line.
DEFAULT_AGENT_ENV_FILE = Path("runtime/voice-agent/agent.env")
DEFAULT_AGENT_BASE_URL = "https://api.deepseek.com"
DEFAULT_AGENT_MODEL = "deepseek-chat"
DEFAULT_AGENT_TIMEOUT_SECONDS = 20.0
DEFAULT_AGENT_DEADLINE_SECONDS = 20.0
DEFAULT_AGENT_MAX_TOOL_ROUNDS = 4
DEFAULT_HOME_SERVICE_URL = "http://127.0.0.1:8765"
FIXED_REPLY_TEXT = "收到。"


def agent_env_file() -> Path:
    raw = os.environ.get("SMART_HOME_AGENT_ENV_FILE")
    return Path(raw) if raw else DEFAULT_AGENT_ENV_FILE


def agent_api_key() -> str:
    """Return the LLM key from the environment, else from the local env file."""
    value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if value:
        return value
    path = agent_env_file()
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, raw = line.partition("=")
        if separator and key.strip() == "DEEPSEEK_API_KEY":
            return raw.strip().strip("\"'")
    return ""


def agent_enabled() -> bool:
    raw = os.environ.get("SMART_HOME_AGENT", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def agent_base_url() -> str:
    return os.environ.get("SMART_HOME_AGENT_BASE_URL", DEFAULT_AGENT_BASE_URL).strip()


def agent_model() -> str:
    return os.environ.get("SMART_HOME_AGENT_MODEL", DEFAULT_AGENT_MODEL).strip()


def agent_timeout_seconds() -> float:
    return _positive_float("SMART_HOME_AGENT_TIMEOUT", DEFAULT_AGENT_TIMEOUT_SECONDS)


def agent_deadline_seconds() -> float:
    return _positive_float("SMART_HOME_AGENT_DEADLINE", DEFAULT_AGENT_DEADLINE_SECONDS)


def agent_max_tool_rounds() -> int:
    raw = os.environ.get("SMART_HOME_AGENT_MAX_TOOL_ROUNDS", "")
    if raw.strip().isdigit() and int(raw.strip()) > 0:
        return int(raw.strip())
    return DEFAULT_AGENT_MAX_TOOL_ROUNDS


def home_service_url() -> str:
    return os.environ.get("SMART_HOME_SERVICE_URL", DEFAULT_HOME_SERVICE_URL).strip()


def _positive_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


# ---------------------------------------------------------------- voiceprints
# Voiceprint identification is opt-in. `SMART_HOME_SPEAKER_MODEL` unset means the
# whole feature is off and the assistant behaves exactly as it did before, which is
# the only safe default for a household that has enrolled nobody.
DEFAULT_VISION_SERVICE_URL = "http://127.0.0.1:8766"
DEFAULT_SPEAKER_MIN_SECONDS = 1.5
DEFAULT_SPEAKER_MATCH_THRESHOLD = 0.55
DEFAULT_SPEAKER_MARGIN_THRESHOLD = 0.08
DEFAULT_SPEAKER_ENROLL_SAMPLES = 5


def _local_env(name: str) -> str:
    """Read one setting from the environment, else from the local env file.

    The same Git-ignored file the LLM key lives in, so every machine-local setting
    for this service sits in one place and survives a restart instead of having to be
    exported in whichever shell happened to launch the service.
    """

    value = os.environ.get(name, "").strip()
    if value:
        return value
    path = agent_env_file()
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, separator, raw = line.partition("=")
        if separator and key.strip() == name:
            return raw.strip().strip("\"'")
    return ""


def _speaker_float(name: str, fallback: float) -> float:
    try:
        value = float(_local_env(name))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def speaker_model_path() -> Path | None:
    """The speaker ONNX file, or None when voiceprint identification is disabled."""

    raw = _local_env("SMART_HOME_SPEAKER_MODEL")
    return Path(raw) if raw else None


def vision_service_url() -> str:
    """The gallery owner. Voiceprints are matched there, never here."""
    return _local_env("SMART_HOME_VISION_URL") or DEFAULT_VISION_SERVICE_URL


def speaker_min_seconds() -> float:
    return _speaker_float("SMART_HOME_SPEAKER_MIN_SECONDS", DEFAULT_SPEAKER_MIN_SECONDS)


def speaker_match_threshold() -> float:
    return _speaker_float("SMART_HOME_SPEAKER_MATCH_THRESHOLD", DEFAULT_SPEAKER_MATCH_THRESHOLD)


def speaker_margin_threshold() -> float:
    return _speaker_float("SMART_HOME_SPEAKER_MARGIN_THRESHOLD", DEFAULT_SPEAKER_MARGIN_THRESHOLD)


def speaker_provider() -> str:
    """Execution provider for the embedding model.

    `cpu` is the default because the installed sherpa-onnx wheel is CPU-only and the
    GPU is already busy with pose and face inference. A CUDA-enabled build can be
    selected here; a wrong value fails loudly at load rather than silently.
    """

    return _local_env("SMART_HOME_SPEAKER_PROVIDER") or "cpu"


def speaker_threads() -> int:
    """Threads for one embedding. One utterance at a time rarely needs more."""

    raw = _local_env("SMART_HOME_SPEAKER_THREADS")
    if raw.strip().isdigit() and int(raw.strip()) > 0:
        return int(raw.strip())
    return 1


def speaker_enrollment_samples() -> int:
    """How many good utterances one enrolment needs.

    Four is the floor at which a gallery can still reject a stranger; five leaves
    room for one unusable recording without asking the user to start over.
    """

    raw = _local_env("SMART_HOME_SPEAKER_ENROLL_SAMPLES")
    if raw.strip().isdigit() and int(raw.strip()) >= 4:
        return int(raw.strip())
    return DEFAULT_SPEAKER_ENROLL_SAMPLES


# ------------------------------------------------------------------- barge-in
# Talking over the assistant is normal in a household, so full interruption is on
# by default. The known TTS waveform is used to reject a matching echo; the
# loudness gate remains the fallback when that match is unreliable. A very quiet
# microphone may need a lower floor, and acoustic echo cancellation is still not
# available. `SMART_HOME_BARGE_IN=0` restores wake-word-only behaviour.
DEFAULT_BARGE_IN_ENABLED = True
DEFAULT_BARGE_MIN_SPEECH_SECONDS = 0.30
DEFAULT_BARGE_MIN_RMS = 0.02
DEFAULT_BARGE_ECHO_RATIO = 1.5
DEFAULT_BARGE_ECHO_CALIBRATION_SECONDS = 0.6


def _local_bool(name: str, fallback: bool) -> bool:
    """Read a yes/no setting: environment first, then the local env file."""

    raw = _local_env(name).strip().lower()
    if not raw:
        return fallback
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return fallback


def barge_in_enabled() -> bool:
    return _local_bool("SMART_HOME_BARGE_IN", DEFAULT_BARGE_IN_ENABLED)


def barge_min_speech_seconds() -> float:
    """How long the user must keep talking before a reply is cut off."""

    return _speaker_float("SMART_HOME_BARGE_MIN_SPEECH", DEFAULT_BARGE_MIN_SPEECH_SECONDS)


def barge_min_rms() -> float:
    """Absolute loudness floor, so room noise alone never interrupts."""

    return _speaker_float("SMART_HOME_BARGE_MIN_RMS", DEFAULT_BARGE_MIN_RMS)


def barge_echo_ratio() -> float:
    """How much louder than the reply's own leakage the user must be."""

    return _speaker_float("SMART_HOME_BARGE_ECHO_RATIO", DEFAULT_BARGE_ECHO_RATIO)


def barge_echo_calibration_seconds() -> float:
    """Head of each reply used to measure leakage; it cannot be interrupted."""

    return _speaker_float("SMART_HOME_BARGE_ECHO_CALIBRATION", DEFAULT_BARGE_ECHO_CALIBRATION_SECONDS)
