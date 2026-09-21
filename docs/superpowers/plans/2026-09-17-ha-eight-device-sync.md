# HA Eight-Device State Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register all eight digital-twin devices in Home Assistant and make the HA-backed SSE snapshot match their authoritative HA states.

**Architecture:** Extend the existing MQTT simulator with three lights, three climate devices, one plug, and one temperature sensor while preserving retained state and availability semantics. Expand `ha_entities.json` with stable one-to-one IDs, then run `home-service` explicitly with the HA backend and verify HA → SSE equality without sending control requests.

**Tech Stack:** Python 3.11, pytest, paho-mqtt, MQTT Discovery, Home Assistant 2026.6, Docker Compose, aiohttp SSE.

**Spec:** `docs/superpowers/specs/2026-09-17-ha-unity-realtime-effects-design.md`

## Global Constraints

- The eight stable device IDs and HA entity IDs must match the mapping table in the spec exactly.
- Unity and Blender remain read-only and never receive HA or MQTT credentials.
- MQTT remains on the Compose-internal network; do not publish port 1883 to the host.
- Do not silently fall back from the HA backend to Memory state.
- Live verification may read current states and create the four approved local demo entities, but must not toggle real devices.
- The working tree already contains unrelated SSE work; preserve it and do not commit or push unless the user explicitly requests that action.

---

### Task 1: Expand the persistent simulator model to eight devices

**Files:**
- Modify: `apps/device-simulator/src/model.py`
- Modify: `apps/device-simulator/tests/test_model.py`

**Interfaces:**
- Produces: `LIGHTS`, `AIR_CONDITIONERS`, `DEVICES`, `initial_state()`, `validate_state(state)`, `transition(state, device, action, payload)`, and backward-compatible `load_state(path)`.
- Consumes: existing JSON state file written by `save_state(path, state)`.

- [ ] **Step 1: Add failing parameterized model tests**

Add tests that assert the complete device set and generic light/AC transitions:

```python
from model import AIR_CONDITIONERS, DEVICES, LIGHTS


def test_initial_state_contains_all_scene_devices():
    assert LIGHTS == ("living_room_light", "bedroom_light", "kitchen_light")
    assert AIR_CONDITIONERS == ("living_room_ac", "bedroom_ac", "kitchen_ac")
    assert set(initial_state()) == set(DEVICES) == {
        "living_room_light", "living_room_ac", "desk_plug",
        "indoor_temperature", "bedroom_light", "bedroom_ac",
        "kitchen_light", "kitchen_ac",
    }


@pytest.mark.parametrize("device", ["living_room_light", "bedroom_light", "kitchen_light"])
def test_every_light_accepts_the_same_command_contract(device):
    changed = transition(initial_state(), device, "set", '{"state":"ON","brightness":35}')
    assert changed[device] == {"state": "ON", "brightness": 35}


@pytest.mark.parametrize("device", ["living_room_ac", "bedroom_ac", "kitchen_ac"])
def test_every_ac_accepts_mode_and_temperature(device):
    cooled = transition(initial_state(), device, "mode/set", "cool")
    cooled = transition(cooled, device, "temperature/set", "18")
    assert cooled[device]["mode"] == "cool"
    assert cooled[device]["target_temperature"] == 18.0
```

Add a migration test using the old four-device persisted shape:

```python
def test_load_migrates_the_previous_four_device_state(tmp_path):
    path = tmp_path / "state.json"
    old = {
        "living_room_light": {"state": "ON", "brightness": 67},
        "bedroom_ac": {"mode": "cool", "target_temperature": 22.0, "current_temperature": 26.0},
        "desk_plug": {"state": "OFF", "power": 0},
        "indoor_temperature": {"temperature": 26.0},
    }
    path.write_text(json.dumps(old), encoding="utf-8")
    loaded = load_state(path)
    assert loaded["living_room_light"]["brightness"] == 67
    assert loaded["living_room_ac"] == initial_state()["living_room_ac"]
    assert loaded["bedroom_light"] == initial_state()["bedroom_light"]
    assert loaded["kitchen_light"] == initial_state()["kitchen_light"]
    assert loaded["kitchen_ac"] == initial_state()["kitchen_ac"]
```

- [ ] **Step 2: Run the model tests and confirm red state**

Run:

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests/test_model.py -v
```

Expected: failures because `LIGHTS`, `AIR_CONDITIONERS`, and the four new states do not exist.

- [ ] **Step 3: Generalize the model implementation**

Define stable groups and build defaults for all devices:

```python
LIGHTS = ("living_room_light", "bedroom_light", "kitchen_light")
AIR_CONDITIONERS = ("living_room_ac", "bedroom_ac", "kitchen_ac")
DEVICES = (*LIGHTS, *AIR_CONDITIONERS, "desk_plug", "indoor_temperature")


def _default_ac():
    return {"mode": "off", "target_temperature": 26.0, "current_temperature": 26.0}


def initial_state():
    return {
        "living_room_light": {"state": "OFF", "brightness": 50},
        "bedroom_light": {"state": "OFF", "brightness": 40},
        "kitchen_light": {"state": "OFF", "brightness": 60},
        "living_room_ac": _default_ac(),
        "bedroom_ac": _default_ac(),
        "kitchen_ac": _default_ac(),
        "desk_plug": {"state": "OFF", "power": 0},
        "indoor_temperature": {"temperature": 26.0},
    }
```

Loop through `LIGHTS` and `AIR_CONDITIONERS` in `validate_state`, `transition`, and `with_temperature`. In `load_state`, merge a persisted subset into `initial_state()` only when its keys are a subset of `DEVICES`, then validate the merged result; continue rejecting unknown devices and malformed records.

- [ ] **Step 4: Run the focused and full simulator tests**

Run:

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests/test_model.py -v
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests -v
```

Expected: all simulator tests pass and the old persisted state remains readable.

- [ ] **Step 5: Record a review checkpoint**

Review `git diff -- apps/device-simulator/src/model.py apps/device-simulator/tests/test_model.py` and leave the changes uncommitted unless the user requests a commit.

---

### Task 2: Publish Discovery, command, availability, and retained state for eight devices

**Files:**
- Modify: `apps/device-simulator/src/discovery.py`
- Modify: `apps/device-simulator/src/runtime.py`
- Modify: `apps/device-simulator/tests/test_discovery.py`
- Modify: `apps/device-simulator/tests/test_runtime.py`

**Interfaces:**
- Consumes: `LIGHTS`, `AIR_CONDITIONERS`, `DEVICES`, and the eight-device state from Task 1.
- Produces: `command_topics(prefix)`, `discovery_messages(prefix)`, `state_messages(state, prefix)`, and `parse_command_topic(topic, prefix)` for every controllable device.

- [ ] **Step 1: Write failing discovery contract tests**

Add exact expectations:

```python
def test_discovery_registers_all_eight_stable_entity_ids():
    messages = discovery_messages("shv")
    assert len(messages) == 8
    assert {message.topic for message in messages} == {
        "homeassistant/light/shv_living_room_light/config",
        "homeassistant/light/shv_bedroom_light/config",
        "homeassistant/light/shv_kitchen_light/config",
        "homeassistant/climate/shv_living_room_ac/config",
        "homeassistant/climate/shv_bedroom_ac/config",
        "homeassistant/climate/shv_kitchen_ac/config",
        "homeassistant/switch/shv_desk_plug/config",
        "homeassistant/sensor/shv_indoor_temperature/config",
    }


def test_complete_state_publication_has_eleven_retained_messages():
    messages = state_messages(initial_state(), "shv")
    assert len(messages) == 11
    assert all(message.retain and message.qos == 1 for message in messages)
```

Parameterize topic parsing over all three lights and all three ACs. Extend the reconnect runtime test to assert eight per-device availability topics and all eleven state topics.

- [ ] **Step 2: Run discovery/runtime tests and confirm red state**

Run:

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests/test_discovery.py apps/device-simulator/tests/test_runtime.py -v
```

Expected: failures report missing new Discovery and state topics.

- [ ] **Step 3: Generalize Discovery generation**

Expand labels and generate publications from the stable groups:

```python
CHINESE_LABELS = {
    "shv_living_room_light": "客厅灯",
    "shv_bedroom_light": "卧室灯",
    "shv_kitchen_light": "厨房灯",
    "shv_living_room_ac": "客厅空调",
    "shv_bedroom_ac": "卧室空调",
    "shv_kitchen_ac": "厨房空调",
    "shv_desk_plug": "智能插座",
    "shv_indoor_temperature": "室内温度",
}
```

For each light publish JSON schema with brightness scale 100. For each AC publish mode and target-temperature topics, modes `off/cool/fan_only`, range `16–30`, step `0.5`, and current temperature from `shv/indoor_temperature/state`. Preserve the no-`device`-block naming invariant so HA produces the exact entity IDs.

Generate `state_messages` as three light JSON states, six AC mode/temperature states, one plug state, and one sensor state. Derive `parse_command_topic` from the same light/AC groups so unsupported topics still fail closed.

- [ ] **Step 4: Run all simulator tests**

Run:

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests -v
```

Expected: all tests pass, including reconnect and fault-isolation cases.

- [ ] **Step 5: Record a review checkpoint**

Review the simulator diff and confirm no MQTT credential, host port, or non-`shv_` entity was added.

---

### Task 3: Expand the home-service HA catalog and SSE contract tests

**Files:**
- Modify: `apps/home-service/config/ha_entities.json`
- Modify: `apps/home-service/config/rooms.json`
- Modify: `apps/home-service/tests/test_ha_control.py`
- Modify: `apps/home-service/tests/test_integration.py`
- Modify: `apps/home-service/tests/test_event_stream.py`
- Modify: `apps/home-service/tests/test_ha_event_subscriber.py`

**Interfaces:**
- Consumes: exact entity IDs published by Task 2.
- Produces: eight catalog records consumed by `load_catalog`, HA reads, event filtering, and normalized snapshots.

- [ ] **Step 1: Write failing real-catalog tests**

Add a catalog test:

```python
def test_project_catalog_maps_all_eight_scene_devices():
    catalog = load_catalog(CATALOG_PATH)
    assert set(catalog) == {
        "living_room_light", "living_room_ac", "desk_plug",
        "indoor_temperature", "bedroom_light", "bedroom_ac",
        "kitchen_light", "kitchen_ac",
    }
    assert catalog["living_room_ac"]["entity_id"] == "climate.shv_living_room_ac"
    assert catalog["bedroom_light"]["entity_id"] == "light.shv_bedroom_light"
    assert catalog["kitchen_light"]["entity_id"] == "light.shv_kitchen_light"
    assert catalog["kitchen_ac"]["entity_id"] == "climate.shv_kitchen_ac"
```

Add a Memory configuration regression test that reads `/snapshot` and asserts its device IDs are the same eight stable IDs. This keeps the compatibility backend structurally aligned without treating its values as HA truth.

Extend the event subscriber fixture with a configured new entity and assert its `state_changed` event produces a snapshot, while an arbitrary noncatalog entity remains ignored.

- [ ] **Step 2: Run focused home-service tests and confirm red state**

Run:

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_ha_control.py apps/home-service/tests/test_event_stream.py apps/home-service/tests/test_ha_event_subscriber.py -v
```

Expected: the real-catalog test fails with four missing IDs.

- [ ] **Step 3: Add the four exact catalog entries**

Add `living_room_ac`, `bedroom_light`, `kitchen_light`, and `kitchen_ac` using the same light/climate schemas as the existing records. Use areas `ke_ting`, `wo_shi`, and `chu_fang`; lights use `turn_on/turn_off` with brightness scale 255; ACs use `set_hvac_mode/set_temperature`, modes `off/cool/fan_only`, temperature range `16–30`, default `cool`, comfort `26`, step `0.5`.

Add `desk_plug` and `indoor_temperature` to `rooms.json` so its `devices` list also contains the same eight stable IDs; preserve the existing six device defaults and use `{"on": false, "power": 0}` for the plug plus `{"temperature": 26.0}` for the sensor.

- [ ] **Step 4: Run the complete home-service suite**

Run:

```powershell
E:\smart-home\.venv\Scripts\python.exe -m pytest apps/home-service/tests -v
```

Expected: all tests pass, including SSE first snapshot, event filtering, coalescing, heartbeat, slow clients, and reconnect.

- [ ] **Step 5: Record a review checkpoint**

Review `ha_entities.json` for exact IDs and confirm no Token or URL was added to tracked files.

---

### Task 4: Register demo entities and prove HA-to-SSE state equality

**Files:**
- Inspect: `infra/home-assistant/compose.override.yaml`
- Runtime artifact: existing simulator state volume
- Runtime process: `apps/home-service/src/server.py`

**Interfaces:**
- Consumes: Docker services `homeassistant`, `mosquitto`, `shv-device-simulator` and the eight-entry HA catalog.
- Produces: HA-backed `GET /events` snapshots containing all eight device IDs.

- [ ] **Step 1: Run static safety checks**

Run:

```powershell
docker compose --env-file E:\smart-home\runtime\home-assistant\compose.env -f D:\dac\docker-compose.yml -f E:\smart-home\infra\home-assistant\compose.override.yaml config
docker ps --format "{{.Names}} {{.Ports}}"
```

Expected: Compose resolves successfully; Home Assistant is loopback-only and Mosquitto still has no host-published port.

- [ ] **Step 2: Rebuild and recreate only the simulator**

Run:

```powershell
docker build --tag shv-device-simulator:local E:\smart-home\apps\device-simulator
docker compose --env-file E:\smart-home\runtime\home-assistant\compose.env -f D:\dac\docker-compose.yml -f E:\smart-home\infra\home-assistant\compose.override.yaml up -d --no-deps --force-recreate device-simulator
docker logs --tail 100 shv-device-simulator
```

Expected: the image builds, only `device-simulator` is recreated, its log shows a successful MQTT connection, and no persisted-state validation error appears.

- [ ] **Step 3: Verify HA registered all eight entities without controlling them**

Read `/api/states` with the existing local HA session or local credential and retain only sanitized fields: `entity_id`, `state`, `brightness`, `temperature`, `current_temperature`, `power`, and availability. Assert the eight exact entity IDs exist. Do not call `/api/services`.

- [ ] **Step 4: Restart home-service explicitly in HA mode**

Stop only the process listening on `127.0.0.1:8765`, then start the existing server command with `HOME_SERVICE_BACKEND=ha`, existing `HA_ENV_FILE`, `HA_ENTITY_CATALOG`, and `HA_OPERATION_DB`. Do not print environment values or the Token.

- [ ] **Step 5: Compare HA state to the SSE first snapshot**

Capture one HA state read and the first `snapshot` event from `http://127.0.0.1:8765/events`. Normalize brightness `0–255` to `0–100` exactly as `ha_service.py` does, then compare all eight IDs and their supported fields. Expected: `backend` is `ha`, all eight devices exist, and every normalized value matches.

- [ ] **Step 6: Verify reconnect without changing device state**

Restart `home-service` once, reconnect the SSE reader, and confirm the new first snapshot again matches HA. This proves state recovery from HA rather than retained in-process Memory state.

- [ ] **Step 7: Save sanitized evidence**

Store only pass/fail output and entity IDs under `E:\smart-home\runtime\verification`; exclude tokens, headers, full environment dumps, and MQTT credentials.
