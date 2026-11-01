"""The exact payload shapes the voice service sends, through the real parser.

Companion to ``test_speaker_enroll_seam.py`` in the same suite: that file checks the
voice side produces the right keyword arguments, and this one checks the gallery's
request parser accepts them. Together they cover the version-skew question that
matters — an old client that never heard of ``accumulate``, and a new one that sends it.

Kept in its own module because importing the vision server pulls in the vision
``config``, which collides with the voice service's same-named module if both are
imported in one process.
"""

from __future__ import annotations

import pytest

from server import VisionRequestError, speaker_enrolment


def test_an_old_client_that_never_heard_of_accumulate_is_still_accepted():
    """Absent must mean absent: the field is optional, not defaulted by the client."""

    person_id, vectors, accumulate = speaker_enrolment(
        {"person_id": "dad", "embeddings": [[1, 0, 0], [0.9, 0.1, 0]]}
    )

    assert person_id == "dad"
    assert vectors == [(1.0, 0.0, 0.0), (0.9, 0.1, 0.0)]
    assert accumulate is False


def test_a_new_client_asking_to_accumulate_is_understood():
    _person_id, _vectors, accumulate = speaker_enrolment(
        {"person_id": "dad", "embeddings": [[1, 0, 0]], "accumulate": True}
    )

    assert accumulate is True


@pytest.mark.parametrize("value", [0, 1, "true", None, [], {}])
def test_a_truthy_non_boolean_is_refused_rather_than_coerced(value):
    """A gallery that guesses here would silently pick between replace and merge."""

    with pytest.raises(VisionRequestError) as caught:
        speaker_enrolment({"person_id": "dad", "embeddings": [[1, 0, 0]], "accumulate": value})

    assert caught.value.code == "invalid_accumulate"
