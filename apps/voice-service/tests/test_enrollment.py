"""Voice enrolment, driven by utterances rather than uploaded audio.

The rules that matter here are about what must *not* happen: progress must never
advance on rejected audio, nothing may be stored before the last sample, and a
cancel must leave the gallery untouched.
"""

from __future__ import annotations

import numpy as np
import pytest

from enrollment import (
    DEFAULT_MIN_SAMPLE_SECONDS,
    REJECTION_REASONS,
    EnrollmentStatus,
    VoiceEnrollmentSession,
    idle_status,
)
from speaker import SpeakerModelError, VoiceQuality

GOOD = (1.0, 0.0, 0.0)
AUDIO = np.zeros(48000, dtype="float32")


class FakeEmbedder:
    """Returns one embedding per call and records the min_seconds it was asked for."""

    def __init__(self, embedding=GOOD, reason=None, error=None):
        self.embedding = embedding
        self.reason = reason
        self.error = error
        self.min_seconds_seen = []
        self.calls = 0

    def embed(self, samples, sample_rate, min_seconds=None):
        self.calls += 1
        self.min_seconds_seen.append(min_seconds)
        if self.error is not None:
            raise self.error
        if self.reason is not None:
            return None, VoiceQuality(False, self.reason, 1.0)
        return self.embedding, VoiceQuality(True, None, 3.0)


class Recorder:
    def __init__(self, failure=None, error=None):
        self.failure = failure
        self.error = error
        self.saved = []

    def __call__(self, person_id, embeddings):
        self.saved.append((person_id, list(embeddings)))
        if self.error is not None:
            raise self.error
        return self.failure


def session(save=None, **kwargs):
    recorder = save if save is not None else Recorder()
    return VoiceEnrollmentSession("dad", recorder, **kwargs), recorder


# ------------------------------------------------------------------- collection


def test_a_good_sample_advances_the_count():
    agent, _save = session(required=3)

    status = agent.submit(AUDIO, 16000, FakeEmbedder())

    assert status.state == "collecting"
    assert status.accepted == 1
    assert status.active is True


def test_enrolment_demands_a_longer_sample_than_a_question_does():
    """The gate is the session's, not the embedder's default: enrolment quality is
    what every later recognition depends on."""
    agent, _save = session()
    embedder = FakeEmbedder()

    agent.submit(AUDIO, 16000, embedder)

    assert embedder.min_seconds_seen == [DEFAULT_MIN_SAMPLE_SECONDS]


@pytest.mark.parametrize("reason", REJECTION_REASONS)
def test_a_rejected_sample_never_advances_progress(reason):
    agent, save = session(required=2)
    embedder = FakeEmbedder(reason=reason)

    status = agent.submit(AUDIO, 16000, embedder)

    assert status.accepted == 0
    assert status.last_reason == reason
    assert status.active is True
    assert save.saved == []


def test_an_unknown_rejection_reason_is_normalised():
    agent, _save = session()

    status = agent.submit(AUDIO, 16000, FakeEmbedder(reason="something_new"))

    assert status.last_reason == "not_recognisable"


def test_an_empty_embedding_is_not_a_sample():
    agent, save = session(required=1)

    status = agent.submit(AUDIO, 16000, FakeEmbedder(embedding=()))

    assert status.accepted == 0
    assert save.saved == []


# -------------------------------------------------------------------- storing


def test_nothing_is_stored_until_the_last_sample_arrives():
    agent, save = session(required=3)
    embedder = FakeEmbedder()

    agent.submit(AUDIO, 16000, embedder)
    agent.submit(AUDIO, 16000, embedder)
    assert save.saved == []

    status = agent.submit(AUDIO, 16000, embedder)

    assert status.state == "saved"
    assert status.active is False
    assert len(save.saved) == 1
    person_id, embeddings = save.saved[0]
    assert person_id == "dad"
    assert len(embeddings) == 3


def test_a_stored_enrolment_keeps_no_voiceprint_in_memory():
    """After a save the gallery is the only place voiceprints live."""
    agent, save = session(required=1)

    agent.submit(AUDIO, 16000, FakeEmbedder())

    assert save.saved[0][1] == [GOOD]
    assert agent.status().state == "saved"


def test_a_failing_store_marks_the_enrolment_failed_and_keeps_nothing():
    agent, _save = session(required=1, save=Recorder(failure="声纹库当前不可用"))

    status = agent.submit(AUDIO, 16000, FakeEmbedder())

    assert status.state == "failed"
    assert status.last_reason == "save_failed"
    assert status.message == "声纹库当前不可用"
    assert status.accepted == 0


def test_a_store_that_raises_never_reaches_the_voice_loop():
    agent, _save = session(required=1, save=Recorder(error=RuntimeError("boom")))

    status = agent.submit(AUDIO, 16000, FakeEmbedder())

    assert status.state == "failed"
    assert status.message == "RuntimeError"


def test_a_model_problem_fails_the_enrolment_rather_than_looping():
    agent, _save = session()
    embedder = FakeEmbedder(error=SpeakerModelError("speaker_model_missing"))

    status = agent.submit(AUDIO, 16000, embedder)

    assert status.state == "failed"
    assert status.last_reason == "speaker_model_missing"


# --------------------------------------------------------------------- cancel


def test_cancelling_stores_nothing_at_all():
    agent, save = session(required=5)
    embedder = FakeEmbedder()
    agent.submit(AUDIO, 16000, embedder)
    agent.submit(AUDIO, 16000, embedder)

    status = agent.cancel()

    assert status.state == "idle"
    assert status.accepted == 0
    assert status.active is False
    assert save.saved == []


def test_a_cancelled_session_ignores_later_samples():
    agent, save = session(required=1)
    agent.cancel()

    status = agent.submit(AUDIO, 16000, FakeEmbedder())

    assert status.state == "idle"
    assert save.saved == []


def test_a_saved_session_ignores_later_samples():
    agent, save = session(required=1)
    agent.submit(AUDIO, 16000, FakeEmbedder())

    agent.submit(AUDIO, 16000, FakeEmbedder())

    assert len(save.saved) == 1


# --------------------------------------------------------------------- status


def test_an_idle_status_is_a_fresh_object_each_time():
    first = idle_status()
    second = idle_status()
    first.state = "collecting"

    assert second.state == "idle"


def test_the_status_is_serialisable_for_the_console():
    agent, _save = session(required=4)

    payload = agent.status().to_dict()

    assert payload == {
        "state": "collecting",
        "active": True,
        "person_id": "dad",
        "accepted": 0,
        "required": 4,
        "min_seconds": DEFAULT_MIN_SAMPLE_SECONDS,
        "last_reason": None,
        "message": None,
    }


def test_an_unknown_state_is_a_programming_error():
    with pytest.raises(ValueError):
        EnrollmentStatus("nearly_done")


def test_a_session_needs_a_person_and_a_store():
    with pytest.raises(ValueError):
        VoiceEnrollmentSession("", Recorder())
    with pytest.raises(ValueError):
        VoiceEnrollmentSession("dad", None)
    with pytest.raises(ValueError):
        VoiceEnrollmentSession("dad", Recorder(), required=0)
