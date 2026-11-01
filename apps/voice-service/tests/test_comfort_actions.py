"""Actual local policy and session dispatch over a controlled device boundary."""
import threading

import pytest

from agent_client import AgentCancelled, ChatResponse, ToolCall
from agent_session import AgentSession
from agent_tools import ToolArgumentError, ToolResult
from ambient_intent import IntentDecision
from comfort_actions import ComfortActions


class Executor:
    def __init__(self):
        self.calls = []
        self.state = {"on": True, "brightness": 50}
        self.online = True
        self.failure = None
        self.after_query = lambda: None

    def catalog(self):
        return [{"id": "bedroom_" + kind, "name": "卧室设备", "type": kind,
                 "room_id": "bedroom", "room_name": "卧室", "controllable": True}
                for kind in ("ac", "light")]

    def resolve_self_device(self, tool, speaker_id):
        if speaker_id != "dad":
            raise ToolArgumentError("speaker_unknown", "请告诉我是哪个房间？")
        return "bedroom_" + ("ac" if tool == "adjust_ac" else "light"), "卧室"

    def execute(self, name, arguments, operation_id=None):
        self.calls.append((name, arguments, operation_id))
        if name == "query_device_status":
            self.after_query()
            return ToolResult(name, True, data={"devices": [{"id": "bedroom_light",
                "online": self.online, "state": self.state}]})
        if self.failure:
            return ToolResult(name, False, error_code=self.failure, message="设备没有响应")
        return ToolResult(name, True, phrase="已确认设备设置", operation_id=operation_id)


@pytest.mark.parametrize("need", ["cooler", "warmer"])
def test_comfort_uses_backend_step_and_actual_confirmation(need):
    executor = Executor()
    outcome = ComfortActions(executor).execute(need, None, "dad")
    assert outcome.ok and "卧室" in outcome.text
    name, args, operation_id = executor.calls[0]
    assert name == "adjust_ac" and args == {"device_id": "bedroom_ac", "direction": need}
    assert operation_id.startswith("voice-")
    assert "已确认" in outcome.text


def test_unknown_speaker_asks_room_then_room_answer_executes_without_second_model_call():
    executor = Executor()
    session = AgentSession(object(), executor)
    reply = session.handle_admitted("天气好热", IntentDecision("comfort", "cooler"))
    assert not reply.ok and "房间" in reply.text and executor.calls == []
    reply = session.handle_admitted("卧室", IntentDecision("follow_up", room="卧室"))
    assert reply.ok and executor.calls[0][0] == "adjust_ac"


@pytest.mark.parametrize("text", ["别降温了，改为关闭卧室灯", "卧室，不过不要降温了", "不是卧室"])
def test_room_in_correction_does_not_execute_pending_comfort(text):
    executor = Executor()
    executor.schemas = lambda: []

    class Chat:
        def chat(self, messages, tools):
            return ChatResponse("好的，已取消先前的降温请求。")

    session = AgentSession(Chat(), executor)
    session.handle_admitted("好热啊", IntentDecision("comfort", "cooler"))
    reply = session.handle_admitted(text, IntentDecision("follow_up", room="卧室"))
    assert reply.ok
    assert executor.calls == []
    assert session.comfort.pending_need(None) is None


@pytest.mark.parametrize("text", ["卧室", "是卧室", "在卧室", "我在卧室。", "卧室吧"])
def test_pure_room_answer_still_completes_pending_comfort(text):
    executor = Executor()
    session = AgentSession(object(), executor)
    session.handle_admitted("好热啊", IntentDecision("comfort", "cooler"))
    reply = session.handle_admitted(text, IntentDecision("follow_up", room="卧室"))
    assert reply.ok
    assert executor.calls[0][:2] == ("adjust_ac", {"device_id": "bedroom_ac", "direction": "cooler"})


def test_replacement_light_command_runs_instead_of_pending_cooling():
    executor = Executor()
    executor.schemas = lambda: []

    class Chat:
        responses = iter([
            ChatResponse(None, [ToolCall("off", "set_light", {"device_id": "bedroom_light", "on": False})]),
            ChatResponse("已关闭卧室灯"),
        ])
        def chat(self, messages, tools):
            return next(self.responses)

    session = AgentSession(Chat(), executor)
    session.handle_admitted("好热啊", IntentDecision("comfort", "cooler"))
    session.handle_admitted("别降温了，改为关闭卧室灯", IntentDecision("follow_up", room="卧室"))
    assert [(name, args) for name, args, _ in executor.calls] == [
        ("set_light", {"device_id": "bedroom_light", "on": False})]


def test_cooldown_survives_session_reset_and_expires():
    now = [0]
    executor = Executor()
    actions = ComfortActions(executor, clock=lambda: now[0])
    assert actions.execute("cooler", None, "dad").ok
    actions.reset()
    assert actions.execute("cooler", None, "dad").ignored
    assert len(executor.calls) == 1
    now[0] = 61
    assert not actions.execute("cooler", None, "dad").ignored
    assert len(executor.calls) == 2


def test_failed_confirmation_is_not_spoken_as_success():
    executor = Executor()
    executor.failure = "confirmation_timeout"
    outcome = ComfortActions(executor).execute("cooler", "卧室", None)
    assert not outcome.ok and "没有确认" in outcome.text and "已确认" not in outcome.text


@pytest.mark.parametrize("need,on,brightness,target", [
    ("brighter", False, None, 70), ("brighter", True, 95, 100),
    ("dimmer", True, 5, 1), ("dimmer", True, 50, 30),
])
def test_light_adjustment_reads_current_state_and_clamps(need, on, brightness, target):
    executor = Executor()
    executor.state = {"on": on, "brightness": brightness}
    assert ComfortActions(executor).execute(need, "卧室", None).ok
    assert executor.calls[-1][1] == {"device_id": "bedroom_light", "on": True, "brightness": target}


@pytest.mark.parametrize("state,online", [({"on": True}, True), ({"on": True, "brightness": 40}, False)])
def test_light_with_unknown_state_or_offline_never_writes(state, online):
    executor = Executor()
    executor.state, executor.online = state, online
    assert not ComfortActions(executor).execute("brighter", "卧室", None).ok
    assert [c[0] for c in executor.calls] == ["query_device_status"]


def test_cancel_after_state_query_prevents_write():
    executor = Executor()
    cancel = threading.Event()
    executor.after_query = cancel.set
    actions = ComfortActions(executor)
    with pytest.raises(AgentCancelled):
        actions.execute("brighter", "卧室", None, cancel)
    assert [c[0] for c in executor.calls] == ["query_device_status"]
    executor.after_query = lambda: None
    assert not actions.execute("brighter", "卧室", None).ignored


def test_same_complaint_does_not_bypass_cooldown_when_voiceprint_is_short():
    executor = Executor()
    actions = ComfortActions(executor)
    actions.execute("cooler", "卧室", "dad")
    assert actions.execute("cooler", "卧室", None).ignored
    assert len(executor.calls) == 1


def test_pending_room_answer_can_be_short_without_reusing_identity():
    executor = Executor()
    def no_location(*args):
        raise ToolArgumentError("location_stale", "请告诉我是哪个房间？")
    executor.resolve_self_device = no_location
    session = AgentSession(object(), executor)
    session.speaker_person_id = "dad"
    assert not session.handle_admitted("天气好热", IntentDecision("comfort", "cooler")).ok
    # "卧室" may be too short for a voiceprint. Its explicit room still allows
    # the pending comfort action; it grants no inferred self-target identity.
    reply = session.handle_admitted("卧室", IntentDecision("follow_up", room="卧室"))
    assert reply.ok and executor.calls[0][1]["device_id"] == "bedroom_ac"


def test_different_known_speaker_cannot_inherit_pending_comfort():
    actions = ComfortActions(Executor())
    actions.execute("cooler", None, "unknown_a")
    assert actions.pending_need("dad") is None


def test_pending_unknown_complaint_accepts_newly_identified_explicit_room_answer():
    executor = Executor()
    session = AgentSession(object(), executor)
    session.handle_admitted("好热啊", IntentDecision("comfort", "cooler"))
    session.speaker_person_id = "dad"
    reply = session.handle_admitted("卧室", IntentDecision("follow_up", room="卧室"))
    assert reply.ok and executor.calls[0][0] == "adjust_ac"


@pytest.mark.parametrize("phase", ["before_dispatch", "during_lookup"])
def test_cancelled_explicit_command_never_reaches_device(phase):
    executor = Executor()
    session = AgentSession(object(), executor)
    session.speaker_person_id = "dad"
    cancel = threading.Event()
    if phase == "before_dispatch":
        cancel.set()
    else:
        resolve = executor.resolve_self_device
        def lookup(*args):
            cancel.set()
            return resolve(*args)
        executor.resolve_self_device = lookup
    reply = session.handle_admitted("开灯", IntentDecision("command"), cancel=cancel)
    assert reply.cancelled and not executor.calls


def test_explicit_followup_can_adjust_again_and_keeps_confirmed_room_context():
    executor = Executor()
    executor.schemas = lambda: [{"type": "function", "function": {"name": "adjust_ac"}}]
    class Chat:
        def __init__(self):
            self.seen = []
        def chat(self, messages, tools):
            self.seen.append(list(messages))
            if len(self.seen) == 1:
                return ChatResponse(None, [ToolCall("followup", "adjust_ac", {
                    "device_id": "bedroom_ac", "direction": "cooler"})])
            return ChatResponse("模型自由生成的完成说明")
    client = Chat()
    session = AgentSession(client, executor)
    first = session.handle_admitted("卧室好热", IntentDecision("comfort", "cooler", "卧室"))
    assert first.ok and not client.seen
    second = session.handle_admitted("再低一点", IntentDecision("follow_up"))
    assert second.ok and len(executor.calls) == 2
    assert [call[0] for call in executor.calls] == ["adjust_ac", "adjust_ac"]
    assert any("卧室" in str(item.get("content")) for item in client.seen[0])
    assert second.text == "已确认设备设置"  # Device result owns the spoken success.


@pytest.mark.parametrize("state", [[], "invalid", 3])
def test_malformed_light_state_fails_closed(state):
    executor = Executor()
    executor.state = state
    outcome = ComfortActions(executor).execute("brighter", "卧室", None)
    assert not outcome.ok
    assert len(executor.calls) == 1
