# Home Assistant Closed-Loop Completion Implementation Plan

> **已完成：计划 2026-09-13，完成 2026-09-13（当天实现并验收）。** 下面所有任务均已实现并通过
> 验收，证据见 [`reports/2026-09-13-ha-closed-loop-report.md`](../../reports/2026-09-13-ha-closed-loop-report.md)
> （301 单测 + 真实栈 14/14）。复选框按本项目惯例保持未勾选：只有真人复核后才勾。
> 本文件所在目录 `plans/completed/` 即“已完成”集合；它取代并废弃了
> `plans/2026-09-12-virtual-devices-implementation.md`（该文件已于 2026-09-25 删除，
> 内容在 Git 历史里；其四设备范围后来被
> [`specs/2026-09-17-ha-unity-realtime-effects-design.md`](../../specs/2026-09-17-ha-unity-realtime-effects-design.md)
> 扩到八设备）。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build four MQTT virtual devices that Home Assistant discovers and that `home-service` controls through a persistent idempotent state machine with observed-state confirmation and crash recovery.

**Architecture:** Reuse the existing `D:/dac` Home Assistant Compose project, harden its Mosquitto service, and add a separately built simulator container through a repository-owned override. Keep the existing in-memory `home-service` intact for regressions; add an explicitly selected HA application backed by a standard-library HTTP gateway and SQLite operation store. Home Assistant supplies observed state, while the simulator owns emulated actuation state.

**Tech Stack:** Python 3.11, pytest 9.1.1, paho-mqtt 2.x, SQLite, Home Assistant REST API, MQTT Discovery, Mosquitto 2.x, Docker Compose v2, PowerShell.

---

## File map

### Device simulator

- Create `apps/device-simulator/src/model.py`: pure validated device transitions and atomic JSON persistence.
- Create `apps/device-simulator/src/discovery.py`: MQTT topic names, Discovery payloads and state payloads.
- Create `apps/device-simulator/src/runtime.py`: MQTT connection, availability, commands, delayed execution and one-shot failure control.
- Create `apps/device-simulator/src/main.py`: environment parsing and process lifecycle only.
- Create `apps/device-simulator/tests/test_discovery.py`: payload/retain/topic contract tests.
- Create `apps/device-simulator/tests/test_runtime.py`: transport-independent command/fault/reconnect tests.
- Create `apps/device-simulator/requirements.lock.txt`: pinned MQTT client.
- Create `apps/device-simulator/Dockerfile`: deterministic simulator image.

### Home Assistant deployment

- Create `infra/home-assistant/compose.override.yaml`: remove broker host ports and add simulator.
- Create `infra/home-assistant/mosquitto.conf`: authenticated internal-only broker configuration.
- Create `infra/home-assistant/.env.example`: nonsecret variable names and paths.
- Modify `tools/ha-check/setup_mqtt.py`: authenticated, idempotent MQTT config flow using local secrets.
- Create `tools/ha-check/prepare_stack.ps1`: generate local credentials/password file and build simulator image.
- Create `tools/ha-check/acceptance.py`: machine-readable real-stack checks and fault scenarios.

### HA-backed home-service

- Modify `apps/home-service/src/ha_gateway.py`: safe HTTP transport with explicit submission-uncertainty errors.
- Create `apps/home-service/src/operation_store.py`: SQLite operation state machine and atomic idempotency reservation.
- Create `apps/home-service/src/ha_service.py`: catalog, validation, HA state normalization, service calls, confirmation and startup reconciliation.
- Modify `apps/home-service/src/server.py`: select `memory` or `ha` explicitly and route through a common application interface.
- Modify `apps/home-service/config/ha_entities.json`: add per-device domain, services and confirmation metadata.
- Replace/extend `apps/home-service/tests/test_ha_control.py`: state-machine, confirmation, recovery and secret-safety tests.
- Create `apps/home-service/tests/test_operation_store.py`: SQLite concurrency and transition tests.
- Modify `apps/home-service/README.md`: HA mode setup, state semantics and operator commands.

### Verification records

- Create `docs/superpowers/reports/2026-09-13-ha-closed-loop-report.md`: commands, measured results and explicit unverified gates.
- Modify `.gitignore`: allow only the new source/config roots while retaining exclusions for `.env`, password files, SQLite and runtime state.

---

### Task 1: Complete the pure simulator model

**Files:**
- Create: `apps/device-simulator/src/model.py`
- Modify: `apps/device-simulator/tests/test_model.py`
- Test: `apps/device-simulator/tests/test_model.py`

- [ ] **Step 1: Extend the failing tests with strict whole-state validation**

Add tests that reject booleans as numbers, reject extra/missing device records, preserve brightness when switching off, and accept only finite temperatures:

```python
def test_load_rejects_missing_or_extra_devices(tmp_path):
    path = tmp_path / "state.json"
    state = initial_state()
    del state["desk_plug"]
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="device set"):
        load_state(path)


def test_load_rejects_boolean_numeric_fields(tmp_path):
    path = tmp_path / "state.json"
    state = initial_state()
    state["living_room_light"]["brightness"] = True
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="brightness"):
        load_state(path)
```

- [ ] **Step 2: Run the model tests and confirm the missing module failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests/test_model.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'model'`.

- [ ] **Step 3: Implement the complete pure model**

Implement these public functions and keep all transitions copy-on-write:

```python
DEVICES = ("living_room_light", "bedroom_ac", "desk_plug", "indoor_temperature")


def initial_state():
    return {
        "living_room_light": {"state": "OFF", "brightness": 50},
        "bedroom_ac": {"mode": "off", "target_temperature": 26.0, "current_temperature": 26.0},
        "desk_plug": {"state": "OFF", "power": 0},
        "indoor_temperature": {"temperature": 26.0},
    }


def transition(state, device, action, payload):
    current = validate_state(copy.deepcopy(state))
    if device == "living_room_light" and action == "set":
        command = json.loads(payload)
        _validate_light_command(command)
        current[device].update(command)
    elif device == "bedroom_ac" and action == "mode/set":
        if payload not in {"off", "cool", "fan_only"}:
            raise ValueError("invalid ac mode")
        current[device]["mode"] = payload
    elif device == "bedroom_ac" and action == "temperature/set":
        value = json.loads(payload)
        _finite_number("target_temperature", value, 16, 30)
        current[device]["target_temperature"] = float(value)
    elif device == "desk_plug" and action == "set":
        if payload not in {"ON", "OFF"}:
            raise ValueError("invalid plug state")
        current[device] = {"state": payload, "power": 60 if payload == "ON" else 0}
    else:
        raise ValueError("unsupported command")
    return validate_state(current)


def save_state(path, state):
    validated = validate_state(copy.deepcopy(state))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(validated, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)
```

Use these shared validators and loaders so every numeric check rejects booleans and non-finite values:

```python
def _finite_number(name, value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} out of range")


def _validate_light_command(command):
    if not isinstance(command, dict) or not command or set(command) - {"state", "brightness"}:
        raise ValueError("invalid light command")
    if "state" in command and command["state"] not in {"ON", "OFF"}:
        raise ValueError("invalid light state")
    if "brightness" in command:
        _finite_number("brightness", command["brightness"], 0, 100)
        if not isinstance(command["brightness"], int):
            raise ValueError("brightness must be an integer")


def validate_state(state):
    if not isinstance(state, dict) or set(state) != set(DEVICES):
        raise ValueError("invalid device set")
    _validate_persisted_light(state["living_room_light"])
    _validate_persisted_ac(state["bedroom_ac"])
    _validate_persisted_plug(state["desk_plug"])
    _validate_persisted_sensor(state["indoor_temperature"])
    return state


def with_temperature(state, value):
    _finite_number("temperature", value, -40, 85)
    changed = validate_state(copy.deepcopy(state))
    changed["indoor_temperature"]["temperature"] = float(value)
    changed["bedroom_ac"]["current_temperature"] = float(value)
    return changed


def load_state(path):
    path = Path(path)
    if not path.exists():
        return initial_state()
    return validate_state(json.loads(path.read_text(encoding="utf-8")))
```

Add the exact persisted-record validators:

```python
def _exact(record, keys, name):
    if not isinstance(record, dict) or set(record) != set(keys):
        raise ValueError(f"invalid {name} shape")


def _validate_persisted_light(record):
    _exact(record, {"state", "brightness"}, "light")
    if record["state"] not in {"ON", "OFF"}:
        raise ValueError("invalid light state")
    _finite_number("brightness", record["brightness"], 0, 100)
    if not isinstance(record["brightness"], int):
        raise ValueError("brightness must be an integer")


def _validate_persisted_ac(record):
    _exact(record, {"mode", "target_temperature", "current_temperature"}, "ac")
    if record["mode"] not in {"off", "cool", "fan_only"}:
        raise ValueError("invalid ac mode")
    _finite_number("target_temperature", record["target_temperature"], 16, 30)
    _finite_number("current_temperature", record["current_temperature"], -40, 85)


def _validate_persisted_plug(record):
    _exact(record, {"state", "power"}, "plug")
    if record["state"] not in {"ON", "OFF"}:
        raise ValueError("invalid plug state")
    _finite_number("power", record["power"], 0, 60)


def _validate_persisted_sensor(record):
    _exact(record, {"temperature"}, "sensor")
    _finite_number("temperature", record["temperature"], -40, 85)
```

- [ ] **Step 4: Run the model tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests/test_model.py -v
```

Expected: all model tests pass.

- [ ] **Step 5: Commit the model**

```powershell
git add apps/device-simulator/src/model.py apps/device-simulator/tests/test_model.py
git commit -m "feat(simulator): add validated device state model"
```

### Task 2: Add MQTT Discovery and the fault-aware runtime

**Files:**
- Create: `apps/device-simulator/src/discovery.py`
- Create: `apps/device-simulator/src/runtime.py`
- Create: `apps/device-simulator/src/main.py`
- Create: `apps/device-simulator/tests/test_discovery.py`
- Create: `apps/device-simulator/tests/test_runtime.py`

- [ ] **Step 1: Write Discovery contract tests**

Test stable unique IDs, device grouping, command topics, availability, brightness scaling and retained state publication:

```python
def test_all_discovery_payloads_have_stable_ids_and_availability():
    messages = discovery_messages("shv")
    assert len(messages) == 4
    payloads = [json.loads(item.payload) for item in messages]
    assert {p["unique_id"] for p in payloads} == {
        "shv_living_room_light", "shv_bedroom_ac", "shv_desk_plug", "shv_indoor_temperature"
    }
    assert all(p["availability_topic"] == "shv/status" for p in payloads)
    assert all(item.retain for item in messages)


def test_commands_are_never_retained():
    assert command_topics("shv")["living_room_light"] == "shv/living_room_light/set"
    assert all(not item.retain for item in command_publications_for_tests())
```

- [ ] **Step 2: Write runtime tests around a fake publisher**

Use a fake clock and publisher; verify successful execution, delayed publication, one-shot failure preserving state, offline rejection, and reconnect bootstrap:

```python
def test_one_shot_failure_preserves_state_and_publishes_fault(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    before = runtime.state
    runtime.fail_next("living_room_light")
    runtime.handle("shv/living_room_light/set", '{"state":"ON"}')
    assert runtime.state == before
    assert publisher.last("shv/events/fault")["device_id"] == "living_room_light"


def test_reconnect_republishes_discovery_availability_and_state(tmp_path):
    publisher = FakePublisher()
    runtime = SimulatorRuntime(tmp_path / "state.json", publisher, sleep=lambda _: None)
    runtime.on_connected()
    assert publisher.has_topic("homeassistant/light/shv_living_room_light/config", retain=True)
    assert publisher.last_raw("shv/status") == "online"
    assert publisher.has_topic("shv/living_room_light/state", retain=True)
```

- [ ] **Step 3: Run both new test files and verify they fail**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests/test_discovery.py apps/device-simulator/tests/test_runtime.py -v
```

Expected: import failures for `discovery` and `runtime`.

- [ ] **Step 4: Implement Discovery payload builders**

Use one immutable publication type and explicit component payloads:

```python
@dataclass(frozen=True)
class Publication:
    topic: str
    payload: str
    retain: bool = True
    qos: int = 1


def discovery_messages(prefix="shv"):
    device = {"identifiers": ["shv_virtual_home"], "name": "SHV Virtual Home", "manufacturer": "SHV"}
    availability = {"availability_topic": f"{prefix}/status", "payload_available": "online", "payload_not_available": "offline"}
    return [
        Publication("homeassistant/light/shv_living_room_light/config", json.dumps({
            "name": "客厅灯", "unique_id": "shv_living_room_light", "device": device,
            "command_topic": f"{prefix}/living_room_light/set", "state_topic": f"{prefix}/living_room_light/state",
            "schema": "json", "brightness": True, "brightness_scale": 100, **availability,
        }, ensure_ascii=False)),
        Publication("homeassistant/climate/shv_bedroom_ac/config", json.dumps({
            "name": "卧室空调", "unique_id": "shv_bedroom_ac", "device": device,
            "mode_command_topic": f"{prefix}/bedroom_ac/mode/set", "mode_state_topic": f"{prefix}/bedroom_ac/mode/state",
            "temperature_command_topic": f"{prefix}/bedroom_ac/temperature/set", "temperature_state_topic": f"{prefix}/bedroom_ac/temperature/state",
            "current_temperature_topic": f"{prefix}/indoor_temperature/state", "modes": ["off", "cool", "fan_only"],
            "min_temp": 16, "max_temp": 30, "temp_step": 0.5, **availability,
        }, ensure_ascii=False)),
        Publication("homeassistant/switch/shv_desk_plug/config", json.dumps({
            "name": "智能插座", "unique_id": "shv_desk_plug", "device": device,
            "command_topic": f"{prefix}/desk_plug/set", "state_topic": f"{prefix}/desk_plug/state", **availability,
        }, ensure_ascii=False)),
        Publication("homeassistant/sensor/shv_indoor_temperature/config", json.dumps({
            "name": "室内温度", "unique_id": "shv_indoor_temperature", "device": device,
            "state_topic": f"{prefix}/indoor_temperature/state", "device_class": "temperature",
            "state_class": "measurement", "unit_of_measurement": "°C", **availability,
        }, ensure_ascii=False)),
    ]


def state_messages(state, prefix="shv"):
    light = state["living_room_light"]
    ac = state["bedroom_ac"]
    plug = state["desk_plug"]
    sensor = state["indoor_temperature"]
    return [
        Publication(f"{prefix}/living_room_light/state", json.dumps(light)),
        Publication(f"{prefix}/bedroom_ac/mode/state", ac["mode"]),
        Publication(f"{prefix}/bedroom_ac/temperature/state", str(ac["target_temperature"])),
        Publication(f"{prefix}/desk_plug/state", plug["state"]),
        Publication(f"{prefix}/indoor_temperature/state", str(sensor["temperature"])),
    ]


def parse_command_topic(topic, prefix="shv"):
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != prefix:
        raise ValueError("unknown command topic")
    device = parts[1]
    action = "/".join(parts[2:])
    if (device, action) not in {
        ("living_room_light", "set"), ("bedroom_ac", "mode/set"),
        ("bedroom_ac", "temperature/set"), ("desk_plug", "set"),
    }:
        raise ValueError("unknown command topic")
    return device, action
```

- [ ] **Step 5: Implement runtime and entry point**

`SimulatorRuntime.handle()` must check online/fault state, delay before transition, save before publishing, and publish sanitized fault JSON on exceptions. `main.py` must configure paho MQTT v2, set a retained last will of `offline`, subscribe only to known command topics, and expose fault controls only through `SHV_FAULT_FILE` polled from a container-local mount:

```python
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        raise RuntimeError(f"mqtt connect failed: {reason_code}")
    for topic in command_topics(prefix).values():
        client.subscribe(topic, qos=1)
    runtime.on_connected()


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="shv-device-simulator")
client.username_pw_set(required("MQTT_USERNAME"), required("MQTT_PASSWORD"))
client.will_set(f"{prefix}/status", "offline", qos=1, retain=True)
client.reconnect_delay_set(min_delay=1, max_delay=30)
```

- [ ] **Step 6: Run all simulator tests**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests -v
```

Expected: all simulator tests pass with no broker required.

- [ ] **Step 7: Commit MQTT behavior**

```powershell
git add apps/device-simulator/src apps/device-simulator/tests
git commit -m "feat(simulator): publish MQTT virtual devices"
```

### Task 3: Package and securely attach the simulator to the existing stack

**Files:**
- Create: `apps/device-simulator/requirements.lock.txt`
- Create: `apps/device-simulator/Dockerfile`
- Create: `infra/home-assistant/compose.override.yaml`
- Create: `infra/home-assistant/mosquitto.conf`
- Create: `infra/home-assistant/.env.example`
- Create: `tools/ha-check/prepare_stack.ps1`
- Modify: `.gitignore`

- [ ] **Step 1: Pin and package the simulator**

Use:

```text
paho-mqtt==2.1.0
```

Create a non-root image:

```dockerfile
FROM python:3.11.9-slim
RUN useradd --create-home --uid 10001 simulator
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY src/ ./src/
USER simulator
ENV PYTHONUNBUFFERED=1
CMD ["python", "src/main.py"]
```

- [ ] **Step 2: Create authenticated Mosquitto configuration**

```conf
listener 1883
allow_anonymous false
password_file /mosquitto/config/password_file
persistence true
persistence_location /mosquitto/data/
log_dest stdout
connection_messages true
```

- [ ] **Step 3: Create the Compose override**

The override must reset both existing host ports, mount repository-owned broker config and ignored runtime secrets, and add simulator health/restart behavior:

```yaml
services:
  homeassistant:
    ports: !override
      - "127.0.0.1:8123:8123"
  mosquitto:
    ports: !reset []
    volumes:
      - ${SHV_MOSQUITTO_CONFIG}:/mosquitto/config/mosquitto.conf:ro
      - ${SHV_MOSQUITTO_PASSWORD_FILE}:/mosquitto/config/password_file:ro
      - ./mosquitto/data:/mosquitto/data
      - ./mosquitto/log:/mosquitto/log
  device-simulator:
    image: shv-device-simulator:local
    container_name: shv-device-simulator
    restart: unless-stopped
    depends_on:
      mosquitto:
        condition: service_started
    env_file:
      - ${SHV_ENV_FILE}
    volumes:
      - ${SHV_RUNTIME_DIR}:/runtime
    environment:
      MQTT_HOST: mosquitto
      MQTT_PORT: "1883"
      SHV_STATE_FILE: /runtime/device-state.json
      SHV_FAULT_FILE: /runtime/fault-control.json
```

- [ ] **Step 4: Add a credential/bootstrap script**

`prepare_stack.ps1` must create `runtime/home-assistant`, generate separate random passwords for `homeassistant` and `simulator`, build the password file with `eclipse-mosquitto:2`, write the ignored env file, and build `shv-device-simulator:local`. It must never print secret values:

```powershell
$runtime = Join-Path $RepoRoot 'runtime\home-assistant'
New-Item -ItemType Directory -Force $runtime | Out-Null
$haPassword = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$simPassword = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
Set-Content "$runtime\password_file" '' -NoNewline
# Run mosquitto_passwd twice with passwords passed through stdin; do not place them in command-line arguments.
Set-Content "$runtime\stack.env" @"
MQTT_USERNAME=simulator
MQTT_PASSWORD=$simPassword
MQTT_HA_USERNAME=homeassistant
MQTT_HA_PASSWORD=$haPassword
"@
Write-Host 'Generated local MQTT credentials (values not displayed).'
```

The implementation must use `docker run --rm -i` with stdin and verify command exit codes before continuing.

- [ ] **Step 5: Add nonsecret `.env.example` and ignore rules**

Document absolute Windows paths required by Compose:

```dotenv
SHV_MOSQUITTO_CONFIG=E:/智能家居/infra/home-assistant/mosquitto.conf
SHV_MOSQUITTO_PASSWORD_FILE=E:/智能家居/runtime/home-assistant/password_file
SHV_ENV_FILE=E:/智能家居/runtime/home-assistant/stack.env
SHV_RUNTIME_DIR=E:/智能家居/runtime/home-assistant
```

Keep `runtime/`, `.env`, password files, SQLite and simulator state ignored. Explicitly allow simulator source, tests, Dockerfile, requirements, HA infra and HA check tools.

- [ ] **Step 6: Validate image and merged Compose without starting services**

```powershell
.\tools\ha-check\prepare_stack.ps1
Copy-Item infra\home-assistant\.env.example runtime\home-assistant\compose.env
# Replace only local path values if the checkout moved.
docker compose --env-file runtime\home-assistant\compose.env -f D:\dac\docker-compose.yml -f infra\home-assistant\compose.override.yaml config
```

Expected: exit 0; `mosquitto` has no `ports` key with host bindings; `device-simulator` uses `shv-device-simulator:local`; secrets are represented as env-file references rather than printed values.

- [ ] **Step 7: Commit deployment files**

```powershell
git add .gitignore apps/device-simulator/Dockerfile apps/device-simulator/requirements.lock.txt infra/home-assistant tools/ha-check/prepare_stack.ps1
git commit -m "build(ha): add secure simulator stack override"
```

### Task 4: Implement the persistent operation state machine

**Files:**
- Create: `apps/home-service/src/operation_store.py`
- Create: `apps/home-service/tests/test_operation_store.py`

- [ ] **Step 1: Write state-store tests first**

Cover atomic reservation, canonical parameter equality, ID conflicts, legal transitions and startup reconciliation queries:

```python
def test_reserve_is_idempotent_and_rejects_different_parameters(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite3")
    first = store.reserve("op-1", "set_light", "living_room_light", {"brightness": 50}, {"state": "on", "brightness": 50})
    replay = store.reserve("op-1", "set_light", "living_room_light", {"brightness": 50}, {"state": "on", "brightness": 50})
    assert first.created and not replay.created
    with pytest.raises(OperationConflict):
        store.reserve("op-1", "set_light", "living_room_light", {"brightness": 60}, {"state": "on", "brightness": 60})


def test_submitted_is_never_returned_as_retryable(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite3")
    store.reserve("op-1", "set_light", "living_room_light", {"on": True}, {"state": "on"})
    store.transition("op-1", "submitted")
    assert [op.id for op in store.unfinished()] == ["op-1"]
    with pytest.raises(InvalidTransition):
        store.transition("op-1", "accepted")
```

- [ ] **Step 2: Run and confirm the missing module failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_operation_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'operation_store'`.

- [ ] **Step 3: Implement SQLite schema and transitions**

Use one connection per method, WAL mode, `BEGIN IMMEDIATE`, canonical JSON with sorted keys, and the exact terminal/nonterminal states:

```python
TRANSITIONS = {
    "accepted": {"submitted", "confirmed", "rejected"},
    "submitted": {"confirmed", "unconfirmed", "rejected"},
    "unconfirmed": {"confirmed"},
    "confirmed": set(),
    "rejected": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  params_json TEXT NOT NULL,
  target_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('accepted','submitted','confirmed','unconfirmed','rejected')),
  result_json TEXT,
  error_code TEXT,
  error TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
)
"""
```

`reserve()` must compare `kind`, `target_id` and canonical `params_json`; it returns `Reservation(operation, created)` and never overwrites existing rows. `transition()` must update only when `status` equals the caller-observed old state, preventing concurrent invalid transitions.

- [ ] **Step 4: Run store tests including two-thread reservation**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_operation_store.py -v
```

Expected: all store tests pass and exactly one thread reports `created=True`.

- [ ] **Step 5: Commit the operation store**

```powershell
git add apps/home-service/src/operation_store.py apps/home-service/tests/test_operation_store.py
git commit -m "feat(home): add persistent operation state machine"
```

### Task 5: Complete safe HA transport and confirmed control

**Files:**
- Modify: `apps/home-service/src/ha_gateway.py`
- Create: `apps/home-service/src/ha_service.py`
- Modify: `apps/home-service/config/ha_entities.json`
- Replace: `apps/home-service/tests/test_ha_control.py`

- [ ] **Step 1: Replace draft tests with complete gateway/control tests**

Use `FakeGateway` with scripted reads/calls. Cover light and AC normalization, no-op, offline, not-found, pre-submit failure, ambiguous submit failure, explicit rejection, timeout, dedupe after restart and recovery:

```python
def test_timeout_is_persisted_unconfirmed_and_never_retried(tmp_path):
    gateway = FakeGateway(confirm=False)
    app = make_app(tmp_path, gateway)
    body = {"device_id": "living_room_light", "on": True, "operation_id": "one"}
    first = app.handle("POST", "/tool/set_light", {}, body)[1]
    second = make_app(tmp_path, gateway).handle("POST", "/tool/set_light", {}, body)[1]
    assert first["error_code"] == "confirmation_timeout"
    assert second == first
    assert len(gateway.calls) == 1


def test_submitted_recovery_only_reconciles(tmp_path):
    gateway = FakeGateway(confirm=False)
    service = make_service(tmp_path, gateway)
    service.store.reserve("one", "set_light", "living_room_light", {"on": True}, {"state": "on"})
    service.store.transition("one", "submitted")
    make_service(tmp_path, gateway).reconcile_unfinished()
    assert not gateway.calls
    assert service.store.get("one").status == "unconfirmed"
```

- [ ] **Step 2: Run HA tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_ha_control.py -v
```

Expected: failure because `ha_service.py` is absent and the draft gateway lacks submission-uncertainty classification.

- [ ] **Step 3: Make transport outcomes explicit**

Keep response bodies and headers out of exceptions. Add phases so a connection error before opening is `backend_unavailable`, while an exception after request submission is represented as `submission_unknown`:

```python
class GatewayError(Exception):
    def __init__(self, code: str, may_have_submitted: bool = False):
        self.code = code
        self.may_have_submitted = may_have_submitted
        super().__init__(code)


def call(self, domain, service, data):
    try:
        return self.request("POST", f"services/{domain}/{service}", data)
    except GatewayError as exc:
        if exc.code == "backend_unavailable":
            raise GatewayError("submission_unknown", may_have_submitted=True) from None
        raise
```

Tests must assert `repr(exc)` contains neither token nor Authorization header. Preserve disabled proxy and redirect behavior.

- [ ] **Step 4: Implement catalog and HA state normalization**

Make catalog records explicit:

```json
{
  "living_room_light": {
    "entity_id": "light.shv_living_room_light",
    "type": "light",
    "room_id": "living_room",
    "room_name": "客厅",
    "name": "客厅灯"
  },
  "bedroom_ac": {
    "entity_id": "climate.shv_bedroom_ac",
    "type": "ac",
    "room_id": "bedroom",
    "room_name": "卧室",
    "name": "卧室空调"
  }
}
```

Retain the plug and temperature entries. `normalize_entity()` must convert HA light brightness 0–255 to rounded 0–100, climate `fan_only` without renaming, `unavailable` to `online=False`, and `last_updated` to an opaque version.

- [ ] **Step 5: Implement `HAServiceApp` and control state machine**

Expose the same transport-independent `handle(method, path, query, body)` shape as `HomeServiceApp`. Implement `/health`, `/tool/device_status`, `/tool/room_status`, `/tool/set_light`, and `/tool/set_ac`; return 404 for spatial/test mutation routes in HA mode.

Before each write:

```python
params = normalize_light_request(body)
reservation = store.reserve(operation_id, "set_light", device_id, params, target_from(params))
if not reservation.created:
    return http_result(reservation.operation)
entity = read_and_validate(device_id, "light")
if target_matches(entity, reservation.operation.target):
    return finish_confirmed(reservation.operation, entity, noop=True)
store.transition(operation_id, "submitted")
try:
    gateway.call("light", "turn_off" if params.get("on") is False else "turn_on", service_data(params))
except GatewayError as exc:
    return finish_unconfirmed(...) if exc.may_have_submitted else finish_rejected(...)
return confirm_until_deadline(reservation.operation)
```

Use `time.monotonic()` and an injected sleep function. Validate booleans separately from numbers and reject NaN/Infinity. Require caller-supplied operation IDs in HA mode.

- [ ] **Step 6: Implement startup reconciliation**

`reconcile_unfinished()` must process `accepted`, `submitted` and `unconfirmed` exactly as the spec states. An accepted operation reads first and may submit once; submitted/unconfirmed operations only read and become `confirmed` if the target is observed, otherwise submitted becomes `unconfirmed` and unconfirmed stays unchanged.

- [ ] **Step 7: Run HA and legacy home-service tests**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_operation_store.py apps/home-service/tests/test_ha_control.py -v
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests -q
```

Expected: HA tests pass; all pre-existing memory-backend tests still pass.

- [ ] **Step 8: Commit HA control**

```powershell
git add apps/home-service/src/ha_gateway.py apps/home-service/src/ha_service.py apps/home-service/config/ha_entities.json apps/home-service/tests/test_ha_control.py
git commit -m "feat(home): confirm Home Assistant device operations"
```

### Task 6: Add explicit backend selection to the server

**Files:**
- Modify: `apps/home-service/src/server.py`
- Create: `apps/home-service/tests/test_backend_selection.py`

- [ ] **Step 1: Write backend-selection tests**

```python
def test_ha_backend_requires_url_token_catalog_and_database(monkeypatch):
    monkeypatch.setenv("HOME_SERVICE_BACKEND", "ha")
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="HOME_ASSISTANT_TOKEN"):
        build_app_from_environment()


def test_unknown_backend_never_falls_back_to_memory(monkeypatch):
    monkeypatch.setenv("HOME_SERVICE_BACKEND", "typo")
    with pytest.raises(SystemExit, match="HOME_SERVICE_BACKEND"):
        build_app_from_environment()
```

Also assert `memory` remains the default only when the variable is absent, and HA mode calls `reconcile_unfinished()` before serving.

- [ ] **Step 2: Run the new tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests/test_backend_selection.py -v
```

Expected: import failure for `build_app_from_environment`.

- [ ] **Step 3: Refactor HTTP serving around an app protocol**

Change `serve(state, ...)` to `serve(app, ...)`, keep `HomeServiceApp` unchanged, and add:

```python
def _runtime_environment():
    values = dict(os.environ)
    env_file = values.get("HA_ENV_FILE")
    if env_file:
        for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in {"HOME_ASSISTANT_URL", "HOME_ASSISTANT_TOKEN"}:
                values.setdefault(key.strip(), value.strip())
    return values


def build_app_from_environment():
    values = _runtime_environment()
    backend = values.get("HOME_SERVICE_BACKEND", "memory")
    if backend == "memory":
        return HomeServiceApp(build_default_state(default_config_path()))
    if backend != "ha":
        raise SystemExit("HOME_SERVICE_BACKEND must be memory or ha")
    token = required_value(values, "HOME_ASSISTANT_TOKEN")
    catalog_path = Path(values.get("HA_ENTITY_CATALOG", default_catalog_path()))
    db_path = Path(required_value(values, "HA_OPERATION_DB"))
    gateway = HAGateway(required_value(values, "HOME_ASSISTANT_URL"), token)
    app = HAServiceApp(gateway, load_catalog(catalog_path), db_path)
    app.reconcile_unfinished()
    return app
```

`--print-snapshot` remains memory-only and must reject `HOME_SERVICE_BACKEND=ha` with an explicit message instead of silently building memory state.

- [ ] **Step 4: Run server and full home-service tests**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests -q
```

Expected: all tests pass, including existing loopback bind protection.

- [ ] **Step 5: Commit backend selection**

```powershell
git add apps/home-service/src/server.py apps/home-service/tests/test_backend_selection.py
git commit -m "feat(home): select Home Assistant backend explicitly"
```

### Task 7: Configure HA and add repeatable real-stack acceptance

**Files:**
- Modify: `tools/ha-check/setup_mqtt.py`
- Create: `tools/ha-check/acceptance.py`
- Modify: `apps/home-service/README.md`

- [ ] **Step 1: Make MQTT setup consume both broker credentials**

Load the ignored simulator/broker env file named by `SHV_ENV_FILE`, then submit the HA-specific broker credentials without printing values:

```python
def load_env_file(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in {"MQTT_HA_USERNAME", "MQTT_HA_PASSWORD", "MQTT_HOST", "MQTT_PORT"}:
            values[key.strip()] = value.strip()
    return values


local = load_env_file(required("SHV_ENV_FILE"))
payload = {
    "broker": local.get("MQTT_HOST", "mosquitto"),
    "port": int(local.get("MQTT_PORT", "1883")),
    "username": local["MQTT_HA_USERNAME"],
    "password": local["MQTT_HA_PASSWORD"],
}
flow = request("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", payload)
print(json.dumps({key: flow.get(key) for key in ("type", "step_id", "errors", "reason")}))
```

If an MQTT entry already exists, reconfigure it rather than returning early: POST `/api/config/config_entries/flow` with `{"handler":"mqtt","entry_id": entry_id}`; require a `broker` form, preserve broker/port/protocol from its `data_schema` defaults, and submit the new username/password to `/api/config/config_entries/flow/{flow_id}`. Accept only `type=abort, reason=reconfigure_successful`; on another form report sanitized `step_id/errors` and fail. Never edit `.storage/core.config_entries` directly. This internal API is version-sensitive, so if its response shape differs, stop and instruct the user to use Settings → Devices & services → MQTT → Reconfigure rather than deleting the entry.

- [ ] **Step 2: Add an acceptance script with explicit assertions**

`acceptance.py` must:

1. Read HA URL/token through `connection.py`.
2. Wait a bounded 60 seconds for the four exact entity IDs.
3. Assert initial availability and normalized states.
4. Call the local `home-service` with fresh UUID operation IDs.
5. Confirm light 50%, AC cool/24℃, plug state query and temperature query.
6. Write fault-control JSON atomically for offline, fail-next and delay cases.
7. Assert each API error code and that HA never reports the failed target state.
8. Print only a JSON summary with pass/fail names and durations.

Core result shape:

```python
results.append({"name": "light_50_confirmed", "ok": payload["ok"] and payload["data"]["state"]["brightness"] == 50})
if not all(item["ok"] for item in results):
    print(json.dumps({"ok": False, "checks": results}, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps({"ok": True, "checks": results}, ensure_ascii=False, indent=2))
```

- [ ] **Step 3: Document exact operator commands**

Add this HA-mode sequence to `apps/home-service/README.md`:

```powershell
.\tools\ha-check\prepare_stack.ps1
docker compose --env-file runtime\home-assistant\compose.env -f D:\dac\docker-compose.yml -f infra\home-assistant\compose.override.yaml up -d
$env:HA_ENV_FILE='E:\smart-home\runtime\home-assistant\ha.env'
$env:SHV_ENV_FILE='E:\smart-home\runtime\home-assistant\stack.env'
.\.venv\Scripts\python.exe tools\ha-check\setup_mqtt.py
$env:HOME_SERVICE_BACKEND='ha'
$env:HOME_ASSISTANT_URL='http://127.0.0.1:8123'
$env:HA_OPERATION_DB='E:\smart-home\runtime\home-assistant\operations.sqlite3'
.\.venv\Scripts\python.exe apps\home-service\src\server.py
```

State clearly that the user creates a Home Assistant long-lived token and places it in ignored `ha.env`; never include the token in a command history example.

- [ ] **Step 4: Run static checks**

```powershell
.\.venv\Scripts\python.exe -m compileall -q apps/device-simulator/src apps/home-service/src tools/ha-check
.\.venv\Scripts\python.exe -m pytest apps/device-simulator/tests apps/home-service/tests -q
```

Expected: compile exit 0 and all simulator/home tests pass.

- [ ] **Step 5: Commit setup, acceptance and docs**

```powershell
git add tools/ha-check/setup_mqtt.py tools/ha-check/acceptance.py apps/home-service/README.md
git commit -m "test(ha): add repeatable closed-loop acceptance"
```

### Task 8: Execute real-stack verification and record evidence

**Files:**
- Create: `docs/superpowers/reports/2026-09-13-ha-closed-loop-report.md`

- [ ] **Step 1: Ask the user for the only required manual secret action**

Ask the user to create a Home Assistant long-lived access token at `http://127.0.0.1:8123/profile/security`, save it as `HOME_ASSISTANT_TOKEN=<value>` in `runtime/home-assistant/ha.env`, and reply only “已保存”. Never ask them to paste the token into chat.

- [ ] **Step 2: Start the merged stack and inspect exposure**

```powershell
docker compose --env-file runtime\home-assistant\compose.env -f D:\dac\docker-compose.yml -f infra\home-assistant\compose.override.yaml up -d
docker compose --env-file runtime\home-assistant\compose.env -f D:\dac\docker-compose.yml -f infra\home-assistant\compose.override.yaml ps
```

Expected: HA, Mosquitto and simulator are running; only HA publishes host port 8123; neither 1883 nor 9001 is published.

- [ ] **Step 3: Configure MQTT and verify entities**

```powershell
$env:HA_ENV_FILE='E:\smart-home\runtime\home-assistant\ha.env'
$env:SHV_ENV_FILE='E:\smart-home\runtime\home-assistant\stack.env'
.\.venv\Scripts\python.exe tools\ha-check\setup_mqtt.py
.\.venv\Scripts\python.exe tools\ha-check\connection.py
```

Expected: MQTT setup reports existing/created without secrets; connection output includes HA version and `"mqtt_loaded": true`.

- [ ] **Step 4: Start HA-backed home-service**

```powershell
$env:HOME_SERVICE_BACKEND='ha'
$env:HOME_ASSISTANT_URL='http://127.0.0.1:8123'
$env:HA_ENV_FILE='E:\smart-home\runtime\home-assistant\ha.env'
$env:HA_OPERATION_DB='E:\smart-home\runtime\home-assistant\operations.sqlite3'
.\.venv\Scripts\python.exe apps\home-service\src\server.py
```

Expected: service binds only `127.0.0.1:8765`, reports HA backend, and completes startup reconciliation before accepting requests.

- [ ] **Step 5: Run automated real-stack acceptance**

```powershell
$env:HA_ENV_FILE='E:\smart-home\runtime\home-assistant\ha.env'
.\.venv\Scripts\python.exe tools\ha-check\acceptance.py
```

Expected: JSON output has `"ok": true`; checks include entity discovery, light, AC, offline, fail-next, delay/timeout and recovery reconciliation.

- [ ] **Step 6: Perform user-visible HA page confirmation**

Ask the user to refresh `http://127.0.0.1:8123`, confirm the four entities appear once, change the light brightness, and reply with whether HA and `home-service` show the same value. Record this as user-confirmed evidence, not automated evidence.

- [ ] **Step 7: Run full repository regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests apps/voice-service/tests apps/device-simulator/tests -q
.\.venv\Scripts\python.exe -m compileall -q apps/home-service/src apps/voice-service/src apps/device-simulator/src tools/ha-check

git diff --check
```

Expected: all tests pass, compile exits 0, and `git diff --check` has no output.

- [ ] **Step 8: Write the evidence report**

Record exact commands, timestamps, pass counts, Compose service/port observations, entity IDs, fault scenario outcomes, recovery behavior and user page confirmation. If any manual or real-stack gate was not run, label it `未验证` and do not infer success.

- [ ] **Step 9: Commit the verified report**

```powershell
git add docs/superpowers/reports/2026-09-13-ha-closed-loop-report.md
git commit -m "docs: record HA closed-loop verification"
```

- [ ] **Step 10: Confirm a clean, secret-free deliverable**

```powershell
git status --short
git grep -n -I -E "HOME_ASSISTANT_TOKEN=.+|MQTT_PASSWORD=.+|Authorization: Bearer .+"
```

Expected: no unintended untracked implementation files; grep finds no populated secrets. Existing unrelated working-tree changes, if any, are listed and left untouched.
