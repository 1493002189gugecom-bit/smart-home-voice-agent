"""Voice agent orchestration: bounded tool loop, bounded context, honest wording.

The tool results — not the model's prose — decide what the user is told after
any write. Model text is also held back from the console until the tool choice
is known, so a hallucinated success is never shown as a provisional answer.
"""
from __future__ import annotations

import inspect
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_client import (
    AgentCancelled,
    AgentClient,
    AgentError,
    ChatResponse,
    ToolCall,
)
from agent_tools import (
    ADJUST_TOOL,
    END_TOOL,
    WRITE_TOOLS,
    HomeToolExecutor,
    ToolArgumentError,
    ToolResult,
    new_operation_id,
    summarize_queries,
)

DEFAULT_MAX_TOOL_ROUNDS = 4
DEFAULT_DEADLINE_SECONDS = 20.0
DEFAULT_MAX_MESSAGES = 12
# Spoken when the model closes the session without producing any words.
FAREWELL_TEXT = "好的，有需要随时喊我。"

SYSTEM_PROMPT = (
    "你是一个中文家庭语音助手，负责通过提供的工具控制家里的设备。"
    "只能操作工具允许的设备，不得编造设备状态，也不要声称控制列表以外的设备。"
    "如果用户表达含糊或缺少必要信息，先用一句话追问。"
    "设备操作结果以工具返回为准，不要说工具没有报告的成功。"
    "温度必须来自用户或本地配置：用户说“调到24度”是绝对目标，用 set_ac；"
    "说“再调低两度”是相对变化，用 adjust_ac 并传 degrees=2，按设备当前设定温度计算；"
    "只说热或冷、再低一点就用 adjust_ac 但省略 degrees，按本地一档调整；"
    "用户说“有点热”也要照做，不要反复追问。"
    "用户没有说房间而要求调整自己周围的灯或空调时，调用对应工具并传 target='self'，"
    "不要凭设备列表猜一个 device_id；本地程序会把本轮声纹与摄像头位置绑定后选择设备。"
    "上下文里出现“本轮可信定位”时，说明本地已经确认了说话人和房间："
    "用户没说房间就直接执行，不要再问是哪个房间；"
    "上下文里只有“本轮无可信定位”或根本没有这一类话时，才按工具提示追问房间。"
    "用户明确说出房间或设备时直接使用那个设备，不要改成 self。"
    "当 self 解析失败时按工具提示追问房间，不要宣称已经执行。"
    "当用户表达告别或结束对话的意图（再见、拜拜、不聊了、我先去忙、回头再说等），"
    "调用 end_conversation 工具并简短告别，不要挽留、不要反问、不要再发起新话题。"
    "你能通过摄像头了解家人当前在哪个房间：用户问“我在哪”“爸爸在哪”“家里有谁”“妈妈在干什么”时，"
    "必须先用 query_person_location 查询，再照结果回答，不要凭印象或上一轮的房间回答。"
    "摄像头只能看到画面里有谁，无法判断正在说话的是谁：不要断言说话人就是画面里的某个人，"
    "只能说“我看到爸爸在卧室”这类基于画面的说法。"
    "关于“我是谁”“你知道我是谁吗”：只有当上下文里出现过“声纹判定”那一条时，你才知道说话人是谁，"
    "此时照它说，并用“声纹判定”这个出处（例如“声纹判定是爸爸”），不要说得比它更肯定；"
    "上下文里没有“声纹判定”时，必须如实说明你无法通过声音辨认说话人，"
    "可以说出摄像头当前看到的人和房间，请他确认。"
    "绝不要在同一句话里既声称无法辨认声音、又报出声音判断出的身份。"
    "声纹判定只说明是谁在说话，不说明他在哪个房间：位置一律以摄像头为准，"
    "不要用声纹推出的身份去猜房间。判定含糊时如实说不确定，不要假装知道说话人是谁。"
    "回复要口语化、简短，适合直接朗读，不要使用列表或 Markdown。"
)


def build_system_prompt(catalog: list[dict[str, Any]]) -> str:
    """Tell the model exactly which devices exist, from the live catalog."""
    controllable = [device for device in catalog if device.get("controllable")]
    if not controllable:
        return SYSTEM_PROMPT
    described = "、".join(
        f"{device.get('name') or device['id']}（{device['id']}，位于{device.get('room_name') or device.get('room_id')}）"
        for device in controllable
    )
    return SYSTEM_PROMPT + f" 你目前可以控制：{described}。"


ERROR_TEXT = {
    "agent_auth_failed": "语音助手鉴权失败，请检查本地密钥配置。",
    "agent_unavailable": "语音助手暂时无法连接，请稍后再试。",
    "agent_rejected": "语音助手拒绝了这次请求。",
    "agent_invalid_response": "语音助手返回了无法识别的结果。",
    "agent_timeout": "这次请求处理时间过长，已停止。",
    "tool_loop_exceeded": "操作步骤过多，已停止，请说得更具体一些。",
    "backend_unavailable": "家居服务当前不可用。",
    "empty_transcript": "我没有听清，请再说一次。",
}


def _canonical(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _cancelled(cancel: "threading.Event | None") -> bool:
    return cancel is not None and cancel.is_set()


def _accepts_cancel(func) -> bool:
    """Whether a client (or a test double) declares the optional cancel argument."""

    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return "cancel" in parameters


def _call_with_cancel(func, *args, cancel: "threading.Event | None" = None):
    """Forward `cancel` only to a callee that declares it.

    Test doubles and older wiring implement the two-argument client contract; they
    must keep working, and a missing cancellation point is not an error.
    """

    if cancel is None or not _accepts_cancel(func):
        return func(*args)
    return func(*args, cancel=cancel)


def _compact(transcript: str) -> str:
    return re.sub(r"[\s，。！？,.!?、]+", "", transcript or "")


def parse_relative_ac_change(transcript: str) -> tuple[str, float] | None:
    """Read only one unambiguous current relative amount from the user's words."""
    if any(marker in transcript for marker in ("不要", "别", "不是", "刚才", "之前", "原先")):
        return None
    matches = list(re.finditer(
        r"(?:再)?(调低|降低|调高|升高|低|高)\s*(\d+(?:\.\d+)?|[一二两三四五六七八九十半])\s*度",
        transcript,
    ))
    if len(matches) != 1:
        return None
    match = matches[0]
    spoken = match.group(2)
    chinese = {"半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    degrees = chinese[spoken] if spoken in chinese else float(spoken)
    direction = "cooler" if "低" in match.group(1) or "降" in match.group(1) else "warmer"
    return direction, float(degrees)


def is_direct_self_ac_change(transcript: str) -> bool:
    """Narrow fast path for an imperative such as '空调调高一度'."""
    compact = _compact(transcript)
    return re.fullmatch(
        r"(?:请|帮我|麻烦你|给我)?(?:把)?(?:这里的|这边的|我这的)?空调(?:的)?(?:温度)?(?:再)?"
        r"(?:调高|调低|升高|降低)(?:\d+(?:\.\d+)?|[一二两三四五六七八九十半])度(?:吧|一下)?",
        compact,
    ) is not None


# The optional prefixes below are the only wording that may precede a room-less
# imperative. A phrase naming a room or a specific lamp ("打开客厅灯", "开台灯")
# deliberately fails these patterns and goes to the model instead: acting on the
# wrong device because a regex was too greedy is exactly the failure this module
# exists to prevent.
_HOUSEHOLD = r"(?:请|帮我|麻烦你|给我)?"
_HERE = r"(?:这里的|这边的|我这的|我房间的)?"


def _switch(kind: str, verbs: str) -> re.Pattern[str]:
    """Match "开灯" and "灯打开" alike, and nothing that names a room."""

    before = rf"{_HOUSEHOLD}(?:把)?(?:{_HERE})?(?:{verbs})(?:一下|下)?(?:{_HERE})?{kind}(?:给我)?(?:吧|一下)?"
    after = rf"{_HOUSEHOLD}(?:把)?(?:{_HERE})?{kind}(?:给我)?(?:{verbs})(?:一下|下|了)?(?:吧)?"
    return re.compile(rf"(?:{before}|{after})")


# Longest verb first: Python alternation takes the first branch that matches, so
# "打开" must be tried before "开" or the trailing "开" would be left unmatched.
_LIGHT_ON = _switch("灯", "打开|开启|开")
_LIGHT_OFF = _switch("灯", "关掉|关闭|关上|关")
_AC_ON = _switch("空调", "打开|开启|开")
_AC_OFF = _switch("空调", "关掉|关闭|关上|关")


@dataclass(frozen=True)
class DirectSelfCommand:
    """A phrase unambiguous enough to act on without a cloud round trip."""

    tool: str
    arguments: dict[str, Any]


def parse_direct_self_command(transcript: str) -> DirectSelfCommand | None:
    """The most ordinary household imperatives, resolved locally.

    This is a latency and reliability fast path for "开灯" and "空调调高一度",
    never a replacement for intent parsing: anything with a room, a device name, a
    negation or a second clause falls through to the model unchanged. Every command
    it returns still goes through the same tier table, the same speaker-and-camera
    resolution and the same spoken room-name guard.
    """

    compact = _compact(transcript)
    if not compact or any(marker in compact for marker in ("不要", "别", "不用", "先不")):
        return None
    if _LIGHT_ON.fullmatch(compact):
        return DirectSelfCommand("set_light", {"target": "self", "on": True})
    if _LIGHT_OFF.fullmatch(compact):
        return DirectSelfCommand("set_light", {"target": "self", "on": False})
    if _AC_ON.fullmatch(compact):
        return DirectSelfCommand("set_ac", {"target": "self", "on": True})
    if _AC_OFF.fullmatch(compact):
        return DirectSelfCommand("set_ac", {"target": "self", "on": False})
    relative = parse_relative_ac_change(transcript)
    if relative is not None and is_direct_self_ac_change(transcript):
        direction, degrees = relative
        return DirectSelfCommand(
            ADJUST_TOOL, {"target": "self", "direction": direction, "degrees": degrees}
        )
    return None


@dataclass
class AgentReply:
    text: str
    ok: bool
    error_code: str | None = None
    tool_results: list[ToolResult] = field(default_factory=list)
    # True when the model judged that the user wants to stop talking. The loop
    # returns to standby after speaking, so "再见" ends the session instead of
    # starting a chat about goodbyes.
    end_conversation: bool = False
    # True when the user interrupted this turn. The text is then empty on purpose:
    # a cancelled answer is stale the moment it exists and must never be spoken.
    cancelled: bool = False

    @property
    def wrote_device(self) -> bool:
        return any(result.name in WRITE_TOOLS for result in self.tool_results)


class AgentSession:
    """One conversation. `reset()` is called when the loop leaves active state."""

    def __init__(
        self,
        client: AgentClient,
        executor: HomeToolExecutor,
        *,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        clock=time.monotonic,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        self.client = client
        self.executor = executor
        self.max_tool_rounds = int(max_tool_rounds)
        self.deadline_seconds = float(deadline_seconds)
        self.max_messages = int(max_messages)
        self.clock = clock
        self.system_prompt = system_prompt
        self.history: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        # Optional callback for an answer with no device writes. Model deltas
        # are buffered until the response proves it will not call a write tool.
        self.on_delta = None
        # The speaker line for the *current* turn only. It is consumed by the next
        # `handle` and never survives it, so a verdict can only ever describe the
        # sentence it arrived with.
        self.speaker_line: str | None = None
        self.speaker_person_id: str | None = None

    # ------------------------------------------------------------------ public
    def reset(self) -> None:
        """End the conversation: drop context and any pending target."""
        self.history = [{"role": "system", "content": self.system_prompt}]
        self.speaker_line = None
        self.speaker_person_id = None

    def _refresh_scene_context(self) -> None:
        """Fold the live camera view and this turn's speaker line into the prompt.

        The model cannot see or hear the house, so without this it asks which room
        the user is in even though the vision service already knows, and it cannot
        use the second person at all. Both are data, never instructions, and both
        are re-read every turn so a stale room or a stale speaker is never spoken.
        """
        reader = getattr(self.executor, "scene_context", None)
        summary = None
        if callable(reader):
            try:
                summary = reader()
            except Exception:
                summary = None
        parts = [self.system_prompt]
        if summary:
            parts.append(str(summary))
        if self.speaker_line:
            parts.append(self.speaker_line)
        self_line = self._self_target_line()
        if self_line:
            parts.append(self_line)
        content = " ".join(parts)
        if self.history and self.history[0].get("role") == "system":
            self.history[0]["content"] = content
        else:
            self.history.insert(0, {"role": "system", "content": content})

    def _self_target_line(self) -> str | None:
        """Ask the executor whether a room-less command can resolve right now.

        Conjuring a room from the voiceprint alone is forbidden, so this goes
        through the executor, which owns both the camera observation and the rule
        about how fresh it must be. An executor without the method (tests, older
        wiring) injects nothing.
        """

        if not self.speaker_person_id:
            return None
        reader = getattr(self.executor, "self_target_line", None)
        if not callable(reader):
            return None
        try:
            return reader(self.speaker_person_id)
        except Exception:
            return None

    def handle(self, transcript: str, cancel: "threading.Event | None" = None) -> AgentReply:
        if not isinstance(transcript, str) or not transcript.strip():
            return AgentReply(text=ERROR_TEXT["empty_transcript"], ok=False, error_code="empty_transcript")

        self._refresh_scene_context()
        # Consumed: the next turn must produce its own verdict, or say nothing.
        self.speaker_line = None
        speaker_person_id = self.speaker_person_id
        self.speaker_person_id = None
        self.history.append({"role": "user", "content": transcript.strip()})
        relative_ac_change = parse_relative_ac_change(transcript)
        direct = parse_direct_self_command(transcript)
        if direct is not None:
            result = self._run_tool(
                ToolCall(id="local_self", name=direct.tool, arguments=direct.arguments),
                {}, relative_ac_change, speaker_person_id,
            )
            text = self._write_outcomes([result])
            self.history.append({"role": "assistant", "content": text})
            self._trim()
            return AgentReply(text=text, ok=result.ok, error_code=result.error_code,
                              tool_results=[result])
        deadline = self.clock() + self.deadline_seconds
        collected: list[ToolResult] = []
        write_operations: dict[str, str] = {}
        ending = False

        # One extra iteration beyond the tool budget so the model always gets a
        # chance to answer in words after its last tool result.
        for round_index in range(self.max_tool_rounds + 1):
            if self.clock() >= deadline:
                return self._fail("agent_timeout", collected)
            if _cancelled(cancel):
                return self._cancelled_reply(collected)

            # A streamed model sentence may arrive before its tool calls. Hold it
            # until the response proves this turn has no device write to report.
            pending_delta: str | None = None

            def hold_delta(text: str) -> None:
                nonlocal pending_delta
                pending_delta = text

            try:
                response = self._ask(cancel, hold_delta)
            except AgentCancelled:
                return self._cancelled_reply(collected)
            except AgentError as exc:
                return self._fail(exc.code, collected)

            if not response.wants_tools:
                writes = [result for result in collected if result.name in WRITE_TOOLS]
                if not writes and pending_delta and self.on_delta is not None:
                    self.on_delta(pending_delta)
                text = self._final_text(response, collected, ending=ending)
                self.history.append({"role": "assistant", "content": text})
                self._trim()
                # `ok` reports whether the house actually changed, not merely that
                # a reply was produced, so a partial scene failure shows up in the
                # logs instead of only in the spoken sentence.
                return AgentReply(
                    text=text,
                    ok=all(result.ok for result in writes),
                    tool_results=collected,
                    end_conversation=ending,
                )

            if round_index >= self.max_tool_rounds:
                # The tool budget is spent and the model still wants more.
                break

            if any(call.name == END_TOOL for call in response.tool_calls):
                ending = True

            # The assistant turn that requested the tools must be echoed back
            # before any tool result: OpenAI-compatible APIs reject a `tool`
            # message that has no preceding assistant `tool_calls`.
            self.history.append(self._assistant_tool_message(response))

            for call in response.tool_calls:
                if _cancelled(cancel):
                    return self._cancelled_reply(collected)
                result = self._run_tool(call, write_operations, relative_ac_change, speaker_person_id)
                collected.append(result)
                self.history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result.payload(), ensure_ascii=False),
                    }
                )

        return self._fail("tool_loop_exceeded", collected)

    # ----------------------------------------------------------------- internal
    def _ask(self, cancel: "threading.Event | None" = None,
             hold_delta: Callable[[str], None] | None = None) -> ChatResponse:
        """Ask the model once, buffering streamed text when watched.

        Streaming is opt-in per session and per client: a client without
        `chat_stream` still works. Text is released only after the model's
        response proves there is no device write in this turn.

        `cancel` is only forwarded to a client that declares it, so a test double
        with the two-argument contract keeps working unchanged.
        """
        stream = getattr(self.client, "chat_stream", None)
        if self.on_delta is not None and callable(stream):
            return _call_with_cancel(
                stream, self._messages(), self.executor.schemas(), hold_delta, cancel=cancel
            )
        return _call_with_cancel(
            self.client.chat, self._messages(), self.executor.schemas(), cancel=cancel
        )

    @staticmethod
    def _assistant_tool_message(response: ChatResponse) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in response.tool_calls
            ],
        }

    def _run_tool(self, call: ToolCall, write_operations: dict[str, str],
                  relative_ac_change: tuple[str, float] | None = None,
                  speaker_person_id: str | None = None) -> ToolResult:
        name = call.name
        arguments = dict(call.arguments)
        if name in WRITE_TOOLS and arguments.get("target") == "self":
            if arguments.get("device_id") is not None:
                return ToolResult(name=name, ok=False, error_code="invalid_arguments",
                                  message="不能同时指定设备和我所在的房间", arguments=arguments)
            resolver = getattr(self.executor, "resolve_self_device", None)
            if not callable(resolver):
                return ToolResult(name=name, ok=False, error_code="location_unavailable",
                                  message="暂时无法按位置选择设备，请直接说出房间", arguments=arguments)
            try:
                device_id, room_name = resolver(name, speaker_person_id)
            except Exception as exc:
                if not isinstance(exc, ToolArgumentError):
                    raise
                return ToolResult(name=name, ok=False, error_code=exc.code,
                                  message=exc.message, arguments=arguments)
            arguments = {key: value for key, value in arguments.items() if key != "target"}
            arguments["device_id"] = device_id
        else:
            room_name = None
        if relative_ac_change is not None:
            direction, degrees = relative_ac_change
            if call.name == "set_ac":
                # The user supplied a delta, so an absolute model guess cannot
                # become a device target. Keep only its catalogued device id.
                name = ADJUST_TOOL
                arguments = {"device_id": arguments.get("device_id"),
                             "direction": direction, "degrees": degrees}
            if call.name == ADJUST_TOOL:
                if arguments.get("direction") != direction:
                    return ToolResult(name=call.name, ok=False, error_code="invalid_arguments",
                                      message="调温方向与用户说法不一致", arguments=arguments)
                arguments["degrees"] = degrees
        # A repeated identical write inside one request must replay the first
        # operation instead of commanding the device a second time.
        key = name + ":" + _canonical(arguments)
        operation_id = None
        if name in WRITE_TOOLS:
            operation_id = write_operations.get(key)
            if operation_id is None:
                operation_id = new_operation_id()
                write_operations[key] = operation_id
        result = self.executor.execute(name, arguments, operation_id)
        if room_name and result.ok:
            # A mistaken speaker verdict is easier to correct when the spoken
            # confirmation always names the actual room that was operated on.
            phrase = result.phrase or result.message or "已执行"
            if room_name not in phrase:
                result.phrase = f"{room_name}：{phrase}"
            result.resolved_room_name = room_name
        return result

    def _final_text(
        self, response: ChatResponse, collected: list[ToolResult], *, ending: bool = False
    ) -> str:
        writes = [result for result in collected if result.name in WRITE_TOOLS]
        if writes:
            # For every write, including explicit room/device targets and partial
            # success, the observed operation result owns the spoken answer.
            return self._write_outcomes(writes)
        content = (response.content or "").strip()
        if content:
            return content
        if ending:
            # The model asked to close the session without words; never end in
            # silence, which would look like a crash.
            return FAREWELL_TEXT
        summary = summarize_queries(collected)
        return summary or "没有查询到结果。"

    @staticmethod
    def _write_outcomes(writes: list[ToolResult]) -> str:
        parts = []
        for result in writes:
            if result.ok:
                parts.append(result.phrase or "设备操作已确认，但未返回具体状态")
            else:
                parts.append(AgentSession._failure_text([result]))
        return "；".join(dict.fromkeys(parts))

    @staticmethod
    def _failure_text(failed: list[ToolResult]) -> str:
        parts = []
        for result in failed:
            message = result.message or "操作失败"
            if result.error_code == "confirmation_timeout":
                message = "请求已提交，但没有确认到设备状态"
            elif result.error_code == "offline":
                message = message or "设备当前离线"
            parts.append(message)
        return "；".join(dict.fromkeys(parts))

    def _messages(self) -> list[dict[str, Any]]:
        return list(self.history)

    def _trim(self) -> None:
        """Keep the system prompt plus a bounded tail, never starting on a tool."""
        if len(self.history) <= self.max_messages + 1:
            return
        head, tail = self.history[0], self.history[-(self.max_messages):]
        while tail and tail[0].get("role") == "tool":
            tail.pop(0)
        self.history = [head, *tail]

    def _fail(self, code: str, collected: list[ToolResult]) -> AgentReply:
        # The model can fail after a device operation already completed. Report
        # the observed outcomes and the unfinished conversation separately.
        writes = [result for result in collected if result.name in WRITE_TOOLS]
        text = (self._write_outcomes(writes) + "；后续处理未完成") if writes else ERROR_TEXT.get(
            code, "请求失败。"
        )
        return AgentReply(
            text=text, ok=False, error_code=code, tool_results=collected
        )

    def _cancelled_reply(self, collected: list[ToolResult]) -> AgentReply:
        """The user talked over this turn, so no part of it may be spoken."""

        self._drop_abandoned_turn()
        return AgentReply(
            text="", ok=False, error_code="cancelled", tool_results=collected, cancelled=True
        )

    def _drop_abandoned_turn(self) -> None:
        """Remove the interrupted turn from the context it would otherwise poison.

        A trailing assistant message that requested tools with no matching tool
        results is rejected by the next API call, and a question the user has
        already answered is noise. The user's next sentence starts a fresh turn, so
        the abandoned one leaves nothing behind.
        """

        while len(self.history) > 1:
            role = self.history[-1].get("role")
            self.history.pop()
            if role == "user":
                return


def create_agent_session(
    api_key: str,
    service_url: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 20.0,
    **session_options: Any,
) -> AgentSession:
    """Build the production session: DeepSeek client over the local tool executor."""
    import config

    client = AgentClient(
        api_key,
        base_url=base_url or config.agent_base_url(),
        model=model or config.agent_model(),
        timeout=timeout,
    )
    executor = HomeToolExecutor(service_url)
    # The prompt names the devices the catalog actually exposes, so the model
    # knows the real scope instead of guessing.
    session_options.setdefault("system_prompt", build_system_prompt(executor.catalog()))
    return AgentSession(client, executor, **session_options)
