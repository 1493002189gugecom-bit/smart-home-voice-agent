"""Typed ASR turns with an isolated conversation and optional announcements.

HTTP only acknowledges acceptance; correlated events carry the eventual result.
One turn runs at a time, and a repeated request id never repeats a device action.
Voiceprint identity and microphone conversation are never borrowed here.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from ambient_intent import AmbientHistory, create_intent_classifier
from text_speech import SpeechFailure
from agent_client import AgentCancelled
from voice_stages import TurnStages


class TextConversation:
    def __init__(self, agent_factory, bus, *, classifier_factory=create_intent_classifier,
                 strip_wake=lambda text: text, clock=time.monotonic, idle_seconds=60, announce=None):
        self.agent_factory = agent_factory
        self.bus = bus
        self.classifier_factory = classifier_factory
        self.strip_wake = strip_wake
        self.clock = clock
        self.idle_seconds = idle_seconds
        self.announce = announce
        self.agent = None
        self.classifier = None
        self.history = AmbientHistory(seconds=idle_seconds)
        self.active = False
        self.woken = False
        self.deadline = 0.0
        self._lock = threading.Lock()
        self._busy = False
        self._requests = OrderedDict()
        self._cancel = threading.Event()

    def cancel(self):
        with self._lock:
            self._cancel.set()

    def submit(self, body):
        request_id, text = body["request_id"], body["text"].strip()
        speak = body.get("speak", False)
        with self._lock:
            previous = self._requests.get(request_id)
            if previous is not None:
                if previous != (text, speak):
                    return {"ok": False, "error_code": "request_conflict", "message": "请求编号已用于另一条消息"}
                return {"ok": True, "request_id": request_id, "duplicate": True}
            if self._busy:
                return {"ok": False, "error_code": "text_busy", "message": "上一条消息仍在处理中，请等回复后再发送"}
            self._busy = True
            self._cancel = threading.Event()
            self._requests[request_id] = (text, speak)
            while len(self._requests) > 200:
                self._requests.popitem(last=False)
            self.bus.publish("transcript", input_mode="text", request_id=request_id, transcript=text)
            self.bus.publish("text_turn", input_mode="text", request_id=request_id, turn_status="processing")
            worker = threading.Thread(target=self._run, args=(request_id, text, speak), name="typed-asr-turn", daemon=True)
            worker.start()
        return {"ok": True, "request_id": request_id, "duplicate": False}

    def _reset(self):
        self.active = self.woken = False
        self.history.reset()
        if self.agent is not None:
            self.agent.reset()

    def _run(self, request_id, text, speak=False):
        def publish(event_type, **fields):
            # No `state` field: typed turns must not change the microphone state.
            if event_type == "playback_state" and fields.get("source_event") == "tts_start":
                stages.start("playback")
            fields.setdefault("input_mode", "text")
            fields.setdefault("request_id", request_id)
            return self.bus.publish(event_type, **fields)

        status, message, error_code = "failed", "文字对话处理失败，请稍后再试。", None
        stages = TurnStages(publish, request_id, "text")
        try:
            stages.start("intent")
            if self.agent is None:
                self.agent = self.agent_factory()
            if self.active and self.clock() >= self.deadline:
                self._reset()
            utterance = self.strip_wake(text)
            if utterance != text:
                self._reset()
                self.active = self.woken = True
            self.agent.speaker_line = None
            self.agent.speaker_person_id = None
            if self.woken:
                stages.start("agent")
                reply = self.agent.handle(utterance or "你好", cancel=self._cancel)
            else:
                if self.classifier is None:
                    self.classifier = self.classifier_factory(self.agent)
                if self.classifier is None:
                    raise ValueError("intent_configuration_missing")
                rooms = [item.get("room_name") for item in self.agent.executor.catalog()]
                decision = self.classifier.classify(
                    text, active=self.active, rooms=rooms,
                    context=self.history.context(None, self.clock()),
                    cancel=self._cancel,
                )
                if decision.kind == "ignore":
                    if decision.reason == "intent_unavailable":
                        status, message, error_code = "failed", "意图服务暂时不可用，本句未执行。", "intent_unavailable"
                    else:
                        self.history.append("user", text, None, self.clock())
                        status, message = "ignored", "本句未触发家居助手。"
                    return
                if not self.active:
                    self.agent.reset()
                stages.start("agent")
                reply = self.agent.handle_admitted(text, decision, cancel=self._cancel)
            if getattr(reply, "ignored", False):
                status, message = "ignored", "刚刚已处理相同需求，本次未重复执行。"
                return
            if getattr(reply, "cancelled", False):
                status, message, error_code = "failed", "本轮已取消。", "cancelled"
                return
            publish("agent_reply", text=reply.text, ok=reply.ok, error_code=reply.error_code,
                    end_conversation=reply.end_conversation, tools=[result.name for result in reply.tool_results])
            for result in reply.tool_results:
                publish("tool_result", name=result.name, ok=result.ok, error_code=result.error_code,
                        phrase=result.phrase, operation_id=result.operation_id)
            self.history.append("user", text, None, self.clock())
            self.history.append("assistant", reply.text, None, self.clock())
            self.active = True
            self.deadline = self.clock() + self.idle_seconds
            if reply.end_conversation:
                self._reset()
            status, message, error_code = "completed", "", reply.error_code
            if speak and reply.text:
                if self._cancel.is_set():
                    raise AgentCancelled()
                stages.start("tts_synthesis")
                try:
                    if self.announce is None:
                        raise SpeechFailure({"stage": "tts_synthesis", "error_code": "tts_disabled", "error_type": "Unavailable",
                                             "message": "当前语音服务未启用播报，回复文字仍可查看。"})
                    self.announce(reply.text, publish, cancel=self._cancel)
                except SpeechFailure as error:
                    # The conversation and device result are already complete.
                    # A failed announcement must not erase a pending room answer.
                    if error.details["error_code"] == "speech_cancelled":
                        publish("playback_state", source_event="tts_interrupted", interrupted=True)
                    else:
                        publish("service_error", source_event="tts_error", **error.details)
                    message, error_code = error.details["message"], error.details["error_code"]
                finally:
                    if self.active:
                        self.deadline = self.clock() + self.idle_seconds
        except AgentCancelled:
            self._reset()
            message, error_code = "已停止回复，已确认的设备操作仍然有效。", "cancelled"
        except Exception:  # keep provider details and credentials out of public errors
            self._reset()
            error_code = "text_turn_failed"
        finally:
            stages.finish("failed" if status == "failed" else status)
            # Publish completion before accepting another turn, including ignores
            # and errors, so the browser cannot remain stuck on “thinking”.
            with self._lock:
                publish("text_turn", turn_status=status, message=message, error_code=error_code)
                self._busy = False
