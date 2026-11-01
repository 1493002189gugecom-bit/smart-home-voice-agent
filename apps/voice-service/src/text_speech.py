"""Schedule typed replies on the microphone loop's sole audio output thread."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from voice_errors import failure_details


class SpeechFailure(RuntimeError):
    def __init__(self, details):
        super().__init__(details["error_code"])
        self.details = details


@dataclass
class SpeechRequest:
    text: str
    publish: object
    done: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    failure: dict | None = None


class TextSpeechMailbox:
    def __init__(self, timeout=120):
        self.pending = queue.Queue(maxsize=1)
        self.timeout = timeout
        self.lock = threading.Lock()
        self.active = None

    def stop(self):
        with self.lock:
            if self.active:
                self.active.cancel.set()
            for request in list(self.pending.queue):
                request.cancel.set()

    def cancelled(self):
        with self.lock:
            return self.active is not None and self.active.cancel.is_set()

    def is_set(self):
        return self.cancelled()

    def submit(self, text, publish, *, cancel=None):
        request = SpeechRequest(text, publish, cancel=cancel if cancel is not None else threading.Event())
        self.pending.put_nowait(request)
        if not request.done.wait(self.timeout):
            request.cancel.set()
            raise SpeechFailure({"stage": "tts_synthesis", "error_code": "tts_timeout", "error_type": "TimeoutError",
                                 "message": "等待语音播报超时，回复文字仍可查看。"})
        if request.failure:
            raise SpeechFailure(request.failure)

    def take(self):
        try:
            return self.pending.get_nowait()
        except queue.Empty:
            return None

    def process(self, request, render, player, *, stream_render=None, stream_player=None):
        stage = "tts_synthesis"
        from voice_runtime import VoiceCancelled
        with self.lock:
            self.active = request
        try:
            if request.cancel.is_set():
                raise VoiceCancelled("speech_cancelled")
            request.publish("playback_state", source_event="tts_synthesizing", text=request.text)
            if stream_render is not None:
                def first_output(latency):
                    nonlocal stage
                    stage = "playback"
                    request.publish("playback_state", source_event="tts_start", text=request.text)
                completed = stream_player(stream_render(request.text, request.cancel),
                                          should_interrupt=request.cancel.is_set, on_stream_started=first_output)
                if request.cancel.is_set():
                    raise VoiceCancelled("speech_cancelled")
                request.publish("playback_state", source_event="tts_end", text=request.text, interrupted=completed is False)
                return
            samples, rate = render(request.text)
            if request.cancel.is_set():
                raise VoiceCancelled("speech_cancelled")
            stage = "playback"
            request.publish("playback_state", source_event="tts_start", text=request.text)
            completed = player(samples, rate, should_interrupt=request.cancel.is_set)
            if request.cancel.is_set():
                raise VoiceCancelled("speech_cancelled")
            request.publish("playback_state", source_event="tts_end", text=request.text, interrupted=completed is False)
        except Exception as error:
            request.failure = failure_details(stage, error)
        finally:
            with self.lock:
                self.active = None
            request.done.set()
