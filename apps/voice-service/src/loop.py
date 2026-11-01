"""Hybrid semantic admission and wake-once continuous voice with the cloud Agent.

Behavior:
- Uses a working system-default microphone first, with physical-device fallbacks.
- Hybrid listening transcribes VAD utterances and admits household intent using
  a separate tool-free classifier. Keyword-only standby remains selectable.
- After waking, VAD segments continuous utterances and SenseVoice transcribes.
- With `--agent`, the transcript is routed through the cloud LLM, which may call
  the restricted home-service tools; the reply is spoken with the configured TTS.
  Without it the loop speaks a fixed acknowledgement and touches no device.
- The active session returns to natural listening (or keyword standby) after the idle timeout without a
  completed utterance, or immediately on "退出"/"结束对话".
- The user may talk over the assistant. While a reply plays, or while the cloud
  model is still being asked, sustained speech stops that work and becomes the
  beginning of the next command; a fresh wake word still interrupts playback too.
  Recognition of what the reply itself said is prevented by a loudness gate, not
  by an echo canceller, so the README documents the limits.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import json
import queue
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from diagnostics import DiagnosticWriter

import numpy as np
import sounddevice as sd

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import agent_session
import audio_utils
import config
import device_preferences
import playback
import voice_models
from agent_client import AgentCancelled
from ambient_intent import AmbientHistory, create_intent_classifier
from barge_in import (
    BargeInMonitor,
    BargeInSettings,
    PlaybackBargeIn,
    SpeechBargeDetector,
    take_queued_blocks,
    voiced_seconds,
)
from control_server import create_control_server, start_control_server
from enrollment import VoiceEnrollmentSession, idle_status
from event_bus import VoiceEventBus
from event_contract import PUBLIC_EVENT_TYPES, PUBLIC_FIELDS
from speaker import SpeakerEmbedder
from speaker_identity import SpeakerIdentityClient
from text_conversation import TextConversation
from text_speech import TextSpeechMailbox
from voice_runtime import StopSignal, CombinedCancel, VoiceCancelled
from voice_stages import TurnStages
from intent_evaluation import IntentEvaluation
from audio_processing import MicrophoneProcessor, acoustic_summary
from voice_errors import failure_details

BLOCK_SIZE = 512
IDLE_TIMEOUT_SECONDS = 20.0
MAX_UTTERANCE_SECONDS = 15.0
# How much audio before speech onset is kept. The command path needs only enough to
# avoid clipping the first syllable, but the wake path reuses this ring to keep the
# wake phrase itself: a measured "你好小屋" is 1.12 s, and without it a short command
# is the whole utterance and the speaker gate rejects it as too_short.
PRE_ROLL_BLOCKS = 10
WAKE_PRE_ROLL_BLOCKS = 64
MIN_VAD_SPEECH_SECONDS = 0.18
DIAGNOSTICS = DiagnosticWriter(component="voice")
# Streamed answer coalescing. Small enough to still look like typing, sparse
# enough that a long reply cannot flood the console's bounded event queue.
DELTA_MIN_INTERVAL_SECONDS = 0.12
DELTA_MIN_GROWTH_CHARS = 24


def should_idle_timeout(state: str, in_speech: bool, now: float, deadline: float) -> bool:
    """Return whether an active, currently silent session has expired."""
    return state == "active" and not in_speech and now >= deadline


def deadline_after_reply(
    idle_timeout: float,
    play_reply: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    """Run optional playback, then start the full user waiting window."""
    if play_reply is not None:
        play_reply()
    return clock() + idle_timeout


def enqueue_if_enabled(q: queue.Queue, block: np.ndarray, enabled: bool) -> bool:
    """Queue one microphone block unless playback/processing suppresses input."""
    if not enabled:
        return False
    q.put_nowait(block)
    return True


def route_microphone_block(
    command_queue: queue.Queue,
    barge_queue: queue.Queue,
    block: np.ndarray,
    *,
    accepting_commands: bool,
    barge_listening: bool,
) -> bool:
    """Keep reply audio out of command recognition while checking for wake."""
    if accepting_commands:
        command_queue.put_nowait(block)
    elif barge_listening:
        barge_queue.put_nowait(block)
    else:
        return False
    return True


class WakeBargeIn:
    """A fresh KWS stream dedicated to one TTS playback."""

    def __init__(self, kws):
        self.kws = kws
        self.stream = kws.create_stream()
        self.keyword: str | None = None

    def detect(self, block: np.ndarray, sample_rate: int) -> str | None:
        """Feed one block; return the wake word it completed, else None."""

        self.stream.accept_waveform(sample_rate, block)
        while self.kws.is_ready(self.stream):
            self.kws.decode_stream(self.stream)
            keyword = self.kws.get_result(self.stream)
            if keyword:
                self.keyword = keyword
                return keyword
        return None

    def poll(self, blocks: queue.Queue, sample_rate: int) -> bool:
        while True:
            try:
                block = blocks.get_nowait()
            except queue.Empty:
                return False
            if self.detect(block, sample_rate):
                return True


def play_with_barge(
    samples: np.ndarray,
    sample_rate: int,
    target: audio_utils.PlaybackTarget,
    detector: PlaybackBargeIn,
    barge_queue: queue.Queue,
    on_stream_started: Callable[[float], None] | None = None,
    *, cancel=None,
) -> str | None:
    """Play a reply; return why it stopped: "speech", "wake", or None if finished."""

    if detector.speech is not None:
        detector.speech.start_playback(reference=samples, reference_rate=sample_rate)

    def should_interrupt() -> bool:
        if cancel is not None and cancel.is_set():
            detector.reason = "cancelled"
            return True
        return detector.poll(barge_queue, config.SAMPLE_RATE) is not None

    completed = play(samples, sample_rate, target, on_stream_started, should_interrupt)
    return None if completed else (detector.reason or "wake")


def collect_barge_head(
    detector: SpeechBargeDetector,
    blocks: queue.Queue,
    *,
    keep_pre_roll: bool,
) -> list[np.ndarray]:
    """Preserve the confirmed speech and mic blocks waiting behind it."""
    head = detector.take_captured(keep_pre_roll=keep_pre_roll)
    head.extend(take_queued_blocks(blocks))
    return head


def play_with_wake_interrupt(
    samples: np.ndarray,
    sample_rate: int,
    target: audio_utils.PlaybackTarget,
    kws,
    barge_queue: queue.Queue,
    on_stream_started: Callable[[float], None] | None = None,
) -> str | None:
    """Return the wake word that stopped playback, or None on completion."""
    detector = WakeBargeIn(kws)
    reason = play_with_barge(
        samples, sample_rate, target, PlaybackBargeIn(wake=detector), barge_queue, on_stream_started,
    )
    return detector.keyword if reason == "wake" else None


def should_exit(transcript: str) -> bool:
    """Last-resort exit words.

    The agent decides intent through the ``end_conversation`` tool; this covers
    the case where the model is unavailable but the phrase is unmistakable.
    """
    return any(word in transcript for word in ("退出", "结束对话", "再见", "拜拜"))


def is_non_command_filler(transcript: str) -> bool:
    """Ignore a bare hesitation that VAD/ASR may emit from a short noise burst."""
    compact = re.sub(r"[\s，。！？,.!?、~～…]+", "", transcript or "")
    return bool(compact) and all(char in "嗯啊呃额哦唔" for char in compact)


def strip_wake_phrases(transcript: str, wake_phrases) -> str:
    """Remove the wake phrase from the front of a transcript.

    The wake audio is kept so the utterance is long enough to identify a speaker, so
    the phrase the user spoke to wake the assistant comes back from ASR and is not
    part of the instruction. Only a leading phrase is removed: "你好小屋" inside a
    sentence is content, and rewriting it would change what the user said.

    Whitespace and the punctuation the user paused with are cleaned up afterwards, so
    "你好小屋，把灯打开" becomes "把灯打开" rather than "，把灯打开".
    """

    text = (transcript or "").strip()
    for phrase in sorted(wake_phrases or (), key=len, reverse=True):
        if text.startswith(phrase):
            text = text[len(phrase):]
            break
    return re.sub(r"^[\s，。！？,.!?、~～…：；:;]+", "", text).strip()


def seed_wake_vad(vad, blocks) -> int:
    """Restore segmentation state from retained wake audio and count real speech."""
    return sum(1 for block in blocks if vad.is_speech(block))


def has_enough_voiced_audio(voiced_blocks: int, sample_rate: int) -> bool:
    """A short noise burst cannot pass merely because the buffer has pre-roll."""
    return voiced_blocks * BLOCK_SIZE >= MIN_VAD_SPEECH_SECONDS * sample_rate


def drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def play(
    samples: np.ndarray,
    sample_rate: int,
    target: audio_utils.PlaybackTarget,
    on_stream_started: Callable[[float], None] | None = None,
    should_interrupt: Callable[[], bool] | None = None,
) -> bool:
    """Play through the verified target, reporting stream latency once."""
    try:
        return playback.play(
            samples, sample_rate, target,
            on_stream_started=on_stream_started,
            should_interrupt=should_interrupt,
        )
    finally:
        if playback.reference_sink is not None:
            playback.reference_sink.end_reference()


def wake_tone(sample_rate: int = 24000) -> np.ndarray:
    """Return a short, low-amplitude two-note wake acknowledgement."""
    part = int(0.08 * sample_rate)
    t = np.arange(part, dtype=np.float32) / sample_rate
    first = 0.12 * np.sin(2 * np.pi * 660 * t)
    second = 0.12 * np.sin(2 * np.pi * 880 * t)
    gap = np.zeros(int(0.03 * sample_rate), dtype=np.float32)
    return np.concatenate([first, gap, second]).astype(np.float32)


def synthesize_reply(tts, text: str, speaker_id: int | None):
    """Synthesize a reply with the arguments the configured backend accepts.

    Only the local Kokoro backend takes a speaker id; the Edge backend encodes the
    voice in its own configuration. Passing a speaker id to Edge raises, which
    silently turned every spoken reply into a caught error.
    """
    if tts.provider == "kokoro":
        return voice_models.synthesize(tts, text, speaker_id)
    return voice_models.synthesize(tts, text)


def resolve_speaker(embedder, identity, executor, samples, sample_rate):
    """One utterance's verdict, display name and why there is no verdict.

    Every failure degrades to "we do not know". A missing model, a stopped vision
    service, an empty gallery or a person who cannot be named must never drop a
    turn, and a missing verdict only costs one clarifying question.

    The third value matters as much as the first: without it the console cannot
    tell "the audio was too short to judge" from "nobody is enrolled", and both
    look identical to "voiceprints are switched off".
    """

    if embedder is None or identity is None or not embedder.configured:
        return None, None, "disabled"
    try:
        embedding, quality = embedder.embed(samples, sample_rate)
        if embedding is None:
            # The audio gates refused it; their reason is the honest explanation.
            return None, None, quality.reason or "not_recognisable"
        verdict = identity.verdict(embedding)
        name = None
        reader = getattr(executor, "person_names", None)
        if callable(reader) and verdict.person_id:
            name = reader().get(verdict.person_id)
        return verdict, name, verdict.reason
    except Exception:
        # Speaker identification is an enhancement; the spoken turn is not.
        return None, None, "unavailable"


def speaker_fields(
    verdict, display_name, reason: str | None = None, *,
    audio_seconds: float | None = None, min_seconds: float | None = None,
) -> dict[str, object]:
    """Public transcript fields describing the speaker verdict.

    Published whenever voiceprints are configured, including when no verdict was
    produced. Publishing nothing used to be indistinguishable from four different
    situations at once, which is exactly the confusion this prevents.
    """

    if verdict is None:
        fields = {
            "speaker_state": "disabled" if reason == "disabled" else "unknown",
            "speaker_reason": reason or "not_evaluated",
        }
        if reason == "too_short" and audio_seconds is not None and min_seconds is not None:
            # Durations explain this verdict without retaining audio or a speaker
            # template. The two values refer to the recorded clip and this run's
            # configured minimum, which can differ from the shipped default.
            fields["speaker_audio_seconds"] = round(audio_seconds, 2)
            fields["speaker_min_seconds"] = round(min_seconds, 2)
        return fields
    return {
        "speaker_id": verdict.person_id,
        "speaker_name": display_name if verdict.usable else None,
        "speaker_confidence": verdict.confidence,
        "speaker_state": verdict.state,
        "speaker_reason": verdict.reason,
    }


def start_enrollment(person_id, *, required, embedder, identity, directory_reader, active_session,
                     accumulate=None):
    """Decide whether an enrolment may start, and say why not when it may not.

    Pure policy: everything it needs is passed in, so the refusal rules — no model,
    one enrolment at a time, an id the directory does not know — are provable without
    a microphone or a running service.

    ``accumulate`` is threaded through to the gallery so a second enrolment keeps the
    first one's voiceprints; `None` leaves the gallery's own default in place.
    """

    if embedder is None or identity is None:
        return None, {"ok": False, "error_code": "speaker_disabled", "message": "未配置声纹模型，无法录入声纹"}
    if active_session is not None and active_session.active:
        return None, {"ok": False, "error_code": "enrollment_active", "message": "已有声纹录入正在进行"}
    if not callable(directory_reader):
        return None, {"ok": False, "error_code": "person_directory_unavailable", "message": "人物名单暂不可用，请检查 home-service"}
    if person_id not in directory_reader():
        return None, {"ok": False, "error_code": "unknown_person", "message": "请先在身份页添加该人物"}
    options = {} if accumulate is None else {"accumulate": bool(accumulate)}
    session = VoiceEnrollmentSession(
        person_id,
        save=lambda pid, embeddings: identity.enroll_person(pid, embeddings, **options),
        required=required,
    )
    return session, {"ok": True, "enrollment": session.status().to_dict()}


def enrollment_fields(status) -> dict[str, object]:
    """Public fields for one enrolment step, so the console can show progress.

    `enroll_state` rather than `state`: the transcript-style `state` field already
    means the voice loop's own state, and the two must not be confused.
    """

    return {
        "person_id": status.person_id,
        "accepted": status.accepted,
        "required": status.required,
        "min_seconds": status.min_seconds,
        "last_reason": status.last_reason,
        "enroll_state": status.state,
        "message": status.message,
    }


def run_text_session(agent, tts, output_target, args, log_event, log_handle) -> int:
    """Type sentences instead of speaking them.

    This exists because the wake word has never been validated on real speech, so
    a microphone problem must not be the only way to try the agent. Everything
    after the transcript — tools, confirmation, honest wording, TTS — is the same
    code path as the voice loop.
    """
    if agent is None:
        print("--text requires --agent (or SMART_HOME_AGENT=1)", file=sys.stderr)
        return 2

    print('文字模式：直接输入一句话，例如「打开客厅灯」「我出门了」。输入「退出」结束。')
    log_event("text_session_start", state="active")
    try:
        while True:
            try:
                line = input("你说> ").strip()
            except EOFError:
                print()
                break
            if not line:
                continue
            if line in {"退出", "结束", "结束对话", "quit", "exit"}:
                # A literal quit command stays available as a hard escape hatch;
                # farewells like "再见" go to the model, which decides intent.
                print("[exit] 结束")
                log_event("text_session_end", state="standby", transcript=line)
                break

            started = time.perf_counter()
            reply = None
            try:
                reply = agent.handle(line)
            except Exception as exc:  # noqa: BLE001 - a crash must not end the session
                reply_text = "语音助手出现异常，请稍后再试。"
                print(f"[agent error] {type(exc).__name__}", file=sys.stderr)
                log_event("agent_error", state="active", error_type=type(exc).__name__)
            else:
                reply_text = reply.text
                log_event(
                    "agent_reply",
                    state="active",
                    text=reply_text,
                    ok=reply.ok,
                    error_code=reply.error_code,
                    end_conversation=reply.end_conversation,
                    tools=[result.name for result in reply.tool_results],
                    seconds=round(time.perf_counter() - started, 3),
                )
                for result in reply.tool_results:
                    log_event(
                        "tool_result",
                        state="active",
                        name=result.name,
                        ok=result.ok,
                        error_code=result.error_code,
                        phrase=result.phrase,
                        operation_id=result.operation_id,
                    )
            print(f"小屋> {reply_text}")
            if tts is not None:
                try:
                    audio, rate = synthesize_reply(tts, reply_text, args.speaker_id)
                    play(audio, rate, output_target)
                except Exception as exc:  # noqa: BLE001 - keep the text visible
                    print(f"[tts error] {type(exc).__name__}", file=sys.stderr)
                    log_event("tts_error", state="active", error_type=type(exc).__name__)
            if reply is not None and reply.end_conversation:
                print("[exit] 对话已结束")
                log_event("text_session_end", state="standby", reason="agent_end_conversation")
                break
    except KeyboardInterrupt:
        print("\nstopped")
        log_event("keyboard_interrupt", state="active")
    finally:
        if log_handle is not None:
            log_handle.close()
    return 0


class VoiceStartupError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def initialize_voice(args, preferences):
    agent_requested = args.agent or config.agent_enabled()
    agent_api_key = config.agent_api_key() if agent_requested else ""
    if agent_requested and not agent_api_key:
        raise VoiceStartupError("agent_key_missing")
    try:
        input_device = audio_utils.select_input_device(args.input_contains, config.SAMPLE_RATE, preferred=preferences["input"])
    except Exception as exc:
        raise VoiceStartupError("audio_input_unavailable") from exc
    try:
        output_target = None if args.no_tts else audio_utils.select_playback_target(args.output_contains, preferred=preferences["output"])
    except Exception as exc:
        raise VoiceStartupError("audio_output_unavailable") from exc
    print(f"input : #{input_device.index} {input_device.name} [{input_device.hostapi}]")
    if output_target:
        print(f"output: {output_target.describe()}")
    print(f"model root: {config.models_dir()}")
    print("text mode: microphone and speech models are not used" if args.text else "loading KWS/VAD/ASR/TTS ...")
    if ((not args.text and config.asr_provider() == "funasr") or (not args.no_tts and config.tts_provider() == "cosyvoice")) and not config.dashscope_key():
        raise VoiceStartupError("speech_api_key_missing")
    try:
        kws = None if args.text else voice_models.create_kws(args.keywords_file)
        vad = None if args.text else voice_models.create_vad()
        asr = None if args.text else voice_models.create_asr()
        tts = None if args.no_tts else voice_models.create_tts()
    except Exception as exc:
        raise VoiceStartupError("voice_model_unavailable") from exc
    try:
        agent = (agent_session.create_agent_session(
            agent_api_key, args.service_url,
            deadline_seconds=args.agent_deadline,
            max_tool_rounds=args.agent_max_tool_rounds,
        ) if agent_requested else None)
    except Exception as exc:
        raise VoiceStartupError("agent_initialization_failed") from exc
    if agent is not None:
        print(f"agent: {config.agent_model()} -> {args.service_url}")
    return input_device, output_target, kws, vad, asr, tts, agent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords-file", type=Path, default=Path("tools/voice-check/cases/wake-keywords.txt"))
    parser.add_argument("--input-contains", "--input-device", dest="input_contains", default=None)
    parser.add_argument("--output-contains", "--output-device", dest="output_contains", default=None)
    parser.add_argument("--idle-timeout", type=float, default=IDLE_TIMEOUT_SECONDS)
    parser.add_argument("--listening-mode", choices=("wake", "hybrid"), default=config.listening_mode(),
                        help="hybrid: semantic admission without a wake word; wake: keyword only")
    parser.add_argument("--log-file", type=Path, default=None, help="opt-in diagnostic log; may contain conversation text")
    parser.add_argument("--speaker-id", type=int, default=47)
    parser.add_argument("--no-tts", action="store_true", help="print only; useful for diagnostics")
    parser.add_argument("--startup-check", action="store_true", help="validate devices/models, then exit without opening the microphone")
    parser.add_argument("--run-seconds", type=float, default=0.0, help="diagnostic: stop automatically after N seconds")
    parser.add_argument("--control-host", default="127.0.0.1")
    parser.add_argument("--control-port", type=int, default=8767)
    parser.add_argument("--no-control-api", action="store_true")
    parser.add_argument(
        "--agent",
        action="store_true",
        help="route transcripts through the cloud LLM agent (also enabled by SMART_HOME_AGENT=1)",
    )
    parser.add_argument("--service-url", default=config.home_service_url())
    parser.add_argument("--agent-deadline", type=float, default=config.agent_deadline_seconds())
    parser.add_argument("--agent-max-tool-rounds", type=int, default=config.agent_max_tool_rounds())
    parser.add_argument(
        "--text",
        action="store_true",
        help="type instead of speaking: skips wake word, VAD and ASR (needs --agent)",
    )
    args = parser.parse_args()

    selection_queue: queue.Queue[dict] = queue.Queue(maxsize=1)
    selection_lock = threading.Lock()
    event_bus = VoiceEventBus(max_events=200)
    preferences = device_preferences.load()

    def select_devices(body: dict) -> dict:
        chosen: dict[str, dict[str, str] | None] = {}
        for kind in ("input", "output"):
            value = body[kind]
            if value == "auto":
                chosen[kind] = None
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("invalid device index")
            devices = audio_utils.list_input_devices() if kind == "input" else audio_utils.list_output_devices()
            device = next((item for item in devices if item.index == value), None)
            if device is None:
                raise ValueError("device unavailable")
            preferred = {"name": device.name, "hostapi": device.hostapi}
            selected = (audio_utils.select_input_device(preferred=preferred) if kind == "input"
                        else audio_utils.select_playback_target(preferred=preferred))
            if selected.index != value:
                raise ValueError("device unavailable")
            chosen[kind] = preferred
        with selection_lock:
            selection = event_bus.selection_status()
            if selection_queue.full() or (selection is not None and selection["state"] == "applying"):
                return {"ok": False, "error_code": "selection_busy", "message": "another device change is in progress"}
            request_id = uuid.uuid4().hex
            event_bus.set_selection_status(request_id, "applying")
            selection_queue.put_nowait({"request_id": request_id, "preferences": chosen})
        return {"ok": True, "state": "applying", "request_id": request_id}

    # Voiceprints stay off unless a model file is configured: an un-enrolled
    # household must behave exactly as it did before, and pay nothing for it.
    speaker_embedder = None
    speaker_identity = None
    if config.speaker_model_path() is not None:
        speaker_embedder = SpeakerEmbedder(
            config.speaker_model_path(),
            min_seconds=config.speaker_min_seconds(),
            provider=config.speaker_provider(),
            num_threads=config.speaker_threads(),
        )
        speaker_identity = SpeakerIdentityClient(
            config.vision_service_url(),
            threshold=config.speaker_match_threshold(),
            margin_threshold=config.speaker_margin_threshold(),
        )
        print(f"speaker: {config.speaker_model_path()} -> {config.vision_service_url()}")

    # At most one enrolment at a time. It lives here because the microphone does:
    # a sample is an utterance this loop captured, never audio a client uploaded.
    enrollment = None

    def identity_handler(action: str, body: dict) -> dict:
        nonlocal enrollment
        required = config.speaker_enrollment_samples()
        if action == "status":
            current = enrollment.status() if enrollment is not None else idle_status(required)
            # `configured` is what lets the console tell "switched off" apart from
            # "listened and did not recognise": the two need different guidance.
            return {"ok": True, "configured": speaker_embedder is not None, "enrollment": current.to_dict()}
        if action == "cancel":
            if enrollment is None:
                return {"ok": True, "enrollment": idle_status(required).to_dict()}
            status = enrollment.cancel()
            enrollment = None
            return {"ok": True, "enrollment": status.to_dict()}

        person_id = str(body.get("person_id") or "")
        # The directory is the single identity list: an id that is not in it would
        # create a gallery entry nobody could ever be shown as. `agent` is bound
        # later in this function and read here only when a request arrives.
        session, payload = start_enrollment(
            person_id,
            required=required,
            embedder=speaker_embedder,
            identity=speaker_identity,
            directory_reader=getattr(getattr(agent, "executor", None), "person_names", None),
            active_session=enrollment,
            accumulate=config.speaker_enrollment_accumulates(),
        )
        if session is not None:
            enrollment = session
        return payload

    text_conversation = None
    text_speech = TextSpeechMailbox()
    speech_stop = StopSignal()
    def stop_reply():
        speech_stop.stop()
        text_speech.stop()
        if text_conversation is not None:
            text_conversation.cancel()
        return {"ok": True, "message": "已请求停止回复；已确认的设备操作仍然有效。"}
    evaluation = IntentEvaluation(lambda: create_intent_classifier(agent))

    def submit_text(body):
        if text_conversation is None:
            return {"ok": False, "error_code": "text_unavailable", "message": "文字对话未就绪，请启用 Agent 并重启语音服务"}
        return text_conversation.submit(body)

    control_server = None
    if not args.no_control_api and not args.startup_check:
        control_server = create_control_server(
            event_bus, args.control_host, args.control_port,
            on_select=select_devices, on_identity=identity_handler, on_text=submit_text,
            on_stop=stop_reply, evaluation=evaluation,
        )
        start_control_server(control_server)
        print(f"voice control API: http://{args.control_host}:{args.control_port}")

    startup_selection_changed = False
    startup_request_id: str | None = None
    while True:
        try:
            input_device, output_target, kws, vad, asr, tts, agent = initialize_voice(args, preferences)
            if startup_selection_changed:
                device_preferences.save(preferences)
            break
        except VoiceStartupError as exc:
            if args.startup_check or control_server is None:
                raise
            if startup_request_id is not None:
                event_bus.set_selection_status(startup_request_id, "failed", exc.code)
            event_bus.health_summary = {"error_code": exc.code, "input_device": None, "output_device": None}
            event_bus.publish("service_error", state="degraded", error_type=exc.code)
            print(f"[startup error] {exc.code}", file=sys.stderr)
            queued = selection_queue.get()
            preferences = queued["preferences"]
            startup_request_id = queued["request_id"]
            selection_queue.task_done()
            startup_selection_changed = True

    if args.startup_check:
        print("startup check OK: devices and all requested models initialized")
        return 0

    log_handle = None
    if args.log_file is not None:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = args.log_file.open("a", encoding="utf-8", buffering=1)
    event_bus.health_summary = {
        "input_device": input_device.name,
        "input_device_index": input_device.index,
        "output_device": output_target.describe() if output_target is not None else None,
        "output_device_index": output_target.index if output_target is not None else None,
        "agent_enabled": agent is not None,
        "loop_mode": "text" if args.text else "microphone",
    }
    if startup_request_id is not None:
        event_bus.set_selection_status(startup_request_id, "applied")

    def log_event(event: str, **fields) -> None:
        record = {
            "timestamp": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        }
        if log_handle is not None:
            log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        public_type = PUBLIC_EVENT_TYPES.get(event)
        if public_type is not None:
            safe = {key: value for key, value in fields.items() if key in PUBLIC_FIELDS[public_type]}
            event_bus.publish(public_type, source_event=event, **safe)

    if agent is not None:
        def strip_typed_wake(text):
            phrases = config.wake_words(args.keywords_file)
            return strip_wake_phrases(text, phrases) if any(text.startswith(phrase) for phrase in phrases) else text

        text_conversation = TextConversation(
            lambda: agent_session.create_agent_session(
                config.agent_api_key(), args.service_url,
                deadline_seconds=args.agent_deadline, max_tool_rounds=args.agent_max_tool_rounds,
            ),
            event_bus,
            strip_wake=strip_typed_wake,
            announce=text_speech.submit if tts is not None and not args.text else None,
        )
        event_bus.health_summary["text_chat_enabled"] = True
        # The sentence is shown while it is being written, so a slow turn is
        # visible instead of silent. Transient by design: a streamed answer must
        # not evict the conversation from the bounded event history.
        streamed = {"text": "", "at": 0.0}

        def publish_agent_delta(text: str) -> None:
            now = time.monotonic()
            # One event per keystroke-sized burst. Publishing per token would
            # flood the console's bounded subscriber queue and drop real events;
            # a suppressed tail is harmless because the finished reply replaces it.
            if text.rstrip() == streamed["text"] or (
                now - streamed["at"] < DELTA_MIN_INTERVAL_SECONDS
                and len(text) - len(streamed["text"]) < DELTA_MIN_GROWTH_CHARS
            ):
                return
            streamed.update({"text": text.rstrip(), "at": now})
            event_bus.publish_transient("agent_delta", text=text)

        agent.on_delta = publish_agent_delta

    if args.text:
        try:
            return run_text_session(agent, tts, output_target, args, log_event, log_handle)
        finally:
            if control_server is not None:
                control_server.shutdown()
                control_server.server_close()

    audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=128)
    barge_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=128)
    accept_input = True
    barge_listening = False
    overflow_count = 0

    processor = MicrophoneProcessor(config.SAMPLE_RATE)
    selected_asr = config.asr_provider()
    playback.reference_sink = processor
    recognition_session = None
    event_bus.health_summary.update({"audio_processing": processor.status,
        "asr_provider": selected_asr, "tts_provider": getattr(tts, "provider", None),
        "speech_api_configured": bool(config.dashscope_key()), "stop_supported": True,
        "intent_evaluation_enabled": agent is not None})

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        nonlocal overflow_count
        if status:
            print(f"audio status: {status}", file=sys.stderr)
            log_event("audio_status", status=str(status))
        raw_block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        captured_at = time.monotonic()
        if time_info is not None:
            captured_at -= max(0, float(time_info.currentTime - time_info.inputBufferAdcTime))
        block = processor.process(raw_block, captured_at)
        try:
            route_microphone_block(
                audio_queue, barge_queue, block,
                accepting_commands=accept_input,
                barge_listening=barge_listening,
            )
        except queue.Full:
            overflow_count += 1

    classifier = None
    intent_error_code = None
    if args.listening_mode == "hybrid" and agent is not None:
        try:
            classifier = create_intent_classifier(agent)
        except ValueError:
            intent_error_code = "intent_configuration_missing"
            log_event("intent_error", state="degraded", error_type="intent_configuration_missing")
            print("[intent] 配置不完整，保留唤醒词模式", file=sys.stderr)
    hybrid = classifier is not None
    rest_state = "listening" if hybrid else "standby"
    state = rest_state
    active_origin = None
    ambient_actor = None
    ambient_history = AmbientHistory()
    intent_client = getattr(classifier, "client", None)
    event_bus.health_summary.update({
        "listening_mode": "hybrid" if hybrid else "wake",
        "requested_listening_mode": args.listening_mode,
        "intent_provider": getattr(intent_client, "provider", None),
        "intent_model": getattr(intent_client, "model", None),
        "intent_status": "ready" if hybrid else "configuration_error" if intent_error_code else "disabled",
        "intent_error_code": intent_error_code,
    })
    kws_stream = kws.create_stream()
    active_deadline = 0.0
    pre_roll: collections.deque[np.ndarray] = collections.deque(maxlen=PRE_ROLL_BLOCKS)
    # A longer ring lives only while standby is listening for the wake word, so the
    # phrase itself can be recovered the moment it is detected. It is not used to
    # seed ordinary command utterances, where a long pre-roll would prepend room
    # noise to every sentence.
    wake_ring: collections.deque[np.ndarray] = collections.deque(maxlen=WAKE_PRE_ROLL_BLOCKS)
    merge_wake = config.wake_command_merge_enabled()
    utterance: list[np.ndarray] = []
    in_speech = False
    utterance_token = None
    voiced_blocks = 0
    wake_seeded = False
    trailing_silence_blocks = 0
    discarding_ambient = False
    end_silence_blocks = max(1, int(np.ceil(
        config.command_end_silence_seconds() * config.SAMPLE_RATE / BLOCK_SIZE
    )))

    wake_phrases = config.wake_words(args.keywords_file)
    if hybrid:
        print("ready: 可以直接说家居需求，也可以继续使用唤醒词（Ctrl+C 退出）")
    elif wake_phrases:
        spoken = "或".join(f"“{phrase}”" for phrase in wake_phrases)
        print(f"ready: 请说{spoken}（Ctrl+C 退出）")
    else:
        print(f"ready: 未从 {args.keywords_file} 读到唤醒词，请检查该文件（Ctrl+C 退出）")
    # Full barge-in needs a second VAD stream: it resets once per reply and its
    # state must not be poisoned by the audio coming out of the speaker, so the
    # stream that segments commands stays untouched. Built only when there is
    # something to interrupt and the feature is on.
    barge_detector = None
    if not args.no_tts and output_target is not None and config.barge_in_enabled():
        barge_detector = SpeechBargeDetector(
            voice_models.create_vad(),
            config.SAMPLE_RATE,
            BargeInSettings(
                min_speech_seconds=config.barge_min_speech_seconds(),
                min_rms=config.barge_min_rms(),
                echo_ratio=config.barge_echo_ratio(),
                echo_calibration_seconds=config.barge_echo_calibration_seconds(),
            ),
        )
        print(
            "barge-in: 播报和思考过程中都可以直接说话打断"
            f"（回声门限 {config.barge_echo_ratio():g}×，确认 {config.barge_min_speech_seconds():g}s）"
        )
    elif not args.no_tts:
        print("barge-in: 已关闭（SMART_HOME_BARGE_IN=0），只能靠唤醒词打断播报")
    log_event(
        "ready",
        state=state,
        input_device=input_device.name,
        output_device=output_target.describe() if output_target else None,
        idle_timeout_seconds=args.idle_timeout,
        wake_words=wake_phrases,
        agent_enabled=agent is not None,
        barge_in=barge_detector is not None,
        listening_mode="hybrid" if hybrid else "wake",
    )
    run_deadline = time.monotonic() + args.run_seconds if args.run_seconds > 0 else None
    try:
        with contextlib.ExitStack() as stream_scope:
            live_stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            device=input_device.index,
            channels=1,
            dtype="float32",
            callback=callback,
            )
            stream_scope.callback(lambda: live_stream.close())
            live_stream.start()
            while True:
                speech_request = text_speech.take()
                if speech_request is not None:
                    # Typed speech shares the selected output and never overlaps
                    # a microphone reply. Suppress input so TTS cannot wake itself.
                    accept_input = barge_listening = False
                    if recognition_session is not None:
                        recognition_session.close()
                        recognition_session = None
                    utterance.clear()
                    pre_roll.clear()
                    wake_ring.clear()
                    utterance_token = None
                    in_speech = wake_seeded = discarding_ambient = False
                    voiced_blocks = trailing_silence_blocks = 0
                    vad.reset()
                    kws_stream = kws.create_stream()
                    drain(audio_queue)
                    drain(barge_queue)
                    try:
                        text_speech.process(
                            speech_request,
                            lambda text: tts.synthesize(text, cancel=CombinedCancel(speech_stop.token(), text_speech)) if isinstance(tts, voice_models.TtsEngine) and tts.provider != "kokoro" else synthesize_reply(tts, text, args.speaker_id),
                            lambda samples, rate, **options: play(samples, rate, output_target, **options),
                            stream_render=(lambda text, cancel: tts.stream(text, cancel=cancel)) if getattr(tts, "supports_streaming", False) else None,
                            stream_player=lambda chunks, **options: playback.play_chunks(chunks, output_target, **options),
                        )
                    finally:
                        drain(audio_queue)
                        drain(barge_queue)
                        accept_input = True
                        if state == "active":
                            active_deadline = deadline_after_reply(args.idle_timeout)
                    continue
                try:
                    selection_request = selection_queue.get_nowait()
                except queue.Empty:
                    selection_request = None
                if selection_request is not None:
                    requested = selection_request["preferences"]
                    request_id = selection_request["request_id"]
                    try:
                        next_input = audio_utils.select_input_device(
                            args.input_contains, config.SAMPLE_RATE, preferred=requested["input"]
                        )
                        next_output = None if args.no_tts else audio_utils.select_playback_target(
                            args.output_contains, preferred=requested["output"]
                        )
                        for kind, selected in (("input", next_input), ("output", next_output)):
                            preferred = requested[kind]
                            if preferred is not None and (selected is None or selected.name != preferred["name"] or selected.hostapi != preferred["hostapi"]):
                                raise VoiceStartupError(f"audio_{kind}_unavailable")
                        if next_input.index != input_device.index:
                            replacement = sd.InputStream(
                                samplerate=config.SAMPLE_RATE, blocksize=BLOCK_SIZE,
                                device=next_input.index, channels=1, dtype="float32", callback=callback,
                            )
                            replacement.start()
                            live_stream.stop()
                            live_stream.close()
                            live_stream = replacement
                        input_device, output_target = next_input, next_output
                        preferences = requested
                        device_preferences.save(preferences)
                        event_bus.health_summary.update({
                            "input_device": input_device.name,
                            "input_device_index": input_device.index,
                            "output_device": output_target.describe() if output_target else None,
                            "output_device_index": output_target.index if output_target else None,
                            "error_code": None,
                        })
                        state = rest_state
                        active_origin = None
                        ambient_actor = None
                        ambient_history.reset()
                        if agent is not None:
                            agent.reset()
                        kws_stream = kws.create_stream()
                        vad.reset()
                        pre_roll.clear()
                        wake_ring.clear()
                        utterance.clear()
                        wake_seeded = False
                        in_speech = False
                        discarding_ambient = False
                        drain(audio_queue)
                        event_bus.publish("voice_state", state=rest_state, reason="audio_devices_changed")
                        event_bus.set_selection_status(request_id, "applied")
                    except Exception as exc:
                        code = exc.code if isinstance(exc, VoiceStartupError) else "audio_device_switch_failed"
                        event_bus.set_selection_status(request_id, "failed", code)
                        event_bus.publish("service_error", state=state, error_type=type(exc).__name__)
                    finally:
                        selection_queue.task_done()
                now = time.monotonic()
                if run_deadline is not None and now >= run_deadline:
                    print("diagnostic run complete")
                    break
                # InputStream continuously queues blocks, even for silence, so
                # the idle deadline must be checked on every iteration rather
                # than only when queue.get() times out.
                if should_idle_timeout(state, in_speech, now, active_deadline):
                    print(f"[timeout] {args.idle_timeout:g} 秒无新语音，回到{rest_state}")
                    log_event("idle_timeout", state=rest_state, idle_timeout_seconds=args.idle_timeout)
                    state = rest_state
                    active_origin = None
                    ambient_actor = None
                    ambient_history.reset()
                    kws_stream = kws.create_stream()
                    vad.reset()
                    pre_roll.clear()
                    wake_ring.clear()
                    utterance.clear()
                    wake_seeded = False
                    if agent is not None:
                        agent.reset()
                try:
                    block = audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if state in {"standby", "listening"}:
                    # Keep a bounded copy separate from the ordinary command
                    # pre-roll. KWS consumes the wake audio before the active
                    # state exists; without this ring the wake phrase (and the
                    # short command immediately after it) can never contribute
                    # to speaker verification.
                    if merge_wake:
                        wake_ring.append(block)
                    kws_stream.accept_waveform(config.SAMPLE_RATE, block)
                    while kws.is_ready(kws_stream):
                        kws.decode_stream(kws_stream)
                        keyword = kws.get_result(kws_stream)
                        if keyword:
                            print(f"[wake] {keyword}；进入连续对话")
                            log_event("wake", state="active", keyword=keyword)
                            kws.reset_stream(kws_stream)
                            # In merged mode keep recording continuously. A
                            # blocking acknowledgement with input disabled loses
                            # exactly the command spoken immediately after wake.
                            wake_audio = list(wake_ring) if merge_wake else []
                            wake_ring.clear()
                            if not merge_wake:
                                accept_input = False
                                drain(audio_queue)
                                if output_target is not None:
                                    play(wake_tone(), 24000, output_target)
                                drain(audio_queue)
                                accept_input = True
                            state = "active"
                            active_origin = "wake"
                            discarding_ambient = False
                            ambient_history.reset()
                            if agent is not None:
                                agent.reset()
                            active_deadline = time.monotonic() + args.idle_timeout
                            vad.reset()
                            pre_roll.clear()
                            utterance = wake_audio
                            # Replay retained blocks into VAD so the boundary
                            # follows the complete wake+command utterance, even
                            # when KWS detects late inside the command. Count
                            # actual VAD-positive blocks, never invented seconds.
                            voiced_blocks = seed_wake_vad(vad, wake_audio)
                            wake_seeded = False
                            in_speech = bool(wake_audio)
                            utterance_token = speech_stop.token()
                            trailing_silence_blocks = 0
                            break
                    if state != "listening":
                        continue

                speech = vad.is_speech(block)
                if discarding_ambient:
                    trailing_silence_blocks = 0 if speech else trailing_silence_blocks + 1
                    if trailing_silence_blocks >= end_silence_blocks:
                        # Ignore the rest of an overlong sentence too: a later
                        # clause may retract the earlier comfort complaint.
                        discarding_ambient = False
                        in_speech = False
                        vad.reset()
                        wake_ring.clear()
                        kws_stream = kws.create_stream()
                    continue
                if not in_speech:
                    pre_roll.append(block)
                    if speech:
                        if wake_seeded:
                            # Keep the wake phrase as speaker context, but use
                            # only the post-wake VAD span as the command onset.
                            utterance.extend(pre_roll)
                            wake_seeded = False
                            voiced_blocks += 1
                        else:
                            utterance = list(pre_roll)
                            voiced_blocks = 1
                        pre_roll.clear()
                        in_speech = True
                        utterance_token = speech_stop.token()
                        trailing_silence_blocks = 0
                        if selected_asr == "funasr":
                            from cloud_speech import RealtimeAsr
                            recognition_session = RealtimeAsr(lambda text: event_bus.publish_transient("asr_partial", text=text, input_mode="microphone") if active_origin == "wake" else None)
                            for captured in utterance:
                                recognition_session.feed(captured)
                else:
                    # Preserve every block, including an internal pause. Commit
                    # only after sustained silence, not the first negative VAD.
                    utterance.append(block)
                    if selected_asr == "funasr":
                        from cloud_speech import RealtimeAsr
                        if recognition_session is None:
                            recognition_session = RealtimeAsr(lambda text: event_bus.publish_transient("asr_partial", text=text, input_mode="microphone") if active_origin == "wake" else None)
                            for captured in utterance:
                                recognition_session.feed(captured)
                        else:
                            recognition_session.feed(block)
                    if speech:
                        voiced_blocks += 1
                        trailing_silence_blocks = 0
                    else:
                        trailing_silence_blocks += 1

                at_length_limit = len(utterance) * BLOCK_SIZE >= MAX_UTTERANCE_SECONDS * config.SAMPLE_RATE
                if in_speech and (trailing_silence_blocks >= end_silence_blocks or at_length_limit):
                    if hybrid and active_origin != "wake" and at_length_limit and trailing_silence_blocks < end_silence_blocks:
                        utterance.clear()
                        pre_roll.clear()
                        if recognition_session is not None:
                            recognition_session.close()
                            recognition_session = None
                        discarding_ambient = True
                        trailing_silence_blocks = 0
                        log_event("ignored_utterance", state=state, reason="ambient_utterance_too_long")
                        continue
                    samples = np.concatenate(utterance) if utterance else np.empty(0, dtype=np.float32)
                    utterance.clear()
                    pre_roll.clear()
                    in_speech = False
                    wake_seeded = False
                    vad.reset()
                    if state == "listening":
                        # A completed passive utterance must not seed a later wake.
                        wake_ring.clear()
                        kws_stream = kws.create_stream()
                    if not has_enough_voiced_audio(voiced_blocks, config.SAMPLE_RATE):
                        if recognition_session is not None:
                            recognition_session.close()
                            recognition_session = None
                        log_event("ignored_utterance", state=state, reason="too_little_speech")
                        continue

                    turn_id = "turn-" + uuid.uuid4().hex
                    turn_started = time.perf_counter()
                    turn_outcome = "ok"
                    voice_stage = "asr"
                    turn_stop = utterance_token if utterance_token is not None else speech_stop.token()
                    utterance_token = None
                    stages = TurnStages(event_bus.publish, turn_id, "microphone")
                    stages.start("asr")
                    DIAGNOSTICS.emit("capture", trace_id=turn_id,
                                     duration_ms=len(samples) * 1000 / config.SAMPLE_RATE)

                    accept_input = False
                    # Nothing is routed to the command queue while a turn is being
                    # processed; whether the microphone goes to the barge queue
                    # instead is decided per phase below.
                    barge_listening = False
                    drain(audio_queue)
                    barge_interrupted = False
                    barge_head: list[np.ndarray] = []
                    monitor = None
                    transcript = ""
                    decision = None
                    new_ambient_session = False

                    def stop_thinking_monitor(*, preserve_pending=False) -> list[np.ndarray]:
                        nonlocal barge_listening, accept_input
                        if monitor is None:
                            return []
                        monitor.stop()
                        if not monitor.interrupted and not preserve_pending:
                            barge_listening = False
                            return []
                        accept_input = True
                        barge_listening = False
                        return collect_barge_head(barge_detector, barge_queue, keep_pre_roll=True)

                    try:
                        if enrollment is not None and enrollment.active:
                            # Enrolment consumes the utterance: no transcript, no
                            # agent, no reply. The user is watching progress in the
                            # console, and a spoken answer would only interrupt the
                            # next sample.
                            status = enrollment.submit(samples, config.SAMPLE_RATE, speaker_embedder)
                            log_event("voice_enrollment", state=state, **enrollment_fields(status))
                            if not status.active:
                                enrollment = None
                            continue
                        if turn_stop.is_set():
                            raise VoiceCancelled("speech_cancelled")
                        t0 = time.perf_counter()
                        if recognition_session is not None:
                            try:
                                transcript = recognition_session.result(cancel=turn_stop)
                                auxiliary = voice_models.transcribe(asr, samples, config.SAMPLE_RATE)
                                transcript = voice_models.Transcript(transcript,
                                    emotion=getattr(auxiliary, "emotion", ""), event=getattr(auxiliary, "event", ""),
                                    lang=getattr(auxiliary, "lang", ""))
                            finally:
                                recognition_session.close()
                                recognition_session = None
                        else:
                            transcript = voice_models.transcribe(asr, samples, config.SAMPLE_RATE)
                        acoustic = acoustic_summary(transcript, samples)
                        if acoustic["invalid_audio"] or (hybrid and active_origin != "wake" and acoustic["non_speech"]
                                and not any(str(transcript).startswith(phrase) for phrase in wake_phrases)):
                            turn_outcome = "ignored"
                            log_event("ignored_utterance", state=state, reason="non_speech_audio")
                            continue
                        raw_transcript = transcript
                        if merge_wake or hybrid:
                            transcript = strip_wake_phrases(transcript, wake_phrases)
                        has_spoken_wake = any(raw_transcript.strip().startswith(phrase) for phrase in wake_phrases)
                        if hybrid and has_spoken_wake and active_origin != "wake":
                            state, active_origin = "active", "wake"
                            active_deadline = time.monotonic() + args.idle_timeout
                            agent.reset()
                            ambient_history.reset()
                            log_event("wake", state=state, reason="asr_wake_phrase")
                        asr_seconds = time.perf_counter() - t0
                        DIAGNOSTICS.emit("asr", trace_id=turn_id, duration_ms=asr_seconds * 1000)
                        if not transcript:
                            log_event("ignored_utterance", state=state, reason="empty_or_wake_only")
                            continue
                        if is_non_command_filler(transcript):
                            log_event("ignored_utterance", state=state, reason="non_command_filler")
                            continue
                        # Who spoke is evidence about this one sentence, so it is
                        # resolved here and consumed by the next handle() call.
                        voice_stage = "speaker"
                        stages.start("speaker")
                        speaker_started = time.perf_counter()
                        verdict, speaker_name, speaker_reason = resolve_speaker(
                            speaker_embedder, speaker_identity,
                            getattr(agent, "executor", None),
                            samples, config.SAMPLE_RATE,
                        )
                        DIAGNOSTICS.emit("speaker", trace_id=turn_id,
                                         duration_ms=(time.perf_counter() - speaker_started) * 1000)
                        # The score and the audio length are the two numbers a
                        # "why didn't it recognise me" report needs, and neither is
                        # recoverable after the fact: diagnostics used to record only
                        # the stage duration, so a low similarity was invisible. The
                        # identity never leaves here and the name is never recorded.
                        DIAGNOSTICS.emit(
                            "speaker_verdict",
                            trace_id=turn_id,
                            outcome="ok" if verdict is not None else "skipped",
                            error_code=(verdict.state if verdict is not None
                                        else (speaker_reason or "not_evaluated")),
                            confidence=(verdict.confidence if verdict is not None else None),
                            audio_seconds=len(samples) / config.SAMPLE_RATE,
                        )
                        speaker_id = verdict.person_id if verdict and verdict.usable else None
                        if hybrid and active_origin != "wake":
                            if state == "active" and speaker_id is not None and ambient_actor is not None and speaker_id != ambient_actor:
                                agent.reset()
                                ambient_history.reset()
                                state, active_origin = rest_state, None
                            elif state == "active" and ambient_actor is None and speaker_id is not None:
                                ambient_history.bind_first_speaker(speaker_id, time.monotonic())
                                ambient_actor = speaker_id
                            # A short follow-up may have no usable voiceprint. Keep
                            # dialogue context, but never reuse identity for tools.
                            context_actor = speaker_id if speaker_id is not None else ambient_actor
                            if barge_detector is not None:
                                barge_detector.reset()
                                drain(barge_queue)
                                monitor = BargeInMonitor(barge_detector, barge_queue)
                                monitor.start()
                                barge_listening = True
                            catalog = agent.executor.catalog()
                            rooms = [item.get("room_name") for item in catalog]
                            voice_stage = "intent"
                            stages.start("intent")
                            intent_started = time.perf_counter()
                            decision = classifier.classify(
                                transcript, active=state == "active", rooms=rooms,
                                context=ambient_history.context(context_actor, time.monotonic()),
                                acoustic=acoustic,
                                cancel=CombinedCancel(turn_stop, monitor.cancel if monitor is not None else None),
                            )
                            DIAGNOSTICS.emit("intent", trace_id=turn_id,
                                             duration_ms=(time.perf_counter() - intent_started) * 1000,
                                             outcome=decision.kind, error_code=decision.reason)
                            if decision.kind == "ignore":
                                if decision.reason == "intent_unavailable":
                                    event_bus.health_summary.update({"intent_status": "unavailable", "intent_error_code": decision.reason})
                                    log_event("intent_error", state=state, error_type="intent_unavailable")
                                else:
                                    event_bus.health_summary.update({"intent_status": "ready", "intent_error_code": None})
                                    ambient_history.append("user", transcript, context_actor, time.monotonic())
                                turn_outcome = "ignored"
                                log_event("ignored_utterance", state=state, reason=decision.reason or "ambient_chatter")
                                continue
                            event_bus.health_summary.update({"intent_status": "ready", "intent_error_code": None})
                            if state != "active":
                                agent.reset()
                                new_ambient_session = True
                                state, active_origin, ambient_actor = "active", "ambient", speaker_id
                                active_deadline = time.monotonic() + args.idle_timeout
                                log_event("natural_trigger", state=state, reason=decision.kind)
                        print(f"[asr {asr_seconds:.2f}s] {transcript}")
                        log_event(
                            "asr", state=state, transcript=transcript,
                            audio_event=acoustic["audio_event"], emotion=acoustic["emotion"], clipped_ratio=acoustic["clipped_ratio"],
                            seconds=round(asr_seconds, 3),
                            **speaker_fields(
                                verdict, speaker_name, speaker_reason,
                                audio_seconds=len(samples) / config.SAMPLE_RATE,
                                min_seconds=(speaker_embedder.min_seconds if speaker_embedder else None),
                            ),
                        )
                        # The agent decides intent, including whether the user is
                        # saying goodbye, so it must run before the keyword
                        # fallback. Matching keywords first would cut the session
                        # off before the farewell could be spoken.
                        if transcript and agent is not None:
                            # Set immediately before the call that consumes it: a
                            # turn that never reaches the agent must not leave a
                            # verdict behind for the next sentence.
                            agent.speaker_line = verdict.prompt_line(speaker_name) if verdict else None
                            agent.speaker_person_id = verdict.person_id if verdict and verdict.usable else None
                            reply_text = config.FIXED_REPLY_TEXT
                            agent_ended_conversation = False
                            # Thinking-phase interruption. The cloud call is the
                            # longest silence in a turn, so the microphone is
                            # routed to the barge queue here and a worker thread
                            # watches it: a new sentence cancels the request
                            # instead of being dropped on the floor.
                            if barge_detector is not None and monitor is None:
                                barge_detector.reset()
                                drain(barge_queue)
                                monitor = BargeInMonitor(barge_detector, barge_queue)
                                monitor.start()
                                barge_listening = True
                            if agent is not None:
                                voice_stage = "agent"
                                stages.start("agent")
                                agent_started = time.perf_counter()
                                try:
                                    cancel = CombinedCancel(turn_stop, monitor.cancel if monitor is not None else None)
                                    agent_reply = (agent.handle_admitted(transcript, decision, cancel=cancel)
                                                   if decision is not None else agent.handle(transcript, cancel=cancel))
                                    if getattr(agent_reply, "ignored", False):
                                        turn_outcome = "ignored"
                                        log_event("ignored_utterance", state=state, reason=agent_reply.error_code)
                                        if new_ambient_session:
                                            state, active_origin, ambient_actor = rest_state, None, None
                                            agent.reset()
                                            log_event("ambient_suppressed", state=state, reason=agent_reply.error_code)
                                        continue
                                    DIAGNOSTICS.emit("agent", trace_id=turn_id,
                                                     outcome="cancelled" if agent_reply.cancelled else "ok" if agent_reply.ok else "error",
                                                     duration_ms=(time.perf_counter() - agent_started) * 1000,
                                                     error_code=agent_reply.error_code)
                                    reply_text = agent_reply.text
                                    agent_ended_conversation = agent_reply.end_conversation
                                    if agent_reply.cancelled:
                                        turn_outcome = "cancelled"
                                        continue
                                    if decision is not None:
                                        ambient_history.append("user", transcript, context_actor, time.monotonic())
                                        ambient_history.append("assistant", reply_text, context_actor, time.monotonic())
                                    print(f"[agent] {reply_text or '<cancelled>'}")
                                    log_event(
                                        "agent_reply",
                                        state=state,
                                        text=reply_text,
                                        ok=agent_reply.ok,
                                        error_code=agent_reply.error_code,
                                        end_conversation=agent_reply.end_conversation,
                                        cancelled=agent_reply.cancelled,
                                        tools=[result.name for result in agent_reply.tool_results],
                                        seconds=round(time.perf_counter() - agent_started, 3),
                                    )
                                    for result in agent_reply.tool_results:
                                        DIAGNOSTICS.emit("device_operation", trace_id=turn_id,
                                                         operation_id=result.operation_id,
                                                         outcome="ok" if result.ok else "error",
                                                         error_code=result.error_code)
                                        log_event(
                                            "tool_result",
                                            state=state,
                                            name=result.name,
                                            ok=result.ok,
                                            error_code=result.error_code,
                                            message=result.message,
                                            phrase=result.phrase,
                                            operation_id=result.operation_id,
                                        )
                                except Exception as exc:
                                    # Never let an agent crash kill the voice loop.
                                    reply_text = "语音助手出现异常，请稍后再试。"
                                    print(f"[agent error] {type(exc).__name__}", file=sys.stderr)
                                    log_event(
                                        "agent_error", state=state, error_type=type(exc).__name__
                                    )
                                    DIAGNOSTICS.emit("agent", trace_id=turn_id, outcome="error",
                                                     duration_ms=(time.perf_counter() - agent_started) * 1000,
                                                     error_code="agent_exception")

                            def play_reply() -> None:
                                nonlocal accept_input, barge_listening, barge_interrupted, turn_outcome, active_origin, voice_stage, monitor
                                if turn_stop.is_set():
                                    raise VoiceCancelled("speech_cancelled")
                                reply, sr, synth_seconds = reply_text, config.SAMPLE_RATE, 0.0
                                if getattr(tts, "supports_streaming", False):
                                    voice_stage = "tts_synthesis"
                                    stages.start("tts_synthesis")
                                    stream_started = time.perf_counter()
                                    # Keep monitoring through network gaps as well as PCM output.
                                    # Re-arm its echo guard before playback, on the main thread.
                                    pending = stop_thinking_monitor()
                                    if pending:
                                        barge_head.extend(pending)
                                        barge_interrupted = True
                                        turn_outcome = "cancelled"
                                        return
                                    if barge_detector is not None:
                                        barge_detector.start_playback()
                                        monitor = BargeInMonitor(barge_detector, barge_queue)
                                        monitor.start()
                                        barge_listening = True
                                    def first_pcm(latency):
                                        nonlocal voice_stage
                                        voice_stage = "playback"
                                        stages.start("playback")
                                        first_audio_seconds = time.perf_counter()-stream_started
                                        DIAGNOSTICS.emit("tts_synthesis", trace_id=turn_id, duration_ms=first_audio_seconds*1000)
                                        DIAGNOSTICS.emit("tts_first_output", trace_id=turn_id,
                                                         duration_ms=(time.perf_counter()-turn_started+latency)*1000)
                                        log_event("tts_start", state=state, text=reply_text, synth_seconds=round(first_audio_seconds, 3))
                                    token = CombinedCancel(turn_stop, monitor.cancel if monitor is not None else None)
                                    completed = playback.play_chunks(tts.stream(reply_text, cancel=token),
                                        output_target, should_interrupt=token.is_set, on_stream_started=first_pcm)
                                    interrupted = stop_thinking_monitor()
                                    if interrupted:
                                        barge_head.extend(interrupted)
                                        barge_interrupted = True
                                        turn_outcome = "cancelled"
                                    if barge_detector is not None:
                                        barge_detector.stop_playback()
                                    log_event("tts_end", state=state, text=reply_text, interrupted=not completed)
                                    return
                                if tts is not None:
                                    # Do not echo the transcript: it could contain a
                                    # wake word.
                                    voice_stage = "tts_synthesis"
                                    stages.start("tts_synthesis")
                                    tts_started = time.perf_counter()
                                    reply, sr = (tts.synthesize(reply_text, cancel=CombinedCancel(turn_stop, monitor.cancel if monitor is not None else None))
                                        if isinstance(tts, voice_models.TtsEngine) and tts.provider != "kokoro" else synthesize_reply(tts, reply_text, args.speaker_id))
                                    synth_seconds = time.perf_counter() - tts_started
                                    DIAGNOSTICS.emit("tts_synthesis", trace_id=turn_id,
                                                     duration_ms=synth_seconds * 1000)
                                interrupted = stop_thinking_monitor()
                                if interrupted:
                                    turn_outcome = "cancelled"
                                    barge_head.extend(interrupted)
                                    barge_interrupted = True
                                    drain(barge_queue)
                                    print("[barge] 你插话了，这条回复不再播报")
                                    log_event(
                                        "agent_interrupted", state=state, text=reply_text,
                                        captured_seconds=round(
                                            voiced_seconds(barge_head, config.SAMPLE_RATE), 2
                                        ),
                                    )
                                    return
                                if tts is None:
                                    print(f"[reply] {reply_text}")
                                    return
                                hint = (
                                    "播报中可以随时说话打断，也可以说唤醒词"
                                    if barge_detector is not None
                                    else "播报中说唤醒词，等播报停止后再说新命令"
                                )
                                print(f"[tts] {hint}")
                                log_event(
                                    "tts_start", state=state, text=reply_text,
                                    synth_seconds=round(synth_seconds, 3),
                                    barge_in_hint=hint,
                                )

                                def record_first_output(portaudio_latency: float) -> None:
                                    submitted = time.perf_counter() - tts_started
                                    DIAGNOSTICS.emit("tts_first_output", trace_id=turn_id,
                                                     duration_ms=(time.perf_counter() - turn_started + portaudio_latency) * 1000)
                                    log_event(
                                        "tts_first_output_estimate",
                                        state=state,
                                        seconds=round(submitted + portaudio_latency, 3),
                                        synthesis_seconds=round(synth_seconds, 3),
                                        portaudio_latency_seconds=round(portaudio_latency, 3),
                                        estimated=True,
                                    )

                                voice_stage = "playback"
                                stages.start("playback")
                                drain(barge_queue)
                                reason = None
                                wake = WakeBargeIn(kws) if kws is not None else None
                                barge_listening = barge_detector is not None or wake is not None
                                try:
                                    if barge_listening:
                                        if barge_detector is not None:
                                            barge_detector.reset()
                                        reason = play_with_barge(
                                            reply, sr, output_target,
                                            PlaybackBargeIn(speech=barge_detector, wake=wake),
                                            barge_queue, record_first_output, cancel=turn_stop,
                                        )
                                    else:
                                        play(reply, sr, output_target, record_first_output, turn_stop.is_set)
                                finally:
                                    if reason == "speech":
                                        accept_input = True
                                    barge_listening = False
                                    if reason == "speech" and barge_detector is not None:
                                        # Collect before clearing the queue. These
                                        # blocks may contain the rest of the command.
                                        barge_head.extend(collect_barge_head(
                                            barge_detector, barge_queue, keep_pre_roll=True,
                                        ))
                                    if barge_detector is not None:
                                        barge_detector.stop_playback()
                                    if reason == "speech":
                                        # New mic blocks now continue in the normal
                                        # command queue while this turn unwinds.
                                        accept_input = True
                                    else:
                                        drain(barge_queue)
                                if reason == "speech":
                                    turn_outcome = "cancelled"
                                    # The detector keeps echo-reduced pre-roll
                                    # (not raw reply leakage), so a quiet first
                                    # syllable is retained with confirmed speech.
                                    barge_interrupted = True
                                    print("[barge] 你打断了播报，接着说")
                                    log_event(
                                        "tts_interrupted", state=state, text=reply_text,
                                        keyword=None, interrupted=True, reason="speech",
                                        captured_seconds=round(
                                            voiced_seconds(barge_head, config.SAMPLE_RATE), 2
                                        ),
                                    )
                                elif reason == "wake":
                                    turn_outcome = "cancelled"
                                    active_origin = "wake"
                                    ambient_history.reset()
                                    # The wake phrase itself was only a gate. The
                                    # normal VAD/ASR path receives the next sentence.
                                    drain(audio_queue)
                                    vad.reset()
                                    pre_roll.clear()
                                    utterance.clear()
                                    accept_input = True
                                    barge_interrupted = True
                                    log_event(
                                        "tts_interrupted", state=state, text=reply_text,
                                        keyword=wake.keyword if wake is not None else None,
                                        interrupted=True, reason="wake",
                                    )
                                else:
                                    print("[tts] 播放结束，恢复识别")
                                log_event(
                                    "tts_end", state=state, text=reply_text, interrupted=bool(reason)
                                )

                            # Processing and playback do not consume the user's
                            # waiting window. The full timeout starts now, after
                            # the reply has completed.
                            active_deadline = deadline_after_reply(args.idle_timeout, play_reply)

                            if agent_ended_conversation and not barge_interrupted:
                                # The model judged that the user said goodbye, so
                                # stop listening now that the farewell was spoken.
                                print(f"[exit] 对话已结束，回到{rest_state}")
                                log_event("agent_end_conversation", state=rest_state)
                                state, active_origin, ambient_actor = rest_state, None, None
                                ambient_history.reset()
                                kws_stream = kws.create_stream()
                                wake_ring.clear()
                                wake_seeded = False
                                if agent is not None:
                                    agent.reset()
                        elif should_exit(transcript):
                            # Only reached without an agent: an unmistakable phrase
                            # still has to work when the model is unavailable.
                            print("[exit] 回到待唤醒")
                            log_event("exit_command", state="standby", transcript=transcript)
                            state = "standby"
                            kws_stream = kws.create_stream()
                            wake_ring.clear()
                            wake_seeded = False
                    except (AgentCancelled, VoiceCancelled):
                        if recognition_session is not None:
                            recognition_session.close()
                            recognition_session = None
                        log_event("agent_interrupted", state=state, reason="cancelled")
                        turn_outcome = "cancelled"
                    except Exception as exc:
                        turn_outcome = "error"
                        details = failure_details(voice_stage, exc)
                        DIAGNOSTICS.emit("turn_error", trace_id=turn_id, outcome="error",
                                         error_code=details["error_code"])
                        # A model/playback failure must not leave recognition
                        # permanently disabled. Start a fresh waiting window
                        # after the visible error feedback.
                        print(f"[voice error] {details['error_code']} ({type(exc).__name__})", file=sys.stderr)
                        log_event("voice_error", state=state, **details)
                        if state == "active" and transcript:
                            active_deadline = deadline_after_reply(args.idle_timeout)
                    finally:
                        stages.finish("failed" if turn_outcome == "error" else turn_outcome)
                        DIAGNOSTICS.emit("turn", trace_id=turn_id, outcome=turn_outcome,
                                         duration_ms=(time.perf_counter() - turn_started) * 1000)
                        if monitor is not None:
                            # A failure anywhere above must not leave a thread
                            # holding the microphone queue.
                            if not barge_interrupted:
                                barge_head.extend(stop_thinking_monitor(preserve_pending=True))
                                if barge_head and monitor.interrupted:
                                    barge_interrupted = True
                                    log_event("agent_interrupted", state=state, reason="speech",
                                              captured_seconds=round(voiced_seconds(barge_head, config.SAMPLE_RATE), 2))
                            else:
                                monitor.stop()
                        barge_listening = False
                        if barge_head:
                            # The interruption is the beginning of the next
                            # command. Handing it to the normal VAD path here is
                            # what makes "空调调高一度" a complete sentence instead
                            # of a truncated one, and it is why the words that
                            # stopped the reply are never thrown away.
                            pre_roll.clear()
                            utterance = list(barge_head)
                            utterance_token = speech_stop.token()
                            barge_head.clear()
                            in_speech = True
                            vad.reset()
                            voiced_blocks = 0
                            trailing_silence_blocks = 0
                            for captured in utterance:
                                if vad.is_speech(captured):
                                    voiced_blocks += 1
                                    trailing_silence_blocks = 0
                                else:
                                    trailing_silence_blocks += 1
                            accept_input = True
                        else:
                            if not barge_interrupted:
                                drain(audio_queue)
                            accept_input = True

    except sd.PortAudioError:
        event_bus.health_summary["error_code"] = "audio_input_open_failed"
        event_bus.publish("service_error", state="degraded", error_type="audio_input_open_failed")
        if control_server is None:
            raise
        selection_request = selection_queue.get()
        selection_queue.task_done()
        device_preferences.save(selection_request["preferences"])
        event_bus.set_selection_status(selection_request["request_id"], "applied")
        control_server.shutdown()
        control_server.server_close()
        control_server = None
        return main()
    except KeyboardInterrupt:
        print("\nstopped")
        log_event("keyboard_interrupt", state=state)
    finally:
        print(f"audio queue overflows: {overflow_count}")
        speech_stop.stop()
        text_speech.stop()
        if recognition_session is not None:
            recognition_session.close()
        if playback.reference_sink is processor:
            playback.reference_sink = None
        log_event("stopped", state=state, audio_queue_overflows=overflow_count)
        if log_handle is not None:
            log_handle.close()
        if control_server is not None:
            control_server.shutdown()
            control_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
