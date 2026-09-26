from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from agent_client import AgentClient, AgentError
from agent_tools import (
    FORBIDDEN_TARGET_REASONS,
    HOME_TOOL_NAMES,
    READ_TOOLS,
    SCENE_TOOL,
    SESSION_TOOLS,
    TARGET_TIERS,
    TIER_EXPLICIT_ONLY,
    TIER_FORBIDDEN,
    TIER_NOT_A_HOUSE_ACTION,
    TIER_REVERSIBLE_SELF,
    WRITE_TOOLS,
    HomeToolExecutor,
    ToolArgumentError,
    build_tool_schemas,
    may_use_self_target,
    normalize_arguments,
    summarize_queries,
    target_tier,
    SELF_LOCATION_MAX_AGE_MS,
)

SECRET = "sk-super-secret-key-value"

CATALOG = [
    {"id": "living_room_light", "type": "light", "name": "客厅灯", "room_name": "客厅", "controllable": True},
    {"id": "bedroom_ac", "type": "ac", "name": "卧室空调", "room_name": "卧室", "controllable": True},
    {"id": "desk_plug", "type": "switch", "name": "智能插座", "room_name": "客厅", "controllable": True},
    {"id": "indoor_temperature", "type": "sensor", "name": "室内温度", "room_name": "客厅", "controllable": False},
]


class ScriptedOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else {}
        if isinstance(outcome, BaseException):
            raise outcome
        # A ready-made response object (an SSE body) is used as-is; a plain
        # payload is wrapped so callers can keep passing dicts.
        return outcome if hasattr(outcome, "__enter__") else JsonResponse(outcome)


class JsonResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()

    def __iter__(self):
        return iter([])


def chat_response(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


# --------------------------------------------------------------------- client
def test_client_parses_content_and_tool_calls():
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener(
        [chat_response(None, [tool_call("c1", "set_light", {"device_id": "living_room_light", "on": True})])]
    )

    response = client.chat([{"role": "user", "content": "开灯"}], [])

    assert response.wants_tools
    assert response.tool_calls[0].name == "set_light"


def test_client_requires_an_api_key():
    with pytest.raises(ValueError, match="API key"):
        AgentClient("   ")


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (HTTPError("u", 401, "nope", {}, None), "agent_auth_failed"),
        (HTTPError("u", 403, "nope", {}, None), "agent_auth_failed"),
        (HTTPError("u", 400, "bad", {}, None), "agent_rejected"),
        (HTTPError("u", 503, "down", {}, None), "agent_unavailable"),
        (URLError("offline"), "agent_unavailable"),
        (TimeoutError(), "agent_unavailable"),
    ],
)
def test_client_maps_transport_failures_to_closed_codes(outcome, expected):
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener([outcome])

    with pytest.raises(AgentError) as caught:
        client.chat([{"role": "user", "content": "hi"}], [])

    assert caught.value.code == expected
    assert SECRET not in repr(caught.value)


# ------------------------------------------------------------------ streaming
class StreamResponse:
    """Minimal SSE body: iterating it yields one raw line per frame."""

    status = 200

    def __init__(self, frames):
        self.frames = frames

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter([
            f"data: {json.dumps(frame)}\n".encode() if isinstance(frame, dict) else str(frame).encode()
            for frame in self.frames
        ])


def stream_frame(content=None, tool_calls=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"choices": [{"delta": delta}]}


def streamed_body(client):
    return json.loads(client.opener.requests[0].data.decode())


def test_streaming_reports_the_sentence_while_it_is_written():
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener([StreamResponse([stream_frame("我看到"), stream_frame("爸爸在卧室"), "data: [DONE]"])])
    seen = []

    response = client.chat_stream([{"role": "user", "content": "我在哪"}], [], seen.append)

    assert response.content == "我看到爸爸在卧室"
    # Accumulated text, not fragments: the caller owns no buffering.
    assert seen == ["我看到", "我看到爸爸在卧室"]


def test_streaming_asks_the_api_to_stream():
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener([StreamResponse(["data: [DONE]"])])

    client.chat_stream([{"role": "user", "content": "hi"}], [])

    assert streamed_body(client)["stream"] is True


def test_the_plain_chat_call_still_does_not_stream():
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener([chat_response("好")])

    client.chat([{"role": "user", "content": "hi"}], [])

    assert "stream" not in streamed_body(client)


def test_streaming_reassembles_a_fragmented_tool_call():
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener([
        StreamResponse([
            stream_frame(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "set_light", "arguments": '{"device_'}}]),
            stream_frame(tool_calls=[{"index": 0, "function": {"arguments": 'id": "living_room_light", "on": true}'}}]),
            "data: [DONE]",
        ])
    ])

    response = client.chat_stream([{"role": "user", "content": "开灯"}], [])

    assert response.tool_calls[0].id == "c1"
    assert response.tool_calls[0].name == "set_light"
    assert response.tool_calls[0].arguments == {"device_id": "living_room_light", "on": True}


def test_a_malformed_stream_frame_never_loses_the_answer():
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener([
        StreamResponse(["data: {not json", ": keepalive", stream_frame("好的"), "data: [DONE]"])
    ])

    assert client.chat_stream([{"role": "user", "content": "hi"}], []).content == "好的"


def test_streaming_uses_the_same_closed_failure_codes():
    client = AgentClient(SECRET, base_url="http://localhost:9")
    client.opener = ScriptedOpener([HTTPError("u", 401, "nope", {}, None)])

    with pytest.raises(AgentError) as caught:
        client.chat_stream([{"role": "user", "content": "hi"}], [])

    assert caught.value.code == "agent_auth_failed"
    assert SECRET not in repr(caught.value)


# ---------------------------------------------------- catalog-derived surface
def test_tool_surface_is_derived_from_the_catalog():
    schemas, by_kind = build_tool_schemas(CATALOG)
    names = [schema["function"]["name"] for schema in schemas]

    assert names == [
        "query_room_status",
        "query_device_status",
        "query_person_location",
        "set_light",
        "set_ac",
        "adjust_ac",
        "set_switch",
        "end_conversation",
    ]
    assert by_kind == {
        "set_light": ["living_room_light"],
        "set_ac": ["bedroom_ac"],
        "set_switch": ["desk_plug"],
    }
    # The read-only sensor is queryable but never controllable.
    assert "indoor_temperature" not in [item for ids in by_kind.values() for item in ids]
    # Ending the conversation is always available, scene list or not.
    assert "end_conversation" in names


def test_relative_ac_tool_preserves_explicit_degree_amount():
    schemas, by_kind = build_tool_schemas(CATALOG)
    schema = next(item["function"] for item in schemas if item["function"]["name"] == "adjust_ac")
    assert "degrees" in schema["parameters"]["properties"]
    assert normalize_arguments(
        "adjust_ac", {"device_id": "bedroom_ac", "direction": "cooler", "degrees": 2}, by_kind
    ) == {"device_id": "bedroom_ac", "direction": "cooler", "degrees": 2.0}


def test_self_target_schema_is_available_for_local_reversible_controls():
    schemas, _ = build_tool_schemas(CATALOG)
    for name in ("set_light", "set_ac", "adjust_ac"):
        schema = next(item["function"] for item in schemas if item["function"]["name"] == name)
        assert schema["parameters"]["properties"]["target"]["enum"] == ["self"]
        assert "device_id" not in schema["parameters"].get("required", [])
    switch = next(item["function"] for item in schemas if item["function"]["name"] == "set_switch")
    assert "target" not in switch["parameters"]["properties"]
    assert "device_id" in switch["parameters"]["required"]


def test_self_device_uses_exact_speaker_fresh_room_and_unique_catalog_device(monkeypatch):
    import time

    now_ms = int(time.time() * 1000)
    catalog = [
        {"id": "living_room_ac", "type": "ac", "room_id": "living_room", "controllable": True},
        {"id": "bedroom_ac", "type": "ac", "room_id": "bedroom", "room_name": "卧室", "controllable": True},
    ]
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=catalog)
    requests = []

    def fake_request(method, path, query, body):
        requests.append((method, path, query))
        return 200, {"ok": True, "data": {"persons": [
            {"id": "dad", "room_id": "bedroom", "room_name": "卧室",
             "location_known": True, "observed_at_ms": now_ms},
        ]}}

    monkeypatch.setattr(executor, "_request", fake_request)
    assert executor.resolve_self_device("adjust_ac", "dad") == ("bedroom_ac", "卧室")
    assert requests == [("GET", "/tool/person_location", {"person": "dad"})]


def test_self_device_rejects_unknown_or_stale_location(monkeypatch):
    import time

    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=[
        {"id": "bedroom_ac", "type": "ac", "room_id": "bedroom", "controllable": True},
    ])
    with pytest.raises(ToolArgumentError) as missing:
        executor.resolve_self_device("adjust_ac", None)
    assert missing.value.code == "speaker_unknown"

    old_ms = int(time.time() * 1000) - SELF_LOCATION_MAX_AGE_MS - 1
    monkeypatch.setattr(executor, "_request", lambda *args: (200, {"ok": True, "data": {"persons": [
        {"id": "dad", "room_id": "bedroom", "location_known": True, "observed_at_ms": old_ms},
    ]}}))
    with pytest.raises(ToolArgumentError) as stale:
        executor.resolve_self_device("adjust_ac", "dad")
    assert stale.value.code == "location_unknown"


def test_self_device_rejects_multiple_same_kind_devices(monkeypatch):
    import time

    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=[
        {"id": "bedroom_ac_1", "type": "ac", "room_id": "bedroom", "controllable": True},
        {"id": "bedroom_ac_2", "type": "ac", "room_id": "bedroom", "controllable": True},
    ])
    now_ms = int(time.time() * 1000)
    monkeypatch.setattr(executor, "_request", lambda *args: (200, {"ok": True, "data": {"persons": [
        {"id": "dad", "room_id": "bedroom", "room_name": "卧室", "location_known": True,
         "observed_at_ms": now_ms},
    ]}}))
    with pytest.raises(ToolArgumentError) as ambiguous:
        executor.resolve_self_device("adjust_ac", "dad")
    assert ambiguous.value.code == "device_ambiguous"


# ------------------------------------------------- what the model is told
def _fresh_person(room_id="bedroom", room_name="卧室", age_ms=800):
    import time

    return {"id": "dad", "room_id": room_id, "room_name": room_name,
            "location_known": True, "observed_at_ms": int(time.time() * 1000) - age_ms}


BEDROOM_CATALOG = [
    {"id": "bedroom_light", "type": "light", "name": "卧室灯", "room_id": "bedroom",
     "room_name": "卧室", "controllable": True},
    {"id": "bedroom_ac", "type": "ac", "name": "卧室空调", "room_id": "bedroom",
     "room_name": "卧室", "controllable": True},
    {"id": "bedroom_plug", "type": "switch", "name": "卧室插座", "room_id": "bedroom",
     "room_name": "卧室", "controllable": True},
    {"id": "living_room_light", "type": "light", "name": "客厅灯", "room_id": "living_room",
     "room_name": "客厅", "controllable": True},
]


def test_the_self_target_line_lets_the_model_act_instead_of_asking(monkeypatch):
    """This line is the whole difference between "which room?" and doing it: it
    states that the speaker's room is known and names that room's devices."""
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=BEDROOM_CATALOG)
    monkeypatch.setattr(executor, "_request", lambda *args: (
        200, {"ok": True, "data": {"persons": [_fresh_person()]}},
    ))

    line = executor.self_target_line("dad")

    assert line is not None
    assert "本轮可信定位" in line
    assert "卧室灯" in line and "卧室空调" in line
    assert "客厅灯" not in line
    assert "不要再问房间" in line
    # The socket is never offered as a self target: an outlet's load is unknown,
    # so that tier stays explicit-only.
    assert "插座" not in line


def test_the_self_target_line_says_why_it_cannot_resolve(monkeypatch):
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=BEDROOM_CATALOG)
    stale = _fresh_person(age_ms=SELF_LOCATION_MAX_AGE_MS + 1)
    monkeypatch.setattr(executor, "_request", lambda *args: (
        200, {"ok": True, "data": {"persons": [stale]}},
    ))

    line = executor.self_target_line("dad")

    assert line is not None
    assert "本轮无可信定位" in line
    # The reason has to name the missing observation, or the model cannot tell
    # "nobody is enrolled" from "the camera has not seen you for a while".
    assert f"{SELF_LOCATION_MAX_AGE_MS / 1000:g} 秒" in line
    assert "必须先问清房间" in line


def test_no_speaker_means_no_self_target_line_at_all(monkeypatch):
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=BEDROOM_CATALOG)
    monkeypatch.setattr(executor, "_request", lambda *args: (_ for _ in ()).throw(
        AssertionError("a location was queried without a speaker")
    ))

    assert executor.self_target_line(None) is None


def test_a_new_device_in_the_catalog_becomes_controllable_without_code_changes():
    """This is the property that made per-device work unnecessary."""
    extended = CATALOG + [
        {"id": "kitchen_light", "type": "light", "name": "厨房灯", "room_name": "厨房", "controllable": True}
    ]

    _, by_kind = build_tool_schemas(extended)

    assert sorted(by_kind["set_light"]) == ["kitchen_light", "living_room_light"]


def test_control_tools_are_omitted_when_no_such_device_exists():
    sensor_only = [device for device in CATALOG if device["type"] == "sensor"]

    _, by_kind = build_tool_schemas(sensor_only)

    assert by_kind == {}


def test_every_control_kind_maps_to_a_declared_tool_name():
    assert set(HOME_TOOL_NAMES) == {
        "query_room_status",
        "query_device_status",
        "query_person_location",
        "set_light",
        "set_ac",
        "set_switch",
        "run_scene",
        "adjust_ac",
        "end_conversation",
    }


# ---------------------------------------------------------------- validation
def test_device_must_come_from_the_catalog():
    _, by_kind = build_tool_schemas(CATALOG)

    with pytest.raises(ToolArgumentError):
        normalize_arguments("set_light", {"device_id": "garage_door", "on": True}, by_kind)
    with pytest.raises(ToolArgumentError):
        # A real device, but the wrong tool for its type.
        normalize_arguments("set_switch", {"device_id": "living_room_light", "on": True}, by_kind)


@pytest.mark.parametrize(
    "arguments",
    [
        {"device_id": "living_room_light", "brightness": 101},
        {"device_id": "living_room_light", "brightness": True},
        {"device_id": "living_room_light", "brightness": 50.5},
        {"device_id": "living_room_light", "on": "true"},
        {"device_id": "living_room_light"},
        {"device_id": "living_room_light", "on": True, "turbo": 1},
    ],
)
def test_light_arguments_are_validated_locally(arguments):
    _, by_kind = build_tool_schemas(CATALOG)

    with pytest.raises(ToolArgumentError):
        normalize_arguments("set_light", arguments, by_kind)


def test_switch_requires_an_explicit_on():
    _, by_kind = build_tool_schemas(CATALOG)

    normalized = normalize_arguments("set_switch", {"device_id": "desk_plug", "on": True}, by_kind)
    assert normalized == {"device_id": "desk_plug", "on": True}

    with pytest.raises(ToolArgumentError):
        normalize_arguments("set_switch", {"device_id": "desk_plug"}, by_kind)


# ------------------------------------------------------------------ executor
def test_query_by_device_sends_the_parameter_name_the_backend_expects():
    """Regression: sending `device_id` made the backend return every device, so
    callers silently read the first one (the light) instead of the target."""
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    captured = {}

    def fake_request(method, path, query, body):
        captured.update({"method": method, "path": path, "query": query})
        return 200, {"ok": True, "data": {"devices": [{"id": "desk_plug", "state": {"on": False}}]}}

    executor._request = fake_request
    result = executor.execute("query_device_status", {"device_id": "desk_plug"})

    assert captured["query"] == {"device": "desk_plug"}
    assert result.data["devices"][0]["id"] == "desk_plug"


def test_executor_fetches_the_catalog_when_not_supplied():
    executor = HomeToolExecutor("http://127.0.0.1:9")
    executor.opener = ScriptedOpener([{"ok": True, "data": {"devices": CATALOG}}])

    names = [schema["function"]["name"] for schema in executor.schemas()]

    assert "set_switch" in names
    assert executor.device_name("desk_plug") == "智能插座"


def test_invalid_arguments_never_reach_the_backend():
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    calls = []
    executor._request = lambda *args, **kwargs: calls.append(args) or (200, {"ok": True})

    result = executor.execute("set_light", {"device_id": "garage_door", "on": True})

    assert result.ok is False
    assert calls == []


def test_switch_posts_to_its_own_endpoint_with_the_operation_id():
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    sent = {}

    def fake_request(method, path, query, body):
        sent.update({"method": method, "path": path, "body": body})
        return 200, {"ok": True, "data": {"state": {"on": True}}, "phrase": "已打开智能插座", "operation_id": "op-1"}

    executor._request = fake_request
    result = executor.execute("set_switch", {"device_id": "desk_plug", "on": True}, "op-1")

    assert sent["path"] == "/tool/set_switch"
    assert sent["body"] == {"device_id": "desk_plug", "on": True, "operation_id": "op-1"}
    assert result.ok and result.phrase == "已打开智能插座"


def test_backend_unavailable_is_reported_without_claiming_success():
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    executor._request = lambda *args, **kwargs: (0, {})

    result = executor.execute("set_switch", {"device_id": "desk_plug", "on": True})

    assert result.ok is False
    assert result.error_code == "backend_unavailable"


# ------------------------------------------------------- camera-owned persons
def test_person_query_reaches_the_camera_owned_route():
    """Regression: home-service exposed this route but the voice agent never
    offered the tool, so the assistant could not answer "我在哪" from the live
    camera even though the observation was already there."""
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    captured = {}

    def fake_request(method, path, query, body):
        captured.update({"method": method, "path": path, "query": query})
        return 200, {
            "ok": True,
            "data": {
                "persons": [
                    {
                        "id": "dad",
                        "display_name": "爸爸",
                        "room_name": "卧室",
                        "location_known": True,
                        "pose": "standing",
                    },
                    {"id": "mom", "display_name": "妈妈", "location_known": False},
                ]
            },
        }

    executor._request = fake_request
    result = executor.execute("query_person_location", {})

    assert (captured["method"], captured["path"]) == ("GET", "/tool/person_location")
    # There is no filter for "家里有谁": an omitted person means everyone.
    assert captured["query"] is None
    assert result.ok
    assert summarize_queries([result]) == "摄像头看到爸爸在卧室，站着；妈妈：摄像头当前没有看到"


def test_person_query_forwards_the_name_only_when_one_is_given():
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    captured = {}
    executor._request = lambda method, path, query, body: captured.update({"query": query}) or (
        200,
        {"ok": True, "data": {"persons": []}},
    )

    executor.execute("query_person_location", {"person": "爸爸"})

    assert captured["query"] == {"person": "爸爸"}


def test_scene_context_says_what_the_camera_sees_right_now():
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    executor._request = lambda *args, **kwargs: (
        200,
        {
            "ok": True,
            "data": {
                "persons": [
                    {
                        "id": "dad",
                        "display_name": "爸爸",
                        "room_name": "卧室",
                        "location_known": True,
                        "pose": "lying",
                    }
                ]
            },
        },
    )

    assert executor.scene_context() == "摄像头实时观察：爸爸在卧室，躺着。其他已注册家人当前没有被摄像头看到。"


def test_scene_context_is_explicit_when_nobody_is_seen():
    """An unknown location is "not seen", never "in another room"."""
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    executor._request = lambda *args, **kwargs: (
        200,
        {"ok": True, "data": {"persons": [{"id": "dad", "display_name": "爸爸", "location_known": False}]}},
    )

    assert executor.scene_context() == "摄像头实时观察：当前画面里没有识别到已注册的家人。"


def test_scene_context_never_breaks_a_turn_when_the_backend_is_down():
    executor = HomeToolExecutor("http://127.0.0.1:9", catalog=CATALOG)
    executor._request = lambda *args, **kwargs: (0, {})

    assert executor.scene_context() is None


# ------------------------------------------------------------ target authority
def test_every_house_changing_tool_has_a_declared_target_tier():
    """An unclassified action must never be reachable by a voiceprint default."""
    allowed = {TIER_REVERSIBLE_SELF, TIER_EXPLICIT_ONLY, TIER_FORBIDDEN, TIER_NOT_A_HOUSE_ACTION}

    for name in WRITE_TOOLS + SESSION_TOOLS:
        assert target_tier(name) in allowed, name


def test_read_tools_have_no_tier_because_nothing_is_targeted():
    """A query filter is not a write target, so asking for its tier is a mistake."""
    for name in READ_TOOLS:
        with pytest.raises(ToolArgumentError):
            target_tier(name)


def test_only_reversible_self_actions_accept_a_self_target():
    assert may_use_self_target("set_ac") is True
    assert may_use_self_target("adjust_ac") is True
    assert may_use_self_target("set_switch") is False
    assert may_use_self_target(SCENE_TOOL) is False
    assert may_use_self_target("end_conversation") is False


def test_every_forbidden_action_explains_itself():
    forbidden = [name for name, tier in TARGET_TIERS.items() if tier == TIER_FORBIDDEN]

    assert forbidden
    for name in forbidden:
        assert FORBIDDEN_TARGET_REASONS.get(name, "").strip(), name


def test_an_unclassified_tool_is_refused_rather_than_defaulted():
    """Defaulting an unknown tool to 'safe' is how a lock ends up voice-controlled."""
    with pytest.raises(ToolArgumentError):
        target_tier("set_door_lock")


def test_a_forbidden_tier_limits_targeting_without_removing_the_feature():
    """`forbidden` means "never auto-targeted", never "unavailable to the user"."""
    assert SCENE_TOOL in HOME_TOOL_NAMES
    assert SCENE_TOOL in WRITE_TOOLS
    assert TARGET_TIERS[SCENE_TOOL] == TIER_FORBIDDEN
