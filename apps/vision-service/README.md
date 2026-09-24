# Vision Service — local face, pose, and room observation

Loopback-only service that owns the single active camera and all visual models for
the smart-home project. It publishes person location, identity, and pose to
`home-service`, which is the only component allowed to serve those facts to the
voice agent and to Unity.

> **Status: 已实现，尚未验证 (implemented, not verified).** The author of this
> service did not run any test, compilation, model inference, camera capture, or
> screenshot check. Every functional check in this document is the user's to run.

## Boundary

| Rule | Value |
| --- | --- |
| Bind address | `127.0.0.1:8766` only — never the LAN |
| Device control | none; no Home Assistant tool, alarm, or speech output |
| Cameras | exactly one physical camera active at a time |
| Raw frames | memory and the local perception console preview only; never written to disk |
| Locations | only `living_room`, `bedroom`, `kitchen`, and only as observations |

## Offline model setup (required, and never automatic)

Startup must **never** download a model. If a file is absent, the API reports
`model_missing` and continues running so the perception console can explain why.

| Model | Expected path | Notes |
| --- | --- | --- |
| YOLO26n-pose | `D:/smart-home-models/yolo26n-pose.pt` | pose boxes + 17 COCO keypoints |
| InsightFace pack | `D:/smart-home-models/insightface` (`buffalo_l`) | detection, alignment, embeddings |

Both paths come from `config/vision.json` and must be absolute; the loader rejects
relative paths instead of guessing.

Tracking explicitly uses `bytetrack.yaml` with `persist=True`, so a track id stays
stable inside one capture session and is discarded when the session changes.

### Licensing you must accept before placing models or distributing

- **Ultralytics** ships under AGPL-3.0; commercial use requires their Enterprise
  licence. Review it before shipping anything built on YOLO26.
- **InsightFace pretrained packs** are published for non-commercial research use.
  Verify the terms for your own situation before redistribution.

This document does not grant any licence on your behalf.

## Dependencies

Declared in `pyproject.toml`; use the dedicated `.venv-vision` environment so its
GPU packages do not disturb the voice/home environments: `insightface`, `numpy`,
`onnxruntime-gpu`, `opencv-python`, `ultralytics`.

`onnxruntime-gpu` is preferred so the RTX 4060 can be used; the provider list in
`config/vision.json` falls back to `CPUExecutionProvider`. Face review runs every
`face_review_interval_frames` frames so face inference stays off the critical path.

For a fresh environment, install the CUDA 12.8 PyTorch wheels first, then the
service dependencies (the model files themselves remain outside the repository):

```powershell
.\.venv-vision\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.\.venv-vision\Scripts\python.exe -m pip install -e apps\vision-service
```

## Launch order

Order matters, because home-service owns the shared token this service reads.

```powershell
# 1) home-service creates runtime/vision/home-service.token when it starts
cd E:\smart-home
$env:HOME_SERVICE_BACKEND = 'ha'
.\.venv\Scripts\python.exe apps\home-service\src\server.py --port 8765

# 2) vision-service reads that token and the local models
.\.venv-vision\Scripts\python.exe apps\vision-service\src\main.py

# 3) Start the perception console; Unity may independently read home-service SSE
```

Check the configuration without opening a camera or a model:

```powershell
.\.venv-vision\Scripts\python.exe apps\vision-service\src\main.py --print-config
```

Starting vision-service before home-service is not fatal: it reports
`sync_state: disconnected` and the perception console says the location is not synced,
rather than pretending a person was placed.

## Local API

JSON bodies must be objects with exactly the documented keys; a typo returns
`422`. Errors always use `{"ok": false, "error_code": "...", "message": "..."}`.

| Method | Route | Body |
| --- | --- | --- |
| GET | `/health` | — |
| GET | `/cameras` | — (probes indices 0–7 on demand) |
| GET | `/config` | — |
| POST | `/camera/select` | `{"kind":"device","device_id":"0"}` or `{"kind":"url","url":"..."}` |
| POST | `/room/select` | `{"room_id":"living_room"}` |
| POST | `/monitor/start` | `{}` |
| POST | `/monitor/pause` | `{}` |
| GET | `/results` | — |
| GET | `/preview.jpg` | — (JPEG, `Cache-Control: no-store`) |
| GET | `/preview.mjpeg` | — (continuous multipart JPEG stream, no-store) |
| POST | `/registration/start` | `{"person_id":"dad"}` |
| GET | `/registration` | — |
| POST | `/registration/cancel` | `{}` |
| DELETE | `/registration/{person_id}` | — |

Status codes: `409` for an invalid mode transition, `422` for an invalid source,
room, or person, `503` when models or the camera are unavailable, `404` for an
unknown route.

## What home-service receives

`vision-service` is the only writer of `/vision/observations`,
`/vision/withdraw`, and `/vision/offline`, authenticated with the `X-Vision-Token`
header read from `runtime/vision/home-service.token`. It never creates that file.

Only a track with `identity_state: "confirmed"` and a unique known `person_id`
can set a person's room. Coordinates are normalized `[0,1]` image space; `x` is
the body-box horizontal centre and `y` is the box bottom.

## Registration

Six guided steps: `front`, `turn_left`, `turn_right`, `look_up`, `look_down`,
`blink`. Each step needs two quality-approved samples and contributes one
prototype centroid; `blink` proves liveness without adding a duplicate prototype.
Afterwards the record replaces the old one atomically and a five-second
confirmation window tries to recognise the person three times.

Registration never assigns a room, and monitoring publication stops first so the
person enrolling is not also reported as a located observation.

Rejection reasons are specific, not generic: multiple faces, no face, face too
small, blurred, too dark or too bright, incomplete landmarks, wrong action, or an
interrupted camera.

Liveness here is a basic hint only. It does not resist a photo, a video, or a
deepfake, and this service must never be described as identity authentication.

## Privacy

- Frames are never persisted; only the newest frame is processed and older frames
  are dropped instead of queued.
- Face embeddings are stored under the Git-ignored `runtime/vision` directory and
  encrypted for the current Windows user with DPAPI.
- Deleting a person removes every stored prototype for that person.
- Logs and API errors never contain raw images, embeddings, or a full video URL —
  a camera URL is reported as scheme, host, and a short fingerprint.
- The service binds to loopback only.

## User-owned acceptance checklist

Every item below is **未执行，由用户手动验收**. The implementer claims no measured
FPS, recognition rate, or visual correctness.

1. Register all three family members end to end.
2. A registered person is recognised automatically; an unregistered person shows
   as unknown.
3. Multiple people, head turns, and brief occlusion do not cause frequent name
   swapping.
4. Standing, sitting, lying, suspected fall, and hand raised are observable.
5. After switching among living room, bedroom, and kitchen, person location
   provenance is correct.
6. After camera switching, disconnect, or a person leaving, no stale position
   remains.
7. Unity, perception-console, vision-service, home-service, and voice-service can run at
   the same time.
