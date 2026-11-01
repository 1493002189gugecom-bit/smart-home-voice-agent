"""Real WebSocket serialization against a loopback fixture, no paid services."""
import asyncio
import threading
import time

from aiohttp import web
import numpy as np
import pytest

import config
from cloud_speech import RealtimeAsr, stream_tts
from voice_runtime import VoiceCancelled


class SpeechServer:
    def __init__(self, handler):
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self._serve, args=(handler,), daemon=True)
        self.thread.start()
        assert self.ready.wait(3)

    def _serve(self, handler):
        asyncio.set_event_loop(self.loop)
        async def wrapped(request):
            try:
                assert request.headers.get("Authorization") == "bearer fixture-key"
                return await handler(request)
            except BaseException as error:
                self.errors.append(error)
                raise
        async def start():
            app = web.Application(); app.router.add_get("/speech", wrapped)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            self.ready.set()
        self.loop.run_until_complete(start())
        self.loop.run_forever()
        self.loop.close()

    def close(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(3)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(3)


def setup_server(monkeypatch, handler):
    server = SpeechServer(handler)
    monkeypatch.setattr(config, "speech_ws_url", lambda: f"ws://127.0.0.1:{server.port}/speech")
    monkeypatch.setattr(config, "dashscope_key", lambda: "fixture-key")
    monkeypatch.setattr(config, "speech_timeout", lambda: 1)
    return server


async def begin(request, task):
    ws = web.WebSocketResponse(); await ws.prepare(request)
    start = await ws.receive_json()
    assert start["payload"]["task"] == task
    task_id = start["header"]["task_id"]
    async def event(kind, payload=None):
        await ws.send_json({"header": {"event":kind, "task_id": task_id}, "payload": payload or {}})
    await event("task-started")
    return ws, task_id, event


def test_tts_streams_pcm_before_completion_and_reassembles_odd_bytes(monkeypatch):
    release = threading.Event()
    expected = np.array([1000,-2000,3000,-4000], dtype="<i2").tobytes()
    async def handler(request):
        ws, task_id, event = await begin(request, "tts")
        text = await ws.receive_json(); end = await ws.receive_json()
        assert text["header"]["task_id"] == end["header"]["task_id"] == task_id
        assert text["payload"]["input"]["text"] == "你好"
        assert end["header"]["action"] == "finish-task"
        await ws.send_bytes(expected[:1]); await ws.send_bytes(expected[1:4])
        while not release.is_set(): await asyncio.sleep(.005)
        await ws.send_bytes(expected[4:]); await event("task-finished")
        return ws
    server = setup_server(monkeypatch, handler)
    try:
        chunks = stream_tts("你好")
        first, rate = next(chunks)
        assert len(first) == 2 and rate == 24000
        release.set()
        remaining = list(chunks)
        audio = np.concatenate([first]+[row[0] for row in remaining])
        np.testing.assert_allclose(audio, np.frombuffer(expected, dtype="<i2")/32768)
        assert not server.errors
    finally:
        release.set(); server.close()


def test_tts_truncated_stream_reports_failure_instead_of_success(monkeypatch):
    async def handler(request):
        ws, _, _ = await begin(request, "tts")
        await ws.receive_json(); await ws.receive_json()
        await ws.send_bytes(b"\x00\x01"); await ws.close()
        return ws
    server = setup_server(monkeypatch, handler)
    try:
        with pytest.raises(RuntimeError, match="incomplete_audio"):
            list(stream_tts("你好"))
    finally:
        server.close()


def test_asr_uses_final_sentence_and_never_a_retracted_partial(monkeypatch):
    partials = []
    async def handler(request):
        ws, _, event = await begin(request, "asr")
        message = await ws.receive()
        assert len(message.data) > 0
        await event("result-generated", {"output":{"sentence":{"sentence_id":1,"text":"打开空调","sentence_end":False}}})
        while True:
            message = await ws.receive()
            if message.type == web.WSMsgType.TEXT:
                assert message.json()["header"]["action"] == "finish-task"
                break
        await event("result-generated", {"output":{"sentence":{"sentence_id":1,"text":"不要开空调","sentence_end":True}}})
        await event("task-finished")
        return ws
    server = setup_server(monkeypatch, handler)
    try:
        recognizer = RealtimeAsr(partials.append)
        recognizer.feed(np.ones(512,dtype=np.float32)*.1)
        assert recognizer.result() == "不要开空调"
        assert partials == ["打开空调"] and not server.errors
        recognizer.close()
    finally:
        server.close()


def test_cloud_speech_requires_explicit_key(monkeypatch):
    monkeypatch.setattr(config, "dashscope_key", lambda: "")
    with pytest.raises(ValueError, match="key_missing"):
        list(stream_tts("你好"))
