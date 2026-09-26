# Perception Console UX, Registration, and Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make registration visibly guided, make the console comfortable and responsive, and make voice device selection follow the system default with safe fallback and recovery.

**Architecture:** Keep React on port 8770 and proxy voice/vision through existing allow-lists. Move audio device policy into `audio_utils`, expose voice health and device selection before model startup, and keep failed initialization observable. Reuse the vision MJPEG endpoint inside a guided identity workflow.

**Tech Stack:** Python 3.11, sounddevice/PortAudio, React, TypeScript, Vite, CSS.

**Spec:** `docs/superpowers/specs/2026-09-24-perception-console-ux-registration-audio-design.md`

## Global Constraints

- Bind services to `127.0.0.1`; React requests only port 8770.
- Do not save raw camera frames, raw audio, transcripts, or replies.
- Do not overwrite a family member's face record during verification without that member's explicit selection.
- Continue directly on `main` under the user's previously approved workspace policy; preserve the existing `loop.py` working-tree change.
- The user now permits model inference and targeted tests for this work.

## Review Focus

- A missing or invalid system default must lead to a usable fallback or an honest degraded state, never a silent virtual-device guess.
- Changing audio devices while capture is live must not leave the old stream open or report a new device before it works.
- Registration must never show a stale JPEG as live after stream disconnect or camera change.
- The `no_face` message must be distinguishable from camera failure and from low-quality detected faces.
- A failed voice initialization must leave the local control API responsive without leaking secrets or model paths.

---

### Task 1: Audio device policy and observable voice startup

**Files:**
- Modify: `apps/voice-service/src/audio_utils.py`
- Modify: `apps/voice-service/src/loop.py`
- Modify: `apps/voice-service/src/control_server.py`
- Modify: `apps/perception-console/src/proxy.py`
- Test: `apps/voice-service/tests/test_audio_utils.py`
- Test: `apps/voice-service/tests/test_control_server.py`

**Interfaces:**
- Produces: `audio_utils.select_input_device()` and `select_playback_target()` with system-default-first automatic policy; `GET /health` with actual selected input/output and degraded error; `GET /devices` with default/selected/usable annotations; `POST /devices/select` with explicit automatic or stable device choice.
- Consumes: `sounddevice.default.device`, `sounddevice.query_devices`, existing `VoiceEventBus`, existing loopback gateway.

- [x] Add tests for valid system defaults, invalid-default fallback, a vanished manual choice, and degraded health on an ephemeral HTTP port.
- [ ] Record the expected-failure run for the focused tests.
- [x] Implement default-first selection, stable preference storage under ignored runtime, early API binding, and explicit retry/reconfigure lifecycle; allow-list the POST route on 8770.
- [x] Run the focused tests and voice-service suite; confirm pass and sanitized error responses.
- [ ] Commit the backend and test files with `feat: make voice audio selection recoverable`.

### Task 2: Guided visible registration

**Files:**
- Modify: `apps/perception-console/web/src/App.tsx`
- Create: `apps/perception-console/web/src/registration.ts`
- Test: `apps/perception-console/web/src/registration.test.ts`
- Modify: `apps/perception-console/web/package.json` only if a lightweight test runner is required.

**Interfaces:**
- Produces: a shared live-preview component and registration reason mapping; identity flow uses `/api/vision/cameras`, `/api/vision/camera/select`, `/api/vision/registration/start`, `/api/vision/registration`, `/api/vision/registration/cancel`, and `/api/vision/preview.mjpeg`.

- [x] Add focused tests for `no_face`, camera faults, multiple faces, quality guidance, and recovery when vision is degraded.
- [ ] Add interaction coverage for a stale preview and an absent camera; record the expected-failure run for focused tests.
- [x] Build the registration work area with camera selection, live MJPEG preview, progress, cancellation, and safe camera switch handling.
- [x] Correct pose ratios for frame aspect ratio and widen neutral-front tolerance; cover both with synthetic regression tests.
- [x] Make blur and front-pose guidance actionable and frame-specific.
- [x] Run focused tests and a frontend build; inspect the desktop UI and verify the live camera preview without enrolling a family member.
- [ ] Inspect the 375px layout and the registration workspace without persisting a family identity.
- [ ] Commit with `feat: guide face registration with live preview`.

### Task 3: Console visual system and voice device controls

**Files:**
- Modify: `apps/perception-console/web/src/App.tsx`
- Modify: `apps/perception-console/web/src/styles.css`
- Modify: `apps/perception-console/web/src/api.ts`
- Modify: `apps/perception-console/README.md`

**Interfaces:**
- Consumes: Task 1 `/devices` and `/devices/select`, Task 2 registration preview and reason labels.
- Produces: responsive warm-light UI, consistent semantic status treatment, selected/default device controls and apply feedback.

- [x] Add front-end checks for device choice serialization, pending/confirmed state, and status copy.
- [x] Implement warm-light design tokens, responsive navigation/layout, semantic service states, accessible focus and reduced-motion styles, and audio selection controls.
- [x] Run frontend checks, `npm run build`, and read-only proxy/service checks; verify current device selection and a short camera preview without media persistence.
- [x] Update usage notes.
- [ ] Commit with `feat: polish perception console and audio controls`.

## Final verification

- [x] Run relevant backend and frontend tests, React production build, and `git diff --check`.
- [x] Verify live camera preview and monitoring inference without writing camera frames or enrolling a family member.
- [x] Verify voice health, default-device readback, fallback reporting, and recovery behavior through tests.
- [x] Confirm the connected webcam can produce a clear face frame; corrected pose geometry and verified the new vision service starts idle with the camera reselected.
- [ ] Inspect the 375px layout and registration workspace; enrollment was not started because the current identity cards are persistent family profiles.
- [x] Apply automatic audio selection in the live UI and verify it reports success and retains the system-default devices.
- [ ] Commit the completed changes after the remaining UI acceptance checks.
