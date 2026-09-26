"""DeepSeek (OpenAI-compatible) chat completions transport for the voice agent.

Failures are reported as a small closed set of codes. Neither the API key nor any
response body is ever attached to an exception or written to a log.
"""
from __future__ import annotations

import json
import threading
import urllib.error
from dataclasses import dataclass, field
from typing import Any
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# Closed set of failure codes the session knows how to speak about honestly.
ERROR_CODES = (
    "agent_auth_failed",
    "agent_unavailable",
    "agent_rejected",
    "agent_invalid_response",
)


class AgentError(Exception):
    def __init__(self, code: str):
        self.code = code
        # Only the fixed code enters args/repr: never the key, the URL, or a body.
        super().__init__(code)


class AgentCancelled(Exception):
    """The user talked over this request, so its answer is no longer wanted.

    Raising rather than returning keeps the cancellation out of the honest failure
    codes: a cancelled turn did not fail, and nothing about it may be spoken.
    """


def _cancelled(cancel: "threading.Event | None") -> bool:
    return cancel is not None and cancel.is_set()


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102 - see base class
        return None


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise AgentError("agent_invalid_response")
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise AgentError("agent_invalid_response") from None
    if not isinstance(parsed, dict):
        raise AgentError("agent_invalid_response")
    return parsed


def _agent_error(exc: BaseException) -> AgentError:
    """One closed mapping for both transports, so codes never drift apart."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return AgentError("agent_auth_failed")
        return AgentError("agent_unavailable" if exc.code >= 500 else "agent_rejected")
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return AgentError("agent_unavailable")
    return AgentError("agent_invalid_response")


class AgentClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 20.0,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("DeepSeek API key required")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, payload: dict[str, Any]) -> Request:
        return Request(
            self.base_url + "/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
        )

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            # Deterministic tool choice makes the loop reproducible and testable.
            "temperature": 0,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        return payload

    def _post(self, payload: dict[str, Any], cancel: "threading.Event | None" = None) -> dict[str, Any]:
        if _cancelled(cancel):
            raise AgentCancelled()
        request = self._request(payload)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise _agent_error(exc) from None

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel: "threading.Event | None" = None,
    ) -> ChatResponse:
        body = self._post(self._payload(messages, tools), cancel)
        try:
            choices = body["choices"]
            message = choices[0]["message"]
        except (KeyError, IndexError, TypeError):
            raise AgentError("agent_invalid_response") from None

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise AgentError("agent_invalid_response")

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            try:
                function = raw["function"]
                calls.append(
                    ToolCall(
                        id=str(raw.get("id") or f"call_{len(calls)}"),
                        name=str(function["name"]),
                        arguments=_parse_arguments(function.get("arguments")),
                    )
                )
            except (KeyError, TypeError):
                raise AgentError("agent_invalid_response") from None
        return ChatResponse(content=content, tool_calls=calls)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta=None,
        cancel: "threading.Event | None" = None,
    ) -> ChatResponse:
        """Same contract as `chat`, but report the sentence while it is written.

        `on_delta` receives the text accumulated so far, not the fragment, so a
        caller can render or speak it without owning any buffering. Tool calls
        arrive fragmented across chunks and are therefore reassembled by index.

        `cancel` is polled between stream frames: leaving the `with` block closes
        the socket, which ends the request upstream as well. This is the only
        transport that can be abandoned mid-answer, which is why the voice loop
        prefers it.

        The assistant cannot identify the speaker and this changes nothing about
        that: it only shortens the silence before the answer appears.
        """
        if _cancelled(cancel):
            raise AgentCancelled()
        payload = self._payload(messages, tools)
        payload["stream"] = True
        parts: list[str] = []
        fragments: dict[int, dict[str, str]] = {}
        try:
            with self.opener.open(self._request(payload), timeout=self.timeout) as response:
                for raw in response:
                    if _cancelled(cancel):
                        raise AgentCancelled()
                    line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                    line = line.strip()
                    if not line.startswith("data:"):
                        # Blank keepalives and comment frames are normal.
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        # A single unreadable frame must not lose the answer.
                        continue
                    self._absorb(chunk, parts, fragments, on_delta)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise _agent_error(exc) from None

        content = "".join(parts) or None
        calls: list[ToolCall] = []
        for index in sorted(fragments):
            slot = fragments[index]
            if not slot["name"]:
                raise AgentError("agent_invalid_response")
            calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{len(calls)}",
                    name=slot["name"],
                    arguments=_parse_arguments(slot["arguments"]),
                )
            )
        return ChatResponse(content=content, tool_calls=calls)

    @staticmethod
    def _absorb(
        chunk: dict[str, Any],
        parts: list[str],
        fragments: dict[int, dict[str, str]],
        on_delta,
    ) -> None:
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        if not isinstance(delta, dict):
            return
        text = delta.get("content")
        if isinstance(text, str) and text:
            parts.append(text)
            if on_delta is not None:
                on_delta("".join(parts))
        for raw in delta.get("tool_calls") or []:
            if not isinstance(raw, dict):
                continue
            index = raw.get("index")
            index = index if isinstance(index, int) and index >= 0 else len(fragments)
            slot = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if raw.get("id"):
                slot["id"] = str(raw["id"])
            function = raw.get("function")
            if isinstance(function, dict):
                if function.get("name"):
                    slot["name"] = str(function["name"])
                argument = function.get("arguments")
                if isinstance(argument, str):
                    slot["arguments"] += argument
