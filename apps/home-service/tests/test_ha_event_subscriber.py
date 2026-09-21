import asyncio
import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from ha_event_subscriber import HAEventSubscriber, websocket_url  # noqa: E402
from event_stream import EventHub  # noqa: E402


class FakeMessage:
    def __init__(self, payload):
        self.type = "TEXT"
        self.data = json.dumps(payload)


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = [FakeMessage(item) for item in messages]
        self.sent = []

    async def receive_json(self):
        message = self.messages.pop(0)
        return json.loads(message.data)

    async def send_json(self, payload):
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class FakeWebSocketContext:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, websocket):
        self.websocket = websocket
        self.urls = []

    def ws_connect(self, url, **_kwargs):
        self.urls.append(url)
        return FakeWebSocketContext(self.websocket)

    async def close(self):
        return None


def state_event(entity_id):
    return {
        "id": 1,
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "data": {"entity_id": entity_id},
        },
    }


def test_websocket_url_preserves_path_prefix_and_switches_scheme():
    assert websocket_url("http://127.0.0.1:8123") == "ws://127.0.0.1:8123/api/websocket"
    assert websocket_url("https://ha.example/prefix/") == "wss://ha.example/prefix/api/websocket"


def test_subscriber_authenticates_and_filters_catalog_entities():
    async def scenario():
        websocket = FakeWebSocket(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"id": 1, "type": "result", "success": True},
                state_event("light.ignored"),
                state_event("light.shv_bedroom_light"),
            ]
        )
        session = FakeSession(websocket)
        snapshots = EventHub()
        statuses = []

        def publish_snapshot(entity_id):
            snapshots.publish("snapshot", {"changed_entity_id": entity_id})

        subscriber = HAEventSubscriber(
            "http://127.0.0.1:8123",
            "secret-token",
            {"light.shv_bedroom_light"},
            publish_snapshot,
            statuses.append,
            session_factory=lambda: session,
        )

        await subscriber.run_once()

        assert session.urls == ["ws://127.0.0.1:8123/api/websocket"]
        assert websocket.sent == [
            {"type": "auth", "access_token": "secret-token"},
            {"id": 1, "type": "subscribe_events", "event_type": "state_changed"},
        ]
        snapshot = snapshots.wait_after(0, timeout=0)
        assert snapshot is not None
        assert snapshot.data == {"changed_entity_id": "light.shv_bedroom_light"}
        assert snapshots.wait_after(snapshot.id, timeout=0) is None
        assert statuses == ["upstream_connected", "upstream_disconnected"]
        assert "secret-token" not in repr(statuses)

    asyncio.run(scenario())


def test_subscriber_rejects_failed_auth_without_exposing_token():
    async def scenario():
        websocket = FakeWebSocket(
            [{"type": "auth_required"}, {"type": "auth_invalid", "message": "secret-token"}]
        )
        session = FakeSession(websocket)
        statuses = []
        subscriber = HAEventSubscriber(
            "http://127.0.0.1:8123",
            "secret-token",
            {"light.allowed"},
            lambda _entity_id: None,
            statuses.append,
            session_factory=lambda: session,
        )

        try:
            await subscriber.run_once()
        except RuntimeError as exc:
            assert str(exc) == "ha_auth_failed"
        else:
            raise AssertionError("authentication failure must raise")

        assert "secret-token" not in repr(statuses)

    asyncio.run(scenario())


def test_reconnect_failures_use_capped_backoff(monkeypatch):
    class FailingSession:
        def ws_connect(self, _url, **_kwargs):
            raise OSError("HA is offline")

        async def close(self):
            return None

    async def scenario():
        delays = []
        statuses = []

        async def record_wait(_wait, delay):
            delays.append(delay)
            return False

        monkeypatch.setattr(asyncio, "to_thread", record_wait)
        subscriber = None

        def on_status(value):
            statuses.append(value)
            if len(statuses) == 7:
                subscriber.stop()

        subscriber = HAEventSubscriber(
            "http://127.0.0.1:8123",
            "secret-token",
            {"light.allowed"},
            lambda _entity_id: None,
            on_status,
            session_factory=FailingSession,
        )

        await subscriber.run_forever()

        assert delays == [1, 2, 4, 8, 16, 30]
        assert statuses == ["upstream_error"] * 7

    asyncio.run(scenario())
