"""Exercise actual loop routing across interruption handoff, without a microphone."""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

import loop


@pytest.mark.parametrize("phase", ["playback", "thinking"])
def test_interruption_keeps_onset_pause_and_live_continuation(tmp_path, monkeypatch, phase):
    callbacks = []
    captured = []
    calls = []
    plays = []

    def send(values):
        for value in values:
            callbacks[0](np.full((512, 1), value, dtype=np.float32), 512, None, None)

    class InputStream:
        def __init__(self, **kwargs):
            callbacks.append(kwargs["callback"])
        def start(self):
            send([1.0] * 40 + [2.0] * 20 + [0.0] * 30)
        def close(self):
            pass

    class Kws:
        count = 0
        pending = False
        def create_stream(self):
            return self
        def accept_waveform(self, rate, block):
            self.count += 1
            self.pending = True
        def is_ready(self, stream):
            return self.pending
        def decode_stream(self, stream):
            self.pending = False
        def get_result(self, stream):
            return "你好小屋" if self.count == 40 else ""
        def reset_stream(self, stream):
            pass

    class Vad:
        def reset(self):
            pass
        def is_speech(self, block):
            return bool(block[0] > 0.01)

    def interrupt():
        # Weak first syllable, confirmed speech, a 480 ms hesitation,
        # and a second phrase, all routed through the real callback.
        send([0.005] * 3 + [0.4] * 10 + [0.0] * 15 + [0.5] * 10)

    class Agent:
        def handle(self, text, cancel=None):
            calls.append(text)
            if phase == "thinking" and len(calls) == 1:
                interrupt()
                assert cancel.wait(2.0)
            return SimpleNamespace(text="答复", ok=True, cancelled=bool(cancel and cancel.is_set()),
                                   error_code=None, end_conversation=False, tool_results=[])
        def reset(self):
            pass

    def playback(reply, rate, target, detector, blocks, on_output, *, cancel=None):
        plays.append(True)
        if phase == "playback" and len(plays) == 1:
            detector.speech.start_playback()
            # Simulate a reliable echo residual, leaving user's audio unchanged.
            detector.speech._without_playback_echo = lambda block: block
            interrupt()
            return detector.poll(blocks, 16000)
        return None

    original_collect = loop.collect_barge_head
    def collect(detector, blocks, *, keep_pre_roll):
        head = original_collect(detector, blocks, keep_pre_roll=keep_pre_roll)
        # These arrive exactly during queue handoff and must enter audio_queue.
        send([0.6] + [0.0] * 30)
        return head

    log = tmp_path / "continuity.jsonl"
    monkeypatch.setattr(sys, "argv", ["loop.py", "--no-control-api", "--run-seconds", "0.5", "--log-file", str(log)])
    monkeypatch.setattr(loop.device_preferences, "load", lambda: {"input": None, "output": None})
    monkeypatch.setattr(loop.config, "speaker_model_path", lambda: None)
    monkeypatch.setattr(loop.config, "wake_command_merge_enabled", lambda: True)
    monkeypatch.setattr(loop.config, "wake_words", lambda _: ["你好小屋"])
    monkeypatch.setattr(loop.config, "command_end_silence_seconds", lambda: 0.7)
    monkeypatch.setattr(loop.config, "barge_in_enabled", lambda: True)
    monkeypatch.setattr(loop.config, "barge_min_rms", lambda: 0.02)
    monkeypatch.setattr(loop.config, "barge_min_speech_seconds", lambda: 0.30)
    output = SimpleNamespace(index=2, describe=lambda: "speaker")
    monkeypatch.setattr(loop, "initialize_voice", lambda args, prefs: (
        SimpleNamespace(index=1, name="mic"), output, Kws(), Vad(), object(), object(), Agent()))
    monkeypatch.setattr(loop.voice_models, "create_vad", Vad)
    monkeypatch.setattr(loop.sd, "InputStream", InputStream)
    monkeypatch.setattr(loop, "synthesize_reply", lambda *a: (np.ones(16000), 16000))
    monkeypatch.setattr(loop, "play_with_barge", playback)
    monkeypatch.setattr(loop, "collect_barge_head", collect)
    monkeypatch.setattr(loop, "resolve_speaker", lambda *a: (None, None, "not_evaluated"))
    def transcribe(asr, audio, rate):
        captured.append(audio.copy())
        return "你好小屋，开灯" if len(captured) == 1 else "不要开灯，改成开空调"
    monkeypatch.setattr(loop.voice_models, "transcribe", transcribe)

    assert loop.main() == 0
    assert calls == ["开灯", "不要开灯，改成开空调"]
    assert len(captured) == 2
    audio = captured[1].reshape(-1, 512)[:, 0]
    assert np.allclose(audio[:3], 0.005)
    assert np.allclose(audio[3:13], 0.4)
    assert np.allclose(audio[13:28], 0.0)
    assert np.allclose(audio[28:38], 0.5)
    assert np.isclose(audio[38], 0.6)
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(e["event"] in ("tts_interrupted", "agent_interrupted") for e in events)
