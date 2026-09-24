"""Wake-once continuous local voice loop with the cloud Agent.

Behavior:
- Explicitly uses the physical microphone, not Windows' virtual default.
- Standby listens only for one of the configured wake words.
- After waking, VAD segments continuous utterances and SenseVoice transcribes.
- With `--agent`, the transcript is routed through the cloud LLM, which may call
  the restricted home-service tools; the reply is spoken with the configured TTS.
  Without it the loop speaks a fixed acknowledgement and touches no device.
- The active session returns to standby after the idle timeout without a
  completed utterance, or immediately on "退出"/"结束对话".
- Microphone blocks are discarded while TTS is playing, preventing playback
  from triggering recognition.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import queue
import sys
import time
from pathlib import Path
from typing import Callable

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
import playback
import voice_models
from control_server import create_control_server, start_control_server
from event_bus import VoiceEventBus

BLOCK_SIZE = 512
IDLE_TIMEOUT_SECONDS = 20.0
MAX_UTTERANCE_SECONDS = 15.0
PRE_ROLL_BLOCKS = 10


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


def should_exit(transcript: str) -> bool:
    """Last-resort exit words.

    The agent decides intent through the ``end_conversation`` tool; this covers
    the case where the model is unavailable but the phrase is unmistakable.
    """
    return any(word in transcript for word in ("退出", "结束对话", "再见", "拜拜"))


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
) -> None:
    """Play through the verified target, reporting stream latency once."""
    audio = playback.resample(samples, sample_rate, target.sample_rate)
    frames = playback.to_channels(audio, target.channels)
    with sd.OutputStream(
        samplerate=target.sample_rate,
        channels=target.channels,
        dtype="float32",
        device=target.index,
    ) as stream:
        if on_stream_started is not None:
            on_stream_started(float(stream.latency))
        stream.write(frames)
        time.sleep(0.35)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords-file", type=Path, default=Path("tools/voice-check/cases/wake-keywords.txt"))
    parser.add_argument("--input-contains", "--input-device", dest="input_contains", default=config.DEFAULT_INPUT_DEVICE)
    parser.add_argument("--output-contains", "--output-device", dest="output_contains", default=config.DEFAULT_OUTPUT_DEVICE)
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

    # Validate the agent configuration before loading any model, so a missing key
    # fails immediately instead of after a slow model warm-up.
    agent_requested = args.agent or config.agent_enabled()
    agent_api_key = ""
    if agent_requested:
        agent_api_key = config.agent_api_key()
        if not agent_api_key:
            print(
                "agent requested but DEEPSEEK_API_KEY is not set.\n"
                f"Put it in {config.agent_env_file()} (git-ignored) or the environment.",
                file=sys.stderr,
            )
            return 2

    input_device = audio_utils.select_input_device(args.input_contains, config.SAMPLE_RATE)
    output_target = None if args.no_tts else audio_utils.select_playback_target(args.output_contains)
    print(f"input : #{input_device.index} {input_device.name} [{input_device.hostapi}]")
    if output_target:
        print(f"output: {output_target.describe()}")
    print(f"model root: {config.models_dir()}")
    if args.text:
        print("text mode: microphone and speech models are not used"
              + ("" if args.no_tts else "; loading TTS"))
    else:
        print("loading KWS/VAD/ASR" + (" ..." if args.no_tts else "/TTS ..."))

    # Text mode never touches the microphone, so the speech models are skipped and
    # startup stays fast.
    kws = None if args.text else voice_models.create_kws(args.keywords_file)
    vad = None if args.text else voice_models.create_vad()
    asr = None if args.text else voice_models.create_asr()
    tts = None if args.no_tts else voice_models.create_tts()

    # The agent is opt-in. A missing key is a hard, explicit failure rather than a
    # silent fallback, because pretending to control devices is worse than not
    # starting at all.
    agent = None
    if agent_requested:
        agent = agent_session.create_agent_session(
            agent_api_key,
            args.service_url,
            deadline_seconds=args.agent_deadline,
            max_tool_rounds=args.agent_max_tool_rounds,
        )
        print(f"agent: {config.agent_model()} -> {args.service_url}")

    if args.startup_check:
        print("startup check OK: devices and all requested models initialized")
        return 0

    log_handle = None
    if args.log_file is not None:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = args.log_file.open("a", encoding="utf-8", buffering=1)
    event_bus = VoiceEventBus(max_events=200)
    event_bus.health_summary = {
        "input_device": input_device.name,
        "output_device": output_target.describe() if output_target is not None else None,
        "agent_enabled": agent is not None,
        "loop_mode": "text" if args.text else "microphone",
    }
    control_server = None
    if not args.no_control_api:
        control_server = create_control_server(event_bus, args.control_host, args.control_port)
        start_control_server(control_server)
        print(f"voice control API: http://{args.control_host}:{args.control_port}")

    public_event_types = {
        "ready": "voice_state",
        "wake": "voice_state",
        "idle_timeout": "voice_state",
        "text_session_start": "voice_state",
        "text_session_end": "voice_state",
        "agent_end_conversation": "voice_state",
        "exit_command": "voice_state",
        "stopped": "voice_state",
        "asr": "transcript",
        "agent_reply": "agent_reply",
        "tool_result": "tool_result",
        "tts_start": "playback_state",
        "tts_end": "playback_state",
        "tts_error": "service_error",
        "agent_error": "service_error",
        "voice_error": "service_error",
        "audio_status": "service_error",
    }
    public_fields = {
        "voice_state": {"state", "keyword", "reason"},
        "transcript": {"state", "transcript", "seconds"},
        "agent_reply": {"state", "text", "ok", "error_code", "end_conversation", "tools", "seconds"},
        "tool_result": {"state", "name", "ok", "error_code", "phrase", "operation_id"},
        "playback_state": {"state", "text", "synth_seconds"},
        "service_error": {"state", "error_type"},
    }

    def log_event(event: str, **fields) -> None:
        record = {
            "timestamp": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        }
        if log_handle is not None:
            log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        public_type = public_event_types.get(event)
        if public_type is not None:
            safe = {key: value for key, value in fields.items() if key in public_fields[public_type]}
            event_bus.publish(public_type, source_event=event, **safe)

    if args.text:
        try:
            return run_text_session(agent, tts, output_target, args, log_event, log_handle)
        finally:
            if control_server is not None:
                control_server.shutdown()
                control_server.server_close()

    audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=128)
    accept_input = True
    overflow_count = 0

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        nonlocal overflow_count
        if status:
            print(f"audio status: {status}", file=sys.stderr)
            log_event("audio_status", status=str(status))
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            enqueue_if_enabled(audio_queue, block, accept_input)
        except queue.Full:
            overflow_count += 1

    state = "standby"
    kws_stream = kws.create_stream()
    active_deadline = 0.0
    pre_roll: collections.deque[np.ndarray] = collections.deque(maxlen=PRE_ROLL_BLOCKS)
    utterance: list[np.ndarray] = []
    in_speech = False

    wake_phrases = config.wake_words(args.keywords_file)
    if wake_phrases:
        spoken = "或".join(f"“{phrase}”" for phrase in wake_phrases)
        print(f"ready: 请说{spoken}（Ctrl+C 退出）")
    else:
        print(f"ready: 未从 {args.keywords_file} 读到唤醒词，请检查该文件（Ctrl+C 退出）")
    log_event(
        "ready",
        state=state,
        input_device=input_device.name,
        output_device=output_target.describe() if output_target else None,
        idle_timeout_seconds=args.idle_timeout,
        wake_words=wake_phrases,
        agent_enabled=agent is not None,
    )
    run_deadline = time.monotonic() + args.run_seconds if args.run_seconds > 0 else None
    try:
        with sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            device=input_device.index,
            channels=1,
            dtype="float32",
            callback=callback,
        ):
            while True:
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
                elif speech:
                    utterance.append(block)
                    if len(utterance) * BLOCK_SIZE >= MAX_UTTERANCE_SECONDS * config.SAMPLE_RATE:
                        speech = False

                if in_speech and not speech:
                    samples = np.concatenate(utterance) if utterance else np.empty(0, dtype=np.float32)
                    utterance.clear()
                    pre_roll.clear()
                    in_speech = False
                    vad.reset()
                    if len(samples) < int(0.35 * config.SAMPLE_RATE):
                        continue

                    accept_input = False
                    drain(audio_queue)
                    transcript = ""
                    try:
                        t0 = time.perf_counter()
                        transcript = voice_models.transcribe(asr, samples, config.SAMPLE_RATE)
                        asr_seconds = time.perf_counter() - t0
                        print(f"[asr {asr_seconds:.2f}s] {transcript or '<empty>'}")
                        log_event("asr", state=state, transcript=transcript, seconds=round(asr_seconds, 3))
                        # The agent decides intent, including whether the user is
                        # saying goodbye, so it must run before the keyword
                        # fallback. Matching keywords first would cut the session
                        # off before the farewell could be spoken.
                        if transcript and agent is not None:
                            reply_text = config.FIXED_REPLY_TEXT
                            agent_ended_conversation = False
                            if agent is not None:
                                agent_started = time.perf_counter()
                                try:
                                    agent_reply = agent.handle(transcript)
                                    reply_text = agent_reply.text
                                    agent_ended_conversation = agent_reply.end_conversation
                                    print(f"[agent] {reply_text}")
                                    log_event(
                                        "agent_reply",
                                        state=state,
                                        text=reply_text,
                                        ok=agent_reply.ok,
                                        error_code=agent_reply.error_code,
                                        end_conversation=agent_reply.end_conversation,
                                        tools=[result.name for result in agent_reply.tool_results],
                                        seconds=round(time.perf_counter() - agent_started, 3),
                                    )
                                    for result in agent_reply.tool_results:
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

                            def play_reply() -> None:
                                if tts is None:
                                    print(f"[reply] {reply_text}")
                                    return
                                # Do not echo the transcript: it could contain a wake word.
                                tts_started = time.perf_counter()
                                reply, sr = synthesize_reply(tts, reply_text, args.speaker_id)
                                synth_seconds = time.perf_counter() - tts_started
                                print("[tts] 播放期间暂停识别")
                                log_event("tts_start", state=state, text=reply_text, synth_seconds=round(synth_seconds, 3))

                                def record_first_output(portaudio_latency: float) -> None:
                                    submitted = time.perf_counter() - tts_started
                                    log_event(
                                        "tts_first_output_estimate",
                                        state=state,
                                        seconds=round(submitted + portaudio_latency, 3),
                                        synthesis_seconds=round(synth_seconds, 3),
                                        portaudio_latency_seconds=round(portaudio_latency, 3),
                                        estimated=True,
                                    )

                                play(reply, sr, output_target, record_first_output)
                                print("[tts] 播放结束，恢复识别")
                                log_event("tts_end", state=state, text=reply_text)

                            # Processing and playback do not consume the user's
                            # waiting window. The full timeout starts now, after
                            # the reply has completed.
                            active_deadline = deadline_after_reply(args.idle_timeout, play_reply)

                            if agent_ended_conversation:
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
                        # A model/playback failure must not leave recognition
                        # permanently disabled. Start a fresh waiting window
                        # after the visible error feedback.
                        print(f"[voice error] {type(exc).__name__}: {exc}", file=sys.stderr)
                        log_event("voice_error", state=state, error_type=type(exc).__name__, message=str(exc))
                        if state == "active" and transcript:
                            active_deadline = deadline_after_reply(args.idle_timeout)
                    finally:
                        drain(audio_queue)
                        accept_input = True

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
