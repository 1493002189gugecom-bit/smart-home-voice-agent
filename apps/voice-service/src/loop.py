"""Wake-once continuous local voice loop with the cloud Agent.

Behavior:
- Uses a working system-default microphone first, with physical-device fallbacks.
- Standby listens only for one of the configured wake words.
- After waking, VAD segments continuous utterances and SenseVoice transcribes.
- With `--agent`, the transcript is routed through the cloud LLM, which may call
  the restricted home-service tools; the reply is spoken with the configured TTS.
  Without it the loop speaks a fixed acknowledgement and touches no device.
- The active session returns to standby after the idle timeout without a
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

BLOCK_SIZE = 512
IDLE_TIMEOUT_SECONDS = 20.0
MAX_UTTERANCE_SECONDS = 15.0
PRE_ROLL_BLOCKS = 10
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
) -> str | None:
    """Play a reply; return why it stopped: "speech", "wake", or None if finished."""

    if detector.speech is not None:
        detector.speech.start_playback(reference=samples, reference_rate=sample_rate)

    def should_interrupt() -> bool:
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
    return playback.play(
        samples, sample_rate, target,
        on_stream_started=on_stream_started,
        should_interrupt=should_interrupt,
    )


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


def start_enrollment(person_id, *, required, embedder, identity, directory_reader, active_session):
    """Decide whether an enrolment may start, and say why not when it may not.

    Pure policy: everything it needs is passed in, so the refusal rules — no model,
    one enrolment at a time, an id the directory does not know — are provable without
    a microphone or a running service.
    """

    if embedder is None or identity is None:
        return None, {"ok": False, "error_code": "speaker_disabled", "message": "未配置声纹模型，无法录入声纹"}
    if active_session is not None and active_session.active:
        return None, {"ok": False, "error_code": "enrollment_active", "message": "已有声纹录入正在进行"}
    if not callable(directory_reader):
        return None, {"ok": False, "error_code": "person_directory_unavailable", "message": "人物名单暂不可用，请检查 home-service"}
    if person_id not in directory_reader():
        return None, {"ok": False, "error_code": "unknown_person", "message": "请先在身份页添加该人物"}
    session = VoiceEnrollmentSession(
        person_id,
        save=lambda pid, embeddings: identity.enroll_person(pid, embeddings),
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
        )
        if session is not None:
            enrollment = session
        return payload

    control_server = None
    if not args.no_control_api and not args.startup_check:
        control_server = create_control_server(
            event_bus, args.control_host, args.control_port,
            on_select=select_devices, on_identity=identity_handler,
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

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        nonlocal overflow_count
        if status:
            print(f"audio status: {status}", file=sys.stderr)
            log_event("audio_status", status=str(status))
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            route_microphone_block(
                audio_queue, barge_queue, block,
                accepting_commands=accept_input,
                barge_listening=barge_listening,
            )
        except queue.Full:
            overflow_count += 1

    state = "standby"
    kws_stream = kws.create_stream()
    active_deadline = 0.0
    pre_roll: collections.deque[np.ndarray] = collections.deque(maxlen=PRE_ROLL_BLOCKS)
    utterance: list[np.ndarray] = []
    in_speech = False
    voiced_blocks = 0

    wake_phrases = config.wake_words(args.keywords_file)
    if wake_phrases:
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
                        state = "standby"
                        kws_stream = kws.create_stream()
                        vad.reset()
                        pre_roll.clear()
                        utterance.clear()
                        drain(audio_queue)
                        event_bus.publish("voice_state", state="standby", reason="audio_devices_changed")
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
                    print(f"[timeout] {args.idle_timeout:g} 秒无新语音，回到待唤醒")
                    log_event("idle_timeout", state="standby", idle_timeout_seconds=args.idle_timeout)
                    state = "standby"
                    kws_stream = kws.create_stream()
                    vad.reset()
                    pre_roll.clear()
                    utterance.clear()
                    if agent is not None:
                        agent.reset()
                try:
                    block = audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if state == "standby":
                    kws_stream.accept_waveform(config.SAMPLE_RATE, block)
                    while kws.is_ready(kws_stream):
                        kws.decode_stream(kws_stream)
                        keyword = kws.get_result(kws_stream)
                        if keyword:
                            print(f"[wake] {keyword}；进入连续对话")
                            log_event("wake", state="active", keyword=keyword)
                            kws.reset_stream(kws_stream)
                            accept_input = False
                            drain(audio_queue)
                            if output_target is not None:
                                play(wake_tone(), 24000, output_target)
                            drain(audio_queue)
                            accept_input = True
                            state = "active"
                            active_deadline = time.monotonic() + args.idle_timeout
                            vad.reset()
                            pre_roll.clear()
                            utterance.clear()
                            in_speech = False
                            break
                    continue

                speech = vad.is_speech(block)
                if not in_speech:
                    pre_roll.append(block)
                    if speech:
                        utterance = list(pre_roll)
                        pre_roll.clear()
                        in_speech = True
                        voiced_blocks = 1
                elif speech:
                    utterance.append(block)
                    voiced_blocks += 1
                    if len(utterance) * BLOCK_SIZE >= MAX_UTTERANCE_SECONDS * config.SAMPLE_RATE:
                        speech = False

                if in_speech and not speech:
                    samples = np.concatenate(utterance) if utterance else np.empty(0, dtype=np.float32)
                    utterance.clear()
                    pre_roll.clear()
                    in_speech = False
                    vad.reset()
                    if not has_enough_voiced_audio(voiced_blocks, config.SAMPLE_RATE):
                        log_event("ignored_utterance", state=state, reason="too_little_speech")
                        continue

                    turn_id = "turn-" + uuid.uuid4().hex
                    turn_started = time.perf_counter()
                    turn_outcome = "ok"
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
                        t0 = time.perf_counter()
                        transcript = voice_models.transcribe(asr, samples, config.SAMPLE_RATE)
                        asr_seconds = time.perf_counter() - t0
                        DIAGNOSTICS.emit("asr", trace_id=turn_id, duration_ms=asr_seconds * 1000)
                        print(f"[asr {asr_seconds:.2f}s] {transcript or '<empty>'}")
                        if is_non_command_filler(transcript):
                            log_event("ignored_utterance", state=state, reason="non_command_filler")
                            continue
                        # Who spoke is evidence about this one sentence, so it is
                        # resolved here and consumed by the next handle() call.
                        speaker_started = time.perf_counter()
                        verdict, speaker_name, speaker_reason = resolve_speaker(
                            speaker_embedder, speaker_identity,
                            getattr(agent, "executor", None),
                            samples, config.SAMPLE_RATE,
                        )
                        DIAGNOSTICS.emit("speaker", trace_id=turn_id,
                                         duration_ms=(time.perf_counter() - speaker_started) * 1000)
                        log_event(
                            "asr", state=state, transcript=transcript,
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
                            if barge_detector is not None:
                                barge_detector.reset()
                                drain(barge_queue)
                                monitor = BargeInMonitor(barge_detector, barge_queue)
                                monitor.start()
                                barge_listening = True
                            if agent is not None:
                                agent_started = time.perf_counter()
                                try:
                                    agent_reply = agent.handle(
                                        transcript, cancel=monitor.cancel if monitor is not None else None
                                    )
                                    DIAGNOSTICS.emit("agent", trace_id=turn_id,
                                                     outcome="cancelled" if agent_reply.cancelled else "ok" if agent_reply.ok else "error",
                                                     duration_ms=(time.perf_counter() - agent_started) * 1000,
                                                     error_code=agent_reply.error_code)
                                    reply_text = agent_reply.text
                                    agent_ended_conversation = agent_reply.end_conversation
                                    if agent_reply.cancelled:
                                        turn_outcome = "cancelled"
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

                            def stop_thinking_monitor() -> list[np.ndarray]:
                                """End thinking-phase listening; return a barge, if any.

                                Synthesis happens before this is called, so a user
                                who speaks while the reply is still being generated
                                is heard too — and the reply they interrupted is
                                discarded instead of being played late.
                                """
                                nonlocal barge_listening
                                if monitor is None:
                                    return []
                                monitor.stop()
                                barge_listening = False
                                if not monitor.interrupted:
                                    return []
                                # No echo guard was active here: nothing was
                                # playing, so the pre-roll is the user's own voice.
                                return collect_barge_head(
                                    barge_detector, barge_queue, keep_pre_roll=True,
                                )

                            def play_reply() -> None:
                                nonlocal accept_input, barge_listening, barge_interrupted, turn_outcome
                                reply, sr, synth_seconds = reply_text, config.SAMPLE_RATE, 0.0
                                if tts is not None:
                                    # Do not echo the transcript: it could contain a
                                    # wake word.
                                    tts_started = time.perf_counter()
                                    reply, sr = synthesize_reply(tts, reply_text, args.speaker_id)
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
                                            barge_queue, record_first_output,
                                        )
                                    else:
                                        play(reply, sr, output_target, record_first_output)
                                finally:
                                    barge_listening = False
                                    if reason == "speech" and barge_detector is not None:
                                        # Collect before clearing the queue. These
                                        # blocks may contain the rest of the command.
                                        barge_head.extend(collect_barge_head(
                                            barge_detector, barge_queue, keep_pre_roll=False,
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
                                    # The pre-roll is the tail of the reply the
                                    # microphone also heard. Sending it to ASR would
                                    # be asking the assistant to answer its own words,
                                    # so only the gated speech is kept.
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
                                print("[exit] 对话已结束，回到待唤醒")
                                log_event("agent_end_conversation", state="standby")
                                state = "standby"
                                kws_stream = kws.create_stream()
                                if agent is not None:
                                    agent.reset()
                        elif should_exit(transcript):
                            # Only reached without an agent: an unmistakable phrase
                            # still has to work when the model is unavailable.
                            print("[exit] 回到待唤醒")
                            log_event("exit_command", state="standby", transcript=transcript)
                            state = "standby"
                            kws_stream = kws.create_stream()
                    except Exception as exc:
                        turn_outcome = "error"
                        DIAGNOSTICS.emit("turn_error", trace_id=turn_id, outcome="error",
                                         error_code="voice_processing_error")
                        # A model/playback failure must not leave recognition
                        # permanently disabled. Start a fresh waiting window
                        # after the visible error feedback.
                        print(f"[voice error] {type(exc).__name__}: {exc}", file=sys.stderr)
                        log_event("voice_error", state=state, error_type=type(exc).__name__, message=str(exc))
                        if state == "active" and transcript:
                            active_deadline = deadline_after_reply(args.idle_timeout)
                    finally:
                        DIAGNOSTICS.emit("turn", trace_id=turn_id, outcome=turn_outcome,
                                         duration_ms=(time.perf_counter() - turn_started) * 1000)
                        if monitor is not None:
                            # A failure anywhere above must not leave a thread
                            # holding the microphone queue.
                            monitor.stop()
                        if barge_head:
                            # The interruption is the beginning of the next
                            # command. Handing it to the normal VAD path here is
                            # what makes "空调调高一度" a complete sentence instead
                            # of a truncated one, and it is why the words that
                            # stopped the reply are never thrown away.
                            pre_roll.clear()
                            utterance = list(barge_head)
                            barge_head.clear()
                            in_speech = True
                            voiced_blocks = len(utterance)
                            for captured in utterance:
                                vad.is_speech(captured)
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
        log_event("stopped", state=state, audio_queue_overflows=overflow_count)
        if log_handle is not None:
            log_handle.close()
        if control_server is not None:
            control_server.shutdown()
            control_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
