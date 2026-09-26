"""Voice enrolment: collecting enough good speech to recognise one person.

One constraint shapes this whole module. The microphone belongs to the voice loop,
so a sample is *an utterance the person speaks while an enrolment is active*, not
audio a web page uploads. This mirrors face enrolment, where the camera loop owns
the device and a registration session consumes its frames. The console can therefore
only start, watch and cancel.

Nothing is written until the full set of good samples exists, so cancelling halfway
leaves the gallery exactly as it was — which is the only acceptable behaviour for
biometric data.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from speaker import SpeakerModelError

DEFAULT_REQUIRED_SAMPLES = 5
# Longer than an ordinary question needs: enrolment quality decides every later
# recognition, and it is the one moment where asking the user to repeat is cheap.
DEFAULT_MIN_SAMPLE_SECONDS = 2.0
# Stable codes the console turns into guidance; an unknown reason becomes the last.
REJECTION_REASONS = ("too_short", "silent", "clipping", "low_snr", "not_recognisable")
STATES = ("idle", "collecting", "saved", "failed")


@dataclass
class EnrollmentStatus:
    state: str
    person_id: str | None = None
    accepted: int = 0
    required: int = DEFAULT_REQUIRED_SAMPLES
    min_seconds: float = DEFAULT_MIN_SAMPLE_SECONDS
    last_reason: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError("unknown enrolment state")

    @property
    def active(self) -> bool:
        return self.state == "collecting"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "active": self.active,
            "person_id": self.person_id,
            "accepted": self.accepted,
            "required": self.required,
            "min_seconds": self.min_seconds,
            "last_reason": self.last_reason,
            "message": self.message,
        }


def idle_status(required: int = DEFAULT_REQUIRED_SAMPLES, min_seconds: float = DEFAULT_MIN_SAMPLE_SECONDS) -> EnrollmentStatus:
    """A fresh idle status; never share one instance, it is mutated in place."""

    return EnrollmentStatus("idle", required=required, min_seconds=min_seconds)


class VoiceEnrollmentSession:
    """One person's enrolment, fed by the utterances the loop already captured.

    `save` is called exactly once, with every accepted embedding, at the moment the
    last sample arrives. It returns ``None`` on success or a failure message.
    """

    def __init__(
        self,
        person_id: str,
        save: Callable[[str, list[tuple[float, ...]]], str | None],
        *,
        required: int = DEFAULT_REQUIRED_SAMPLES,
        min_seconds: float = DEFAULT_MIN_SAMPLE_SECONDS,
    ) -> None:
        if not isinstance(person_id, str) or not person_id:
            raise ValueError("person_id is required")
        if not callable(save):
            raise ValueError("save must be callable")
        if int(required) < 1:
            raise ValueError("required must be positive")
        self.person_id = person_id
        self.required = int(required)
        self.min_seconds = float(min_seconds)
        self._save = save
        self._embeddings: list[tuple[float, ...]] = []
        self._status = EnrollmentStatus(
            "collecting", person_id, 0, self.required, self.min_seconds
        )
        self._lock = threading.RLock()

    # ------------------------------------------------------------------- reads

    def status(self) -> EnrollmentStatus:
        with self._lock:
            return self._status

    @property
    def active(self) -> bool:
        return self.status().active

    # ------------------------------------------------------------------ writes

    def submit(self, samples, sample_rate: int, embedder):
        """Take one spoken utterance and return the status after it.

        A refused sample changes only `last_reason`: progress must never advance on
        audio the gate rejected, or the gallery would be built from noise.
        """

        with self._lock:
            if not self._status.active:
                return self._status
            try:
                embedding, quality = embedder.embed(
                    samples, sample_rate, min_seconds=self.min_seconds
                )
            except SpeakerModelError as exc:
                return self._fail(exc.code)
            if embedding is None:
                return self.reject(quality.reason or "not_recognisable")
            return self.accept(embedding)

    def accept(self, embedding) -> EnrollmentStatus:
        with self._lock:
            if not self._status.active:
                return self._status
            vector = tuple(float(value) for value in embedding)
            if not vector:
                return self.reject("not_recognisable")
            self._embeddings.append(vector)
            self._status.accepted = len(self._embeddings)
            self._status.last_reason = None
            if self._status.accepted >= self.required:
                return self._store()
            return self._status

    def reject(self, reason: str) -> EnrollmentStatus:
        with self._lock:
            if self._status.active:
                self._status.last_reason = reason if reason in REJECTION_REASONS else "not_recognisable"
            return self._status

    def cancel(self) -> EnrollmentStatus:
        """Discard everything. Nothing was stored yet, so nothing needs undoing."""

        with self._lock:
            self._embeddings = []
            self._status.state = "idle"
            self._status.accepted = 0
            self._status.last_reason = None
            self._status.message = "已取消录入"
            return self._status

    # ---------------------------------------------------------------- internal

    def _store(self) -> EnrollmentStatus:
        try:
            failure = self._save(self.person_id, list(self._embeddings))
        except Exception as exc:  # noqa: BLE001 - a store crash must not kill the loop
            return self._fail("save_failed", type(exc).__name__)
        if failure:
            return self._fail("save_failed", str(failure))
        self._status.state = "saved"
        self._status.message = f"{self.person_id} 的声纹已保存"
        # Keep nothing in memory after a successful save: the gallery is the only
        # place voiceprints live.
        self._embeddings = []
        return self._status

    def _fail(self, code: str, message: str | None = None) -> EnrollmentStatus:
        self._embeddings = []
        self._status.state = "failed"
        self._status.accepted = 0
        self._status.last_reason = code
        self._status.message = message or code
        return self._status
