"""Loop-side speaker wiring, provable without a microphone.

`resolve_speaker` is the entire "utterance in, prompt line out" logic, so testing
it with fakes covers every branch the live loop can take — including all the ones
that must stay silent, which are the ones that matter.
"""

from __future__ import annotations

import numpy as np
import pytest

import config
from loop import resolve_speaker, speaker_fields, start_enrollment
from speaker import VoiceQuality
from speaker_identity import SpeakerVerdict


@pytest.fixture(autouse=True)
def isolated_env_file(monkeypatch, tmp_path):
    """Never read the machine's real local env file.

    Every speaker setting falls back to `runtime/voice-agent/agent.env`, so once
    somebody configures voiceprints there, tests asserting a default would start
    failing for a reason that has nothing to do with the code.
    """

    monkeypatch.setattr(config, "agent_env_file", lambda: tmp_path / "absent.env")


AUDIO = np.zeros(32000, dtype="float32")
CONFIRMED = SpeakerVerdict("confirmed", person_id="dad", confidence=0.72, margin=0.3)
UNCERTAIN = SpeakerVerdict("uncertain", confidence=0.4, margin=0.3, reason="below_threshold")
UNKNOWN = SpeakerVerdict("unknown", reason="no_enrolment")


class FakeEmbedder:
    def __init__(self, embedding=(1.0, 0.0), error=None, configured=True, reason=None):
        self.embedding = None if embedding is None else tuple(embedding)
        self.error = error
        self.configured = configured
        # The gate's verdict, which the loop must pass on instead of discarding.
        self.reason = reason
        self.calls = []

    def embed(self, samples, sample_rate, min_seconds=None):
        self.calls.append((samples, sample_rate))
        if self.error is not None:
            raise self.error
        return self.embedding, VoiceQuality(self.embedding is not None, self.reason, 2.0)


class FakeIdentity:
    def __init__(self, verdict):
        self.answer = verdict
        self.embeddings = []

    def verdict(self, embedding):
        self.embeddings.append(embedding)
        return self.answer


class FakeExecutor:
    def __init__(self, names=None, error=None):
        self.names = names or {}
        self.error = error

    def person_names(self):
        if self.error is not None:
            raise self.error
        return self.names


def resolve(embedder=None, verdict=CONFIRMED, names=None, executor=None):
    if executor is None:
        executor = FakeExecutor(names if names is not None else {"dad": "爸爸"})
    return resolve_speaker(
        FakeEmbedder() if embedder is None else embedder,
        FakeIdentity(verdict),
        executor,
        AUDIO,
        config.SAMPLE_RATE,
    )


# ----------------------------------------------------------------- the happy path


def test_a_confirmed_verdict_becomes_a_hedged_prompt_line():
    verdict, name, reason = resolve()

    assert name == "爸爸"
    assert reason is None
    assert verdict.prompt_line(name) == "声纹判定：说话人可能是爸爸（置信度 0.72）。"


def test_the_embedding_is_asked_for_once_at_the_capture_rate():
    embedder = FakeEmbedder()

    resolve(embedder=embedder)

    assert embedder.calls == [(AUDIO, config.SAMPLE_RATE)]


# --------------------------------------------------- the branches that stay silent


def test_an_unconfirmed_verdict_reports_its_state_but_names_nobody():
    """The console needs the state; the model must never get a name."""
    verdict, name, reason = resolve(verdict=UNCERTAIN)

    assert verdict.state == "uncertain"
    assert verdict.prompt_line(name) is None
    assert reason == "below_threshold"
    fields = speaker_fields(verdict, name, reason)
    assert fields["speaker_name"] is None
    assert fields["speaker_id"] is None
    assert fields["speaker_confidence"] == pytest.approx(0.4)
    assert fields["speaker_reason"] == "below_threshold"


def test_an_unrecognised_speaker_is_reported_as_unknown_not_as_absent():
    """`unknown` and "voiceprints are off" are different things to show a user."""
    verdict, name, reason = resolve(verdict=UNKNOWN)

    assert verdict is not None
    assert speaker_fields(verdict, name, reason)["speaker_state"] == "unknown"


def test_voiceprints_being_off_means_no_lookup_at_all():
    identity = FakeIdentity(CONFIRMED)

    assert resolve_speaker(
        None, None, FakeExecutor(), AUDIO, config.SAMPLE_RATE
    ) == (None, None, "disabled")
    assert identity.embeddings == []


def test_an_unconfigured_embedder_never_touches_the_model():
    embedder = FakeEmbedder(configured=False)

    assert resolve_speaker(
        embedder, FakeIdentity(CONFIRMED), FakeExecutor(), AUDIO, config.SAMPLE_RATE
    ) == (None, None, "disabled")
    assert embedder.calls == []


def test_audio_the_quality_gate_refuses_says_which_gate():
    """"Too short" is not a speaker; it is silence with a timestamp."""
    assert resolve(embedder=FakeEmbedder(embedding=None, reason="too_short")) == (None, None, "too_short")


def test_a_person_without_a_name_is_never_spoken_as_an_id():
    verdict, name, _reason = resolve(names={})

    assert name is None
    assert verdict.prompt_line(name) is None


@pytest.mark.parametrize(
    "error",
    [RuntimeError("model gone"), OSError("vision service down"), KeyError("odd shape")],
)
def test_any_failure_degrades_to_a_reported_reason(error):
    """Speaker identification is an enhancement; the spoken turn is not."""
    assert resolve(embedder=FakeEmbedder(error=error)) == (None, None, "unavailable")


def test_a_broken_person_directory_never_breaks_the_turn():
    assert resolve(executor=FakeExecutor(error=RuntimeError("home-service down"))) == (None, None, "unavailable")


# ----------------------------------------------------------------- event fields


def test_no_verdict_still_publishes_why_there_is_none():
    """Regression: publishing nothing made "too short", "nobody enrolled" and
    "voiceprints off" indistinguishable in the console."""
    fields = speaker_fields(None, None, "too_short")

    assert fields == {"speaker_state": "unknown", "speaker_reason": "too_short"}


def test_voiceprints_being_off_is_reported_as_disabled_not_as_a_failure():
    assert speaker_fields(None, None, "disabled") == {
        "speaker_state": "disabled",
        "speaker_reason": "disabled",
    }


def test_an_absent_reason_still_produces_a_complete_payload():
    assert speaker_fields(None, None) == {
        "speaker_state": "unknown",
        "speaker_reason": "not_evaluated",
    }


def test_the_published_fields_are_what_the_console_renders():
    assert speaker_fields(CONFIRMED, "爸爸") == {
        "speaker_id": "dad",
        "speaker_name": "爸爸",
        "speaker_confidence": 0.72,
        "speaker_state": "confirmed",
        "speaker_reason": None,
    }


# ---------------------------------------------------------------------- config


def test_voiceprints_are_off_until_a_model_is_configured(monkeypatch):
    monkeypatch.delenv("SMART_HOME_SPEAKER_MODEL", raising=False)

    assert config.speaker_model_path() is None


def test_a_configured_model_path_is_read(monkeypatch):
    monkeypatch.setenv("SMART_HOME_SPEAKER_MODEL", "D:/models/speaker.onnx")

    assert str(config.speaker_model_path()).replace("\\", "/") == "D:/models/speaker.onnx"


def test_the_gallery_defaults_to_the_loopback_vision_service(monkeypatch):
    monkeypatch.delenv("SMART_HOME_VISION_URL", raising=False)

    assert config.vision_service_url() == "http://127.0.0.1:8766"


def test_a_nonsense_threshold_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("SMART_HOME_SPEAKER_MATCH_THRESHOLD", "not a number")

    assert config.speaker_match_threshold() == config.DEFAULT_SPEAKER_MATCH_THRESHOLD


def test_the_embedding_runs_on_cpu_by_default(monkeypatch):
    """The installed sherpa-onnx wheel is CPU-only and the GPU is already busy."""
    monkeypatch.delenv("SMART_HOME_SPEAKER_PROVIDER", raising=False)

    assert config.speaker_provider() == "cpu"


def test_threads_default_to_one_and_reject_nonsense(monkeypatch):
    monkeypatch.delenv("SMART_HOME_SPEAKER_THREADS", raising=False)
    assert config.speaker_threads() == 1

    monkeypatch.setenv("SMART_HOME_SPEAKER_THREADS", "four")
    assert config.speaker_threads() == 1

    monkeypatch.setenv("SMART_HOME_SPEAKER_THREADS", "4")
    assert config.speaker_threads() == 4


def local_env_file(monkeypatch, tmp_path, text):
    path = tmp_path / "agent.env"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "agent_env_file", lambda: path)
    return path


def test_settings_can_live_in_the_local_env_file(monkeypatch, tmp_path):
    """So a restart keeps them without exporting anything in the launching shell."""
    local_env_file(
        monkeypatch, tmp_path,
        "SMART_HOME_SPEAKER_MODEL=D:/smart-home-models/x.onnx\nSMART_HOME_SPEAKER_THREADS=2\n",
    )
    monkeypatch.delenv("SMART_HOME_SPEAKER_MODEL", raising=False)
    monkeypatch.delenv("SMART_HOME_SPEAKER_THREADS", raising=False)

    assert str(config.speaker_model_path()).replace("\\", "/") == "D:/smart-home-models/x.onnx"
    assert config.speaker_threads() == 2


def test_a_real_environment_variable_wins_over_the_file(monkeypatch, tmp_path):
    local_env_file(monkeypatch, tmp_path, "SMART_HOME_SPEAKER_PROVIDER=cuda\n")
    monkeypatch.setenv("SMART_HOME_SPEAKER_PROVIDER", "cpu")

    assert config.speaker_provider() == "cpu"


def test_quotes_and_spacing_in_the_env_file_are_tolerated(monkeypatch, tmp_path):
    local_env_file(monkeypatch, tmp_path, 'SMART_HOME_SPEAKER_MODEL = "D:/models/a.onnx" \n')
    monkeypatch.delenv("SMART_HOME_SPEAKER_MODEL", raising=False)

    assert str(config.speaker_model_path()).replace("\\", "/") == "D:/models/a.onnx"


def test_an_unreadable_env_file_falls_back_to_the_default(monkeypatch, tmp_path):
    """A missing config file must mean "off", never a crash at startup."""
    monkeypatch.setattr(config, "agent_env_file", lambda: tmp_path / "missing" / "agent.env")
    monkeypatch.delenv("SMART_HOME_SPEAKER_MODEL", raising=False)

    assert config.speaker_model_path() is None
    assert config.speaker_provider() == "cpu"


# ------------------------------------------------------- starting an enrolment


class FakeIdentityClient:
    def __init__(self):
        self.enrolled = []

    def enroll_person(self, person_id, embeddings):
        self.enrolled.append((person_id, list(embeddings)))
        return None


# Distinguishes "not supplied, use the fake" from "supplied as None, meaning
# disabled" — without it, a test for the disabled path would silently pass the fake.
_UNSET = object()


def start(person_id="dad", *, embedder=_UNSET, identity=_UNSET, directory=_UNSET, active=None, required=5):
    return start_enrollment(
        person_id,
        required=required,
        embedder=FakeEmbedder() if embedder is _UNSET else embedder,
        identity=FakeIdentityClient() if identity is _UNSET else identity,
        directory_reader=(lambda: {"dad": "爸爸", "mom": "妈妈"}) if directory is _UNSET else directory,
        active_session=active,
    )


def test_an_enrolment_starts_for_a_person_the_directory_knows():
    session, payload = start()

    assert session is not None
    assert payload["ok"] is True
    assert payload["enrollment"]["person_id"] == "dad"
    assert payload["enrollment"]["required"] == 5
    assert payload["enrollment"]["state"] == "collecting"


def test_an_id_the_directory_does_not_know_is_refused():
    """Otherwise the gallery would hold a person the console can never show."""
    session, payload = start("nobody")

    assert session is None
    assert payload["error_code"] == "unknown_person"


def test_a_dynamically_added_person_can_be_enrolled():
    directory = lambda: {"person_" + "ab" * 16: "姑姑"}  # noqa: E731 - inline for readability

    session, payload = start("person_" + "ab" * 16, directory=directory)

    assert session is not None and payload["ok"] is True


def test_enrolment_needs_a_configured_model():
    session, payload = start(embedder=None, identity=None)

    assert session is None
    assert payload["error_code"] == "speaker_disabled"


def test_only_one_enrolment_runs_at_a_time():
    from enrollment import VoiceEnrollmentSession

    running = VoiceEnrollmentSession("mom", lambda pid, items: None, required=5)

    session, payload = start(active=running)

    assert session is None
    assert payload["error_code"] == "enrollment_active"


def test_a_finished_enrolment_does_not_block_the_next_one():
    from enrollment import VoiceEnrollmentSession

    done = VoiceEnrollmentSession("mom", lambda pid, items: None, required=1)
    done.accept((1.0, 0.0))

    session, payload = start(active=done)

    assert session is not None and payload["ok"] is True


def test_a_broken_directory_is_reported_as_such():
    session, payload = start(directory=None)

    assert session is None
    assert payload["error_code"] == "person_directory_unavailable"
