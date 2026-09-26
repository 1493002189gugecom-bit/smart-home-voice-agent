"""The FAR/FRR maths, checked without a model, a microphone or any recordings.

These are the numbers that decide whether voiceprints may target a room at all, so
they are tested directly rather than trusted from a one-off run.
"""

from __future__ import annotations

import pytest

from measure_speaker import (
    Trial,
    bucket_of,
    bucket_report,
    build_report,
    cosine,
    equal_error_rate,
    rates,
    score_trials,
    threshold_for_far,
)


def trials(genuine_scores, impostor_scores, seconds=2.5):
    return [Trial("dad", True, score, seconds) for score in genuine_scores] + [
        Trial("dad", False, score, seconds) for score in impostor_scores
    ]


# ------------------------------------------------------------------- bucketing


@pytest.mark.parametrize(
    ("seconds", "bucket"),
    [(0.4, "under_1s"), (0.99, "under_1s"), (1.0, "1_to_2s"), (1.99, "1_to_2s"), (2.0, "over_2s"), (9.0, "over_2s")],
)
def test_the_duration_buckets_match_the_documented_boundaries(seconds, bucket):
    assert bucket_of(seconds) == bucket


# ------------------------------------------------------------------- similarity


def test_identical_vectors_score_one():
    assert cosine((1.0, 2.0), (1.0, 2.0)) == pytest.approx(1.0)


def test_orthogonal_vectors_score_zero():
    assert cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)


def test_a_zero_vector_is_refused_rather_than_scored():
    with pytest.raises(ValueError):
        cosine((0.0, 0.0), (1.0, 0.0))


# ----------------------------------------------------------------------- rates


def test_rates_count_impostors_accepted_and_genuine_rejected():
    sample = trials([0.9, 0.7, 0.3], [0.8, 0.2])

    far, frr = rates(sample, 0.5)

    assert far == pytest.approx(0.5)     # one of two impostors accepted
    assert frr == pytest.approx(1 / 3)   # one of three genuine rejected


def test_a_threshold_above_every_score_accepts_nobody():
    far, frr = rates(trials([0.9], [0.8]), 1.1)

    assert far == pytest.approx(0.0)
    assert frr == pytest.approx(1.0)


def test_missing_trials_are_reported_as_unknown_not_as_zero():
    """No impostor trials is not the same as no impostor errors."""
    far, frr = rates(trials([0.9], []), 0.5)

    assert far is None
    assert frr == pytest.approx(0.0)


# --------------------------------------------------------------- equal error rate


def test_a_perfectly_separable_set_has_zero_eer():
    eer, threshold = equal_error_rate(trials([0.9, 0.85], [0.2, 0.1]))

    assert eer == pytest.approx(0.0)
    assert threshold is not None


def test_an_overlapping_set_reports_a_non_zero_eer():
    eer, _threshold = equal_error_rate(trials([0.9, 0.4], [0.8, 0.3]))

    assert eer is not None and eer > 0.0


def test_eer_needs_both_classes_to_mean_anything():
    assert equal_error_rate(trials([0.9], [])) == (None, None)


# ----------------------------------------------------------- threshold selection


def test_the_threshold_is_chosen_from_far_alone():
    """Genuine rejection only costs a question; accepting a stranger moves rooms."""
    sample = trials([0.95, 0.30], [0.10, 0.05])

    threshold, frr = threshold_for_far(sample, 0.01)

    assert threshold is not None and threshold > 0.10
    assert frr == pytest.approx(0.0)


def test_a_zero_impostor_rate_is_reachable():
    """Regression: the sweep used to be limited to observed impostor scores, so the
    only zero-FAR point — just above the highest of them — was never a candidate and
    almost every dataset looked like it could not meet the target at all."""
    sample = trials([0.95, 0.30], [0.10, 0.05])

    threshold, _frr = threshold_for_far(sample, 0.01)

    assert rates(sample, threshold)[0] == pytest.approx(0.0)


def test_without_impostor_trials_no_threshold_can_be_chosen():
    """The only genuinely unanswerable case: nobody else was ever recorded."""
    threshold, frr = threshold_for_far(trials([0.99, 0.98], []), 0.01)

    assert threshold is None
    assert frr is None


def test_a_target_without_impostor_trials_cannot_be_answered():
    assert threshold_for_far(trials([0.9], []), 0.01) == (None, None)


# ----------------------------------------------------------------- trial scoring


def test_genuine_trials_are_unordered_pairs_that_are_not_double_counted():
    embeddings = {"dad": [((1.0, 0.0), 2.0), ((0.9, 0.1), 2.0), ((0.8, 0.2), 2.0)]}

    scored = score_trials(embeddings)

    assert len([item for item in scored if item.genuine]) == 3   # C(3,2), not 6


def test_one_recording_produces_no_genuine_trial():
    scored = score_trials({"dad": [((1.0, 0.0), 2.0)]})

    assert [item for item in scored if item.genuine] == []


def test_impostor_trials_are_directional_and_cover_every_pair():
    embeddings = {"dad": [((1.0, 0.0), 2.0)], "mom": [((0.0, 1.0), 2.0)]}

    scored = [item for item in score_trials(embeddings) if not item.genuine]

    assert {(item.person_id, item.score) for item in scored} == {("dad", 0.0), ("mom", 0.0)}


def test_the_bucket_report_splits_by_duration():
    sample = [
        Trial("dad", True, 0.9, 0.5),
        Trial("dad", False, 0.1, 0.5),
        Trial("dad", True, 0.8, 3.0),
        Trial("dad", False, 0.2, 3.0),
    ]

    report = bucket_report(sample)

    assert report["under_1s"]["genuine"] == 1
    assert report["over_2s"]["impostor"] == 1
    assert "1_to_2s" not in report


# ------------------------------------------------------------------- the report


def test_the_report_warns_when_the_dataset_cannot_prove_the_target():
    text = build_report(trials([0.9], [0.1]), 0.01, {"dad": 2})

    assert "无法证明" in text
    assert "冒名试验" in text


def test_the_report_names_people_who_cannot_be_measured():
    text = build_report(trials([0.9], [0.1]), 0.01, {"dad": 2, "mom": 1})

    assert "mom" in text


def test_the_report_says_when_no_threshold_can_be_chosen():
    """With no impostor recordings there is nothing to set a threshold against."""
    text = build_report(trials([0.9, 0.8], []), 0.01, {"dad": 3})

    assert "无法达成目标 FAR" in text
