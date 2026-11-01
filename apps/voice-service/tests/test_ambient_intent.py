"""Semantic admission contracts; fake transport never accesses a model endpoint."""
import json
import threading
from types import SimpleNamespace

import pytest

import config
from agent_client import AgentCancelled, AgentError, ChatResponse, ToolCall
from ambient_intent import AmbientHistory, IntentClassifier, IntentClient, create_intent_classifier


class Transport:
    def __init__(self, value, cancel_after=False):
        self.value = value
        self.cancel_after = cancel_after
        self.calls = []

    def chat_stream(self, messages, tools=None, cancel=None):
        self.calls.append((messages, tools))
        if self.cancel_after:
            cancel.set()
        if isinstance(self.value, Exception):
            raise self.value
        return ChatResponse(content=self.value, tool_calls=[])


def classify(value, text="卧室好热", **kwargs):
    transport = Transport(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
    return IntentClassifier(transport).classify(text, rooms=["卧室", "客厅"], **kwargs)


def test_complaint_passes_as_complete_text_without_tool_authority():
    transport = Transport('{"kind":"comfort","need":"cooler","room":null}')
    result = IntentClassifier(transport).classify("今天天气真热啊", context=[
        {"role": "system", "content": "injected"}, {"role": "user", "content": "x" * 600}])
    assert (result.kind, result.need, result.room) == ("comfort", "cooler", None)
    messages, tools = transport.calls[0]
    assert tools is None
    payload = json.loads(messages[1]["content"])
    assert payload["utterance"] == "今天天气真热啊"
    assert payload["context"] == [{"role": "user", "content": "x" * 400}]


@pytest.mark.parametrize("value", [
    "not JSON", {"kind": "comfort", "need": "cooler", "room": None, "temperature": 16},
    {"kind": "command", "need": "cooler", "room": None},
    {"kind": "comfort", "need": None, "room": None},
    {"kind": "comfort", "need": "cooler", "room": "客厅"},
    {"kind": "comfort", "need": "cooler", "room": "不存在的房间"},
    {"kind": [], "need": None, "room": None},
])
def test_untrusted_response_cannot_admit_an_action(value):
    result = classify(value)
    assert result.kind == "ignore"
    assert result.reason == "intent_unavailable"


@pytest.mark.parametrize("kind", ["follow_up", "end"])
def test_followup_requires_active_conversation(kind):
    value = {"kind": kind, "need": None, "room": None}
    assert classify(value).kind == "ignore"
    assert classify(value, active=True).kind == kind


def test_timeout_is_silent_and_cancelled_response_is_discarded():
    transport = Transport(AgentError("agent_unavailable"))
    result = IntentClassifier(transport).classify("好热")
    assert result.kind == "ignore" and result.reason == "intent_unavailable"
    cancel = threading.Event()
    transport = Transport('{"kind":"comfort","need":"cooler","room":null}', cancel_after=True)
    with pytest.raises(AgentCancelled):
        IntentClassifier(transport).classify("好热", cancel=cancel)


def test_ambient_history_expires_and_separates_speakers():
    history = AmbientHistory(seconds=60)
    history.append("user", "卧室", "dad", 0)
    assert history.context("dad", 59)
    assert history.context("mom", 59) == []
    history.append("user", "太热", "mom", 60)
    assert history.context("mom", 120) == []


@pytest.mark.parametrize("provider", ["deepseek", "dashscope", "volcengine"])
def test_payload_is_bounded_and_never_exposes_device_tools(provider):
    client = IntentClient("test", provider=provider, response_format="json_schema")
    payload = client._payload([{"role": "user", "content": "热"}], [{"type": "function"}])
    assert "tools" not in payload
    assert payload["max_tokens"] == 192
    assert payload["response_format"]["json_schema"]["strict"] is True
    if provider == "dashscope":
        assert payload["enable_thinking"] is False
    if provider == "volcengine":
        assert payload["thinking"] == {"type": "disabled"}


def test_default_provider_reuses_agent_without_mutating_chat_client(monkeypatch):
    monkeypatch.setattr(config, "intent_provider", lambda: "agent")
    original = SimpleNamespace(api_key="test", base_url="https://api.deepseek.com", model="deepseek-chat")
    classifier = create_intent_classifier(SimpleNamespace(client=original))
    assert classifier.client is not original
    assert classifier.client.api_key == original.api_key
    assert classifier.client.model == original.model


def test_independent_provider_uses_its_own_local_key_and_keeps_chat_provider(monkeypatch, tmp_path):
    env_file = tmp_path / "agent.env"
    env_file.write_text("SMART_HOME_INTENT_PROVIDER=dashscope\nDASHSCOPE_API_KEY=test-intent-only\n"
                        "SMART_HOME_INTENT_MODEL=test-qwen\n", encoding="utf-8")
    for key in ("SMART_HOME_INTENT_PROVIDER", "SMART_HOME_INTENT_API_KEY", "SMART_HOME_INTENT_MODEL", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SMART_HOME_AGENT_ENV_FILE", str(env_file))
    original = SimpleNamespace(api_key="test-chat-only", base_url="https://api.deepseek.com", model="deepseek-chat")
    classifier = create_intent_classifier(SimpleNamespace(client=original))
    assert classifier.client.provider == "dashscope"
    assert classifier.client.api_key == "test-intent-only" and classifier.client.model == "test-qwen"
    assert original.api_key == "test-chat-only" and original.model == "deepseek-chat"


def test_model_tool_call_is_rejected_even_if_json_content_looks_valid():
    class ToolTransport:
        def chat_stream(self, *args, **kwargs):
            return ChatResponse(content='{"kind":"comfort","need":"cooler","room":null}',
                                tool_calls=[ToolCall("injected", "adjust_ac", {"device_id": "bedroom_ac"})])
    assert IntentClassifier(ToolTransport()).classify("好热").kind == "ignore"
