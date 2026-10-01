"""The public event contract, and the drift it is meant to make impossible.

Two separate mistakes are guarded here, and both are silent in production:
a new event field that the whitelist drops, and a producer whose field names have
quietly diverged from the contract the console parses.
"""

from __future__ import annotations

from event_contract import PUBLIC_EVENT_TYPES, PUBLIC_FIELDS
from loop import speaker_fields
from speaker_identity import SpeakerVerdict

SPEAKER_FIELDS = {"speaker_id", "speaker_name", "speaker_confidence", "speaker_state", "speaker_reason"}
CONFIRMED = SpeakerVerdict("confirmed", person_id="dad", confidence=0.72, margin=0.3)
UNCERTAIN = SpeakerVerdict("uncertain", confidence=0.4, margin=0.3, reason="below_threshold")


def test_every_public_event_type_has_a_field_whitelist():
    assert set(PUBLIC_EVENT_TYPES.values()) <= set(PUBLIC_FIELDS)


def test_the_transcript_still_carries_its_original_fields():
    assert {"state", "transcript", "seconds"} <= PUBLIC_FIELDS["transcript"]


def test_the_transcript_carries_every_speaker_field():
    """A field missing from this set never reaches the console, with no error."""
    assert SPEAKER_FIELDS <= PUBLIC_FIELDS["transcript"]


def test_the_speaker_producer_and_the_contract_agree():
    """The loop's producer must not drift from the whitelist it publishes through."""
    produced = set(speaker_fields(CONFIRMED, "爸爸"))

    assert produced == SPEAKER_FIELDS
    assert produced <= PUBLIC_FIELDS["transcript"]


def test_the_uncertain_producer_publishes_the_same_keys():
    """Every state must publish the same shape, or the console renders blanks."""
    assert set(speaker_fields(UNCERTAIN, "爸爸")) == SPEAKER_FIELDS


def test_short_clip_duration_fields_reach_the_console():
    """The public-event filter must keep the measured lengths used in the tooltip."""
    produced = set(speaker_fields(
        None, None, "too_short", audio_seconds=0.82, min_seconds=2.4,
    ))

    assert produced <= PUBLIC_FIELDS["transcript"]


def test_barge_in_hint_and_interruption_reach_console():
    assert PUBLIC_EVENT_TYPES["tts_interrupted"] == "playback_state"
    assert {"barge_in_hint", "keyword", "interrupted"} <= PUBLIC_FIELDS["playback_state"]


def test_an_interruption_while_thinking_is_reported_to_the_console():
    """The console's one question is whether the assistant is talking; a turn
    cancelled before playback is a playback-phase event with a reason."""
    assert PUBLIC_EVENT_TYPES["agent_interrupted"] == "playback_state"
    assert {"reason", "captured_seconds"} <= PUBLIC_FIELDS["playback_state"]
    assert "cancelled" in PUBLIC_FIELDS["agent_reply"]
    assert "barge_in" in PUBLIC_FIELDS["voice_state"]
