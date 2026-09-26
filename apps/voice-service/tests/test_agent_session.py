from __future__ import annotations

import json

import pytest

from agent_client import AgentError, ChatResponse, ToolCall
from agent_session import AgentSession, SYSTEM_PROMPT, parse_relative_ac_change
from agent_tools import ToolResult


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages_seen = []

    def chat(self, messages, tools):
        self.messages_seen.append([dict(message) for message in messages])
        outcome = self.responses.pop(0) if self.responses else ChatResponse(content="没有更多回复")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeExecutor:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def schemas(self):
        return [{"type": "function", "function": {"name": "set_light", "parameters": {}}}]

    def execute(self, name, arguments, operation_id=None):
        self.calls.append({"name": name, "arguments": arguments, "operation_id": operation_id})
        if self.results:
            return self.results.pop(0)
        return ToolResult(name=name, ok=True, phrase="已完成", operation_id=operation_id)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def write_call(call_id="c1", name="set_light", **arguments):
    payload = {"device_id": "living_room_light"}
    payload.update(arguments)
    return ToolCall(id=call_id, name=name, arguments=payload)


def session(client, executor, **kwargs):
    clock = kwargs.pop("clock", FakeClock())
    return AgentSession(client, executor, clock=clock, **kwargs), clock


def test_assistant_tool_calls_are_echoed_before_any_tool_result():
    """Regression: OpenAI-compatible APIs reject a `tool` message that has no
    preceding assistant `tool_calls`, which broke every real DeepSeek call."""
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call(on=True)]),
        ChatResponse(content="已打开。"),
    ])
    executor = FakeExecutor()
    agent, _ = session(client, executor)

    agent.handle("打开客厅灯")

    second_call = client.messages_seen[1]
    assistant_index = next(
        index for index, message in enumerate(second_call) if message.get("role") == "assistant"
    )
    tool_index = next(
        index for index, message in enumerate(second_call) if message.get("role") == "tool"
    )
    assert assistant_index < tool_index
    echoed = second_call[assistant_index]["tool_calls"][0]
    assert echoed["function"]["name"] == "set_light"
    assert json.loads(echoed["function"]["arguments"]) == {"device_id": "living_room_light", "on": True}
    assert echoed["id"] == "c1"


def test_single_tool_call_then_spoken_reply():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call(on=True)]),
        ChatResponse(content="已经为你打开客厅灯。"),
    ])
    executor = FakeExecutor([ToolResult(name="set_light", ok=True, phrase="已打开客厅灯", operation_id="op-1")])
    agent, _ = session(client, executor)

    reply = agent.handle("打开客厅灯")

    assert reply.ok
    assert reply.text == "已经为你打开客厅灯。"
    assert len(executor.calls) == 1
    # The session generates the operation id locally; the model never supplies one.
    assert executor.calls[0]["operation_id"].startswith("voice-")


def test_plain_chat_does_not_touch_any_device():
    client = FakeClient([ChatResponse(content="今天天气不错。")])
    executor = FakeExecutor()
    agent, _ = session(client, executor)

    reply = agent.handle("你好呀")

    assert reply.ok
    assert reply.text == "今天天气不错。"
    assert executor.calls == []


def test_spoken_relative_degrees_override_a_missing_model_amount():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[ToolCall(
            id="ac1", name="adjust_ac",
            arguments={"device_id": "bedroom_ac", "direction": "cooler"},
        )]),
        ChatResponse(content="完成"),
    ])
    executor = FakeExecutor()
    agent, _ = session(client, executor)
    agent.handle("再调低两度")
    assert executor.calls[0]["arguments"] == {
        "device_id": "bedroom_ac", "direction": "cooler", "degrees": 2.0,
    }


def test_relative_amount_override_rejects_corrections_and_negations():
    assert parse_relative_ac_change("刚才说调低两度，现在调高一度") is None
    assert parse_relative_ac_change("调低两度，再调高一度") is None
    assert parse_relative_ac_change("不要调高一度") is None
    assert parse_relative_ac_change("空调调高一度") == ("warmer", 1.0)


def test_unqualified_ac_change_uses_current_speaker_and_location_without_model():
    client = FakeClient([])
    executor = FakeExecutor([ToolResult(name="adjust_ac", ok=True, phrase="空调已调高一度")])
    executor.resolve_self_device = lambda name, person_id: ("bedroom_ac", "卧室") if (
        name, person_id) == ("adjust_ac", "dad") else None
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    reply = agent.handle("空调调高一度。")

    assert reply.ok
    assert "卧室" in reply.text
    assert executor.calls[0]["arguments"] == {
        "device_id": "bedroom_ac", "direction": "warmer", "degrees": 1.0,
    }
    assert client.messages_seen == []
    assert agent.speaker_person_id is None


def test_unknown_speaker_prompts_for_room_without_writing():
    from agent_tools import ToolArgumentError

    executor = FakeExecutor()

    def unresolved(name, person_id):
        raise ToolArgumentError("speaker_unknown", "请说出要控制的房间")

    executor.resolve_self_device = unresolved
    agent, _ = session(FakeClient([]), executor)
    reply = agent.handle("空调调高一度")
    assert not reply.ok
    assert reply.error_code == "speaker_unknown"
    assert "房间" in reply.text
    assert executor.calls == []


def test_explicit_room_ac_command_keeps_model_target():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[ToolCall(
            id="ac1", name="adjust_ac",
            arguments={"device_id": "living_room_ac", "direction": "warmer", "degrees": 1},
        )]),
        ChatResponse(content="客厅空调已调高一度"),
    ])
    executor = FakeExecutor()
    executor.resolve_self_device = lambda *_: (_ for _ in ()).throw(AssertionError("self resolver called"))
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    agent.handle("客厅空调调高一度")

    assert executor.calls[0]["arguments"]["device_id"] == "living_room_ac"


def test_model_self_target_resolves_locally_and_names_room():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[ToolCall(
            id="ac1", name="adjust_ac", arguments={"target": "self", "direction": "cooler"},
        )]),
        ChatResponse(content="已调低"),
    ])
    executor = FakeExecutor([ToolResult(name="adjust_ac", ok=True, phrase="已调低")])
    executor.resolve_self_device = lambda name, person_id: ("bedroom_ac", "卧室")
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    reply = agent.handle("有点热")

    assert executor.calls[0]["arguments"]["device_id"] == "bedroom_ac"
    assert "卧室" in reply.text


def test_relative_request_cannot_be_executed_as_absolute_set_ac():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[ToolCall(
            id="ac1", name="set_ac",
            arguments={"device_id": "bedroom_ac", "target_temp": 26},
        )]),
        ChatResponse(content="请再说一次"),
    ])
    executor = FakeExecutor()
    agent, _ = session(client, executor)
    reply = agent.handle("再调低两度")
    assert executor.calls[0]["name"] == "adjust_ac"
    assert executor.calls[0]["arguments"] == {
        "device_id": "bedroom_ac", "direction": "cooler", "degrees": 2.0,
    }
    assert reply.ok


def test_model_prose_is_discarded_when_a_write_failed():
    """The worst possible outcome is speaking a success the device never had."""
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call(on=True)]),
        ChatResponse(content="好的，客厅灯已经打开了！"),
    ])
    executor = FakeExecutor([
        ToolResult(name="set_light", ok=False, error_code="offline", message="客厅灯现在离线")
    ])
    agent, _ = session(client, executor)

    reply = agent.handle("打开客厅灯")

    assert reply.text == "客厅灯现在离线"
    assert "已经打开" not in reply.text


def test_timeout_wording_never_claims_success_or_failure():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call(brightness=50)]),
        ChatResponse(content="灯已经调到 50 了。"),
    ])
    executor = FakeExecutor([
        ToolResult(name="set_light", ok=False, error_code="confirmation_timeout", message="已提交请求，但未观察到目标状态")
    ])
    agent, _ = session(client, executor)

    reply = agent.handle("把客厅灯调到 50")

    assert "没有确认到设备状态" in reply.text
    assert "失败" not in reply.text
    assert "已经" not in reply.text


def test_repeated_identical_write_in_one_turn_reuses_the_operation_id():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call("c1", on=True), write_call("c2", on=True)]),
        ChatResponse(content="已打开。"),
    ])
    executor = FakeExecutor()
    agent, _ = session(client, executor)

    agent.handle("打开客厅灯，再打开客厅灯")

    assert len(executor.calls) == 2
    assert executor.calls[0]["operation_id"] == executor.calls[1]["operation_id"]


def test_distinct_writes_get_distinct_operation_ids():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call("c1", on=True), write_call("c2", brightness=30)]),
        ChatResponse(content="已处理。"),
    ])
    executor = FakeExecutor()
    agent, _ = session(client, executor)

    agent.handle("开灯并调到 30")

    assert executor.calls[0]["operation_id"] != executor.calls[1]["operation_id"]


def test_tool_loop_is_bounded_and_reports_honestly():
    endless = lambda: ChatResponse(content=None, tool_calls=[write_call(on=True)])  # noqa: E731
    client = FakeClient([endless() for _ in range(10)])
    executor = FakeExecutor()
    agent, _ = session(client, executor, max_tool_rounds=2)

    reply = agent.handle("打开客厅灯")

    assert reply.ok is False
    assert reply.error_code == "tool_loop_exceeded"
    assert len(executor.calls) == 2
    # 2 tool rounds plus the one final chance to answer in words.
    assert len(client.messages_seen) == 3


def test_deadline_stops_the_turn_before_any_further_tool_runs():
    clock = FakeClock()
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call(on=True)]),
        ChatResponse(content=None, tool_calls=[write_call(brightness=10)]),
    ])
    executor = FakeExecutor()
    agent, _ = session(client, executor, clock=clock, deadline_seconds=5.0)

    def advance(messages, tools):
        clock.now += 10.0
        return FakeClient.chat(agent.client, messages, tools)

    agent.client.chat = advance

    reply = agent.handle("打开客厅灯")

    assert reply.ok is False
    assert reply.error_code == "agent_timeout"
    # The first round already ran; the second tool call must never be reached.
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    "code",
    ["agent_auth_failed", "agent_unavailable", "agent_rejected", "agent_invalid_response"],
)
def test_llm_failures_produce_honest_text(code):
    client = FakeClient([AgentError(code)])
    executor = FakeExecutor()
    agent, _ = session(client, executor)

    reply = agent.handle("打开客厅灯")

    assert reply.ok is False
    assert reply.error_code == code
    assert reply.text
    assert executor.calls == []


def test_query_only_turn_falls_back_to_a_structured_summary():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[ToolCall(id="q1", name="query_room_status", arguments={"room": "客厅"})]),
        ChatResponse(content=""),
    ])
    executor = FakeExecutor([
        ToolResult(
            name="query_room_status",
            ok=True,
            data={"devices": [{"name": "客厅灯", "type": "light", "state": {"on": True, "brightness": 50}}]},
        )
    ])
    agent, _ = session(client, executor)

    reply = agent.handle("客厅怎么样")

    assert reply.ok
    assert "客厅灯" in reply.text
    assert "50" in reply.text


def test_empty_transcript_is_rejected_without_calling_the_model():
    client = FakeClient([ChatResponse(content="不该被调用")])
    executor = FakeExecutor()
    agent, _ = session(client, executor)

    reply = agent.handle("   ")

    assert reply.ok is False
    assert reply.error_code == "empty_transcript"
    assert client.messages_seen == []


def test_reset_clears_context_and_pending_target():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call(on=True)]),
        ChatResponse(content="已打开。"),
        ChatResponse(content="再说一次吧。"),
    ])
    executor = FakeExecutor()
    agent, _ = session(client, executor)

    agent.handle("打开客厅灯")
    assert len(agent.history) > 1

    agent.reset()

    assert agent.history == [{"role": "system", "content": SYSTEM_PROMPT}]
    agent.handle("好的")
    # The second turn must not carry the first turn's messages.
    assert not any(message.get("content") == "打开客厅灯" for message in client.messages_seen[-1])


def test_history_stays_bounded_across_many_turns():
    responses = [ChatResponse(content=f"回复 {index}") for index in range(20)]
    client = FakeClient(responses)
    executor = FakeExecutor()
    agent, _ = session(client, executor, max_messages=4)

    for index in range(20):
        agent.handle(f"第 {index} 句")

    assert len(agent.history) <= 5
    assert agent.history[0]["role"] == "system"


def test_trimming_never_leaves_a_dangling_tool_message():
    client = FakeClient([
        ChatResponse(content=None, tool_calls=[write_call(on=True)]),
        ChatResponse(content="已打开。"),
    ] + [ChatResponse(content="好的。") for _ in range(6)])
    executor = FakeExecutor()
    agent, _ = session(client, executor, max_messages=2)

    agent.handle("打开客厅灯")
    for index in range(6):
        agent.handle(f"闲聊 {index}")

    assert agent.history[0]["role"] == "system"
    assert agent.history[1].get("role") != "tool"


# ------------------------------------------------------- live camera context
def test_each_turn_folds_the_camera_view_into_the_system_prompt():
    """The model has no eyes, so without this it asks which room the user is in
    even though the local vision service already knows."""
    client = FakeClient([ChatResponse(content="我看到妈妈在客厅。")])
    executor = FakeExecutor()
    looked_up = []
    executor.scene_context = lambda: looked_up.append(1) or "摄像头实时观察：妈妈在客厅。"
    agent, _ = session(client, executor)

    agent.handle("我在哪")

    assert client.messages_seen[0][0]["role"] == "system"
    assert "妈妈在客厅" in client.messages_seen[0][0]["content"]
    assert len(looked_up) == 1


def test_the_view_is_reread_every_turn_instead_of_reused():
    client = FakeClient([ChatResponse(content="好。"), ChatResponse(content="好。")])
    executor = FakeExecutor()
    views = iter(["摄像头实时观察：妈妈在客厅。", "摄像头实时观察：妈妈在卧室。"])
    executor.scene_context = lambda: next(views)
    agent, _ = session(client, executor)

    agent.handle("我在哪")
    agent.handle("我在哪")

    assert "妈妈在客厅" in client.messages_seen[0][0]["content"]
    assert "妈妈在卧室" in client.messages_seen[1][0]["content"]


def test_a_failing_vision_lookup_still_answers_the_user():
    client = FakeClient([ChatResponse(content="我现在看不到你。")])
    executor = FakeExecutor()

    def broken():
        raise RuntimeError("vision service down")

    executor.scene_context = broken
    agent, _ = session(client, executor)

    reply = agent.handle("我在哪")

    assert reply.ok and reply.text == "我现在看不到你。"


def test_reset_drops_the_stale_view_from_the_next_prompt():
    client = FakeClient([ChatResponse(content="好。"), ChatResponse(content="好。")])
    executor = FakeExecutor()
    views = iter(["摄像头实时观察：妈妈在客厅。", "摄像头实时观察：当前画面里没有识别到已注册的家人。"])
    executor.scene_context = lambda: next(views)
    agent, _ = session(client, executor)

    agent.handle("我在哪")
    agent.reset()
    agent.handle("我在哪")

    assert "妈妈在客厅" not in client.messages_seen[1][0]["content"]
    assert "没有识别到已注册的家人" in client.messages_seen[1][0]["content"]


# --------------------------------------------------------------- speaker line
def test_a_speaker_line_is_injected_beside_the_camera_view():
    client = FakeClient([ChatResponse(content="好。")])
    executor = FakeExecutor()
    executor.scene_context = lambda: "摄像头实时观察：爸爸在卧室。"
    agent, _ = session(client, executor)

    agent.speaker_line = "声纹判定：说话人可能是爸爸（置信度 0.72）。"
    agent.handle("我在哪")

    system = client.messages_seen[0][0]["content"]
    assert "爸爸在卧室" in system
    assert "声纹判定：说话人可能是爸爸（置信度 0.72）。" in system


def test_a_speaker_line_never_survives_its_own_turn():
    """A verdict describes one sentence; carrying it forward would silently
    attribute the next speaker's words to the previous one."""
    client = FakeClient([ChatResponse(content="好。"), ChatResponse(content="好。")])
    agent, _ = session(client, FakeExecutor())

    agent.speaker_line = "声纹判定：说话人可能是爸爸（置信度 0.72）。"
    agent.handle("第一句")
    agent.handle("第二句")

    injected = "声纹判定：说话人可能是"
    assert injected in client.messages_seen[0][0]["content"]
    assert injected not in client.messages_seen[1][0]["content"]


def test_no_speaker_data_leaves_the_prompt_exactly_as_before():
    client = FakeClient([ChatResponse(content="好。")])
    agent, _ = session(client, FakeExecutor())

    agent.handle("打开客厅灯")

    # The base prompt mentions 声纹 in its instructions, so this must match the
    # injected verdict's own wording rather than the topic.
    assert "声纹判定：说话人可能是" not in client.messages_seen[0][0]["content"]


def test_the_prompt_makes_the_voice_denial_conditional_on_having_a_verdict():
    """Regression, from a real conversation: the model answered

        "我没法靠声音认出说话的是谁，只能听出声音有点像爸爸"

    because the pre-voiceprint rule ("say you cannot recognise a speaker by voice")
    was still unconditional while a verdict was being injected at the same time.
    """
    assert "绝不要在同一句话里既声称无法辨认声音" in SYSTEM_PROMPT
    # The denial must be tied to the absence of a verdict, not stated outright.
    assert "上下文里没有“声纹判定”时，必须如实说明你无法通过声音辨认说话人" in SYSTEM_PROMPT


def test_the_prompt_still_forbids_deriving_a_room_from_a_voice():
    assert "不要用声纹推出的身份去猜房间" in SYSTEM_PROMPT


def test_the_system_prompt_forbids_deriving_a_room_from_the_speaker():
    """Identity and location are separate evidence; the prompt must say so."""
    prompt = SYSTEM_PROMPT

    assert "位置一律以摄像头为准" in prompt
    assert "不要用声纹推出的身份去猜房间" in prompt


# ---------------------------------------------------------------- streaming
class StreamingClient(FakeClient):
    """Records whether the streaming transport was used at all."""

    def __init__(self, responses):
        super().__init__(responses)
        self.streamed = []

    def chat_stream(self, messages, tools, on_delta):
        self.messages_seen.append([dict(message) for message in messages])
        outcome = self.responses.pop(0) if self.responses else ChatResponse(content="没有更多回复")
        self.streamed.append(on_delta)
        on_delta(outcome.content or "")
        return outcome


def test_deltas_are_streamed_only_when_someone_is_watching():
    client = StreamingClient([ChatResponse(content="我看到爸爸在卧室。"), ChatResponse(content="我看到爸爸在卧室。")])
    agent, _ = session(client, FakeExecutor())
    seen = []

    # Without a listener the plain transport is used, so headless runs and the
    # existing tests are unaffected by streaming.
    assert agent.handle("我在哪").text == "我看到爸爸在卧室。"
    assert client.streamed == []

    agent.on_delta = seen.append
    assert agent.handle("我在哪").text == "我看到爸爸在卧室。"

    assert seen == ["我看到爸爸在卧室。"]
    assert client.streamed == [seen.append]


def test_a_client_without_streaming_still_answers():
    client = FakeClient([ChatResponse(content="好的。")])
    agent, _ = session(client, FakeExecutor())
    agent.on_delta = lambda text: None

    assert agent.handle("你好").text == "好的。"


# ------------------------------------------------------- speaker-bound target
def test_a_fresh_self_target_is_written_into_the_prompt():
    """The user's report: the assistant knew who and where they were and asked
    which room anyway. The resolved room is now part of the turn's context, so a
    plain "空调调高一度" can be executed instead of questioned."""
    client = FakeClient([ChatResponse(content="好。")])
    executor = FakeExecutor()
    executor.self_target_line = lambda person_id: f"本轮可信定位：说话人在卧室（{person_id}）。"
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    agent.handle("有点热")

    system = client.messages_seen[0][0]["content"]
    assert "本轮可信定位：说话人在卧室（dad）。" in system
    # It never survives its own turn, exactly like the camera view. (The base
    # prompt mentions the topic, so the injected wording is what is checked.)
    agent.speaker_person_id = None
    client.responses.append(ChatResponse(content="好。"))
    agent.handle("有点热")
    assert "本轮可信定位：说话人在卧室" not in client.messages_seen[1][0]["content"]


def test_no_confirmed_speaker_means_no_self_target_line():
    client = FakeClient([ChatResponse(content="好。")])
    executor = FakeExecutor()
    executor.self_target_line = lambda person_id: (_ for _ in ()).throw(
        AssertionError("self target asked without a speaker")
    )
    agent, _ = session(client, executor)

    agent.handle("有点热")

    assert "本轮可信定位：说话人在" not in client.messages_seen[0][0]["content"]


def test_a_failing_self_target_lookup_does_not_break_the_turn():
    client = FakeClient([ChatResponse(content="好。")])
    executor = FakeExecutor()
    executor.self_target_line = lambda person_id: (_ for _ in ()).throw(RuntimeError("down"))
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    assert agent.handle("有点热").ok


def test_the_prompt_forbids_asking_for_a_room_when_the_target_is_known():
    assert "用户没说房间就直接执行，不要再问是哪个房间" in SYSTEM_PROMPT


# ------------------------------------------------- room-less direct commands
def test_a_room_less_light_command_is_executed_without_the_model():
    client = FakeClient([])
    executor = FakeExecutor([ToolResult(name="set_light", ok=True, phrase="卧室灯已打开")])
    executor.resolve_self_device = lambda name, person_id: ("bedroom_light", "卧室")
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    reply = agent.handle("把灯打开")

    assert reply.ok and "卧室" in reply.text
    assert executor.calls[0]["name"] == "set_light"
    assert executor.calls[0]["arguments"] == {"device_id": "bedroom_light", "on": True}
    assert client.messages_seen == []


def test_a_room_less_ac_switch_is_executed_without_the_model():
    client = FakeClient([])
    executor = FakeExecutor([ToolResult(name="set_ac", ok=True, phrase="卧室空调已打开")])
    executor.resolve_self_device = lambda name, person_id: ("bedroom_ac", "卧室")
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    reply = agent.handle("关空调")

    assert reply.ok
    assert executor.calls[0]["arguments"] == {"device_id": "bedroom_ac", "on": False}
    assert client.messages_seen == []


def test_a_named_room_never_uses_the_local_fast_path():
    """The fast path may only run when the user left the room out."""
    client = FakeClient([ChatResponse(content="已经打开客厅灯。")])
    executor = FakeExecutor()
    executor.resolve_self_device = lambda *_: (_ for _ in ()).throw(
        AssertionError("the fast path hijacked an explicit room")
    )
    agent, _ = session(client, executor)
    agent.speaker_person_id = "dad"

    agent.handle("打开客厅灯")

    assert executor.calls == []
    assert client.messages_seen != []


def test_a_negated_command_never_takes_the_fast_path():
    client = FakeClient([ChatResponse(content="好，不动灯。")])
    executor = FakeExecutor()
    executor.resolve_self_device = lambda *_: (_ for _ in ()).throw(
        AssertionError("a negation was executed")
    )
    agent, _ = session(client, executor)

    agent.handle("不要开灯")

    assert executor.calls == []


def test_a_relative_temperature_phrase_still_needs_its_exact_shape():
    from agent_session import parse_direct_self_command

    direct = parse_direct_self_command("空调调高一度")
    assert direct is not None
    assert direct.tool == "adjust_ac"
    assert direct.arguments == {"target": "self", "direction": "warmer", "degrees": 1.0}
    # "调高一度" alone names no device, so it is the model's call, not a regex's.
    assert parse_direct_self_command("调高一度") is None
    # The negated relative form is handled by parse_relative_ac_change already.
    assert parse_direct_self_command("不要调高一度") is None


# ------------------------------------------------------ interruption (cancel)
def test_an_interrupted_turn_is_never_spoken():
    import threading

    from agent_client import AgentCancelled

    event = threading.Event()

    class InterruptingClient(FakeClient):
        def chat(self, messages, tools, cancel=None):
            self.messages_seen.append([dict(message) for message in messages])
            event.set()
            raise AgentCancelled()

    agent, _ = session(InterruptingClient([]), FakeExecutor())

    reply = agent.handle("打开客厅灯", cancel=event)

    assert reply.cancelled
    assert reply.text == ""
    assert reply.error_code == "cancelled"


def test_an_interrupted_turn_leaves_no_dangling_tool_call_behind():
    """A trailing assistant `tool_calls` with no tool result is rejected by the
    next API call, so the abandoned turn is removed instead of kept."""
    import threading

    event = threading.Event()

    class ToolThenCancelClient(FakeClient):
        def chat(self, messages, tools, cancel=None):
            self.messages_seen.append([dict(message) for message in messages])
            return ChatResponse(content=None, tool_calls=[write_call(on=True)])

    class InterruptingExecutor(FakeExecutor):
        def execute(self, name, arguments, operation_id=None):
            result = super().execute(name, arguments, operation_id)
            event.set()  # the user started a new sentence during the write
            return result

    agent, _ = session(ToolThenCancelClient([]), InterruptingExecutor())
    agent.speaker_person_id = None

    reply = agent.handle("打开客厅灯", cancel=event)

    assert reply.cancelled
    assert [message["role"] for message in agent.history] == ["system"]


def test_a_cancel_event_that_is_already_set_never_reaches_the_client():
    import threading

    client = FakeClient([ChatResponse(content="不该发出")])
    executor = FakeExecutor()
    agent, _ = session(client, executor)
    event = threading.Event()
    event.set()

    reply = agent.handle("打开客厅灯", cancel=event)

    assert reply.cancelled
    assert client.messages_seen == []
    assert executor.calls == []


def test_a_client_that_does_not_accept_cancel_is_still_asked():
    """Test doubles and older wiring keep the two-argument contract."""
    client = FakeClient([ChatResponse(content="好的。")])
    agent, _ = session(client, FakeExecutor())

    assert agent.handle("你好", cancel=None).text == "好的。"
