# Digital Twin SSE Push Implementation Plan

> **已完成：计划 2026-09-16，完成 2026-09-17（未提交）。** 后端 `/events`、HA WebSocket 订阅、
> 100 ms 防抖、慢客户端快照合并、退避重连，以及 Unity/Blender 只读客户端均已实现并实测：
> 后端/HA 194 项、Unity EditMode 22/22、PlayMode 5/5、Blender 协议 3/3。证据汇总见根
> `task_plan.md`“数字孪生 SSE 推送（完成，未提交）”。本文件所在目录 `plans/completed/`
> 即“已完成”集合；复选框按惯例保持未勾选。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push Home Assistant state changes through a loopback-only SSE stream to read-only Blender and Unity digital-twin clients.

**Architecture:** A bounded `EventHub` publishes canonical snapshots. In HA mode an `aiohttp` WebSocket subscriber listens to filtered `state_changed` events and prompts a fresh normalized snapshot; in memory mode successful mutations publish through the same hub. Blender consumes SSE on a worker thread and applies state via `bpy.app.timers`; Unity consumes SSE with `UnityWebRequest` and a chunk-safe decoder, falling back to the old polling path only when `/events` is unavailable.

**Tech Stack:** Python 3.11, `aiohttp`, `ThreadingHTTPServer`, pytest, Blender 5.2 Python API, Unity 2022.3, C#, UnityWebRequest, NUnit.

**Execution Status (2026-09-17):** Complete. Tasks 1–7 were implemented and verified without committing or pushing. Final evidence: 194 backend/HA tests, 22 Unity EditMode tests, 5 Unity PlayMode tests, 3 Blender protocol tests, Blender offline scene application, Blender live read-only SSE, two idempotent Unity generations, one generated root, and zero Missing Script references.

---

### Task 1: Bounded event hub and canonical snapshots

**Files:**
- Create: `apps/home-service/src/event_stream.py`
- Create: `apps/home-service/tests/test_event_stream.py`

- [ ] **Step 1: Write failing tests for event ordering, bounded history and canonical HA flattening**

```python
def test_event_hub_returns_latest_event_after_sequence():
    hub = EventHub(history_size=2)
    first = hub.publish("status", {"state": "connected"})
    second = hub.publish("snapshot", {"version": 1})
    assert hub.wait_after(first.id, timeout=0) == second


def test_slow_subscriber_receives_latest_snapshot_after_history_compaction():
    hub = EventHub(history_size=2)
    old = hub.publish("snapshot", {"version": 1})
    hub.publish("snapshot", {"version": 2})
    latest = hub.publish("snapshot", {"version": 3})
    assert hub.wait_after(old.id, timeout=0) == latest


def test_canonical_ha_snapshot_flattens_devices_and_uses_integer_versions():
    payload = {"ok": True, "data": {"rooms": [{
        "id": "living_room", "name": "客厅", "devices": [{
            "id": "living_room_light", "version": "2026-09-16T00:00:00+08:00",
            "online": True, "type": "light", "state": {"on": True, "brightness": 50}
        }]
    }]}}
    snapshot = canonical_ha_snapshot(payload, sequence=7, generated_at_ms=123)
    assert snapshot["version"] == 7
    assert snapshot["devices"][0]["version"] == 7
    assert snapshot["devices"][0]["room_id"] == "living_room"
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_event_stream.py -q
```

Expected: collection fails because `event_stream` does not exist.

- [ ] **Step 3: Implement `StreamEvent`, `EventHub`, SSE encoding and canonical converters**

```python
@dataclass(frozen=True)
class StreamEvent:
    id: int
    event: str
    data: dict[str, Any]


class EventHub:
    def __init__(self, history_size: int = 32):
        self._condition = threading.Condition()
        self._events = deque(maxlen=history_size)
        self._next_id = 1

    def publish(self, event: str, data: dict[str, Any]) -> StreamEvent:
        with self._condition:
            item = StreamEvent(self._next_id, event, data)
            self._next_id += 1
            self._events.append(item)
            self._condition.notify_all()
            return item

    def wait_after(self, last_id: int, timeout: float) -> StreamEvent | None:
        with self._condition:
            item = self._find_after(last_id)
            if item is None and timeout > 0:
                self._condition.wait(timeout)
                item = self._find_after(last_id)
            return item
```

`encode_sse` must emit UTF-8 `id`, `event`, one `data` line containing compact JSON, and a blank terminator. Canonical memory and HA snapshots must always contain integer `version`, `backend`, `generated_at_ms`, `rooms`, flattened `devices`, `persons`, `broadcasts`, and `broadcast_queue`.

- [ ] **Step 4: Run Task 1 tests and the existing backend suite**

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_event_stream.py apps/home-service/tests/test_json_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Inspect the diff without committing**

```powershell
git diff -- apps/home-service/src/event_stream.py apps/home-service/tests/test_event_stream.py
```

### Task 2: Home Assistant WebSocket event subscriber

**Files:**
- Create: `apps/home-service/src/ha_event_subscriber.py`
- Create: `apps/home-service/tests/test_ha_event_subscriber.py`

- [ ] **Step 1: Write failing async tests for authentication, filtering and reconnect status**

```python
def test_subscriber_authenticates_and_filters_catalog_entities(fake_ws):
    async def scenario():
        changed = []
        statuses = []
        subscriber = HAEventSubscriber(
            "http://127.0.0.1:8123", "secret",
            {"light.allowed"}, changed.append, statuses.append,
            session_factory=fake_ws.session_factory,
        )
        fake_ws.messages.extend([
            {"type": "auth_required"}, {"type": "auth_ok"},
            {"id": 1, "type": "result", "success": True},
            state_event("light.ignored"), state_event("light.allowed"),
        ])
        await subscriber.run_once()
        assert fake_ws.sent[0] == {"type": "auth", "access_token": "secret"}
        assert fake_ws.sent[1] == {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
        assert changed == ["light.allowed"]
        assert "secret" not in repr(statuses)

    asyncio.run(scenario())
```

- [ ] **Step 2: Run the test and confirm RED**

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_ha_event_subscriber.py -q
```

Expected: import failure for the missing subscriber.

- [ ] **Step 3: Implement the subscriber with a daemon-thread lifecycle**

```python
class HAEventSubscriber:
    def __init__(self, base_url, token, entity_ids, on_change, on_status, *, session_factory=None):
        self.ws_url = websocket_url(base_url)
        self._token = token
        self._entity_ids = frozenset(entity_ids)
        self._on_change = on_change
        self._on_status = on_status
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=lambda: asyncio.run(self.run_forever()), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
```

`run_once` must follow HA's `auth_required` → `auth` → `auth_ok` → `subscribe_events(state_changed)` protocol, ignore non-catalog entities, and emit only sanitized status codes. `run_forever` must use 1, 2, 4, 8, 16, 30 second backoff and stop promptly.

- [ ] **Step 4: Run subscriber tests**

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_ha_event_subscriber.py -q
```

Expected: all tests pass and no secret appears in output.

### Task 3: `/events` SSE endpoint and application integration

**Files:**
- Modify: `apps/home-service/src/server.py`
- Modify: `apps/home-service/README.md`
- Create: `apps/home-service/tests/test_sse_endpoint.py`

- [ ] **Step 1: Write failing HTTP integration tests**

```python
def test_events_sends_snapshot_immediately(running_memory_server):
    response = open_sse(running_memory_server.url + "/events")
    event = read_event(response)
    assert event["event"] == "snapshot"
    assert event["data"]["backend"] == "memory"


def test_successful_memory_mutation_publishes_without_polling(running_memory_server):
    response = open_sse(running_memory_server.url + "/events")
    first = read_event(response)
    post_json(running_memory_server.url + "/tool/set_light", valid_light_command())
    second = read_event(response)
    assert second["id"] > first["id"]
    assert device(second, "living_room_light")["state"]["on"] is True
```

- [ ] **Step 2: Run the endpoint tests and confirm RED**

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_sse_endpoint.py -q
```

Expected: `/events` returns 404.

- [ ] **Step 3: Add `LiveStateStream` and SSE handling to the threaded server**

```python
class _SSEThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def _respond_sse(self):
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("Connection", "keep-alive")
    self.end_headers()
    last_id = 0
    while True:
        item = self.stream.hub.wait_after(last_id, 15.0)
        payload = b": heartbeat\n\n" if item is None else encode_sse(item)
        self.wfile.write(payload)
        self.wfile.flush()
        if item is not None:
            last_id = item.id
```

`LiveStateStream` publishes the initial snapshot, publishes after successful memory mutations, starts the HA subscriber in HA mode, debounces matching HA changes for 100 ms, and republishes a normalized current snapshot. Broken pipe/reset closes only that subscriber connection. Existing routes and payloads remain unchanged.

- [ ] **Step 4: Run endpoint, backend and HA suites**

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests tools/ha-check/tests -q
```

Expected: zero failures.

### Task 4: Blender read-only live-state addon

**Files:**
- Create: `E:/unityroom/unityroom/Assets/SmartHome/BlenderAddon/smart_home_live/protocol.py`
- Create: `E:/unityroom/unityroom/Assets/SmartHome/BlenderAddon/smart_home_live/__init__.py`
- Create: `E:/unityroom/unityroom/Assets/SmartHome/BlenderAddon/tests/test_protocol.py`
- Create: `E:/unityroom/unityroom/Assets/SmartHome/BlenderAddon/tests/blender_apply_test.py`
- Modify: `E:/unityroom/unityroom/Assets/SmartHome/README.md`

- [ ] **Step 1: Write failing pure-Python tests for chunked SSE decoding and display text**

```python
def test_decoder_handles_utf8_event_split_across_chunks():
    decoder = SSEDecoder()
    raw = 'id: 4\nevent: snapshot\ndata: {"devices":[{"name":"客厅灯"}]}\n\n'.encode()
    events = decoder.feed(raw[:23]) + decoder.feed(raw[23:])
    assert events[0].event == "snapshot"
    assert json.loads(events[0].data)["devices"][0]["name"] == "客厅灯"


def test_format_device_state_covers_online_offline_and_temperature():
    assert format_device_state(light(on=True, brightness=50)) == "客厅灯  开 50%"
    assert format_device_state(sensor(temperature=26)) == "室内温度 26°C"
    assert format_device_state(offline_ac()) == "卧室空调  离线"
```

- [ ] **Step 2: Run tests and confirm RED**

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest E:\unityroom\unityroom\Assets\SmartHome\BlenderAddon\tests\test_protocol.py -q
```

Expected: missing module failure.

- [ ] **Step 3: Implement the protocol module and Blender addon**

`protocol.py` contains no `bpy` import and exposes `SSEDecoder`, `StreamEvent`, `devices_by_id`, and `format_device_state`.

`__init__.py` registers:

```python
class SmartHomeLiveSettings(bpy.types.PropertyGroup):
    base_url: bpy.props.StringProperty(default="http://127.0.0.1:8765")
    auto_connect: bpy.props.BoolProperty(default=True)
    connected: bpy.props.BoolProperty(default=False)
    status: bpy.props.StringProperty(default="未连接")


class SMARTHOME_PT_live_panel(bpy.types.Panel):
    bl_label = "智能家居实时状态"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Smart Home"
```

A daemon worker performs only `GET {base_url}/events`; it queues parsed events. A registered Blender timer drains the queue on the main thread. Device roots are found only by exact `smart_home_id`; labels use `Label_<id>`, status parts use exact suffixes, and active lights use `<id>_Glow`. Missing devices become gray with `暂无数据`. Load/unload handlers start and stop only for marked Smart Home scenes.

- [ ] **Step 4: Run pure parser tests and Blender headless apply test**

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest E:\unityroom\unityroom\Assets\SmartHome\BlenderAddon\tests\test_protocol.py -q
& 'E:\blender\blender.exe' --background E:\unityroom\unityroom\Assets\SmartHome\Blender\SmartHomeDigitalTwin.blend --python E:\unityroom\unityroom\Assets\SmartHome\BlenderAddon\tests\blender_apply_test.py
```

Expected: parser tests pass; Blender exits 0 after asserting four exact device bindings, label changes, light intensity, and offline/no-data behavior.

- [ ] **Step 5: Install and enable the addon in the current Blender**

Copy `smart_home_live` to `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\smart_home_live`, refresh addons, enable `smart_home_live`, save preferences, and reconnect the currently open digital-twin scene. Do not write credentials.

### Task 5: Unity chunk-safe SSE decoder

**Files:**
- Create: `E:/unityroom/unityroom/Assets/SmartHome/Runtime/HomeServiceEventStream.cs`
- Create: `E:/unityroom/unityroom/Assets/SmartHome/Tests/EditMode/HomeServiceEventStreamTests.cs`

- [ ] **Step 1: Write failing EditMode tests**

```csharp
[Test]
public void SplitUtf8AndMultipleEventsAreDecoded()
{
    var decoder = new HomeServiceEventDecoder();
    byte[] bytes = Encoding.UTF8.GetBytes("id: 1\nevent: snapshot\ndata: {\"rooms\":[{\"name\":\"客厅\"}]}\n\nid: 2\nevent: status\ndata: {\"state\":\"connected\"}\n\n");
    decoder.Append(bytes, 0, 17);
    decoder.Append(bytes, 17, bytes.Length - 17);
    Assert.AreEqual(2, decoder.Drain().Count);
}

[Test]
public void HeartbeatCommentsDoNotCreateEvents()
{
    var decoder = new HomeServiceEventDecoder();
    decoder.Append(Encoding.UTF8.GetBytes(": heartbeat\n\n"));
    Assert.IsEmpty(decoder.Drain());
}
```

- [ ] **Step 2: Run EditMode and confirm RED**

```powershell
D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe -batchmode -nographics -quit -projectPath E:\unityroom\unityroom -runTests -testPlatform EditMode -testFilter SmartHome.Tests.HomeServiceEventStreamTests -testResults E:\unityroom\unityroom\Logs\SseEditMode.xml -logFile E:\unityroom\unityroom\Logs\SseEditMode.log
```

Expected: compile failure because decoder is missing.

- [ ] **Step 3: Implement UTF-8-safe streaming decoder and DownloadHandler**

```csharp
public sealed class HomeServiceEvent
{
    public string Id { get; set; }
    public string Event { get; set; }
    public string Data { get; set; }
}

public sealed class HomeServiceEventDecoder
{
    private readonly Decoder _utf8 = Encoding.UTF8.GetDecoder();
    private readonly StringBuilder _buffer = new StringBuilder();
    private readonly Queue<HomeServiceEvent> _events = new Queue<HomeServiceEvent>();

    public void Append(byte[] data, int offset, int count)
    {
        int charCount = _utf8.GetCharCount(data, offset, count, false);
        var chars = new char[charCount];
        _utf8.GetChars(data, offset, count, chars, 0, false);
        _buffer.Append(chars);
        ParseFrames();
    }

    public List<HomeServiceEvent> Drain()
    {
        var result = new List<HomeServiceEvent>(_events.Count);
        while (_events.Count > 0) result.Add(_events.Dequeue());
        return result;
    }

    private void ParseFrames()
    {
        string normalized = _buffer.ToString().Replace("\r\n", "\n");
        int end;
        while ((end = normalized.IndexOf("\n\n", StringComparison.Ordinal)) >= 0)
        {
            ParseFrame(normalized.Substring(0, end));
            normalized = normalized.Substring(end + 2);
        }
        _buffer.Clear().Append(normalized);
    }

    private void ParseFrame(string frame)
    {
        if (string.IsNullOrWhiteSpace(frame) || frame.StartsWith(":")) return;
        var item = new HomeServiceEvent { Event = "message", Data = string.Empty };
        var data = new StringBuilder();
        foreach (string line in frame.Split('\n'))
        {
            if (line.StartsWith("id:")) item.Id = line.Substring(3).TrimStart();
            else if (line.StartsWith("event:")) item.Event = line.Substring(6).TrimStart();
            else if (line.StartsWith("data:"))
            {
                if (data.Length > 0) data.Append('\n');
                data.Append(line.Substring(5).TrimStart());
            }
        }
        item.Data = data.ToString();
        if (item.Data.Length > 0) _events.Enqueue(item);
    }
}

internal sealed class HomeServiceDownloadHandler : DownloadHandlerScript
{
    protected override bool ReceiveData(byte[] data, int dataLength)
    {
        decoder.Append(data, 0, dataLength);
        return true;
    }
}
```

- [ ] **Step 4: Run focused EditMode tests**

Expected: zero failures in `SseEditMode.xml`.

### Task 6: Unity stream-first client integration

**Files:**
- Modify: `E:/unityroom/unityroom/Assets/SmartHome/Runtime/HomeServiceClient.cs`
- Modify: `E:/unityroom/unityroom/Assets/SmartHome/Runtime/HomeStateModels.cs`
- Modify: `E:/unityroom/unityroom/Assets/SmartHome/Tests/PlayMode/GeneratedSceneIntegrationTests.cs`
- Modify: `E:/unityroom/unityroom/Assets/SmartHome/README.md`

- [ ] **Step 1: Add failing tests for canonical SSE snapshot and stream preference**

```csharp
[Test]
public void CanonicalSseSnapshotParsesForHaBackend()
{
    HomeSnapshot snapshot = HomeSnapshot.FromStreamJson(Fixtures.CanonicalHaStreamSnapshot);
    Assert.IsTrue(snapshot.Devices["living_room_light"].state.on);
    Assert.AreEqual(26f, snapshot.Devices["indoor_temperature"].state.temperature);
}
```

The PlayMode fake server records paths. Assert `/events` is opened once, a pushed snapshot updates the generated scene, and neither `/snapshot` nor `/tool/room_status` is requested while the stream is healthy.

- [ ] **Step 2: Run focused tests and confirm RED**

Use Unity batchmode with test filters for the new EditMode and PlayMode fixtures. Expected failure: missing stream parser/client behavior.

- [ ] **Step 3: Replace polling startup with stream-first connection**

```csharp
private IEnumerator ConnectionLoop()
{
    while (enabled)
    {
        bool legacyFallback = false;
        yield return ConnectEventStream(value => legacyFallback = value);
        if (legacyFallback)
            yield return PollLegacyUntilDisconnected();
        else
            yield return new WaitForSecondsRealtime(reconnectDelaySeconds);
    }
}
```

`ConnectEventStream` targets `/events`, drains complete events each frame, parses `snapshot` with `HomeSnapshot.FromStreamJson`, reports `status`, and treats only HTTP 404 as legacy fallback. Cancellation occurs in `OnDisable`. Existing public events and `SmartHomeSceneController` remain unchanged.

- [ ] **Step 4: Run all Unity EditMode and PlayMode tests**

```powershell
D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe -batchmode -nographics -quit -projectPath E:\unityroom\unityroom -runTests -testPlatform EditMode -testResults E:\unityroom\unityroom\Logs\EditMode.xml -logFile E:\unityroom\unityroom\Logs\EditMode.log
D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe -batchmode -nographics -quit -projectPath E:\unityroom\unityroom -runTests -testPlatform PlayMode -testResults E:\unityroom\unityroom\Logs\PlayMode.xml -logFile E:\unityroom\unityroom\Logs\PlayMode.log
```

Expected: both runners exit 0 and report zero failures.

### Task 7: End-to-end read-only verification

**Files:**
- Modify only if verification exposes a tested defect.

- [ ] **Step 1: Restart HA-backed home-service with the new code**

Keep the existing HA environment variables and port 8765. Verify `/health` says `backend: ha` and `/events` returns an immediate `snapshot` event.

- [ ] **Step 2: Verify Blender live state**

Open the current digital-twin `.blend`, enable the addon, and confirm exact live values from the current HA snapshot: living-room light on at 50%, desk plug off, indoor temperature 26°C, bedroom AC on/cool at 24°C. Capture a viewport or render screenshot.

- [ ] **Step 3: Verify Unity against the same stream**

Regenerate the scene twice, confirm one `SmartHomeGenerated` root and zero Missing Scripts, run the generated scene, and confirm the HUD reports the SSE/HA connection and the same four device states.

- [ ] **Step 4: Verify passive behavior without controlling real devices**

Use the fake HA WebSocket integration test to emit a catalogued `state_changed` event. Confirm both client test harnesses receive the new snapshot and that request logs contain no POST from Blender or Unity.

- [ ] **Step 5: Run final full suites and inspect artifacts**

Run backend pytest, Unity EditMode, Unity PlayMode, Blender headless apply test, `git diff --check` in `E:\smart-home`, and inspect Unity project file changes. Report exact test counts, paths, screenshots, and any unavailable verification. Do not commit or push.
