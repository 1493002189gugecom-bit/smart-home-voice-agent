"""Cancellation, timeout, DSP frames and shadow evaluation, without devices."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import config
import voice_models
from audio_processing import MicrophoneProcessor, acoustic_summary
from intent_evaluation import IntentEvaluation
from ambient_intent import IntentDecision
from voice_runtime import bounded, run, VoiceCancelled, StopSignal
from voice_stages import TurnStages


def test_bounded_deadline_cancels_and_closes_network_coroutine():
    closed = []
    async def stalled():
        try:
            await asyncio.sleep(10)
        finally:
            closed.append(True)
    with pytest.raises(TimeoutError):
        run(bounded(stalled(), .02))
    assert closed == [True]


def test_stop_rotates_tokens_without_cancelling_the_next_turn():
    stop = StopSignal()
    old = stop.token()
    stop.stop()
    assert old.is_set() and not stop.token().is_set()
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(VoiceCancelled):
        run(bounded(asyncio.sleep(10), 10, cancelled))


def test_sync_speech_runner_can_be_used_inside_an_event_loop():
    async def outer():
        return run(asyncio.sleep(0, result=123))
    assert asyncio.run(outer()) == 123


def test_edge_auth_rejection_is_not_retried_or_exposed(monkeypatch):
    import edge_tts
    attempts = []
    class Rejected(RuntimeError):
        status = 403
    class Communicate:
        def __init__(self, *a, **k): attempts.append(1)
        async def stream(self):
            raise Rejected("private-url-and-key")
            yield
    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    with pytest.raises(RuntimeError) as error:
        voice_models._edge_synthesize("测试", "voice")
    assert len(attempts) == 1 and "private" not in str(error.value)


def test_slow_but_successful_edge_audio_is_not_discarded(monkeypatch):
    import edge_tts
    attempts = []
    class Communicate:
        def __init__(self, *a, **k): attempts.append(1)
        async def stream(self):
            await asyncio.sleep(.02)
            yield {"type": "audio", "data": b"valid"}
    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    monkeypatch.setattr(config, "EDGE_SLOW_SECONDS", .001)
    monkeypatch.setattr(voice_models, "_decode_edge_audio", lambda data: (data, 24000))
    assert voice_models._edge_synthesize("测试", "voice") == (b"valid", 24000)
    assert len(attempts) == 1


def test_edge_has_a_total_deadline_across_retries(monkeypatch):
    import edge_tts
    class Communicate:
        def __init__(self, *a, **k): pass
        async def stream(self):
            await asyncio.sleep(10)
            yield
    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    monkeypatch.setattr(config, "speech_timeout", lambda: .02)
    with pytest.raises(TimeoutError):
        voice_models._edge_synthesize("测试", "voice")


def test_edge_segment_streaming_starts_before_synthesizing_later_sentences(monkeypatch):
    calls = []
    def render(text, voice, **options):
        calls.append(text)
        return np.ones(10), 24000
    monkeypatch.setattr(voice_models, "_edge_synthesize", render)
    stream = voice_models.TtsEngine("edge").stream("你好。卧室空调已确认开启。")
    next(stream)
    assert calls == ["你好。"]
    assert len(list(stream)) == 1 and len(calls) == 2


def test_cloud_voice_startup_reports_missing_key_before_loading_models(monkeypatch):
    import loop
    monkeypatch.setattr(loop.audio_utils, "select_input_device", lambda *a, **k: SimpleNamespace(index=1,name="mic",hostapi="test"))
    monkeypatch.setattr(loop.audio_utils, "select_playback_target", lambda *a, **k: SimpleNamespace(describe=lambda:"speaker"))
    monkeypatch.setattr(config, "agent_enabled", lambda:False)
    monkeypatch.setattr(config, "asr_provider", lambda:"funasr")
    monkeypatch.setattr(config, "dashscope_key", lambda:"")
    monkeypatch.setattr(voice_models, "create_asr", lambda:pytest.fail("missing key must be caught before model loading"))
    args = SimpleNamespace(agent=False, input_contains=None, output_contains=None, no_tts=False, text=False)
    with pytest.raises(loop.VoiceStartupError) as error:
        loop.initialize_voice(args, {"input":None,"output":None})
    assert error.value.code == "speech_api_key_missing"


def test_incremental_playback_uses_one_stream_and_closes_on_cancellation(monkeypatch):
    import playback
    from audio_utils import PlaybackTarget
    calls = []
    class Output:
        latency = .01
        def __init__(self, **kwargs): calls.append("open")
        def start(self): calls.append("start")
        def write(self, frames): calls.append("write")
        def abort(self): calls.append("abort")
        def stop(self): calls.append("stop")
        def close(self): calls.append("close")
    monkeypatch.setattr(playback.sd, "OutputStream", Output)
    class Target:
        index=1;sample_rate=24000;channels=1
    def chunks():
        try:
            yield np.ones(100),24000
            yield np.ones(100),24000
        finally:
            calls.append("producer_closed")
    assert not playback.play_chunks(chunks(), Target(), should_interrupt=lambda:calls.count("write") == 1)
    assert calls.count("open") == 1 and calls.count("write") == 1
    assert "abort" in calls and "producer_closed" in calls and calls[-1] == "close"


def test_native_processing_preserves_size_and_continuity_for_512_sample_blocks():
    processor = MicrophoneProcessor(mode="webrtc")
    rng = np.random.default_rng(42)
    for _ in range(100):
        clean = processor.process((rng.standard_normal(512)*.02).astype(np.float32))
        assert clean.shape == (512,) and np.isfinite(clean).all()
    assert processor.status == "ready" and len(processor.near) < 160
    assert len(processor.output) <= 160


def test_native_aec_reduces_synthetic_echo_after_adapting():
    from pywebrtc_audio import AudioProcessor
    processor = AudioProcessor(sample_rate=16000, echo_cancellation=True, noise_suppression=False)
    rng = np.random.default_rng(9)
    before, after = [], []
    for i in range(400):
        far = (rng.standard_normal(160)*.06).astype(np.float32)
        near = far*.4
        clean = processor.process(near, far)
        if i > 250:
            before.extend(near.tolist()); after.extend(clean.tolist())
    assert np.mean(np.square(after)) < np.mean(np.square(before))*.5


def test_acoustic_events_and_emotion_do_not_claim_reliable_command_intent():
    result = voice_models.Transcript("背景内容", emotion="HAPPY", event="BGM")
    summary = acoustic_summary(result, np.ones(300)*.1)
    assert summary["non_speech"] and summary["emotion"] == "happy"
    assert "text" not in summary and "intent" not in summary
    assert acoustic_summary("普通文字", np.array([np.nan]))["invalid_audio"]


def test_stage_events_have_safe_timings_without_raw_speech():
    events = []
    now = [1.]
    stages = TurnStages(lambda kind, **fields: events.append((kind, fields)), "turn1", "microphone", clock=lambda: now[0])
    stages.start("asr"); now[0] = 1.2; stages.start("intent"); now[0] = 1.5; stages.finish()
    assert [row[1].get("duration_ms") for row in events] == [None, 200, None, 300]
    assert all("text" not in row[1] for row in events)


def wait_evaluation(evaluation):
    until = time.monotonic()+3
    while evaluation.status()["status"] == "running" and time.monotonic() < until:
        time.sleep(.005)
    return evaluation.status()


def test_shadow_evaluation_uses_only_classifier_and_measures_false_triggers():
    class Classifier:
        def classify(self, text, **kwargs):
            return IntentDecision("comfort", "cooler")
    evaluation = IntentEvaluation(Classifier)
    assert evaluation.start({"limit": 6})["ok"]
    report = wait_evaluation(evaluation)
    assert report["status"] == "completed" and report["summary"]["false_triggers"] == 1
    assert report["summary"]["passed"] == 1
    assert "executor" not in vars(evaluation)


def test_evaluation_unavailable_and_cancellation_are_distinct():
    entered = threading.Event()
    class Classifier:
        def classify(self, text, cancel, **kwargs):
            entered.set()
            cancel.wait(1)
            from agent_client import AgentCancelled
            if cancel.is_set(): raise AgentCancelled()
            return IntentDecision("ignore", reason="intent_unavailable")
    evaluation = IntentEvaluation(Classifier)
    evaluation.start({"limit": 2}); assert entered.wait(1)
    assert evaluation.start({})["error_code"] == "evaluation_busy"
    evaluation.stop()
    assert wait_evaluation(evaluation)["status"] == "cancelled"


@pytest.mark.parametrize("body", [{"limit":True}, {"limit":25}, {"text":""}, {"text":"ok", "speaker_id":"dad"}])
def test_evaluation_rejects_unbounded_or_identity_bearing_requests(body):
    with pytest.raises(ValueError):
        IntentEvaluation(lambda: None).start(body)
