"""WebRTC AEC3/NS with a bounded, timestamped speaker reference (no recording)."""
from __future__ import annotations

from collections import deque
import threading
import time

import numpy as np

import config


class MicrophoneProcessor:
    def __init__(self, rate=16000, mode=None):
        self.rate = rate
        self.frame = rate // 100
        self.references = deque()
        self.lock = threading.Lock()
        self.next_reference = 0.0
        self.near = np.empty(0, dtype=np.float32)
        self.output = np.zeros(self.frame, dtype=np.float32)
        self.processor = None
        self.status = "off"
        mode = mode or config.audio_processing()
        if mode != "off":
            try:
                from pywebrtc_audio import AudioProcessor
                self.processor = AudioProcessor(sample_rate=rate, echo_cancellation=True,
                    noise_suppression=True, ns_level=1, auto_gain_control=False)
                self.status = "ready"
            except (ImportError, OSError, RuntimeError):
                self.status = "unavailable"
                if mode == "webrtc":
                    raise RuntimeError("audio_processing_unavailable") from None

    def reference(self, samples, sample_rate, latency=0):
        from playback import resample
        data = resample(samples, sample_rate, self.rate)
        now = time.monotonic()
        with self.lock:
            start = max(now + latency, self.next_reference)
            self.next_reference = start + len(data)/self.rate
            self.references.append((start, data.copy()))
            while self.references and self.references[0][0] < now - 2:
                self.references.popleft()
            # A pathological output driver must not accumulate unbounded PCM.
            while len(self.references) > 160:
                self.references.popleft()

    def end_reference(self):
        with self.lock:
            self.references.clear()
            self.next_reference = 0

    def _far(self, start, size):
        far = np.zeros(size, dtype=np.float32)
        end = start + size/self.rate
        with self.lock:
            while self.references and self.references[0][0] + len(self.references[0][1])/self.rate < start - 0.1:
                self.references.popleft()
            for when, data in self.references:
                lo, hi = max(start, when), min(end, when + len(data)/self.rate)
                if hi > lo:
                    dst, src = int(round((lo-start)*self.rate)), int(round((lo-when)*self.rate))
                    count = min(size-dst, len(data)-src, int(round((hi-lo)*self.rate)))
                    far[dst:dst+count] = data[src:src+count]
        return far

    def process(self, block, captured_at=None):
        data = np.asarray(block, dtype=np.float32).reshape(-1)
        if self.processor is None:
            return data.copy()
        now = time.monotonic() if captured_at is None else captured_at
        start = now - (len(data)+len(self.near))/self.rate
        self.near = np.concatenate((self.near, data))
        cleaned = []
        offset = 0
        while len(self.near) - offset >= self.frame:
            part = np.ascontiguousarray(self.near[offset:offset+self.frame])
            far = self._far(start + offset/self.rate, self.frame)
            cleaned.append(self.processor.process(part, far))
            offset += self.frame
        self.near = self.near[offset:].copy()
        if cleaned:
            self.output = np.concatenate((self.output, *cleaned))
        result = self.output[:len(data)].copy()
        self.output = self.output[len(data):]
        return result


def acoustic_summary(result, samples):
    data = np.asarray(samples).reshape(-1)
    allowed_emotions = {"neutral", "happy", "sad", "angry", "fearful", "disgusted", "surprised"}
    emotion = str(getattr(result, "emotion", "")).lower()
    event = str(getattr(result, "event", "")).lower()
    non_speech = event in {"bgm", "music", "applause", "laughter", "cry", "cough", "sneeze", "breath"}
    return {"emotion": emotion if emotion in allowed_emotions else None,
            "audio_event": event if event in {"speech", "bgm", "music", "applause", "laughter", "cry", "cough", "sneeze", "breath"} else None,
            "non_speech": non_speech,
            "clipped_ratio": round(float(np.mean(np.abs(data) >= 0.999)), 4) if len(data) else 0,
            "invalid_audio": not bool(len(data) and np.all(np.isfinite(data)))}
