import json
import urllib.request
import urllib.error

from control_server import create_control_server, start_control_server
from event_bus import VoiceEventBus
from intent_evaluation import IntentEvaluation
from ambient_intent import IntentDecision
from text_speech import TextSpeechMailbox, SpeechRequest
from test_text_conversation import make_chat, turn, verdict


def request(server, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=data,
                                 headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_control_routes_stop_and_shadow_evaluate_without_any_device_authority():
    calls = []
    class Classifier:
        def classify(self, text, **kwargs): return IntentDecision("ignore")
    server = create_control_server(VoiceEventBus(), port=0,
        on_stop=lambda: calls.append("stop") or {"ok":True}, evaluation=IntentEvaluation(Classifier))
    start_control_server(server)
    try:
        assert request(server, "/conversation/stop", {})[0] == 200
        assert calls == ["stop"]
        assert request(server, "/conversation/stop", {"device":"ac"})[0] == 422
        assert request(server, "/intent/evaluation/start", {"text":"不要开空调"})[0] == 202
        assert request(server, "/intent/evaluation")[1]["total"] == 1
        assert request(server, "/intent/evaluation/cancel", {})[0] == 202
        assert request(server, "/intent/evaluation/start", {"limit":999})[0] == 422
    finally:
        server.shutdown(); server.server_close()


def test_mailbox_stop_cancels_active_and_queued_speech():
    mailbox = TextSpeechMailbox()
    active = SpeechRequest("正在说", lambda *a, **k: None)
    queued = SpeechRequest("排队", lambda *a, **k: None)
    mailbox.active = active; mailbox.pending.put(queued)
    mailbox.stop()
    assert active.cancel.is_set() and queued.cancel.is_set()


def test_typing_cancel_token_is_shared_even_if_stop_arrives_before_enqueue():
    import threading
    mailbox = TextSpeechMailbox(timeout=0)
    cancel = threading.Event()
    cancel.set()
    from text_speech import SpeechFailure
    import pytest
    with pytest.raises(SpeechFailure):
        mailbox.submit("旧回复", lambda *a, **k:None, cancel=cancel)
    job = mailbox.take()
    assert job.cancel is cancel and job.cancel.is_set()


def test_stage_events_do_not_overwrite_mic_state_and_include_playback_timing():
    chat, _, _, _, bus = make_chat([verdict("comfort", "cooler")])
    def announce(text, publish, **options):
        publish("playback_state", source_event="tts_start")
        publish("playback_state", source_event="tts_end")
    chat.announce = announce
    turn(chat,bus,"好热啊",speak=True)
    stages = [event["payload"] for event in bus.history() if event["type"] == "voice_stage"]
    assert {event["stage"] for event in stages} == {"intent", "agent", "tts_synthesis", "playback"}
    assert all("state" not in event and event["input_mode"] == "text" for event in stages)
    assert bus.state == "listening"


def test_cancelling_the_intent_wait_prevents_any_agent_or_device_action():
    import threading
    import uuid
    from agent_client import AgentCancelled
    entered = threading.Event()
    chat, agent, executor, _, bus = make_chat([])
    class Classifier:
        def classify(self, text, cancel, **kwargs):
            entered.set()
            assert cancel.wait(1)
            raise AgentCancelled()
    chat.classifier_factory = lambda _: Classifier()
    subscriber = bus.subscribe()
    try:
        chat.submit({"text":"好热啊", "request_id":str(uuid.uuid4())})
        assert entered.wait(1)
        chat.cancel()
        while True:
            event = subscriber.get(timeout=2)
            if event["type"] == "text_turn" and event["payload"]["turn_status"] != "processing":
                assert event["payload"]["error_code"] == "cancelled"
                break
        assert not executor.calls and not any(event["type"] == "agent_reply" for event in bus.history())
    finally:
        bus.unsubscribe(subscriber)
