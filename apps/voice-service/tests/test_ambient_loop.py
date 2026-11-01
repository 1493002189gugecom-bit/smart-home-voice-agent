"""Exercise the real microphone loop with deterministic audio and model boundaries."""
import json
import queue
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import loop
from agent_client import AgentError, ChatResponse
from agent_session import AgentSession
from ambient_intent import IntentClassifier
from test_comfort_actions import Executor


def run_loop(monkeypatch, tmp_path, scripts, decisions, *, mode="hybrid", idle=20, speakers=None,
             classifier_error=False, expire_after_reply=False, interrupt=None, tts_error=False, typed_audio=False, realtime=False, streaming=False, stop_capture=False):
    callbacks, captured, model_calls = [], [], []
    bursts = iter(scripts)
    responses = iter(decisions)
    transcripts = []
    created = []
    speaker_results = iter(speakers) if speakers else None
    agent_calls = []
    partial_seen = threading.Event()
    partial_count = [0]
    from voice_runtime import StopSignal
    stop_signal = StopSignal()
    get_count = [0]
    monkeypatch.setattr(loop, "StopSignal", lambda: stop_signal)

    class AudioQueue(queue.Queue):
        def __init__(self, *args, **kwargs):
            # Audio is delivered synchronously, faster than a real microphone.
            if kwargs.get("maxsize") == 128:
                kwargs["maxsize"] = 2048
            super().__init__(*args, **kwargs)
            created.append(self)

        def get(self, *args, **kwargs):
            if self is created[1] and kwargs.get("timeout") == 0.1:
                get_count[0] += 1
                if stop_capture and get_count[0] == 10:
                    stop_signal.stop()
            if self is created[1] and self.empty() and kwargs.get("timeout") == 0.1:
                script = next(bursts, None)
                if script is not None:
                    text, wake = script[:2]
                    transcripts.append(text)
                    # 20 voiced blocks plus endpoint silence, no real microphone.
                    values = script[2] if len(script) > 2 else [2.0 if wake else 1.0] * 20 + [0.0] * 24
                    for value in values:
                        callbacks[0](np.full((512, 1), value, dtype=np.float32), 512, None, None)
            return super().get(*args, **kwargs)

    class Input:
        def __init__(self, **kwargs):
            callbacks.append(kwargs["callback"])
        def start(self):
            pass
        def close(self):
            pass

    class Kws:
        def create_stream(self):
            return SimpleNamespace(pending=False, value=0, count=0)
        def is_ready(self, stream):
            return stream.pending
        def decode_stream(self, stream):
            stream.pending = False
        def get_result(self, stream):
            return "你好小屋" if stream.value == 2 and stream.count == 8 else ""
        def reset_stream(self, stream):
            stream.count = 0

    class Stream:
        def __init__(self):
            self.pending = False
            self.value = 0
            self.count = 0
        def accept_waveform(self, rate, block):
            if block[0] != self.value:
                self.count = 0
            self.value, self.pending = block[0], True
            self.count += 1
    kws = Kws()
    kws.create_stream = Stream

    class Vad:
        def reset(self):
            pass
        def is_speech(self, block):
            if np.isclose(block[0], 0.4):
                partial_count[0] += 1
                if partial_count[0] >= 5:
                    partial_seen.set()
            return block[0] > 0

    class Model:
        def chat_stream(self, messages, tools=None, cancel=None):
            data = json.loads(messages[1]["content"])
            model_calls.append(data)
            if interrupt is not None and len(model_calls) == 1:
                values = [0.005] * 3 + [0.4] * (10 if interrupt == "confirmed" else 5)
                for value in values:
                    callbacks[0](np.full((512, 1), value, dtype=np.float32), 512, None, None)
                if interrupt == "confirmed":
                    assert cancel.wait(1.0)
                else:
                    assert partial_seen.wait(1.0)
            decision = next(responses)
            if isinstance(decision, BaseException):
                raise decision
            kind, need, room = decision
            return ChatResponse(content=json.dumps({"kind": kind, "need": need, "room": room}), tool_calls=[])

    executor = Executor()
    agent = AgentSession(SimpleNamespace(), executor)
    original_handle = agent.handle
    def handle(text, cancel=None):
        agent_calls.append(text)
        return original_handle(text, cancel)
    agent.handle = handle
    executor.agent_calls = agent_calls
    classifier = IntentClassifier(Model())
    log = tmp_path / "ambient.jsonl"
    monkeypatch.setattr(loop, "queue", SimpleNamespace(Queue=AudioQueue, Empty=queue.Empty, Full=queue.Full))
    argv = ["loop.py", "--no-control-api", "--listening-mode", mode,
        "--idle-timeout", str(idle), "--run-seconds", "0.3" if interrupt else "0.18", "--log-file", str(log)]
    with_audio = interrupt is not None or tts_error or typed_audio or streaming
    if not with_audio:
        argv.append("--no-tts")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(loop.device_preferences, "load", lambda: {"input": None, "output": None})
    monkeypatch.setattr(loop.config, "speaker_model_path", lambda: None)
    monkeypatch.setattr(loop.config, "wake_command_merge_enabled", lambda: True)
    monkeypatch.setattr(loop.config, "wake_words", lambda _: ["你好小屋"])
    monkeypatch.setattr(loop.config, "command_end_silence_seconds", lambda: 0.7)
    output = SimpleNamespace(index=2, describe=lambda: "fake speaker") if with_audio else None
    monkeypatch.setattr(loop, "initialize_voice", lambda *a: (
        SimpleNamespace(index=1, name="test microphone"), output, kws, Vad(), object(), object() if with_audio else None, agent))
    monkeypatch.setattr(loop.sd, "InputStream", Input)
    monkeypatch.setattr(loop.voice_models, "create_vad", Vad)
    monkeypatch.setattr(loop.config, "barge_in_enabled", lambda: True)
    monkeypatch.setattr(loop, "synthesize_reply", lambda *a: (np.ones(100), 16000))
    if realtime:
        import cloud_speech
        monkeypatch.setattr(loop.config, "asr_provider", lambda: "funasr")
        class Realtime:
            def __init__(self, on_partial):
                on_partial("打开空调")
                self.blocks = []
            def feed(self, block): self.blocks.append(block)
            def result(self, cancel=None):
                assert self.blocks
                return transcripts[-1]
            def close(self): pass
        monkeypatch.setattr(cloud_speech, "RealtimeAsr", Realtime)
    if streaming:
        class StreamingTts:
            supports_streaming = True
            def stream(self, text, cancel=None):
                yield np.ones(100), 24000
        monkeypatch.setattr(loop, "initialize_voice", lambda *a: (
            SimpleNamespace(index=1, name="test microphone"), output, kws, Vad(), object(), StreamingTts(), agent))
        def play_chunks(chunks, target, **options):
            assert target is output
            list(chunks)
            options["on_stream_started"](0)
            return True
        monkeypatch.setattr(loop.playback, "play_chunks", play_chunks)
    if tts_error:
        def synthesize(*args):
            cause = RuntimeError("private provider URL")
            cause.status = 403
            raise RuntimeError("TTS unavailable") from cause
        monkeypatch.setattr(loop, "synthesize_reply", synthesize)
    if typed_audio:
        from text_speech import TextSpeechMailbox, SpeechRequest
        mailbox = TextSpeechMailbox()
        request = SpeechRequest("文字播报", lambda kind, **fields: agent_calls.append((kind, fields)))
        mailbox.pending.put_nowait(request)
        monkeypatch.setattr(loop, "TextSpeechMailbox", lambda: mailbox)
        def play(samples, rate, target, **options):
            assert target is output and not options["should_interrupt"]()
            # A loud reply containing a wake tone must never be admitted as input.
            for value in [2.0] * 20 + [0.0] * 24:
                callbacks[0](np.full((512, 1), value, dtype=np.float32), 512, None, None)
            return True
        monkeypatch.setattr(loop, "play", play)
    monkeypatch.setattr(loop, "play_with_barge", lambda *a: None)
    def create(_):
        if classifier_error:
            raise ValueError("intent_configuration_missing")
        return classifier
    monkeypatch.setattr(loop, "create_intent_classifier", create)
    monkeypatch.setattr(loop, "resolve_speaker", lambda *a: next(speaker_results) if speaker_results else (None, None, "not_evaluated"))
    if expire_after_reply:
        original_deadline = loop.deadline_after_reply
        monkeypatch.setattr(loop, "deadline_after_reply", lambda idle, play_reply=None:
            original_deadline(0, play_reply))
    def transcribe(*args):
        captured.append(args[1].copy())
        return transcripts[-1]
    monkeypatch.setattr(loop.voice_models, "transcribe", transcribe)
    assert loop.main() == 0
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return executor, model_calls, events, captured


def test_natural_complaint_then_room_answer_then_end(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("今天天气真热啊", False), ("卧室", False), ("这样就行", False)],
        [("comfort", "cooler", None), ("follow_up", None, "卧室"), ("end", None, None)])
    assert [c[0] for c in executor.calls] == ["adjust_ac"]
    assert [c["active"] for c in calls] == [False, True, True]
    assert any(e["event"] == "natural_trigger" for e in events)
    assert any(e["event"] == "agent_end_conversation" and e["state"] == "listening" for e in events)


def test_ignored_negation_not_published_or_executed_then_keyword_still_works(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("今天热，不过不要开空调", False), ("你好小屋，这样就行", True)],
        [("ignore", None, None)])
    assert len(calls) == 1  # The keyword path bypasses semantic admission.
    assert all("今天热" not in e.get("transcript", "") for e in events)
    assert any(e["event"] == "wake" for e in events)
    assert executor.agent_calls == ["这样就行"]


def test_wake_only_does_not_transcribe_background_speech(monkeypatch, tmp_path):
    executor, calls, events, captured = run_loop(monkeypatch, tmp_path,
        [("卧室好热", False)], [], mode="wake")
    assert not executor.calls and not calls and not captured


def test_repeated_complaint_does_not_repeat_write(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("卧室好热", False), ("卧室还是好热", False)],
        [("comfort", "cooler", "卧室"), ("comfort", "cooler", "卧室")])
    assert len(executor.calls) == 1
    assert len([e for e in events if e["event"] == "agent_reply"]) == 1


def test_leading_punctuation_cannot_bypass_semantic_admission(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("，今天热，不过不要开空调", False)], [("ignore", None, None)])
    assert len(calls) == 1
    assert not executor.agent_calls and not executor.calls


def test_idle_timeout_returns_to_natural_listening(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("卧室好热", False), ("卧室好暗", False)],
        [("comfort", "cooler", "卧室"), ("comfort", "brighter", "卧室")], expire_after_reply=True)
    assert [c["active"] for c in calls] == [False, False]
    assert any(e["event"] == "idle_timeout" and e["state"] == "listening" for e in events)
    assert [c[0] for c in executor.calls] == ["adjust_ac", "query_device_status", "set_light"]


def test_missing_intent_configuration_preserves_keyword_mode(monkeypatch, tmp_path):
    executor, calls, events, captured = run_loop(monkeypatch, tmp_path,
        [("卧室好热", False), ("你好小屋，关灯", True)], [], classifier_error=True)
    assert not calls
    assert len(captured) == 1 and executor.agent_calls == ["关灯"]
    assert any(e["event"] == "intent_error" for e in events)


def test_short_unknown_followup_keeps_context_but_does_not_reuse_old_identity(monkeypatch, tmp_path):
    from speaker_identity import SpeakerVerdict
    dad = SpeakerVerdict("confirmed", person_id="dad", confidence=0.9, margin=0.4)
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("天气太热了", False), ("再低一点", False)],
        [("comfort", "cooler", None), ("follow_up", None, None)],
        speakers=[(dad, "爸爸", None), (None, None, "too_short")])
    assert calls[1]["active"] is True and calls[1]["context"]
    # Relative followup goes through the existing agent; the only actual device
    # operation in this fake transport is the first, freshly located complaint.
    assert executor.agent_calls == ["再低一点"]


def test_known_speaker_change_drops_previous_dialogue(monkeypatch, tmp_path):
    from speaker_identity import SpeakerVerdict
    dad = SpeakerVerdict("confirmed", person_id="dad", confidence=0.9, margin=0.4)
    mom = SpeakerVerdict("confirmed", person_id="mom", confidence=0.9, margin=0.4)
    _, calls, _, _ = run_loop(monkeypatch, tmp_path,
        [("天气太热了", False), ("再低一点", False)],
        [("comfort", "cooler", None), ("ignore", None, None)],
        speakers=[(dad, "爸爸", None), (mom, "妈妈", None)])
    assert calls[1]["active"] is False and calls[1]["context"] == []


def test_first_confirmed_speaker_keeps_pending_room_question_context(monkeypatch, tmp_path):
    from speaker_identity import SpeakerVerdict
    dad = SpeakerVerdict("confirmed", person_id="dad", confidence=0.9, margin=0.4)
    executor, calls, _, _ = run_loop(monkeypatch, tmp_path,
        [("好热啊", False), ("卧室", False)],
        [("comfort", "cooler", None), ("follow_up", None, "卧室")],
        speakers=[(None, None, "too_short"), (dad, "爸爸", None)])
    assert calls[1]["active"] is True
    assert any("房间" in item["content"] for item in calls[1]["context"])
    assert [c[0] for c in executor.calls] == ["adjust_ac"]


@pytest.mark.parametrize("phase", ["confirmed", "partial"])
def test_intent_wait_preserves_new_sentence_onset_and_drops_stale_action(monkeypatch, tmp_path, phase):
    first = ("comfort", "cooler", "卧室") if phase == "confirmed" else ("ignore", None, None)
    executor, calls, events, captured = run_loop(monkeypatch, tmp_path,
        [("卧室太热了", False), ("卧室太暗了", False, [0.5] * 12 + [0.0] * 24)],
        [first, ("comfort", "brighter", "卧室")], interrupt=phase)
    assert [c[0] for c in executor.calls] == ["query_device_status", "set_light"]
    assert len(captured) == 2
    second = captured[1].reshape(-1, 512)[:, 0]
    assert np.allclose(second[:3], 0.005)
    assert np.allclose(second[3:8], 0.4)


def test_long_unfinished_sentence_is_not_admitted_as_a_partial_command(monkeypatch, tmp_path):
    executor, calls, events, captured = run_loop(monkeypatch, tmp_path,
        [("卧室好热……不过不要开空调", False, [1.0] * 490 + [0.0] * 24), ("卧室好暗", False)],
        [("comfort", "brighter", "卧室")])
    assert len(captured) == 1
    assert [c["utterance"] for c in calls] == ["卧室好暗"]
    assert [c[0] for c in executor.calls] == ["query_device_status", "set_light"]


def test_intent_api_failure_never_executes_or_publishes_ambient_text(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("卧室好热", False), ("你好小屋，关灯", True)], [AgentError("agent_unavailable")])
    assert not executor.calls
    assert executor.agent_calls == ["关灯"]
    assert all("卧室好热" not in e.get("transcript", "") for e in events)
    assert any(e["event"] == "intent_error" and e["error_type"] == "intent_unavailable" for e in events)


def test_microphone_tts_rejection_keeps_reply_and_identifies_failed_stage(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("好热啊", False)], [("comfort", "cooler", None)], tts_error=True)
    assert any(event["event"] == "agent_reply" for event in events)
    failure = next(event for event in events if event["event"] == "voice_error")
    assert failure["stage"] == "tts_synthesis" and failure["error_code"] == "tts_service_rejected"
    assert "403" in failure["message"] and "private" not in str(failure)
    assert not executor.calls


def test_typed_audio_uses_selected_output_and_cannot_wake_the_microphone(monkeypatch, tmp_path):
    executor, calls, events, captured = run_loop(monkeypatch, tmp_path, [], [], typed_audio=True)
    assert not captured and not calls and not executor.calls
    assert not any(event["event"] == "wake" for event in events)
    assert [fields["source_event"] for kind, fields in executor.agent_calls] == ["tts_synthesizing", "tts_start", "tts_end"]


def test_streaming_asr_partial_cannot_trigger_a_retracted_command(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("不要开空调", False)], [("ignore", None, None)], realtime=True)
    assert [call["utterance"] for call in calls] == ["不要开空调"]
    assert not executor.calls and not any(event["event"] == "agent_reply" for event in events)


def test_real_microphone_loop_can_play_streaming_tts_without_losing_its_reply(monkeypatch, tmp_path):
    executor, calls, events, _ = run_loop(monkeypatch, tmp_path,
        [("好热啊", False)], [("comfort", "cooler", None)], streaming=True)
    assert any(event["event"] == "agent_reply" for event in events)
    assert any(event["event"] == "tts_start" for event in events)
    assert any(event["event"] == "tts_end" and not event["interrupted"] for event in events)
    assert not any(event["event"] == "voice_error" for event in events)


def test_stopping_during_capture_cannot_be_lost_before_intent_dispatch(monkeypatch, tmp_path):
    executor, calls, events, captured = run_loop(monkeypatch, tmp_path,
        [("卧室好热", False)], [], stop_capture=True)
    assert not calls and not captured and not executor.calls
    assert not any(event["event"] == "agent_reply" for event in events)
