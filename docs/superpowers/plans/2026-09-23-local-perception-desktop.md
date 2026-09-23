# Local Perception Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React-based local perception console for vision and voice, add a Windows launcher, and remove Unity's old vision-management UI while preserving read-only person-state display.

**Architecture:** A loopback console on port 8770 serves the React application and proxies allow-listed APIs to home-service, vision-service, and a new voice-service HTTP event surface. A PowerShell launcher owns service processes it starts. Unity remains an SSE-only visualization client of home-service.

**Tech Stack:** Python 3.11 stdlib HTTP/SSE, React, TypeScript, Vite, PowerShell, Unity 2022 C# cleanup

**Spec:** `docs/superpowers/specs/2026-09-23-local-perception-desktop-design.md`

## Global Constraints

- Bind all new servers to `127.0.0.1` only.
- Do not persist raw camera frames, raw audio, transcripts, or replies.
- Do not expose API keys, complete model paths, or complete camera URLs.
- Do not run tests, builds, model inference, camera validation, microphone validation, or screenshot acceptance.
- Work directly on `main` as explicitly requested by the user.
- Remove Unity vision-management scripts only after removing their scene components.

## Review Focus

- A missing backend must degrade only its own panel and leave the console usable.
- Proxy paths must be allow-listed rather than accepting arbitrary target URLs.
- Service shutdown must not terminate processes the launcher did not create.
- Voice event payloads must exclude raw audio and secrets.
- Unity must retain HomeService person-location rendering after vision UI removal.

---

### Task 1: Voice event and HTTP surface

**Files:**
- Create: `apps/voice-service/src/event_bus.py`
- Create: `apps/voice-service/src/control_server.py`
- Modify: `apps/voice-service/src/loop.py`
- Modify: `apps/voice-service/README.md`

**Interfaces:**
- Produces: `VoiceEventBus.publish(event_type, **payload)`, `create_control_server(bus, host, port)`, `GET /health`, `GET /history`, `GET /events`, `GET /devices`.
- Consumes: existing `log_event` calls and `audio_utils.list_input_devices/list_output_devices`.

- [ ] Add a bounded, thread-safe 200-event in-memory bus with subscriber queues and secret-field filtering.
- [ ] Add a loopback `ThreadingHTTPServer` exposing health, history, device summaries and SSE.
- [ ] Start the control server with the voice loop and mirror public `log_event` events into the bus.
- [ ] Document port 8767 and the no-persistence event contract.
- [ ] Perform static inspection only and commit `feat: expose local voice status events`.

### Task 2: Perception console gateway and React application

**Files:**
- Create: `apps/perception-console/pyproject.toml`
- Create: `apps/perception-console/src/main.py`
- Create: `apps/perception-console/src/proxy.py`
- Create: `apps/perception-console/web/package.json`
- Create: `apps/perception-console/web/tsconfig.json`
- Create: `apps/perception-console/web/vite.config.ts`
- Create: `apps/perception-console/web/index.html`
- Create: `apps/perception-console/web/src/main.tsx`
- Create: `apps/perception-console/web/src/api.ts`
- Create: `apps/perception-console/web/src/App.tsx`
- Create: `apps/perception-console/web/src/styles.css`
- Create: `apps/perception-console/README.md`

**Interfaces:**
- Consumes: home-service 8765, vision-service 8766 and voice-service 8767.
- Produces: console 8770, `/api/status`, allow-listed `/api/home/*`, `/api/vision/*`, `/api/voice/*`, and five React views.

- [ ] Implement the loopback console server, static-file delivery, unified status and allow-listed proxy.
- [ ] Scaffold the React/TypeScript/Vite application without installing or building dependencies.
- [ ] Implement navigation and overview, vision, voice, identity and service views.
- [ ] Implement bounded polling/SSE clients, honest stale states and binary preview refresh.
- [ ] Document source development and later production build commands without running them.
- [ ] Perform static inspection only and commit `feat: add local perception console`.

### Task 3: Windows lifecycle launcher

**Files:**
- Create: `tools/start-perception.ps1`
- Create: `tools/stop-perception.ps1`
- Create: `runtime/perception/.gitkeep`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: service health ports 8765–8770 and repository Python environments.
- Produces: PID ownership manifest under `runtime/perception/processes.json` and an Edge/Chrome app-mode window.

- [ ] Implement port checks and explicit service command definitions.
- [ ] Start only absent services hidden and record only newly created process IDs.
- [ ] Wait for the console health endpoint with a bounded timeout, then open app mode.
- [ ] Stop only manifest-owned PIDs after rechecking their command lines.
- [ ] Document one-command startup and shutdown.
- [ ] Perform static inspection only and commit `feat: add perception desktop launcher`.

### Task 4: Remove Unity visual-management UI

**Files:**
- Delete: `apps/unity-house-payload/Assets/Scripts/VisionServiceClient.cs`
- Delete: `apps/unity-house-payload/Assets/Scripts/VisionCameraPanel.cs`
- Delete: `apps/unity-house-payload/Assets/Scripts/VisionContracts.cs`
- Delete: `apps/unity-house-payload/Assets/Scripts/VisionOverlayGraphic.cs`
- Delete actual-project counterparts and `VisionCameraLauncher.cs` plus `.meta` files under `E:/unityroom/unityroom/Assets/SmartHome/Runtime/`
- Modify: `apps/unity-house-payload/README.md`
- Modify: `E:/unityroom/unityroom/Assets/SmartHome/Generated/SmartHomeHouse.unity` through Unity MCP

**Interfaces:**
- Preserves: `HomeServiceClient`, home SSE models, scene state application and person-room visualization.
- Removes: every Unity connection to ports 8766 and 8770 and every camera/registration control.

- [ ] Stop Unity Play Mode and remove Vision components from the generated scene through the third-party Unity MCP.
- [ ] Save the scene before deleting scripts.
- [ ] Delete old vision UI/client scripts and their metadata from both payload and actual project.
- [ ] Search both Unity trees for remaining `VisionServiceClient`, `VisionCameraPanel`, `VisionCameraLauncher`, `VisionOverlayGraphic` and `127.0.0.1:8766` references; remove only vision-management references.
- [ ] Do not compile Unity or run Unity tests; commit payload/doc changes as `refactor: move vision controls out of unity`.

### Task 5: Static integration review

**Files:**
- Modify only files needed to correct contradictions found by static review.

**Interfaces:**
- Confirms the preceding tasks use matching paths, ports and event names.

- [ ] Review the full diff against the spec without running code.
- [ ] Run `git diff --check` only.
- [ ] Confirm no generated build output, raw media or secret file is staged.
- [ ] Commit any static corrections as `chore: align perception desktop integration`.
