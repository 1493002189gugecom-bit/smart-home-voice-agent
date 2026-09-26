# Home Service — Authoritative State and Restricted Tools (Phase B)

Plain-text driven service that owns the authoritative home state and exposes
exactly **six** restricted tools. It does not use a microphone, does not talk to
Unity, and does not decide anything by itself: the Agent (Phase D) will call it.

It binds to **127.0.0.1 only**. Requests to bind anywhere else are refused,
because this service is never exposed to the LAN.

## Read-only live events

`GET /events` exposes a loopback-only Server-Sent Events stream for digital
twin viewers. A connection receives a complete canonical `snapshot`
immediately. Later snapshots are published only after a successful memory
mutation or a configured Home Assistant entity emits `state_changed`.

- Home Assistant authentication stays inside home-service. The token is never
  sent to Blender or Unity.
- Only entity IDs listed in `config/ha_entities.json` are observed; unrelated
  Home Assistant events are ignored.
- Changes arriving within 100 ms are combined into one complete snapshot, and
  slow clients receive the newest snapshot instead of a backlog of stale ones.
- The stream sends `: heartbeat` every 15 seconds while idle.
- Home Assistant WebSocket reconnects back off through 1, 2, 4, 8, 16 and 30
  seconds. `status` events report upstream disconnect/recovery without exposing
  credentials or upstream response bodies.
- The endpoint is read-only. `POST /events` is not registered.

Example read-only inspection:

```powershell
curl.exe --no-buffer http://127.0.0.1:8765/events
```

## Run

For normal use, `tools\start-perception.ps1` starts home-service with the
configured Home Assistant backend alongside the other local services. The
commands below are for inspecting or starting this service by itself.

```powershell
# Print the initial snapshot and exit
.\.venv\Scripts\python.exe apps/home-service/src/server.py --print-snapshot

# Serve the JSON API on loopback
.\.venv\Scripts\python.exe apps/home-service/src/server.py --port 8765
```

## The six tools in the memory backend

| Tool | Kind | Notes |
| --- | --- | --- |
| `query_room_status` | read | Rooms, simulated temperature, devices |
| `query_person_location` | read | Camera observation only; unknown without a fresh confirmed one |
| `query_device_status` | read | Device state plus version |
| `set_light` | write | `on`, `brightness` 0–100 |
| `set_ac` | write | `on`, `mode`, `target_temp` within local limits |
| `broadcast_to_room` | write | Queues an announcement task |

Every write returns the wording the Agent is allowed to repeat, for example
`已打开客厅的灯，亮度 50`. The Agent must not invent stronger wording.

## Contracts enforced server-side

- **Stable ids only.** The caller passes natural-language targets (`客厅`, `爸爸`)
  which are resolved against authoritative state; unknown rooms/devices/people
  return `not_found` rather than creating anything.
- **Range and type checks.** Brightness outside 0–100, temperature outside
  16–30 °C (from `config/comfort.json`), unknown AC modes, and light/AC mix-ups
  are rejected before any state change.
- **Offline devices never change state.** An offline device returns
  `offline` and its state is untouched, so no misoperation is recorded.
- **Optimistic concurrency.** Passing a stale `precondition_version` returns
  `version_conflict`; the caller must re-query instead of overwriting.
- **Operation ids are idempotent.** The same `operation_id` replays the stored
  outcome instead of applying the change twice — including broadcasts, which are
  not queued twice.
- **No-ops stay no-ops.** Turning off a light that is already off reports that it
  was already off instead of rewriting state.

## Broadcast state machine

```
queued ──start──▶ playing ──receipt(ok)──▶ played
                     └────receipt(bad)/fail────▶ failed
```

- The queue is owned here; only the head task may start, so **playback is never
  parallel**.
- `start_broadcast` issues a `receipt_id`; `complete_broadcast` requires it to
  match. A missing or mismatched receipt is recorded as `failed`, and the task is
  **not** replayed automatically.
- Wording is derived from state, never guessed:

| State | Wording |
| --- | --- |
| `queued` | 已安排在卧室播报 |
| `playing` | 正在卧室播报 |
| `played` | 已在卧室播报 |
| `failed` | 卧室播报失败 |

Only `played` permits "已播报". `failed` never claims success.

## Multi-target notification

`plan_notification` implements design section 7 for requests like
"叫爸爸和孩子吃饭":

- Targets in the same room are merged into **one** task.
- Different rooms are queued in mention order and played serially.
- A target whose location is unknown is reported as **not notified** and the
  caller is expected to ask ("没有爸爸的位置，未通知，需要全屋播报吗"). There is
  deliberately **no automatic whole-house broadcast**.
- Phrases never claim the target heard or arrived.

## Files

| Path | Purpose |
| --- | --- |
| `src/models.py` | Rooms, people, devices, observations, conversations, operations |
| `src/state.py` | Authoritative state, versions, increments, device writes |
| `src/visual_state.py` | Camera-owned person store: token-authenticated observation ingress and the derived person view |
| `src/tools.py` | The six tools plus server-side validation |
| `src/notify.py` | Multi-target notification planning |
| `src/server.py` | Loopback JSON API and `GET /events` SSE transport |
| `src/event_stream.py` | Canonical snapshots, bounded fan-out and SSE framing |
| `src/ha_event_subscriber.py` | Authenticated, filtered HA WebSocket subscription |
| `config/rooms.json` | Three rooms, six devices, three people |
| `config/comfort.json` | Temperature limits and comfort default |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests -q
```

Covers target resolution, room dedupe, range validation, version conflicts,
operation dedupe, offline devices, partial success, broadcast receipts, duplicate
requests over HTTP, and restart behaviour.

# Camera-sourced person state

Person location is no longer a memory-backend concern. The vision service is the
only writer, through three token-authenticated loopback routes:

| Route | Meaning |
| --- | --- |
| `POST /vision/observations` | One batch of tracks for the active session |
| `POST /vision/withdraw` | Drop specific tracks of the live session |
| `POST /vision/offline` | End a session and withdraw everything it published |

All three require `X-Vision-Token`, compared against
`runtime/vision/home-service.token`. This service creates that file on startup
when it is missing and never logs its value. These routes never reach
`ToolService` or the Home Assistant gateway, so an observation can never actuate
a device.

A whole batch is validated before anything mutates: exact keys, known room and
person, session/camera consistency, an integer timestamp no more than 5 seconds in
the future, age within the observation TTL, normalized `[0,1]` boxes and
keypoints, allowed pose and identity values, unique track ids, and at most one
track per confirmed person. One bad field rejects the entire batch.

Only a track with `identity_state: "confirmed"` and a unique known `person_id`
sets a person's `room_id`; `x`/`y` are normalized image coordinates taken from the
body-box bottom centre. When an observation expires, a session goes offline, or a
track is withdrawn, `room_id`, `x`, `y`, `camera_id`, and `track_id` become
`null`, `location_known` becomes `false`, and `pose` becomes `unknown`. A
historical or simulated room is never restored.

Both backends read this one store, so switching `HOME_SERVICE_BACKEND` cannot
change where a person is shown. The manual position routes `/test/move_person` and
`/test/bind_track` were removed along with the state methods behind them, because
a second source of position authority would contradict the camera observation.

`query_person_location` and multi-target notification planning read the same
store, so they answer "location unknown" until a fresh confirmed observation
exists — including for "叫爸爸吃饭", which now asks for clarification instead of
guessing a room.

# Home Assistant backend

The service can instead drive real Home Assistant entities. Both backends live in
the same process; exactly one is selected, and there is **no silent fallback**.

| Variable | Meaning |
| --- | --- |
| `HOME_SERVICE_BACKEND` | `memory` (default) or `ha`. Any other value refuses to start. |
| `HOME_ASSISTANT_URL` | Usually `http://127.0.0.1:8123` |
| `HOME_ASSISTANT_TOKEN` | Long-lived access token; may instead come from `HA_ENV_FILE` |
| `HA_ENV_FILE` | Local git-ignored file holding `HOME_ASSISTANT_URL` / `HOME_ASSISTANT_TOKEN` |
| `HA_ENTITY_CATALOG` | Defaults to `config/ha_entities.json` |
| `HA_OPERATION_DB` | SQLite path for the operation state machine |

## Create the token yourself

Open <http://127.0.0.1:8123/profile/security>, create a long-lived access token,
and save it locally — never into Git, a script, or a chat message:

```powershell
Set-Content runtime\home-assistant\ha.env "HOME_ASSISTANT_TOKEN=<token>"
```

## Start the closed loop

```powershell
# 1) Generate local broker credentials (idempotent; secrets are never printed)
#    and build the simulator image.
.\tools\ha-check\prepare_stack.ps1

# 2) Start Home Assistant, Mosquitto and the simulator on the existing stack.
docker compose --env-file runtime\home-assistant\compose.env `
  -f D:\dac\docker-compose.yml `
  -f infra\home-assistant\compose.override.yaml up -d

# 3) Point the existing MQTT integration at the new broker credentials.
$env:HA_ENV_FILE     = 'E:\smart-home\runtime\home-assistant\ha.env'
$env:SHV_ENV_FILE    = 'E:\smart-home\runtime\home-assistant\stack.env'
.\.venv\Scripts\python.exe tools\ha-check\setup_mqtt.py

# 4) Run the HA-backed service on loopback.
$env:HOME_SERVICE_BACKEND = 'ha'
$env:HOME_ASSISTANT_URL   = 'http://127.0.0.1:8123'
$env:HA_OPERATION_DB      = 'E:\smart-home\runtime\home-assistant\operations.sqlite3'
.\.venv\Scripts\python.exe apps\home-service\src\server.py

# 5) Verify the whole loop, including fault injection.
.\.venv\Scripts\python.exe tools\ha-check\acceptance.py
```

Only Home Assistant publishes a host port, and only on `127.0.0.1:8123`.
MQTT stays on the Compose-internal network.

## What "confirmed" means here

Writes are persisted as `accepted -> submitted -> confirmed | unconfirmed |
rejected`. The API only reports success after the entity is **observed** in the
target state; an HTTP 200 from Home Assistant proves only that the request was
accepted. A timeout is reported as `confirmation_timeout` with status
`unconfirmed` — never as success, and never as proof the device failed.

Reusing an `operation_id` with the same arguments replays the stored outcome;
reusing it with different arguments returns `operation_id_conflict`. On restart,
`accepted` operations may be submitted once, while `submitted` and `unconfirmed`
operations are only reconciled against observed state, because a blind resend
could actuate a device twice.

Fault controls (offline, fixed delay, next-command failure) are local admin
only: they are driven by `runtime/home-assistant/fault-control.json` and are
never exposed as Agent tools.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests tools/ha-check/tests -q
```
