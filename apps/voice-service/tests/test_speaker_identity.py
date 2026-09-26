"""The speaker-verdict policy: when a similarity may become a name.

No model, no gallery, no network: the vision-service response is scripted, so every
branch — including the ones that must stay silent — is provable on its own.
"""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from speaker_identity import SpeakerIdentityClient, SpeakerVerdict

BASE = "http://127.0.0.1:8766"


class JsonResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ScriptedOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return JsonResponse(self.outcome)


def client(outcome, **kwargs):
    instance = SpeakerIdentityClient(BASE, **kwargs)
    instance.opener = ScriptedOpener(outcome)
    return instance


def match(person_id="dad", similarity=0.9, margin=0.3):
    return {"ok": True, "candidate": {"person_id": person_id, "similarity": similarity, "margin": margin}}


# ------------------------------------------------------------------ the gate


def test_a_confident_match_names_the_person():
    verdict = client(match()).verdict([1.0, 0.0])

    assert verdict.state == "confirmed"
    assert verdict.usable is True
    assert verdict.person_id == "dad"
    assert verdict.confidence == pytest.approx(0.9)


def test_a_weak_match_keeps_only_the_number():
    verdict = client(match(similarity=0.4)).verdict([1.0, 0.0])

    assert verdict.state == "uncertain"
    assert verdict.reason == "below_threshold"
    assert verdict.person_id is None
    assert verdict.confidence == pytest.approx(0.4)


def test_two_similar_candidates_are_not_guessed_between():
    verdict = client(match(similarity=0.9, margin=0.01)).verdict([1.0, 0.0])

    assert verdict.state == "uncertain"
    assert verdict.reason == "ambiguous"
    assert verdict.person_id is None


def test_a_lone_enrolled_person_can_still_be_recognised():
    """A missing runner-up is not evidence of ambiguity, exactly as for faces."""
    verdict = client(match(margin=None)).verdict([1.0, 0.0])

    assert verdict.state == "confirmed"
    assert verdict.person_id == "dad"


def test_an_empty_gallery_is_unknown_and_not_an_error():
    verdict = client(match(person_id=None, similarity=None, margin=None)).verdict([1.0, 0.0])

    assert verdict.state == "unknown"
    assert verdict.reason == "no_enrolment"


def test_thresholds_are_configurable():
    weak = client(match(similarity=0.4), threshold=0.3).verdict([1.0, 0.0])

    assert weak.state == "confirmed"


# ------------------------------------------------------- external degradation


@pytest.mark.parametrize(
    "outcome",
    [
        URLError("vision service is down"),
        TimeoutError(),
        HTTPError("u", 500, "boom", {}, None),
        HTTPError("u", 404, "no route", {}, None),
        OSError("connection reset"),
    ],
)
def test_a_broken_gallery_degrades_to_unknown(outcome):
    verdict = client(outcome).verdict([1.0, 0.0])

    assert verdict.state == "unknown"
    assert verdict.reason == "gallery_unavailable"


@pytest.mark.parametrize(
    "outcome",
    [
        {"ok": True},
        {"ok": True, "candidate": "dad"},
        {"ok": True, "candidate": {"person_id": "dad", "similarity": "high", "margin": 0.3}},
        {"ok": True, "candidate": {"person_id": 7, "similarity": 0.9, "margin": 0.3}},
    ],
)
def test_a_response_that_does_not_match_the_contract_is_unknown(outcome):
    verdict = client(outcome).verdict([1.0, 0.0])

    assert verdict.state == "unknown"
    assert verdict.reason == "invalid_response"


def test_a_non_json_body_is_unknown_rather_than_a_crash():
    instance = SpeakerIdentityClient(BASE)

    class Broken:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"<html>not json</html>"

    class Opener:
        def open(self, request, timeout=None):
            return Broken()

    instance.opener = Opener()

    assert instance.verdict([1.0]).state == "unknown"


# ------------------------------------------------------------ request hygiene


def test_the_request_carries_the_embedding_once_and_nothing_else():
    instance = client(match())

    instance.verdict([1.0, 2.0, 3.0])

    request = instance.requests[0] if hasattr(instance, "requests") else instance.opener.requests[0]
    assert request.get_method() == "POST"
    assert request.full_url == BASE + "/identity/speaker/match"
    assert json.loads(request.data.decode("utf-8")) == {"embedding": [1.0, 2.0, 3.0]}


@pytest.mark.parametrize(
    "embedding",
    [[], ["a"], [True, False], [float("nan")], [float("inf")], "1,0", 5],
)
def test_a_malformed_embedding_is_a_programming_error_not_a_silent_unknown(embedding):
    with pytest.raises(ValueError):
        client(match()).verdict(embedding)


# --------------------------------------------------------------- the verdict


def test_an_unconfirmed_verdict_cannot_carry_a_name():
    with pytest.raises(ValueError):
        SpeakerVerdict("uncertain", person_id="dad")


def test_an_unknown_state_is_a_programming_error():
    with pytest.raises(ValueError):
        SpeakerVerdict("probably")


def test_only_a_confirmed_verdict_produces_a_prompt_line():
    assert "爸爸" in client(match()).verdict([1.0]).prompt_line("爸爸")
    assert client(match(similarity=0.4)).verdict([1.0]).prompt_line("爸爸") is None
    assert client(match()).verdict([1.0]).prompt_line(None) is None


def test_the_prompt_line_is_hedged_and_never_a_claim():
    line = client(match(similarity=0.72)).verdict([1.0]).prompt_line("爸爸")

    assert line == "声纹判定：说话人可能是爸爸（置信度 0.72）。"


def test_the_verdict_serialises_for_the_console():
    payload = client(match(similarity=0.4)).verdict([1.0]).to_dict()

    assert payload == {
        "state": "uncertain",
        "person_id": None,
        "confidence": pytest.approx(0.4),
        "margin": pytest.approx(0.3),
        "reason": "below_threshold",
    }


# ---------------------------------------------------------------- enrolment


def test_enrolment_stores_one_person_as_a_whole():
    instance = client({"ok": True, "person_id": "dad", "count": 2, "persistent": True})

    assert instance.enroll_person("dad", [[1.0, 0.0], [0.0, 1.0]]) is None
    request = instance.opener.requests[0]
    assert request.full_url == BASE + "/identity/speaker/enroll"
    assert json.loads(request.data.decode("utf-8")) == {
        "person_id": "dad",
        "embeddings": [[1.0, 0.0], [0.0, 1.0]],
    }


def test_enrolment_with_nothing_to_store_makes_no_request():
    instance = client({"ok": True})

    assert instance.enroll_person("dad", []) is not None
    assert instance.opener.requests == []


def test_a_rejected_enrolment_reports_the_service_wording():
    instance = client({"ok": False, "message": "人物编号无效"})

    assert instance.enroll_person("dad", [[1.0]]) == "人物编号无效"


def test_an_unreachable_gallery_is_reported_in_words():
    instance = client(URLError("down"))

    assert instance.enroll_person("dad", [[1.0]]) == "声纹库当前不可用"


def test_an_http_error_body_explains_the_failure_when_it_can():
    failure = HTTPError("u", 422, "bad", {}, io.BytesIO(json.dumps({"message": "向量太短"}).encode()))

    assert client(failure).enroll_person("dad", [[1.0]]) == "向量太短"


def test_an_http_error_without_a_body_still_reports_something():
    """Reading an optional error body must never become an unhandled crash."""
    assert client(HTTPError("u", 500, "boom", {}, None)).enroll_person("dad", [[1.0]]) == "声纹保存失败"


def test_a_malformed_enrolment_embedding_is_a_programming_error():
    with pytest.raises(ValueError):
        client({"ok": True}).enroll_person("dad", [["a"]])
