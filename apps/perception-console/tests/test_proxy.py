"""The console proxy is an allow-list, so what it refuses is part of the contract."""

from __future__ import annotations

import pytest

from proxy import ProxyRejected, resolve_target

VOICE = "http://127.0.0.1:8767"


def test_voice_enrolment_routes_are_reachable():
    assert resolve_target("voice", "GET", "/identity/enroll/voice/status") == VOICE + "/identity/enroll/voice/status"
    assert resolve_target("voice", "POST", "/identity/enroll/voice/start") == VOICE + "/identity/enroll/voice/start"
    assert resolve_target("voice", "POST", "/identity/enroll/voice/cancel") == VOICE + "/identity/enroll/voice/cancel"


def test_there_is_no_route_that_uploads_audio_for_enrolment():
    """The microphone belongs to the voice loop, so a browser must never be able to
    hand over a biometric sample it captured itself."""
    with pytest.raises(ProxyRejected):
        resolve_target("voice", "POST", "/identity/enroll/voice/sample")


def test_a_route_allowed_for_one_method_is_not_available_for_another():
    with pytest.raises(ProxyRejected):
        resolve_target("voice", "DELETE", "/identity/enroll/voice/start")


def test_the_speaker_gallery_status_is_reachable():
    """The identity page shows who has a voiceprint, so this route must resolve."""
    assert resolve_target("vision", "GET", "/identity/speaker/status") == "http://127.0.0.1:8766/identity/speaker/status"
    with pytest.raises(ProxyRejected):
        # The gallery is written by the voice service that captured the audio, so
        # the browser still has no write route to it.
        resolve_target("vision", "POST", "/identity/speaker/enroll")


def test_an_unknown_service_is_refused():
    with pytest.raises(ProxyRejected):
        resolve_target("nowhere", "GET", "/health")
