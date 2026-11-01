"""Audio scheduling with no network, microphone or physical output."""
import threading

import pytest

from text_speech import SpeechFailure, SpeechRequest, TextSpeechMailbox
from voice_errors import failure_details


class Rejected(RuntimeError):
    status = 403


def test_chained_provider_rejection_is_actionable_and_keeps_private_details_out():
    try:
        try:
            raise Rejected("private-url?api_key=secret")
        except Rejected as cause:
            raise RuntimeError("private provider response") from cause
    except RuntimeError as error:
        details = failure_details("tts_synthesis", error)
    assert details["error_code"] == "tts_service_rejected"
    assert "403" in details["message"] and "secret" not in str(details)
    assert failure_details("playback", Rejected())["error_code"] == "audio_playback_failed"


def test_submit_waits_until_the_scheduled_audio_has_finished():
    mailbox = TextSpeechMailbox(timeout=2)
    published, played, failures = [], [], []
    def submit():
        try:
            mailbox.submit("你好", lambda event, **fields: published.append((event, fields)))
        except Exception as error:
            failures.append(error)
    worker = threading.Thread(target=submit)
    worker.start()
    request = mailbox.pending.get(timeout=1)
    assert worker.is_alive()
    def player(samples, rate, **options):
        assert worker.is_alive() and not options["should_interrupt"]()
        played.append((samples, rate))
        return True
    mailbox.process(request, lambda text: ([1, 2], 24000), player)
    worker.join(1)
    assert not worker.is_alive() and not failures and played == [([1, 2], 24000)]
    assert [fields["source_event"] for _, fields in published] == ["tts_synthesizing", "tts_start", "tts_end"]


@pytest.mark.parametrize("stage", ["tts_synthesis", "playback"])
def test_audio_errors_finish_the_request_with_a_safe_stage(stage):
    mailbox = TextSpeechMailbox()
    request = SpeechRequest("你好", lambda *a, **k: None)
    def fail(*a, **k):
        raise Rejected("private provider detail")
    mailbox.process(request, fail if stage == "tts_synthesis" else lambda _: ([1], 24000), fail)
    assert request.done.is_set() and request.failure["stage"] == stage
    assert "private" not in str(request.failure)


def test_timed_out_queue_does_not_play_late_audio():
    mailbox = TextSpeechMailbox(timeout=0)
    with pytest.raises(SpeechFailure) as failure:
        mailbox.submit("你好", lambda *a, **k: pytest.fail("cancelled job must stay silent"))
    assert failure.value.details["error_code"] == "tts_timeout"
    request = mailbox.take()
    mailbox.process(request, lambda _: pytest.fail("no late synthesis"), lambda *a: pytest.fail("no late playback"))
    assert request.done.is_set()


def test_cancellation_during_synthesis_prevents_playback():
    request = SpeechRequest("你好", lambda *a, **k: None)
    def render(text):
        request.cancel.set()
        return [1], 24000
    TextSpeechMailbox().process(request, render, lambda *a, **k: pytest.fail("cancelled audio"))
    assert request.done.is_set()
