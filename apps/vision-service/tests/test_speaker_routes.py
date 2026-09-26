"""Speaker identity request parsing and the config contract it depends on.

These are pure functions: no model, no camera, no server. They exist so a
malformed body fails loudly at the edge instead of reaching the encrypted
registry, and so the vision config cannot silently lose the speaker pack.
"""

from __future__ import annotations

import pytest

from server import MAX_EMBEDDING_DIM, VisionRequestError, speaker_embeddings, speaker_query


def test_enrolment_accepts_a_batch_of_samples():
    vectors = speaker_embeddings({"person_id": "dad", "embeddings": [[1, 0, 0], [0.9, 0.1, 0]]})

    assert vectors == [(1.0, 0.0, 0.0), (0.9, 0.1, 0.0)]


def test_an_int_is_accepted_as_a_float():
    assert speaker_embeddings({"person_id": "dad", "embeddings": [[1, 2]]}) == [(1.0, 2.0)]


@pytest.mark.parametrize(
    "body",
    [
        {},                                                        # nothing at all
        {"person_id": "dad"},                                      # no vectors
        {"embeddings": [[1, 0]]},                                  # no person
        {"person_id": "dad", "embeddings": [[1, 0]], "extra": 1},  # typo must fail
        {"person_id": "nobody", "embeddings": [[1, 0]]},           # invalid id
        {"person_id": "person_nothex", "embeddings": [[1, 0]]},    # bad dynamic id
        {"person_id": "dad", "embeddings": []},                     # empty batch
        {"person_id": "dad", "embeddings": [[]]},                   # empty vector
        {"person_id": "dad", "embeddings": [["a", "b"]]},           # not numbers
        {"person_id": "dad", "embeddings": [[True, False]]},        # bool is not a number
        {"person_id": "dad", "embeddings": "1,0"},                  # not a list
    ],
)
def test_a_malformed_enrolment_body_is_refused(body):
    with pytest.raises(VisionRequestError) as caught:
        speaker_embeddings(body)

    assert caught.value.status == 422


def test_an_oversized_vector_is_refused_before_it_is_stored():
    body = {"person_id": "dad", "embeddings": [[0.0] * (MAX_EMBEDDING_DIM + 1)]}

    with pytest.raises(VisionRequestError) as caught:
        speaker_embeddings(body)

    assert caught.value.code == "invalid_embedding"


def test_a_match_takes_exactly_one_vector():
    assert speaker_query({"embedding": [1, 0, 0]}) == (1.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"embedding": []},
        {"embedding": [1, 0], "embeddings": [[1, 0]]},   # both keys is a mistake
        {"vector": [1, 0]},                              # wrong key name
    ],
)
def test_a_malformed_match_body_is_refused(body):
    with pytest.raises(VisionRequestError) as caught:
        speaker_query(body)

    assert caught.value.status == 422
