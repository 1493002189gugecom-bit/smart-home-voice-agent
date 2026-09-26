# Speaker-Bound Operation Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the voice assistant resolve "把空调调低 2 度" against the *speaker's* current camera-confirmed room, by enrolling voiceprints in the same flow as faces under one identity id, injecting the speaker verdict into the prompt (Phase A), and enabling a locally resolved `target: "self"` write path guarded by static consequence tiers (Phase B).

> **Status 2026-09-25 — Phase A implemented, not yet functionally verified.**
> Every Phase A task below is written and unit-tested; no model inference, camera,
> microphone or service start was used to build it. Unit suites: voice-service 270,
> vision-service 56 (+14 subtests), home-service + perception-console +
> tools/voice-check 205, console web build green.
>
> The `- [ ]` boxes are deliberately **left unticked**: per this project's delivery
> rule they may only be ticked once a person has performed the Acceptance steps at
> the end of this file. Delivery wording is exactly "已实现，尚未验证".
>
> **Deviation from the goal statement:** no `SpeakerStateStore` and no `/speaker/*`
> routes were built in home-service. The verdict belongs to one utterance and is
> consumed inside voice-service, so a shared store would be a second source of truth
> for a value nothing else reads. Gallery storage and matching live in vision-service
> (the only DPAPI owner) behind `/identity/speaker/*`; see the Architecture note below.

**Architecture:** Identity templates stay where DPAPI already lives. `vision-service` owns the face registry **and** now the voiceprint gallery (`speaker_registry.py`, same envelope and atomic-write discipline, own entropy label), which keeps exactly one crypto implementation instead of two. `voice-service` owns the microphone, so it computes speaker embeddings locally, asks vision-service for a match, and keeps the verdict for that one utterance — **there is no shared speaker store**, because nothing outside `voice-service` reads it. `home-service` keeps owning the person directory (`runtime/vision/persons.json`) that both biometrics bind to, and remains the only authority for *where* a person is. The consequence tier of every write tool is **declared in code**, never judged by a model. `perception-console` registers face and voice in one person-scoped flow.

**Tech Stack:** Python 3.11; standard-library HTTP servers; ONNX Runtime for the speaker-embedding model; Windows DPAPI via `ctypes` (same mechanism as `apps/home-service/src/registry.py`); React + TypeScript for the console.

**Specs:**
- `docs/superpowers/specs/2026-09-25-speaker-identity-voiceprint-evaluation.md`
- `docs/superpowers/specs/2026-09-25-speaker-bound-operation-target-design.md`

## Global Constraints

- **The authority decision is never made by a model.** "May this action run?" is answered by the static tier table below. Models may only classify *intent* and report *confidence*.
- **One identity id space.** Voiceprint enrolment binds an existing `person_id` from `runtime/vision/persons.json`. No second id space, no voice-only people.
- **Location authority stays with vision.** Voiceprint yields a person, never a room. A room only ever comes from a camera observation with `location_known = true`.
- **Never guess a room.** Unknown or expired location ⇒ ask the user. Never reuse the previous room.
- **Voiceprint never authorizes itself.** A second identity check must use a different evidence type (camera face in the same time window, PIN, or a second device).
- Loopback only. Voice embeddings are DPAPI-protected under Git-ignored `runtime/`, exactly like `face_registry.bin`. No embedding, audio, or frame leaves the machine.
- Phase A must be independently shippable and reversible; Phase B is gated on measured FAR.
- Delivery wording is exactly "已实现，尚未验证" for anything this plan produces until the user performs functional acceptance.

## Consequence Tiers (declared once, in code)

Static data next to the tool surface. This replaces the earlier "称呼 vs 控制" split, which was too coarse.

| Tier | Meaning | Examples | Voiceprint + `self` allowed? |
| --- | --- | --- | --- |
| `reversible_self` | Reversible, local, affects only the speaker | 我房间的灯/空调/插座，省略房间时的默认目标 | ✅ allowed, **播报必须含房间名** |
| `explicit_only` | Affects a named third party | 给爸爸房间关空调、给妈妈发消息 | ❌ must be stated explicitly |
| `forbidden` | Irreversible, safety, or house-wide | 门锁、报警推送对象、全屋场景、批量操作 | ❌ never |

Rules that fall out of the table: voiceprint may only supply the *default target when the user omitted one*; it must never override a target the user actually said.

## File Structure

```text
apps/vision-service/
  src/registry.py                 (+ optional entropy arg on dpapi_protect/unprotect; face behaviour unchanged)
  src/speaker_registry.py         (new) SpeakerRegistry: same envelope + atomic write, own entropy, own file
  src/server.py                   (+ /identity/speaker/{enroll,match,status}, DELETE /identity/speaker/{person_id})
apps/voice-service/
  src/speaker.py                  (new) ONNX embedding + quality gate; stdlib-only top-level imports
  src/speaker_identity.py         (new) vision-service client + threshold gate -> confirmed/uncertain/unknown
  src/identity_flow.py            (new) voice enrolment step machine (>5 samples, >2.0 s each)
  src/agent_session.py            (+ inject the speaker line into the system prompt)
  src/agent_tools.py              (+ target: "self" in _ALLOWED_ARGUMENTS; tiers declared, not judged)
  src/loop.py                     (+ embed per utterance, gate, inject, publish speaker fields)
  config/identity.json            (new) thresholds: min_speech_seconds, match threshold, sample count
apps/perception-console/
  web/src/App.tsx                 (+ one person-scoped enrolment flow: face steps, then voice steps)
  web/src/identity-flow.ts        (new) step machine shared by both biometrics
docs/superpowers/plans/2026-09-25-speaker-bound-operation-target.md  (this file)
```

---

## Phase A — Enrolment + read-only speaker awareness (risk ≈ 0)

No `self` resolution, no behaviour change to any write. Ends with the FAR measurement needed to decide Phase B.

### A1. Speaker embedding adapter
- [ ] Create `apps/voice-service/src/speaker.py`. Top-level imports must be stdlib-only; import `onnxruntime`/`numpy` inside functions (the vision-service convention).
- [ ] API: `embed(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, VoiceQuality]` and `describe_quality(...)`.
- [ ] Quality gate returns reasons (`too_short`, `low_snr`, `clipping`) rather than a bool, so the console can tell the user what to fix.
- [ ] Refuse to embed speech shorter than `min_speech_seconds` (default **1.5**). This is the single most important gate.
- [ ] Fail with a closed error code (`speaker_model_missing`) when the ONNX file is absent; never download at runtime.

### A2. Speaker gallery storage (vision-service, one DPAPI implementation)
- [ ] Add `apps/vision-service/src/speaker_registry.py` beside the existing face registry. **Reuse `registry.dpapi_protect`/`dpapi_unprotect`; do not write a second crypto path.** Add an optional `entropy` argument to those two functions, defaulting to today's face label so `face_registry.bin` keeps decrypting byte-for-byte, and give voice its own label.
- [ ] Same envelope discipline as faces: magic `DSHVOICE1`, schema version, own filename `runtime/vision/speaker_registry.bin`, the 4 MB cap, `_MAX_PROTOTYPES_PER_PERSON`, atomic write + directory fsync, person-id validation through the existing pattern.
- [ ] API mirrors `FaceRegistry`: `replace`, `delete`, `rank`, `prototypes`, `persons`, `counts`, `forget_all`, `persistent`, `embedding_dim`.
- [ ] Reuse `registry.similarity` and its normalisation helper rather than re-deriving cosine maths in a second place.
- [ ] Tests (no model inference): DPAPI round-trip, corrupt file rejected, unknown person rejected, replace leaves no temp file, rank ordering, empty gallery ranks nothing, dimension mismatch rejected.

### A3. Voice-service: per-utterance speaker verdict (no shared store)
- [ ] Create `apps/voice-service/src/speaker_identity.py`: a loopback client for vision-service's speaker endpoints plus the threshold gate.
- [ ] **A `SpeakerStateStore` in home-service is deliberately not built.** The verdict belongs to one utterance and is consumed inside `voice-service` (prompt injection, the transcript event, and Phase B's resolver all live there). A shared store would be a second source of truth for a value nothing else reads; revisit only when a second consumer actually exists.
- [ ] Gate: `verdict(embedding) -> {person_id, confidence, state}`, `state` being `confirmed` / `uncertain` / `unknown` from `config/identity.json`. Below threshold the name is **stripped**, so no caller can accidentally speak it.
- [ ] Expiry is per-utterance by construction: never cache a verdict across turns.
- [ ] Graceful degradation: vision-service down, empty gallery, or missing model ⇒ `unknown`, never an exception in the voice loop.
- [ ] Tests with a fake opener: above/below threshold, timeout, 4xx/5xx, malformed body, empty gallery.

### A4. One enrolment flow for face + voice
- [ ] Voice enrolment is driven by `voice-service` (it owns the microphone) and stored by `vision-service` (it owns DPAPI): `POST /identity/enroll/voice/start|sample|cancel` and `GET /identity/enroll/voice/status` on voice-service, forwarding accepted embeddings to vision-service's `POST /identity/speaker/enroll`.
- [ ] Both biometrics bind the **same `person_id`** read from `runtime/vision/persons.json`; refuse enrolment for an id that is not in the directory.
- [ ] Voice sample acceptance: ≥ 5 accepted samples, each ≥ 2.0 s, with a quality reason reported on every rejection so the UI can say what to fix.
- [ ] Mutual exclusion: voice enrolment must not require the camera; face enrolment keeps reusing `RegistrationSession`'s camera lock so monitoring and enrolment cannot both hold the device. Starting one modality must not discard the other's completed half.
- [ ] Tests: unknown person id refused, cancel mid-flow writes nothing, a rejected sample never advances the step, five accepted samples produce exactly one gallery entry.

### A5. Console: unified enrolment UI
- [ ] Extend the 身份 tab (`App.tsx`) so a person shows two rows — 人脸 3 步、声纹 ≥5 句 — in one flow, with the same success banner pattern as today's `✓ {personName}的人脸已录入成功`.
- [ ] Extract the step machine into `web/src/identity-flow.ts` so both biometrics share it and it can be reviewed without JSX.
- [ ] Show the microphone-in-use device name next to the voice steps (the project has already been bitten by a virtual audio device silently swallowing input).

### A6. Inject the speaker verdict into the prompt (read-only)
- [ ] `apps/voice-service/src/loop.py`: per utterance, embed the audio, gate the verdict through A3, and hand it to the session **for that turn only** — no store, no read-back, no reuse on the next turn.
- [ ] `apps/voice-service/src/agent_session.py`: append a line beside the existing `scene_context`, e.g. `声纹判定：可能是爸爸（0.72）。` Below threshold or unknown ⇒ **inject nothing** (falls back to today's honest behaviour).
- [ ] System prompt addition: the speaker verdict is evidence about *who is talking*, not about *where they are*; never derive a room from it; when the verdict is absent or hedged, say so instead of pretending.
- [ ] Tests: threshold gates injection; unknown speaker injects nothing; existing prompts unchanged when no speaker data exists.

### A7. Tier table (declaration only)
- [ ] Add the tier table as data in `apps/voice-service/src/agent_tools.py` beside `WRITE_TOOLS`/`_ALLOWED_ARGUMENTS`, one entry per write tool. **No enforcement yet** in Phase A; this just makes the authority static and reviewable.
- [ ] Test: every write tool has a tier; every `forbidden` tool has a comment naming its reason.

### A8. Console shows the speaker and the confidence (depends on A5, A6)
- [ ] `apps/voice-service/src/loop.py`: the `transcript` event carries `speaker_id`, `speaker_name`, `speaker_confidence`, `speaker_state` (`confirmed` / `uncertain` / `unknown`).
- [ ] **Check `public_fields` before trusting the payload.** The bus publishes through a per-type field whitelist; a new key that is not added there is dropped silently and the UI would show "未知" forever with no error anywhere.
- [ ] `apps/perception-console/web/src/api.ts`: extend the `VoiceEvent` payload contract, then render a speaker chip on each 你-bubble: name + confidence, e.g. `爸爸 · 0.72`.
- [ ] Honest states, not decoration: below threshold shows **未识别**; an expired verdict shows **身份已过期**; never show a name the system is not willing to act on. A name and a muted "未识别" must be visually distinguishable at a glance.
- [ ] The agent's own bubble stays unlabelled — the speaker is a property of the utterance, not of the reply.
- [ ] Show the verdict on the transcript that produced it and leave it there; do not retro-relabel older turns when a new verdict arrives.
- [ ] Tests: payload survives the whitelist for all three states; a sub-threshold verdict renders `未识别` and never a name.

### A9. FAR / FRR measurement
- [ ] Record ≥ 30 utterances per enrolled member plus ≥ 2 unenrolled speakers; report EER/FAR/FRR **bucketed by utterance length** (<1 s, 1–2 s, >2 s).
- [ ] Set the operating threshold from this data as a `config/identity.json` value. Do not hardcode it in code.
- [ ] Output: a short report in `docs/superpowers/notes/` that Phase B is gated on.

---

## Phase B — `target: "self"` for reversible actions (gated on A9)

### B1. Local resolver
- [ ] Add `resolve_self_target(kind, speaker_id, now_ms)` to `apps/home-service/src/tools.py` as a **pure function**: `(kind, speaker_id, visual snapshot, house state) -> device_id | {reason}`.
- [ ] Branch table: no speaker ⇒ `ask_room`; speaker but unknown location ⇒ `ask_room_unknown_location`; located ⇒ the single device of `kind` in that room; zero or several candidates ⇒ ask, never pick.
- [ ] Table-driven tests for all five branches, including "two ACs in one room" and "room exists but has no AC".

### B2. Tool surface
- [ ] Extend `_ALLOWED_ARGUMENTS` so `target: "self"` is accepted for `reversible_self` tools, mutually exclusive with `device_id`.
- [ ] The model never resolves `self`: `normalize_arguments` passes the symbol through, and the executor calls `resolve_self_target` before any request leaves for home-service.
- [ ] Test: a `forbidden`/`explicit_only` tool rejects `target: "self"` outright.

### B3. Enforcement + spoken guard
- [ ] Enforce the tier table at the single point where a write leaves `agent_tools.execute`.
- [ ] Any write resolved from `self` must produce a spoken sentence containing the resolved room name. Reject-the-reply test: a `self`-resolved success whose wording lacks the room name fails a unit test, because that wording is what downgrades a silent wrong-room write into a correctable one.
- [ ] `ask_room` outcomes become a tool result the model must turn into a question, in the existing error/`ToolResult` shape.

### B4. Optional second factor for higher tiers
- [ ] For any tier above `reversible_self`, require a *different* evidence type. The realistic local option is a camera face confirmation inside the same time window as the utterance (vision already produces this).
- [ ] Implement as a gate stub that returns "需要再次确认" rather than any partial trust. Do not implement PIN or phone confirmation in this plan.

### B5. Config + rollback
- [ ] `config/identity.json` gains `self_resolution: off | reversible_self`. Default `off` = Phase A behaviour.
- [ ] Test: with `off`, a `target: "self"` request asks for a room and never reaches home-service.

### B6. Acceptance wording
- [ ] Update `apps/voice-service/README.md` and the console's 身份 tab copy to state plainly: 声纹只用于称呼与可逆的"我自己"目标；不可逆动作不授权给语音通道。

---

## Tests

- Unit only. No model inference, no microphone capture, no camera, no Unity, no service startup — the user performs functional acceptance.
- Every new module gets table-driven tests, including the negative branches (expired location, unknown speaker, ambiguous room, forbidden tier).
- Regression: the existing suites must stay green (`home-service`, `voice-service`, `vision-service`, `perception-console`).

## Acceptance (performed by the user, not by an agent)

1. Enrol 爸爸 with face **and** voice in one console flow; confirm both land on the same `person_id`.
2. Standing in the camera's room, ask 「我在哪」 ⇒ answers with the room, or honestly says it cannot see the speaker.
3. Unenrolled speaker says 「把空调调低 2 度」 ⇒ asks which room; never writes.
4. Enrolled speaker with **no** fresh camera location says the same ⇒ asks which room; never reuses the previous room.
5. Enrolled speaker with a fresh location ⇒ writes the intended room's AC and **the reply names the room**.
6. 「给爸爸房间关空调」 said by 妈妈 ⇒ not silently redirected by `self`.
7. Turn `self_resolution` off ⇒ behaviour returns to Phase A with no restart-driven surprises.

## Open Decisions (need the user)

- **FAR threshold**: proposed by the implementer as FAR ≤ 1% at ≥1.5 s speech, calibrated in A9 and stored in config. The user explicitly delegated this number.
- Whether Phase B ships at all, or the project stops at Phase A plus the camera-face second factor.

## Explicitly out of scope

- Any model (including Jev/TypeSafe) making the allow/deny decision. Models may classify intent and report calibrated confidence; authority is static code. Jev remains a candidate for intent classification and injection detection only.
- Streaming TTS (streaming text is implemented; the spoken sentence still starts after the full completion).
- Voiceprint-driven room assignment, emotion/age/gender inference, or any second id space.
