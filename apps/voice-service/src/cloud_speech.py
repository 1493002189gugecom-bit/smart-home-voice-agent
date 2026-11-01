"""Official DashScope WebSocket speech protocol, with PCM kept in memory."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import uuid
from urllib.parse import urlparse

import aiohttp
import numpy as np

import config
from voice_runtime import bounded, run, VoiceCancelled


def command(action, task_id, payload=None):
    return {"header": {"action": action, "task_id": task_id, "streaming": "duplex"},
            "payload": payload or {"input": {}}}


def credentials():
    key, url = config.dashscope_key(), config.speech_ws_url()
    parsed = urlparse(url)
    if not key:
        raise ValueError("dashscope_key_missing")
    if parsed.scheme != "wss" and not (parsed.scheme == "ws" and parsed.hostname in {"127.0.0.1", "localhost"}):
        raise ValueError("speech_endpoint_requires_tls")
    return key, url


async def _tts(text, emit):
    key, url = credentials()
    task_id = str(uuid.uuid4())
    payload = {"task_group": "audio", "task": "tts", "function": "SpeechSynthesizer",
               "model": config.cosyvoice_model(), "parameters": {"text_type": "PlainText",
               "voice": config.cosyvoice_voice(), "format": "pcm", "sample_rate": 24000,
               "volume": 50, "rate": 1, "pitch": 1}, "input": {}}
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.ws_connect(url, headers={"Authorization": "bearer " + key}, max_msg_size=4*1024*1024) as ws:
            await ws.send_json(command("run-task", task_id, payload))
            started = finished = False
            tail = b""
            count = 0
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    if not started:
                        raise RuntimeError("speech_protocol_order")
                    tail += message.data
                    length = len(tail) // 2 * 2
                    if length:
                        samples = np.frombuffer(tail[:length], dtype="<i2").astype(np.float32) / 32768
                        count += len(samples)
                        if count > 24000*180:
                            raise RuntimeError("speech_audio_limit")
                        await emit((samples, 24000))
                        tail = tail[length:]
                elif message.type == aiohttp.WSMsgType.TEXT:
                    event = json.loads(message.data)
                    header = event.get("header", {})
                    if header.get("task_id") != task_id:
                        raise RuntimeError("speech_task_mismatch")
                    kind = header.get("event")
                    if kind == "task-started" and not started:
                        started = True
                        await ws.send_json(command("continue-task", task_id, {"input": {"text": text}}))
                        await ws.send_json(command("finish-task", task_id))
                    elif kind == "task-failed":
                        raise RuntimeError("speech_provider_failed")
                    elif kind == "task-finished":
                        finished = True
                        break
                elif message.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED}:
                    break
            if not finished or tail or count == 0:
                raise RuntimeError("speech_incomplete_audio")


def stream_tts(text, cancel=None):
    # Bounded producer queue permits streaming output without blocking cancellation
    # on a slow output endpoint. No retries after any audio has been delivered.
    pending = queue.Queue(maxsize=16)
    stopped = threading.Event()
    class Combined:
        def is_set(self):
            return stopped.is_set() or (cancel is not None and cancel.is_set())
    token = Combined()
    async def emit(item):
        while not token.is_set():
            try:
                pending.put_nowait(item)
                return
            except queue.Full:
                await asyncio.sleep(0.01)
        raise VoiceCancelled("speech_cancelled")
    def producer():
        try:
            run(bounded(_tts(text, emit), config.speech_timeout(), token))
        except Exception as error:
            error.voice_stage = "tts_synthesis"
            while not token.is_set():
                try:
                    pending.put(error, timeout=0.05)
                    break
                except queue.Full:
                    continue
        finally:
            stopped.set()
    thread = threading.Thread(target=producer, name="cloud-tts", daemon=True)
    thread.start()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise VoiceCancelled("speech_cancelled")
            try:
                item = pending.get(timeout=0.05)
            except queue.Empty:
                if stopped.is_set():
                    return
                continue
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stopped.set()
        thread.join(timeout=1)


class RealtimeAsr:
    """Receives microphone blocks while speaking; only final results authorize admission."""
    def __init__(self, on_partial=lambda text: None):
        self.audio = queue.Queue(maxsize=256)
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.ending = threading.Event()
        self.text = ""
        self.error = None
        self.on_partial = on_partial
        self.thread = threading.Thread(target=self._worker, name="stream-asr", daemon=True)
        self.thread.start()

    def feed(self, samples):
        if self.cancel.is_set() or self.finished.is_set():
            return
        data = (np.clip(samples, -1, 1)*32767).astype("<i2").tobytes()
        try:
            self.audio.put_nowait(data)
        except queue.Full:
            self.cancel.set()
            self.error = RuntimeError("asr_queue_overflow")

    def close(self):
        self.cancel.set()
        self.thread.join(timeout=1)

    def result(self, cancel=None):
        self.ending.set()
        deadline = time.monotonic()+config.speech_timeout()+1
        while not self.finished.wait(.05):
            if cancel is not None and cancel.is_set():
                self.close()
                raise VoiceCancelled("speech_cancelled")
            if time.monotonic() >= deadline:
                self.close()
                raise TimeoutError("asr_timeout")
        if self.error:
            raise self.error
        return self.text

    def _worker(self):
        try:
            run(bounded(self._recognize(), config.speech_timeout(), self.cancel))
        except Exception as error:
            if self.error is None:
                self.error = error
        finally:
            self.finished.set()

    async def _recognize(self):
        key, url = credentials()
        task_id = str(uuid.uuid4())
        payload = {"task_group": "audio", "task": "asr", "function": "recognition",
                   "model": config._local_env("SMART_HOME_FUNASR_MODEL") or "fun-asr-realtime",
                   "parameters": {"format": "pcm", "sample_rate": 16000}, "input": {}}
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.ws_connect(url, headers={"Authorization": "bearer " + key}) as ws:
                await ws.send_json(command("run-task", task_id, payload))
                sender = None
                finals = {}
                finished = False
                async def send():
                    while not self.cancel.is_set():
                        try:
                            data = self.audio.get_nowait()
                        except queue.Empty:
                            if self.ending.is_set():
                                await ws.send_json(command("finish-task", task_id))
                                return
                            await asyncio.sleep(0.01)
                        else:
                            await ws.send_bytes(data)
                try:
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            continue
                        event = json.loads(message.data)
                        header = event.get("header", {})
                        if header.get("task_id") != task_id:
                            raise RuntimeError("asr_task_mismatch")
                        kind = header.get("event")
                        if kind == "task-started" and sender is None:
                            sender = asyncio.create_task(send())
                        elif kind == "result-generated":
                            sentence = event.get("payload", {}).get("output", {}).get("sentence", {})
                            if sentence.get("heartbeat"):
                                continue
                            text = str(sentence.get("text", ""))[:1500]
                            if sentence.get("sentence_end") is True:
                                finals[int(sentence.get("sentence_id", 0))] = text
                            else:
                                self.on_partial(text)
                        elif kind == "task-failed":
                            raise RuntimeError("asr_provider_failed")
                        elif kind == "task-finished":
                            finished = True
                            if sender and sender.done():
                                sender.result()
                            break
                    if not finished:
                        raise RuntimeError("asr_incomplete_result")
                    self.text = "".join(finals[key] for key in sorted(finals))
                finally:
                    if sender:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
