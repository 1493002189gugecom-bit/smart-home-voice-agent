from __future__ import annotations

import queue
import json
import sys
from types import SimpleNamespace

import numpy as np

import loop


def test_microphone_startup_reports_enabled_barge_in(tmp_path, monkeypatch):
    """A voice launch must reach the microphone and expose its actual barge state."""

    class InputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            pass

        def close(self):
            pass

    class Kws:
        def create_stream(self):
            return object()

    class Vad:
        def reset(self):
            pass

    output = SimpleNamespace(index=2, name="speaker", hostapi="test", describe=lambda: "speaker")
    selected = (
        SimpleNamespace(index=1, name="microphone", hostapi="test"),
        output, Kws(), Vad(), object(), object(), None,
    )
    log_file = tmp_path / "voice.jsonl"
    monkeypatch.setattr(sys, "argv", ["loop.py", "--no-control-api", "--run-seconds", "0.001", "--log-file", str(log_file)])
    monkeypatch.setattr(loop.device_preferences, "load", lambda: {"input": None, "output": None})
    monkeypatch.setattr(loop.config, "speaker_model_path", lambda: None)
    monkeypatch.setattr(loop.config, "barge_in_enabled", lambda: True)
    monkeypatch.setattr(loop.config, "wake_words", lambda _: ["小屋小屋"])
    monkeypatch.setattr(loop, "initialize_voice", lambda args, preferences: selected)
    monkeypatch.setattr(loop.voice_models, "create_vad", Vad)
    monkeypatch.setattr(loop.sd, "InputStream", InputStream)

    assert loop.main() == 0
    ready = next(json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()
                 if '"event": "ready"' in line)
    assert ready["barge_in"] is True


def test_active_silent_session_times_out_at_deadline():
    assert loop.should_idle_timeout("active", False, now=20.0, deadline=20.0)


def test_continuous_silence_blocks_do_not_disable_timeout():
    # Regression: InputStream queues blocks continuously, so timeout cannot be
    # tied to queue.Empty.
    assert loop.should_idle_timeout("active", False, now=25.0, deadline=20.0)


def test_speech_in_progress_is_not_cut_off_by_idle_deadline():
    assert not loop.should_idle_timeout("active", True, now=25.0, deadline=20.0)


def test_standby_never_uses_active_deadline():
    assert not loop.should_idle_timeout("standby", False, now=25.0, deadline=20.0)


def test_waiting_window_starts_after_reply_playback():
    now = [100.0]

    def fake_play():
        now[0] += 6.25  # synthesis + playback time

    deadline = loop.deadline_after_reply(20.0, fake_play, lambda: now[0])
    assert deadline == 126.25
    assert deadline - now[0] == 20.0


def test_playback_suppression_drops_microphone_block():
    q = queue.Queue()
    accepted = loop.enqueue_if_enabled(q, np.ones(512, dtype=np.float32), enabled=False)
    assert not accepted
    assert q.empty()


def test_enabled_microphone_block_is_queued():
    q = queue.Queue()
    accepted = loop.enqueue_if_enabled(q, np.ones(512, dtype=np.float32), enabled=True)
    assert accepted
    assert q.qsize() == 1


def test_barge_in_requires_a_wake_word_and_does_not_publish_mic_to_command_queue():
    class Stream:
        def accept_waveform(self, sample_rate, block):
            self.last = float(block[0])
            self.ready = True

    class FakeKws:
        def create_stream(self):
            return Stream()

        def is_ready(self, stream):
            return stream.ready

        def decode_stream(self, stream):
            stream.ready = False

        def get_result(self, stream):
            return "小屋" if stream.last == 2.0 else ""

    detector = loop.WakeBargeIn(FakeKws())
    mic = queue.Queue()
    commands = queue.Queue()
    mic.put(np.ones(512, dtype=np.float32))  # speech without wake word
    assert not detector.poll(mic, 16000)
    assert commands.empty()
    mic.put(np.full(512, 2.0, dtype=np.float32))
    assert detector.poll(mic, 16000)
    assert detector.keyword == "小屋"
    assert commands.empty()


def test_microphone_routes_to_barge_queue_only_during_playback():
    commands = queue.Queue()
    barge = queue.Queue()
    block = np.ones(512, dtype=np.float32)
    assert loop.route_microphone_block(commands, barge, block, accepting_commands=False, barge_listening=True)
    assert commands.empty()
    assert barge.qsize() == 1
    assert not loop.route_microphone_block(commands, barge, block, accepting_commands=False, barge_listening=False)
    assert loop.route_microphone_block(commands, barge, block, accepting_commands=True, barge_listening=False)
    assert commands.qsize() == 1


def test_barge_playback_uses_wake_detector_and_returns_keyword(monkeypatch):
    class Stream:
        def accept_waveform(self, sample_rate, block):
            self.last = float(block[0])
            self.ready = True

    class FakeKws:
        def create_stream(self):
            return Stream()

        def is_ready(self, stream):
            return stream.ready

        def decode_stream(self, stream):
            stream.ready = False

        def get_result(self, stream):
            return "小屋" if stream.last == 2.0 else ""

    blocks = queue.Queue()
    blocks.put(np.ones(512, dtype=np.float32))
    blocks.put(np.full(512, 2.0, dtype=np.float32))

    def fake_play(samples, rate, target, on_started, should_interrupt):
        assert should_interrupt()
        return False

    monkeypatch.setattr(loop, "play", fake_play)
    assert loop.play_with_wake_interrupt(
        np.ones(100, dtype=np.float32), 16000, object(), FakeKws(), blocks,
    ) == "小屋"


def test_playback_stops_for_speech_and_says_so(monkeypatch):
    """Full barge-in: the user's own words stop the reply, no wake word needed."""

    class Speech:
        def __init__(self):
            self.fed = 0

        def start_playback(self, **kwargs):
            pass

        def feed(self, block):
            self.fed += 1
            return self.fed >= 2

    class Wake:
        keyword = None

        def detect(self, block, sample_rate):
            return None

    detector = loop.PlaybackBargeIn(speech=Speech(), wake=Wake())
    blocks = queue.Queue()
    blocks.put(np.ones(512, dtype=np.float32))
    blocks.put(np.ones(512, dtype=np.float32))

    def fake_play(samples, rate, target, on_started, should_interrupt):
        assert should_interrupt()
        return False

    monkeypatch.setattr(loop, "play", fake_play)
    assert loop.play_with_barge(
        np.ones(100, dtype=np.float32), 16000, object(), detector, blocks,
    ) == "speech"


def test_playback_passes_reply_waveform_to_barge_detector(monkeypatch):
    """The known reply must reach echo rejection before microphone polling."""

    class Vad:
        def is_speech(self, block):
            return True

        def reset(self):
            pass

    reference = np.random.default_rng(7).normal(0, 0.15, 16000).astype(np.float32)
    speech = loop.SpeechBargeDetector(Vad(), 16000)
    blocks = queue.Queue()
    interrupted_at = []

    def fake_play(samples, rate, target, on_started, should_interrupt):
        for index in range(30):
            echo = 0.5 * reference[index * 512:(index + 1) * 512]
            if index >= 20:
                start = index * 512
                voice = 0.04 * np.sin(2 * np.pi * 180 * np.arange(start, start + 512) / 16000)
                echo = (echo + voice).astype(np.float32)
            blocks.put(echo)
            if should_interrupt():
                interrupted_at.append(index)
                return False
        return True

    monkeypatch.setattr(loop, "play", fake_play)
    assert loop.play_with_barge(
        reference, 16000, object(), loop.PlaybackBargeIn(speech=speech), blocks,
    ) == "speech"
    assert interrupted_at == [29]


def test_interruption_keeps_microphone_tail_for_the_next_command():
    """Speech still queued at cutoff must follow the confirmed first syllables."""

    class Detector:
        def take_captured(self, *, keep_pre_roll):
            assert keep_pre_roll is False
            return [np.full(512, 0.1, dtype=np.float32)]

    blocks = queue.Queue()
    blocks.put(np.full(512, 0.2, dtype=np.float32))
    blocks.put(np.full(512, 0.3, dtype=np.float32))

    captured = loop.collect_barge_head(Detector(), blocks, keep_pre_roll=False)

    assert [round(float(block[0]), 1) for block in captured] == [0.1, 0.2, 0.3]
    assert blocks.empty()


def test_a_completed_reply_reports_no_interruption(monkeypatch):
    class Speech:
        def start_playback(self, **kwargs):
            pass

        def feed(self, block):
            return False

    detector = loop.PlaybackBargeIn(speech=Speech())
    monkeypatch.setattr(loop, "play", lambda *args, **kwargs: True)

    assert loop.play_with_barge(
        np.ones(100, dtype=np.float32), 16000, object(), detector, queue.Queue(),
    ) is None


def test_exit_phrases_reset_session():
    assert loop.should_exit("退出连续对话")
    assert loop.should_exit("请结束对话")
    assert not loop.should_exit("继续聊天")


def test_short_filler_is_not_sent_to_the_agent():
    assert loop.is_non_command_filler("嗯。")
    assert loop.is_non_command_filler("啊，嗯。")
    assert not loop.is_non_command_filler("开灯")
    assert not loop.is_non_command_filler("再调低两度")


def test_vad_gate_counts_actual_speech_not_preroll_silence():
    assert not loop.has_enough_voiced_audio(1, sample_rate=16000)
    assert not loop.has_enough_voiced_audio(4, sample_rate=16000)
    assert loop.has_enough_voiced_audio(6, sample_rate=16000)


def test_text_session_handles_a_sentence_before_exit(monkeypatch):
    lines = iter(["打开客厅灯", "退出"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    handled = []
    events = []

    class Agent:
        def handle(self, text):
            handled.append(text)
            return SimpleNamespace(
                text="已处理", ok=True, error_code=None,
                end_conversation=False, tool_results=[],
            )

    result = loop.run_text_session(
        Agent(), None, None, SimpleNamespace(speaker_id=None),
        lambda event, **fields: events.append(event), None,
    )
    assert result == 0
    assert handled == ["打开客厅灯"]
    assert "agent_reply" in events
