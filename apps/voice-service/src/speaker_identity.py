"""Speaker verdicts for one utterance, resolved against the gallery in vision-service.

The gallery and the matching live in vision-service because that is where the only
DPAPI implementation is. This module owns the *policy* — when a similarity is good
enough to act on — and it is deliberately the only place that can turn a number
into a name.

Two rules make it safe to speak:
  * the verdict is asked once per utterance and never cached, so an old answer can
    never be attributed to a new sentence;
  * below the threshold the name is dropped here, so no caller can leak it by
    forgetting to check `state`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_MATCH_THRESHOLD = 0.55
DEFAULT_MARGIN_THRESHOLD = 0.08
DEFAULT_TIMEOUT_SECONDS = 3.0
# The only three answers a caller may see.
STATES = ("confirmed", "uncertain", "unknown")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102 - see base class
        return None


@dataclass(frozen=True)
class SpeakerVerdict:
    """What the system is willing to say about who just spoke."""

    state: str
    person_id: str | None = None
    confidence: float | None = None
    margin: float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError("unknown speaker state")
        if self.state != "confirmed" and self.person_id is not None:
            # Below the threshold the identity is not merely hidden from the model,
            # it is gone: a caller cannot print what it was never given.
            raise ValueError("an unconfirmed verdict must not carry an identity")

    @property
    def usable(self) -> bool:
        return self.state == "confirmed" and self.person_id is not None

    def prompt_line(self, display_name: str | None) -> str | None:
        """The only wording the model is ever shown; None means inject nothing.

        A name is required: an id nobody can pronounce is worse than silence, so an
        unresolved person injects nothing at all.
        """

        if not self.usable or not display_name:
            return None
        confidence = 0.0 if self.confidence is None else self.confidence
        return f"声纹判定：说话人可能是{display_name}（置信度 {confidence:.2f}）。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "person_id": self.person_id,
            "confidence": self.confidence,
            "margin": self.margin,
            "reason": self.reason,
        }


class SpeakerIdentityClient:
    """Asks the voiceprint gallery about one embedding.

    Everything external degrades to `unknown`: the voice loop must survive a stopped
    vision service, an empty gallery or a missing model, and a wrong `unknown` only
    costs one clarifying question.
    """

    def __init__(
        self,
        base_url: str,
        *,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.threshold = float(threshold)
        self.margin_threshold = float(margin_threshold)
        self.timeout = float(timeout)
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def verdict(self, embedding) -> SpeakerVerdict:
        """Classify one utterance embedding. Only a programming error raises."""

        payload = {"embedding": _vector(embedding)}
        try:
            body = self._post("/identity/speaker/match", payload)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return SpeakerVerdict("unknown", reason="gallery_unavailable")
        candidate = body.get("candidate") if isinstance(body, dict) else None
        if not isinstance(candidate, dict):
            return SpeakerVerdict("unknown", reason="invalid_response")
        return self._gate(candidate)

    def _gate(self, candidate: dict[str, Any]) -> SpeakerVerdict:
        person_id = candidate.get("person_id")
        similarity = candidate.get("similarity")
        margin = candidate.get("margin")
        if person_id is None:
            # A legitimate empty result: nobody is enrolled yet.
            return SpeakerVerdict("unknown", reason="no_enrolment")
        if not isinstance(person_id, str) or not person_id:
            # An id of the wrong type is a broken contract, not an empty gallery,
            # and the console shows these two differently.
            return SpeakerVerdict("unknown", reason="invalid_response")
        if isinstance(similarity, bool) or not isinstance(similarity, (int, float)):
            return SpeakerVerdict("unknown", reason="invalid_response")
        score = float(similarity)
        gap = None if isinstance(margin, bool) or not isinstance(margin, (int, float)) else float(margin)
        if score < self.threshold:
            return SpeakerVerdict("uncertain", confidence=score, margin=gap, reason="below_threshold")
        if gap is not None and gap < self.margin_threshold:
            # Two enrolled people scored almost the same: this is exactly the case
            # where naming one of them would be a guess.
            return SpeakerVerdict("uncertain", confidence=score, margin=gap, reason="ambiguous")
        return SpeakerVerdict("confirmed", person_id=person_id, confidence=score, margin=gap)

    def enroll_person(self, person_id: str, embeddings) -> str | None:
        """Store one person's voiceprints, replacing anything already there.

        Returns ``None`` on success or a human-readable failure reason. Enrolment is
        the only write this client performs, and it is always a whole-person
        replacement so a half-finished enrolment can never leave a mixed gallery.
        """

        vectors = [_vector(item) for item in embeddings]
        if not vectors:
            return "没有可保存的声纹样本"
        try:
            body = self._post("/identity/speaker/enroll", {"person_id": str(person_id), "embeddings": vectors})
        except HTTPError as exc:
            try:
                failure = json.loads(exc.read().decode("utf-8", errors="replace"))
                return str(failure.get("message") or "声纹保存失败")
            except Exception:  # noqa: BLE001 - an error body is optional
                return "声纹保存失败"
        except (URLError, TimeoutError, OSError, ValueError):
            return "声纹库当前不可用"
        if not isinstance(body, dict) or not body.get("ok"):
            message = body.get("message") if isinstance(body, dict) else None
            return str(message or "声纹保存失败")
        return None

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.base_url + path,
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            return json.load(response)


def _vector(embedding) -> list[float]:
    """Validate the embedding once, at the boundary where a bug is still a bug."""

    if isinstance(embedding, (str, bytes)) or not hasattr(embedding, "__iter__"):
        raise ValueError("embedding must be a sequence of numbers")
    values: list[float] = []
    for value in embedding:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("embedding must contain only numbers")
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError("embedding must be finite")
        values.append(number)
    if not values:
        raise ValueError("embedding must not be empty")
    return values
