# Local Face and Pose Vision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a loopback-only local vision service that registers `dad`, `mom`, and `child`, tracks and identifies people, derives stable pose states, publishes camera-sourced room presence through home-service, and exposes a functional Unity camera panel.

**Architecture:** `vision-service` owns the only active camera, runs YOLO26n-pose with ByteTrack, periodically associates InsightFace results with body tracks, and keeps only latest-frame queues. It posts validated active observations to a shared visual-person store in `home-service`; both Memory and HA snapshots merge that same store before existing SSE publication. Unity reads home state from port 8765 and camera/configuration/registration/preview state from the new loopback service on port 8766.

**Tech Stack:** Python 3.11; standard-library HTTP servers; OpenCV; Ultralytics YOLO26n-pose + ByteTrack; InsightFace + ONNX Runtime; Windows DPAPI via `ctypes`; Unity 2022.3 C# with runtime-built uGUI.

**Spec:** `docs/superpowers/specs/2026-09-21-local-face-pose-vision-design.md`

## Global Constraints

- Do not run tests, compilation, Unity batch/Play/EditMode, model inference, camera capture, service startup, or screenshot/video acceptance. User performs all functional verification.
- Delivery wording is exactly “已实现，尚未验证”; never claim availability, passing tests, FPS, accuracy, or visual correctness.
- One physical camera is active at a time; its logical room is exactly one of `living_room`, `bedroom`, or `kitchen`.
- `vision-service` binds only `127.0.0.1:8766`; `home-service` remains on `127.0.0.1:8765`.
- Raw frames remain in memory and the current Unity preview; no default image/video persistence.
- Face embeddings live under Git-ignored `runtime/vision` and are protected for the current Windows user with DPAPI.
- Known identities are exactly `dad`, `mom`, and `child`; weak, conflicting, or unregistered identities are `unknown`.
- Vision is observation-only: no Home Assistant tool invocation, device control, alarm, speech, or rescue action.
- Ultralytics model file is `yolo26n-pose.pt`; tracking explicitly uses `bytetrack.yaml` with persistent per-session track IDs.
- InsightFace model files are local-only. Startup must report `model_missing` rather than silently download a model.
- Preserve all pre-existing uncommitted work; do not edit unrelated device-simulator, HA device, voice, or SSE behavior.

## File Structure

```text
apps/vision-service/
  pyproject.toml                 dependency and Python contract
  README.md                      offline model setup, launch, API and privacy notes
  config/vision.json             thresholds, intervals, queues and model paths
  src/contracts.py               enums and transport dataclasses
  src/config.py                  strict local configuration loading
  src/latest.py                  capacity-one latest-value exchange
  src/capture.py                 physical/URL source enumeration and single-stream session
  src/pose.py                    YOLO/ByteTrack adapter and temporal pose state machine
  src/face.py                    InsightFace adapter, quality gates and body association
  src/registry.py                DPAPI-encrypted atomic embedding registry
  src/registration.py            six-step registration and short confirmation state machine
  src/home_client.py             authenticated observation batch/withdraw/offline client
  src/pipeline.py                session ownership, identity stabilization and result snapshots
  src/server.py                  loopback HTTP API and JPEG preview
  src/main.py                    CLI entry point
apps/home-service/src/
  models.py                      expanded visual observation/person transport fields
  visual_state.py                backend-independent validated visual-person store
  server.py                      restricted ingress routes and SSE publication hook
  event_stream.py                merge visual persons into Memory and HA snapshots
apps/unity-house-payload/Assets/Scripts/
  VisionContracts.cs             vision JSON DTOs and parser
  VisionServiceClient.cs         non-blocking loopback API/preview client
  VisionOverlayGraphic.cs        normalized boxes, skeleton and labels
  VisionCameraPanel.cs           runtime-built uGUI controls and status presentation
  StateParser.cs                 camera provenance and pose fields for known persons
  SceneStateApplier.cs           camera location/pose labels and honest unknown state
.gitignore                       explicit vision source allowlist and runtime/model exclusions
README.md                        architecture and launch-order update
```

## Fixed Local Contracts

All responses use JSON UTF-8 except `/preview.jpg`. Errors use `{"ok":false,"error_code":"snake_case","message":"human-readable"}` and never echo complete video URLs.

`vision-service` routes:

```text
GET    /health
GET    /cameras
GET    /config
POST   /camera/select       {"kind":"device","device_id":"0"} OR {"kind":"url","url":"..."}
POST   /room/select         {"room_id":"living_room|bedroom|kitchen"}
POST   /monitor/start       {}
POST   /monitor/pause       {}
GET    /results
GET    /preview.jpg
POST   /registration/start  {"person_id":"dad|mom|child"}
GET    /registration
POST   /registration/cancel {}
DELETE /registration/{person_id}
```

`GET /results` returns this stable shape:

```json
{
  "ok": true,
  "session_id": "vision-opaque-id",
  "mode": "idle|monitoring|registering|error",
  "camera_id": "device:0",
  "camera_room_id": "living_room",
  "observed_at_ms": 0,
  "sync_state": "synced|pending|disconnected|not_monitoring",
  "tracks": [
    {
      "track_id": "vision-opaque-id:42",
      "bbox": [0.0, 0.0, 1.0, 1.0],
      "keypoints": [[0.0, 0.0, 0.0]],
      "detection_confidence": 0.0,
      "person_id": null,
      "identity_state": "unknown|candidate|confirmed|held|conflict",
      "face_similarity": null,
      "pose": "standing|sitting|lying|suspected_fall|hand_raised|unknown",
      "pose_confidence": 0.0,
      "observed_at_ms": 0,
      "track_state": "active|lost"
    }
  ]
}
```

`vision-service -> home-service` routes require `X-Vision-Token`, read from Git-ignored `runtime/vision/home-service.token`:

```text
POST /vision/observations  {"camera_id":"...","camera_room_id":"living_room","session_id":"...","observed_at_ms":0,"observations":[track...]}
POST /vision/withdraw      {"camera_id":"...","session_id":"...","track_ids":["..."]}
POST /vision/offline       {"camera_id":"...","session_id":"...","observed_at_ms":0,"reason":"camera_disconnected|model_error|service_stopping|source_changed|room_changed"}
```

Only observations with `identity_state == "confirmed"` and a unique known `person_id` affect a person snapshot. A published person has `room_id`, normalized `x/y` from the body-box bottom center, `location_known:true`, `location_source:"camera"`, `camera_id`, `track_id`, `pose`, `pose_confidence`, and `observed_at_ms`. Withdrawal, offline state, or expiry sets `room_id/x/y/camera_id/track_id` to `null`, `location_known:false`, `location_source:"camera"`, and `pose:"unknown"`; it never restores a historical room.

## Review Focus

- A stale observation, future timestamp, invalid normalized coordinate, unknown room/person, or duplicate confirmed identity must reject the whole batch without partially mutating state; Task 6 owns the validation cases.
- Camera/room/source changes must withdraw the old session before the new session can publish; Task 5 owns the session transition cases.
- Face loss for at most 3 seconds may hold an already confirmed identity, but a lost/new track must not inherit it; Task 5 owns identity-window cases.
- Direct lying or bending must not become `suspected_fall` without preceding rapid descent and sustained lying; Task 3 owns temporal pose cases.
- UI/service disconnection and model/camera faults must replace stale overlays and locations with explicit honest states; Tasks 5, 6, and 8 own those state transitions.

---

### Task 1: Vision Service Contracts, Configuration, and Privacy Boundary

**Files:**
- Create: `apps/vision-service/pyproject.toml`
- Create: `apps/vision-service/config/vision.json`
- Create: `apps/vision-service/src/contracts.py`
- Create: `apps/vision-service/src/config.py`
- Create: `apps/vision-service/src/latest.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `VisionConfig.load(path: Path) -> VisionConfig`; `LatestValue[T].put(value)` / `take(timeout)`; enums `ServiceMode`, `IdentityState`, `PoseState`, `TrackState`; dataclasses `NormalizedPoint`, `TrackObservation`, `VisionSnapshot`.
- Consumes: no earlier task interfaces.

- [ ] **Step 1: Add an explicit service dependency contract**

```toml
[project]
name = "smart-home-vision-service"
version = "0.1.0"
requires-python = ">=3.11,<3.12"
dependencies = [
  "insightface>=2.0,<3",
  "numpy>=2.4,<3",
  "onnxruntime-gpu>=1.23,<2",
  "opencv-python>=4.13,<5",
  "ultralytics>=8.4.63,<9",
]
```

Do not install dependencies. Document that InsightFace model licenses and Ultralytics AGPL/Enterprise terms must be accepted by the user before model placement or distribution.

- [ ] **Step 2: Fix conservative defaults in `vision.json`**

```json
{
  "host": "127.0.0.1",
  "port": 8766,
  "home_service_url": "http://127.0.0.1:8765",
  "runtime_dir": "runtime/vision",
  "pose_model": "D:/smart-home-models/yolo26n-pose.pt",
  "insightface_root": "D:/smart-home-models/insightface",
  "insightface_pack": "buffalo_l",
  "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
  "face_review_interval_frames": 5,
  "identity_window_frames": 5,
  "identity_min_votes": 3,
  "identity_similarity_threshold": 0.55,
  "identity_margin_threshold": 0.08,
  "identity_hold_ms": 3000,
  "observation_ttl_ms": 3500,
  "face_min_pixels": 80,
  "face_min_detection_confidence": 0.65,
  "body_min_confidence": 0.35,
  "keypoint_min_confidence": 0.35,
  "preview_jpeg_quality": 75
}
```

- [ ] **Step 3: Implement strict typed parsing**

Reject unknown keys, non-loopback hosts, relative runtime/model paths, invalid room/identity values, non-positive intervals, ratios outside `[0,1]`, and `identity_min_votes > identity_window_frames`. Resolve paths without opening models.

- [ ] **Step 4: Implement capacity-one handoff**

```python
class LatestValue(Generic[T]):
    def put(self, value: T) -> None: ...      # overwrite unconsumed value
    def take(self, timeout: float | None = None) -> T | None: ...
    def close(self) -> None: ...
```

Use one `Condition`; never allocate an unbounded queue.

- [ ] **Step 5: Update repository boundaries**

Allowlist `apps/vision-service/{src,config,pyproject.toml,README.md}` while retaining `runtime/`, `models/`, `*.onnx`, and `*.pt` ignores. Do not unignore embeddings, frames, tokens, or model weights.

- [ ] **Step 6: Perform static review only**

Read the added files and run only `git diff --check -- apps/vision-service .gitignore`. Do not import Python packages or run tests.

### Task 2: Single Camera Session and Latest-Frame Capture

**Files:**
- Create: `apps/vision-service/src/capture.py`

**Interfaces:**
- Consumes: `VisionConfig`, `LatestValue[CapturedFrame]`.
- Produces: `CameraSource(kind, source_id, redacted_label)`; `CapturedFrame(frame_bgr, captured_at_ms, width, height)`; `enumerate_cameras(max_index=8)`; `CaptureSession.start(source)` / `stop(reason)` / `latest_frame` / status callback.

- [ ] **Step 1: Implement safe source parsing and redaction**

Accept integer device IDs or `http/https/rtsp` URLs, but expose/log only scheme, host, and a short SHA-256 fingerprint. Reject embedded credentials from API responses and exception messages.

- [ ] **Step 2: Implement bounded camera enumeration**

Probe indices `0..7` only when `/cameras` is explicitly requested; release every temporary `VideoCapture` in `finally`; return `device:<index>` plus backend-reported width/height when available.

- [ ] **Step 3: Implement the one-stream owner**

Guard `start`/`stop` with an `RLock`, stop and join the old capture thread before opening another source, give each start a new opaque `session_id`, and write frames only to `LatestValue`.

- [ ] **Step 4: Make failure state explicit**

Opening failure, repeated read failure, or stop must close the latest-frame exchange and emit `camera_open_failed`, `camera_disconnected`, or the caller-supplied transition reason. Never retain a stale frame as current.

- [ ] **Step 5: Perform static review only**

Inspect all `VideoCapture` paths for matching release/close logic and run `git diff --check -- apps/vision-service/src/capture.py` only.

### Task 3: Pose Tracking and Temporal Pose State Machine

**Files:**
- Create: `apps/vision-service/src/pose.py`

**Interfaces:**
- Consumes: BGR frame plus `captured_at_ms`; config confidence thresholds.
- Produces: `PoseTracker.load()`, `reset_session()`, `process(frame) -> list[BodyTrack]`; `PoseStateMachine.update(track_id, keypoints, bbox, at_ms) -> PoseEstimate`; `forget(track_id)`.

- [ ] **Step 1: Wire YOLO26 pose and ByteTrack without executing them**

```python
self.model = YOLO(config.pose_model)
result = self.model.track(
    frame_bgr, persist=True, tracker="bytetrack.yaml",
    classes=[0], conf=config.body_min_confidence, verbose=False
)[0]
```

Convert boxes and COCO 17-keypoint `(x,y,confidence)` values to normalized coordinates immediately. Prefix tracker IDs with the capture `session_id`.

- [ ] **Step 2: Implement feature extraction with missing-keypoint handling**

Compute torso angle, shoulder/hip width, hip-knee-ankle flexion, body aspect ratio, wrist-above-shoulder duration, hip-center vertical velocity, and available-keypoint ratio. Any required point below threshold makes that rule unavailable rather than zero-valued.

- [ ] **Step 3: Implement deterministic temporal rules**

Use per-track deques bounded by time and count. Require 300 ms evidence to enter standing/sitting/lying/hand-raised, 500 ms to exit, and `vertical_drop >= 0.18 frame-height/second` followed within 900 ms by lying sustained for 700 ms before `suspected_fall`. Direct lying and bending resolve to lying/unknown, not a fall.

- [ ] **Step 4: Define conflict priority and confidence**

`suspected_fall` overrides `lying`; `hand_raised` is reported only when no fall is active and otherwise acts as an annotation input; mutually supported sitting/standing/lying states become `unknown`. Confidence is the minimum normalized support among the winning rule inputs.

- [ ] **Step 5: Clear state on session or track loss**

`reset_session()` drops all tracker and pose histories; `forget()` removes one track. No temporal evidence survives a new ByteTrack ID.

- [ ] **Step 6: Perform static review only**

Trace the standing, sitting, lying, direct-lying, rapid-descent, raised-hand, insufficient-keypoint, and conflicting-rule branches by reading code. Run `git diff --check` only.

### Task 4: Face Recognition, Encrypted Registry, and Registration Flow

**Files:**
- Create: `apps/vision-service/src/face.py`
- Create: `apps/vision-service/src/registry.py`
- Create: `apps/vision-service/src/registration.py`

**Interfaces:**
- Consumes: current BGR frame, active body boxes, `dad|mom|child`, config thresholds, `runtime/vision`.
- Produces: `FaceEngine.load()` / `analyze(frame) -> list[FaceSample]`; `associate_faces(faces, bodies) -> dict[track_id, FaceSample]`; `FaceRegistry.replace(person_id, prototypes)` / `delete(person_id)` / `rank(embedding)`; `RegistrationSession.accept(frame, faces, at_ms) -> RegistrationStatus`.

- [ ] **Step 1: Load InsightFace strictly from local paths**

Construct `FaceAnalysis(name=pack, root=insightface_root, providers=providers)` only after confirming the expected model directory exists. Disable automatic download paths; expose `model_missing` with the redacted path when absent.

- [ ] **Step 2: Add quality and association gates**

Require one face for registration, configured detection confidence, minimum face pixels, Laplacian sharpness, acceptable brightness, complete five landmarks, and face-center containment inside exactly one body box for monitoring. When two body boxes qualify, choose highest IoU only if its lead over second place is at least `0.10`; otherwise leave unassociated.

- [ ] **Step 3: Implement current-user DPAPI storage**

Serialize only `schema_version`, person IDs, model pack, embedding dimension, and float32 prototypes. Encrypt/decrypt with Windows `CryptProtectData` / `CryptUnprotectData`, write a temporary sibling, `fsync`, then `os.replace`. Delete all prototypes for a person on request. Logs include only person ID and prototype count.

- [ ] **Step 4: Implement identity ranking**

L2-normalize every embedding/prototype. Rank each person by its best cosine similarity; accept a candidate only when the top score reaches `0.55` and exceeds the runner-up by `0.08`. Return score/margin but not embeddings.

- [ ] **Step 5: Implement the six registration gates**

Advance through `front`, `turn_left`, `turn_right`, `look_up`, `look_down`, `blink`. Use five-point landmark geometry for head direction and the InsightFace 106-landmark eyelid ratio for blink; require neutral-open eye history before a closed/open cycle. Each step needs two quality-approved samples and stores one normalized prototype centroid, except blink, which proves the action without adding a duplicate prototype.

- [ ] **Step 6: Atomically replace and confirm**

After all steps, replace the old person record in one registry write and enter `confirming` for 5 seconds. Confirmation needs three accepted frames naming the same person; failure keeps the newly registered record but reports `confirmation_failed` so the user can retry or delete.

- [ ] **Step 7: Perform static review only**

Read all error paths for multi-face, no-face, small/blurred/dark/bright/occluded face, wrong action, interrupted camera, cancelled session, corrupt registry, and DPAPI failure. Run `git diff --check` only.

### Task 5: Vision Pipeline, Identity Stabilization, Home Sync, and HTTP API

**Files:**
- Create: `apps/vision-service/src/home_client.py`
- Create: `apps/vision-service/src/pipeline.py`
- Create: `apps/vision-service/src/server.py`
- Create: `apps/vision-service/src/main.py`

**Interfaces:**
- Consumes: Tasks 1-4 services; fixed HTTP contracts.
- Produces: `VisionRuntime` single owner of mode/source/room/session; loopback API; latest JPEG bytes; authenticated home-service updates.

- [ ] **Step 1: Implement token and home-client behavior**

Read `runtime/vision/home-service.token`; never create or log it in vision-service. Use 2-second request timeouts, coalesce pending observation uploads to the newest batch, and expose `pending`/`disconnected` until a 2xx response confirms sync.

- [ ] **Step 2: Stabilize identity per track**

Keep a deque of the last five valid face rankings per active track. Confirm after three votes for one identity meeting threshold/margin. Hold a confirmed identity without a usable face for at most 3000 ms, never across track loss. Resolve simultaneous duplicate identities by vote count, median similarity, then freshest face time; mark all weaker tracks `conflict`/unknown.

- [ ] **Step 3: Enforce transition ordering**

For source or room changes: pause publication, call `/vision/offline` for the old session and wait for its response, reset capture/tracker/identity/pose state, create the new session, then allow monitoring. Registration similarly withdraws monitoring before capture collection and never assigns a room.

- [ ] **Step 4: Implement latest-only processing and preview**

Processing consumes `LatestValue`; if slower than capture it receives only the newest frame. Publish immutable `VisionSnapshot` and JPEG bytes behind locks. Preview drawing occurs on a copy and includes body boxes, skeleton, identity/unknown label, pose, and confidence without writing frames to disk.

- [ ] **Step 5: Implement all fixed routes**

Validate JSON object bodies and exact allowed keys, serialize state under locks, use `DELETE` for registration deletion, return `409` for invalid mode transitions, `422` for invalid source/room/person, and `503` for missing models/camera. Set `Cache-Control: no-store` on JSON state and preview responses.

- [ ] **Step 6: Make shutdown/faults honest**

On model exception or camera loss, clear results/preview, withdraw or offline the current session, and stay in explicit error state. On process exit, attempt one bounded offline call, then release the camera and stop threads even if home-service is unreachable.

- [ ] **Step 7: Perform static review only**

Trace normal start/pause, registration entry/cancel, room/source change, model error, camera disconnect, home-service disconnect, duplicate identity, face hold expiry, track loss, and shutdown. Run `git diff --check` only.

### Task 6: Backend-Independent Visual State in home-service

**Files:**
- Create: `apps/home-service/src/visual_state.py`
- Modify: `apps/home-service/src/models.py`
- Modify: `apps/home-service/src/state.py`
- Modify: `apps/home-service/src/server.py`
- Modify: `apps/home-service/src/event_stream.py`
- Modify: `apps/home-service/src/ha_service.py`
- Modify: `apps/home-service/config/rooms.json`

**Interfaces:**
- Consumes: authenticated observation/withdraw/offline contracts from Task 5.
- Produces: `VisualStateStore.submit_batch(payload, now_ms)`, `withdraw(...)`, `offline(...)`, `snapshot(now_ms) -> list[dict]`; Memory and HA snapshots with identical visual-person semantics.

- [ ] **Step 1: Expand the domain contract**

Replace manual-binding fields with `camera_room_id`, normalized keypoints, `person_id`, `face_similarity`, `identity_state`, `pose`, `pose_confidence`, and `track_state`. Extend person serialization with `location_source`, `camera_id`, `track_id`, `pose`, `pose_confidence`, and `observed_at_ms`.

- [ ] **Step 2: Create one thread-safe visual store**

Seed the exact person catalog from `rooms.json` but initialize every person with unknown location. Store current sessions/tracks separately from the derived person view. Lazily expire observations older than `observation_ttl_ms` during submit, snapshot, withdraw, and offline.

- [ ] **Step 3: Validate batches atomically**

Before mutation validate exact keys, known room/person, session/camera consistency, integer timestamp no more than 5 seconds in the future, age within TTL, bbox/keypoint coordinates in `[0,1]`, confidences in `[0,1]`, allowed pose/state values, unique track IDs, and unique confirmed person IDs. Reject the complete batch on one error.

- [ ] **Step 4: Restrict ingress without exposing controls**

On startup create `runtime/vision/home-service.token` with `secrets.token_urlsafe(32)` only when absent, write it current-user-readable, and compare `X-Vision-Token` with `secrets.compare_digest`. Handle only the three `/vision/*` routes; never route them through `ToolService` or HA gateway code.

- [ ] **Step 5: Remove manual position authority**

Remove `/test/move_person`, `/test/bind_track`, and manual-binding behavior from runtime routing. Keep person directory lookup for voice queries, but return unknown when the visual store has no fresh confirmed observation. Change configured person `room_id` defaults to `null`.

- [ ] **Step 6: Merge one visual store into both backends**

Attach `visual_state` to both `HomeServiceApp` and `HAServiceApp`. Make `canonical_memory_snapshot` and `canonical_ha_snapshot` accept `persons=visual_state.snapshot(...)`; never use HA or historical Memory person positions. Successful vision mutations immediately call `LiveStateStream.publish_snapshot()`.

- [ ] **Step 7: Perform static review only**

Read validation to cover every Review Focus input, verify every rejection happens before mutation, and trace expiry/withdraw/offline through both canonical snapshot functions. Run `git diff --check` only.

### Task 7: Unity Vision Contracts, Client, Preview, and Overlay

**Files:**
- Create: `apps/unity-house-payload/Assets/Scripts/VisionContracts.cs`
- Create: `apps/unity-house-payload/Assets/Scripts/VisionServiceClient.cs`
- Create: `apps/unity-house-payload/Assets/Scripts/VisionOverlayGraphic.cs`
- Modify: `apps/unity-house-payload/Assets/Scripts/SmartHome.asmdef`

**Interfaces:**
- Consumes: vision-service JSON and JPEG contracts from Task 5.
- Produces: `VisionSnapshotDto`; registration/camera/config DTOs; `VisionServiceClient` events and command coroutines; normalized overlay renderer.

- [ ] **Step 1: Implement strict DTO parsing**

Use the existing `MiniJson` parser and explicit readers for nullable numbers/strings, arrays, bbox, 17 keypoints, status, cameras, registration progress, and errors. Unknown enum strings map to `unknown` rather than throwing.

- [ ] **Step 2: Implement one non-blocking client**

Poll `/results` and `/registration` once per second, fetch `/preview.jpg` only while the panel is visible, and allow at most one request of each kind in flight. All callbacks return on Unity's coroutine/main thread. On failure clear the preview and publish a disconnected status.

- [ ] **Step 3: Implement safe command helpers**

Provide `SelectDevice`, `SelectUrl`, `SelectRoom`, `StartMonitoring`, `PauseMonitoring`, `StartRegistration`, `CancelRegistration`, and `DeleteRegistration`. Serialize JSON with an explicit string-escape helper; never concatenate unescaped URL input.

- [ ] **Step 4: Implement the overlay as a uGUI graphic**

Derive `VisionOverlayGraphic` from `UnityEngine.UI.MaskableGraphic`, disable its raycast target, and generate vertices for normalized body rectangles and COCO skeleton edges. Map source coordinates through the RawImage's aspect-fit rectangle so overlays align at any preview resolution. Draw identity/pose/confidence labels as child UI text managed by the panel, not per-frame GameObject creation.

- [ ] **Step 5: Add required assembly references**

Add `UnityEngine.UI` to the asmdef references only if Unity 2022 requires it for the payload assembly; do not add editor-only or third-party UI packages.

- [ ] **Step 6: Perform static review only**

Inspect request disposal, coroutine cancellation, texture replacement/destruction, JSON escaping, coordinate transforms, and error clearing. Run `git diff --check` only; do not compile Unity.

### Task 8: Runtime uGUI Camera Panel and House Person Presentation

**Files:**
- Create: `apps/unity-house-payload/Assets/Scripts/VisionCameraPanel.cs`
- Modify: `apps/unity-house-payload/Assets/Scripts/StateParser.cs`
- Modify: `apps/unity-house-payload/Assets/Scripts/SceneStateApplier.cs`
- Modify: `apps/unity-house-payload/README.md`

**Interfaces:**
- Consumes: Task 7 client/events and home-service person fields from Task 6.
- Produces: runtime-created Canvas panel, wired controls, honest status/quality messages, pose-aware house labels.

- [ ] **Step 1: Build a responsive runtime uGUI hierarchy**

Create exactly one Screen Space Overlay Canvas when no assigned panel exists, with `CanvasScaler` set to `ScaleWithScreenSize`, reference `1920x1080`, match `0.5`, plus `GraphicRaycaster` and one `EventSystem`. Build `Header`, `Preview`, `Controls`, `Registration`, and `Status` children using vertical/horizontal layout groups and fully qualified `UnityEngine.UI` types.

- [ ] **Step 2: Wire complete camera and room controls**

Add source dropdown, URL input, refresh/select controls, room dropdown for the three fixed rooms, monitoring start/pause, and preview RawImage. Disable room/source changes during an in-progress transition and show the server-provided error instead of assuming success.

- [ ] **Step 3: Wire the registration workflow**

Provide person buttons for 爸爸/妈妈/孩子, current action text for 正视/左转/右转/抬头/低头/眨眼, progress, quality reason, confirm state, cancel, delete, and re-register. Confirmation dialogs must name the person whose embeddings will be replaced/deleted.

- [ ] **Step 4: Display honest operational states**

Present service/model/camera/home-sync state plus explicit messages for disconnected, model missing, camera open failure, no person, unknown person, multiple faces, face too small, poor quality, wrong action, and sync pending. Clear old overlay/preview on disconnect or session change.

- [ ] **Step 5: Extend house person DTO/rendering**

Parse `location_source`, `camera_id`, `track_id`, `pose`, `pose_confidence`, and `observed_at_ms`. For known locations, label `姓名 · 姿态`; for unknown locations, move the marker to an explicit unknown staging area rather than leaving it in the previous room. Remove comments claiming camera detections never move people.

- [ ] **Step 6: Document scene attachment without editor automation**

Describe adding `VisionServiceClient` and `VisionCameraPanel` to a scene GameObject, assigning the client, and keeping one EventSystem. Do not create serialized prefab/scene files by hand and do not open Unity.

- [ ] **Step 7: Perform static review only**

Read the constructed hierarchy for non-zero sizes, anchors/layout ownership, one EventSystem, button listeners, raycast targets, and disconnect clearing. Run `git diff --check` only; no screenshot or PlayMode acceptance.

### Task 9: Documentation, Launch Contract, and Final Static Audit

**Files:**
- Create: `apps/vision-service/README.md`
- Modify: `apps/home-service/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: every prior task's final routes, files, configuration, and limitations.
- Produces: reproducible manual setup/launch instructions and a user-owned acceptance checklist.

- [ ] **Step 1: Document offline model placement and licensing**

State exact YOLO and InsightFace directories, that normal service startup must not download, the GPU/CPU provider behavior, and the separate Ultralytics and pretrained InsightFace licensing obligations.

- [ ] **Step 2: Document launch order and ports**

Use: home-service first (creates token), vision-service second (reads token/models), Unity last. Document the three loopback boundaries and that vision ingress cannot control devices.

- [ ] **Step 3: Document privacy and deletion**

State that frames are not persisted, embeddings are DPAPI-encrypted under `runtime/vision`, URLs/logs are redacted, and deleting a person removes every prototype.

- [ ] **Step 4: Copy the seven user-owned acceptance checks from the spec**

Mark every check “未执行，由用户手动验收”; do not add measured FPS or accuracy values.

- [ ] **Step 5: Run the permitted final audit**

Run only `git status --short`, `git diff --check`, and targeted `rg` searches for `0.0.0.0`, raw URL logging, image writes, embeddings in JSON/logging, manual move/bind routes, unbounded `Queue`, and stale comments. Do not import modules, start services, access live endpoints, run tests, compile, infer, access a camera, or take screenshots.

- [ ] **Step 6: Report exact completion state**

List changed files and any unresolved dependency/model/license/manual-setup needs. Use the phrase “已实现，尚未验证” and explicitly enumerate every prohibited verification category as not run.

---

## 复核状态（2026-09-21，静态复核轮）

Task 1–8 末尾的「Perform static review only」已逐条执行：只读代码、`git status --short`、`git diff --check`、文本搜索。**未运行**任何测试、编译、模块导入、模型推理、摄像头采集、服务启动或截图验收，因此本文件中的实现步骤仍未经过运行验证。

| 复核步骤 | 结果 |
| --- | --- |
| Task 1 Step 6 | 通过；发现计划 Step 2 与 Step 3 自相矛盾（见下） |
| Task 2 Step 5 | 通过（四处 `VideoCapture` 均配对释放） |
| Task 3 Step 6 | **发现 2 个缺陷（A、B）** |
| Task 4 Step 7 | 错误路径齐全；**发现缺陷 D** |
| Task 5 Step 7 | **发现缺陷 E** |
| Task 6 Step 7 | 校验早于变更、过期/撤销/离线上溯两函数均通过；**发现缺陷 C** |
| Task 7 Step 6 | 通过（请求释放、纹理替换/销毁、失败清预览、JSON 转义均在位） |
| Task 8 Step 7 | 通过（唯一 EventSystem、raycast 关闭、监听齐全、尺寸非零、池化销毁） |
| Task 9 Step 5 | 通过（8 项审计全部执行） |

### 复核发现的 5 个缺陷（已按用户授权修复，仍未经运行验证）

- **A** `pose.py` `_select`：举手在站立/坐下/躺下成立时永不报告，与设计 §6 和验收第 4 条冲突。
- **B** `pose.py` `lying_by_shape`：无可用关键点时仍可凭检测框宽高比判为躺下，`available_ratio` 计算后未使用；与设计 §6「关键点不足 → 未知」冲突。
- **C** `home-service/src/server.py` `after_request`：只对 memory 后端发布快照，HA 后端下成功的 `/vision/*` 变更不会立即推送，与 Task 6 Step 6 冲突；修复前需先决定限流策略。
- **D** `pipeline.py` `_on_capture_status`：摄像头中断时未取消注册会话，`/registration` 持续返回中断前的进度，与设计 §11 冲突。
- **E** `pipeline.py` `select_room`：无条件停止采集却不同步模式，导致「monitoring 但无帧、残留上一批 tracks」；计划 Step 3 要求切换后监控继续有效。

执行约束与静态复核记录保存在 Git 忽略的
`.superpowers/sdd/2026-09-21-local-face-pose-vision/progress.md`。

### 最终契约复核追加修复（同样未经运行验证）

- 空观察批次现在会替换该摄像头的上一帧轨迹，人物离开画面后不会只靠 TTL 清除。
- observation 上传、offline/withdraw 使用代际屏障串行化，旧会话的排队帧不能在离线消息后回流。
- InsightFace providers 改在 `FaceAnalysis` 构造时传入，配置的 CUDA/CPU 顺序不再被忽略。
- 注册响应补齐 `active`，确认阶段不再被提前当作完成，最终状态保留给 Unity 轮询读取。
- 注册帧只执行一次人脸分析；注册状态步骤改为面向 UI 的 1–6 编号。
- 单人注册库没有 runner-up 时允许通过 margin 门槛；阈值统一由加载的配置决定。
- DPAPI 输出改用 `ctypes.string_at` 复制并用 64 位安全的 `LocalFree` 释放。
- Unity bbox 与 Python 统一为 `[x1,y1,x2,y2]`；摄像头枚举从每秒探测改为启动一次和手动刷新。
- `/config`、`/cameras`、`/registration` 的服务响应与 Unity 解析结构已对齐。
- Unity home 客户端改读 Memory/HA 共用的规范化完整快照，使视觉位置和过期状态持续更新。

### 未勾选说明

上面的复选框保持未勾选状态：复核只确认了「代码与设计/计划不一致」这类静态事实，未确认任何运行行为。上述缺陷与追加契约问题已按用户授权修复，但**修复本身同样没有运行验证**——没有测试、编译、导入、推理、摄像头、服务或截图。因此这些实现步骤要等真机验收通过后才应标记完成。
