"""What the voice service's enrolment session sends to the voiceprint gallery.

Round 5 added an optional ``accumulate`` field to the enrolment request. The gallery
requires exactly the known fields, so a version skew in either direction must fail
loudly rather than silently replacing a person's voiceprints. The voice half of that
seam is here: does ``start_enrollment`` actually thread the flag through to the
request, and does ``None`` really omit the field entirely?

The gallery half lives in the vision suite (``test_speaker_enroll_payload.py``),
which runs the same payload shapes through the real request parser. The two apps each
ship a module named ``config``, so they cannot be imported in one process; splitting
the test along the seam keeps each half in its own app's import context.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

VOICE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(VOICE_SRC) not in sys.path:
    sys.path.insert(0, str(VOICE_SRC))

from loop import start_enrollment  # noqa: E402


class RecordingIdentity:
    """Stands in for SpeakerIdentityClient: captures what the loop would send."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._error = error

    def enroll_person(self, person_id, embeddings, **options):
        self.calls.append({"person_id": person_id, "embeddings": list(embeddings), **options})
        if self._error is not None:
            raise self._error
        return None


def finish_enrolment(recorder, *, accumulate=None, required=2, person_id="dad"):
    """Drive one whole enrolment and return (status, the recorded call)."""

    session, payload = start_enrollment(
        person_id,
        required=required,
        embedder=object(),                      # only "not None" is checked
        identity=recorder,
        directory_reader=lambda: {person_id: "爸爸"},
        active_session=None,
        accumulate=accumulate,
    )
    assert session is not None, payload
    for index in range(required):
        session.accept((float(index + 1), 0.0, 0.0))
    return session.status(), (recorder.calls[0] if recorder.calls else None)


def test_enrolment_asks_the_gallery_to_accumulate():
    recorder = RecordingIdentity()

    status, call = finish_enrolment(recorder, accumulate=True)

    assert status.state == "saved"
    assert call is not None
    assert call["accumulate"] is True
    assert len(call["embeddings"]) == 2
    assert call["person_id"] == "dad"


def test_accumulation_can_be_turned_off_explicitly():
    recorder = RecordingIdentity()

    _status, call = finish_enrolment(recorder, accumulate=False)

    assert call is not None
    assert call["accumulate"] is False


def test_none_omits_the_key_entirely_so_an_older_gallery_still_works():
    """An older gallery rejects unknown fields, so absent must mean absent."""

    recorder = RecordingIdentity()

    _status, call = finish_enrolment(recorder, accumulate=None)

    assert call is not None
    assert "accumulate" not in call
    assert set(call) == {"person_id", "embeddings"}


@pytest.mark.parametrize("accumulate", [None, False, True])
def test_the_payload_only_ever_has_known_fields(accumulate):
    recorder = RecordingIdentity()

    _status, call = finish_enrolment(recorder, accumulate=accumulate)

    assert call is not None
    assert set(call) <= {"person_id", "embeddings", "accumulate"}
    assert all(len(vector) == 3 for vector in call["embeddings"])


def test_a_gallery_error_becomes_a_failed_enrolment_not_a_crash():
    recorder = RecordingIdentity(error=RuntimeError("gallery exploded"))

    status, _call = finish_enrolment(recorder)

    assert status.state == "failed"
    assert status.accepted == 0


def test_the_default_when_the_caller_says_nothing_is_the_gallery_choice():
    """`start_enrollment` must not invent an accumulate value of its own."""

    recorder = RecordingIdentity()

    _status, call = finish_enrolment(recorder, accumulate=None)

    assert call is not None
    assert "accumulate" not in call


def test_enrolment_is_refused_for_an_unknown_person():
    recorder = RecordingIdentity()

    session, payload = start_enrollment(
        "nobody",
        required=2,
        embedder=object(),
        identity=recorder,
        directory_reader=lambda: {"dad": "爸爸"},
        active_session=None,
        accumulate=True,
    )

    assert session is None
    assert payload["error_code"] == "unknown_person"
    assert recorder.calls == []


def test_enrolment_is_refused_while_another_is_active():
    recorder = RecordingIdentity()
    active = type("Active", (), {"active": True})()

    session, payload = start_enrollment(
        "dad",
        required=2,
        embedder=object(),
        identity=recorder,
        directory_reader=lambda: {"dad": "爸爸"},
        active_session=active,
        accumulate=True,
    )

    assert session is None
    assert payload["error_code"] == "enrollment_active"
    assert recorder.calls == []
