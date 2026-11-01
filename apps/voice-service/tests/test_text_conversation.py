"""Typed final transcripts: real classifier parsing and local action dispatch,
with only API transport and the home-service device boundary replaced.
"""
import json
import threading
import time
import uuid

import pytest

from agent_client import ChatResponse
from agent_session import AgentReply, AgentSession
from agent_tools import ToolArgumentError, ToolResult
from ambient_intent import IntentClassifier
from event_bus import VoiceEventBus
from text_conversation import TextConversation


class Executor:
    def __init__(self):
        self.calls = []

    def catalog(self):
        return [{"id": "bedroom_ac", "name": "卧室空调", "type": "ac",
                 "room_id": "bedroom", "room_name": "卧室", "controllable": True}]

    def resolve_self_device(self, tool, speaker_id):
        assert speaker_id is None  # typed text never grants a voiceprint identity
        raise ToolArgumentError("speaker_unknown", "请告诉我是哪个房间？")

    def execute(self, name, arguments, operation_id=None):
        self.calls.append((name, arguments))
        return ToolResult(name, True, phrase="卧室空调已确认降温", operation_id=operation_id)


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def chat_stream(self, messages, tools=None, cancel=None):
        assert tools is None
        self.requests.append(json.loads(messages[1]["content"]))
        value = next(self.responses)
        return ChatResponse(json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value, [])


def verdict(kind, need=None, room=None):
    return {"kind": kind, "need": need, "room": room}


def make_chat(responses, now=None):
    transport = Transport(responses)
    executor = Executor()
    clock = (lambda: now[0]) if now is not None else time.monotonic
    agent = AgentSession(object(), executor, clock=clock)
    bus = VoiceEventBus()
    bus.state = "listening"
    chat = TextConversation(lambda: agent, bus,
                            classifier_factory=lambda _: IntentClassifier(transport), clock=clock)
    return chat, agent, executor, transport, bus


def turn(chat, bus, text, request_id=None, speak=False):
    subscriber = bus.subscribe()
    try:
        request_id = request_id or str(uuid.uuid4())
        result = chat.submit({"request_id": request_id, "text": text, "speak": speak})
        assert result["ok"]
        while True:
            event = subscriber.get(timeout=3)
            if (event["type"] == "text_turn" and event["payload"]["request_id"] == request_id
                    and event["payload"]["turn_status"] != "processing"):
                return event["payload"]
    finally:
        bus.unsubscribe(subscriber)


def test_complaint_room_answer_and_end_use_real_dispatch_without_audio():
    chat, agent, executor, transport, bus = make_chat([
        verdict("comfort", "cooler"), verdict("follow_up", room="卧室"), verdict("end"), verdict("ignore"),
    ])
    assert turn(chat, bus, "好热啊")["turn_status"] == "completed"
    replies = [event["payload"]["text"] for event in bus.history() if event["type"] == "agent_reply"]
    assert "房间" in replies[-1] and executor.calls == []
    assert turn(chat, bus, "卧室")["turn_status"] == "completed"
    assert executor.calls == [("adjust_ac", {"device_id": "bedroom_ac", "direction": "cooler"})]
    assert transport.requests[1]["active"] and transport.requests[1]["context"][-1]["role"] == "assistant"
    turn(chat, bus, "这样就行")
    assert not chat.active
    turn(chat, bus, "昨天在外面太热了")
    assert not transport.requests[-1]["active"]
    assert bus.state == "listening"
    assert not any(event["type"] == "playback_state" for event in bus.history())
    assert all(event["payload"]["input_mode"] == "text" for event in bus.history())


@pytest.mark.parametrize("response,status", [(verdict("ignore"), "ignored"), ("bad JSON", "failed")])
def test_ignore_and_invalid_intent_do_not_enter_agent(response, status):
    chat, agent, executor, _, bus = make_chat([response])
    agent.handle_admitted = lambda *args: pytest.fail("must not enter Agent")
    result = turn(chat, bus, "别开空调")
    assert result["turn_status"] == status
    assert executor.calls == []
    assert not [event for event in bus.history() if event["type"] == "agent_reply"]


def test_repeated_request_id_does_not_repeat_an_actual_device_write():
    chat, _, executor, _, bus = make_chat([verdict("comfort", "cooler", "卧室")])
    request_id = str(uuid.uuid4())
    turn(chat, bus, "卧室好热", request_id)
    result = chat.submit({"text": "卧室好热", "request_id": request_id})
    assert result["duplicate"] and len(executor.calls) == 1
    assert not chat.submit({"text": "卧室太冷", "request_id": request_id})["ok"]


def test_cooldown_and_idle_expiry_match_voice_policies():
    now = [1]
    chat, _, executor, transport, bus = make_chat([
        verdict("comfort", "cooler", "卧室"), verdict("comfort", "cooler", "卧室"), verdict("ignore"),
    ], now)
    turn(chat, bus, "卧室好热")
    assert turn(chat, bus, "卧室还是好热")["turn_status"] == "ignored"
    assert len(executor.calls) == 1
    now[0] = 62
    turn(chat, bus, "卧室")
    assert not transport.requests[-1]["active"] and transport.requests[-1]["context"] == []


def test_busy_turn_is_rejected_and_errors_allow_the_next_turn():
    entered, release = threading.Event(), threading.Event()
    bus = VoiceEventBus()

    def factory():
        entered.set()
        release.wait(3)
        raise RuntimeError("secret provider details must never be published")

    chat = TextConversation(factory, bus)
    subscriber = bus.subscribe()
    try:
        assert chat.submit({"text": "热", "request_id": str(uuid.uuid4())})["ok"]
        assert entered.wait(2)
        assert chat.submit({"text": "冷", "request_id": str(uuid.uuid4())})["error_code"] == "text_busy"
        release.set()
        while subscriber.get(timeout=3)["payload"].get("turn_status") != "failed":
            pass
        assert not chat._busy
        assert "secret provider" not in json.dumps(bus.history())
    finally:
        release.set()
        bus.unsubscribe(subscriber)


def test_explicit_wake_keeps_original_agent_path_when_classifier_is_unavailable():
    chat, agent, _, _, bus = make_chat([])
    chat.strip_wake = lambda text: text.removeprefix("你好小屋，")
    chat.classifier_factory = lambda _: pytest.fail("wake bypasses intent")
    received = []

    def handle(text, cancel=None):
        received.append(text)
        return AgentReply("你好", True)

    agent.handle = handle
    turn(chat, bus, "你好小屋，打开卧室灯")
    assert received == ["打开卧室灯"]


def test_punctuation_does_not_bypass_intent():
    chat, agent, _, transport, bus = make_chat([verdict("ignore")])
    agent.handle = lambda *args: pytest.fail("punctuation is not a wake phrase")
    turn(chat, bus, "，昨天好热")
    assert transport.requests[0]["utterance"] == "，昨天好热"


def test_optional_speech_and_duplicate_requests_never_repeat_device_actions():
    chat, _, executor, transport, bus = make_chat([verdict("comfort", "cooler", "卧室")])
    announced = []
    chat.announce = lambda text, publish, **options: announced.append(text)
    request_id = str(uuid.uuid4())
    turn(chat, bus, "卧室好热", request_id, speak=True)
    assert len(announced) == len(executor.calls) == len(transport.requests) == 1
    assert chat.submit({"request_id": request_id, "text": "卧室好热", "speak": True})["duplicate"]
    assert chat.submit({"request_id": request_id, "text": "卧室好热", "speak": False})["error_code"] == "request_conflict"
    assert len(announced) == len(executor.calls) == 1


def test_announcement_failure_keeps_the_room_question_and_text_reply():
    from text_speech import SpeechFailure
    chat, _, executor, _, bus = make_chat([verdict("comfort", "cooler"), verdict("follow_up", room="卧室")])
    def fail(text, publish, **options):
        raise SpeechFailure({"stage": "tts_synthesis", "error_code": "tts_synthesis_failed",
                             "error_type": "RuntimeError", "message": "语音合成失败，回复文字仍可查看。"})
    chat.announce = fail
    result = turn(chat, bus, "好热啊", speak=True)
    assert result["turn_status"] == "completed" and result["error_code"] == "tts_synthesis_failed"
    assert chat.active and executor.calls == []
    error = next(event for event in bus.history() if event["type"] == "service_error")
    assert error["payload"]["input_mode"] == "text" and error["payload"]["stage"] == "tts_synthesis"
    turn(chat, bus, "卧室", speak=False)
    assert executor.calls == [("adjust_ac", {"device_id": "bedroom_ac", "direction": "cooler"})]


def test_muted_turn_never_queues_audio():
    chat, _, _, _, bus = make_chat([verdict("comfort", "cooler")])
    chat.announce = lambda *a: pytest.fail("muted turns stay silent")
    assert turn(chat, bus, "好热啊", speak=False)["turn_status"] == "completed"
