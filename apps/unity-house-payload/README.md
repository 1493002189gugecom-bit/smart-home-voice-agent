# Unity House — Phase C payload

Three-room virtual house driven by the authoritative home service. The Unity
client is a **view**: it never decides or mutates state on its own.

## Why this is a payload and not a project

The `ProjectSettings/` and `Packages/` files were previously hand-written here.
That was a mistake: they are Unity-owned serialized files whose schema is tied to
the editor version, so a hand-written copy is incomplete and Unity silently
patches it. The project is now **created by Unity Hub**, and only the files we
actually authored live in this payload.

## Setup

**1. Create the project in Unity Hub**

- Editor: **2022.3.47f1c1** (installed at `D:\unity\edition`)
- Template: **3D (Built-in Render Pipeline)**
- Location/name: create it at `apps/unity-house` (or anywhere you prefer)

**2. Copy our files in**

```powershell
.\.venv\Scripts\python.exe tools/unity-check/install_into_unity.py --project apps/unity-house
```

Add `--dry-run` first to preview. The script only writes under `Assets/` and
never creates `.meta` files — Unity generates those on import, which is the
correct source for them.

**3. Start the home service** (needed for a live connection)

```powershell
.\.venv\Scripts\python.exe apps/home-service/src/server.py --port 8765
```

## Verify connectivity (Phase C step 1)

**a) Offline parser check (no editor needed)**

```powershell
dotnet run --project tools/unity-check/unity-check.csproj
```

Compiles the real `StateParser.cs` with the .NET SDK and validates it against a
captured service payload. See `tools/unity-check/README.md`.

**b) In-editor check**

Create an empty scene, add an empty GameObject, attach `ConnectivityCheck.cs`,
press Play. The on-screen report shows:

- Unity version and API compatibility level
- whether `/health` and `/snapshot` are reachable
- how many rooms/devices/persons were parsed
- whether a WebSocket type exists in this profile

A screenshot of that report is the evidence for step 1.

**c) Manifest and fixture checks (repo side)**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/unity-house/tests -q
```

## Scripts

| Script | Role |
| --- | --- |
| `StateParser.cs` | Dependency-free JSON reader for the service payload |
| `HomeServiceClient.cs` | Loopback HTTP sync (snapshot + increments) and receipts |
| `SceneStateApplier.cs` | Applies state to primitives; drives the broadcast highlight |
| `ConnectivityCheck.cs` | Step 1 verification and on-screen report |
| `VisionContracts.cs` | Vision JSON DTOs, strict readers and the JSON string escaper |
| `VisionServiceClient.cs` | Non-blocking loopback vision API client (8766) and preview |
| `VisionOverlayGraphic.cs` | uGUI graphic that draws body boxes and COCO skeletons |
| `VisionCameraPanel.cs` | Runtime-built camera, monitoring and registration panel |
| `Tests/StateParserTests.cs` | Unity Test Framework tests using a real payload |

## Vision camera panel (local face and pose vision)

The camera panel talks to a second loopback service. `home-service` stays on
`127.0.0.1:8765`; `vision-service` owns the camera on `127.0.0.1:8766`. Nothing
in this panel controls Home Assistant devices.

### Attaching it in the editor (no prefabs, no hand-written scenes)

The payload ships **no** `.prefab` or `.unity` file on purpose: those are
Unity-owned serialized assets, and a hand-written copy is incomplete. Build the
panel from the editor instead — it takes about a minute:

1. In the scene that already contains your `SceneStateApplier` GameObject, create
   **one** empty GameObject (for example `VisionCamera`). Put it at the scene
   root; the panel builds its own UI under it at runtime.
2. Select it and **Add Component → Vision Camera Panel**. `VisionServiceClient`
   is added automatically when the panel's `client` field is empty, so one
   component is enough.
3. If you prefer an explicit client, first **Add Component → Vision Service
   Client**, then drag that same GameObject onto the panel's `Client` field. The
   default `Base Url` is already `http://127.0.0.1:8766`; keep port `8766` and do
   not change it to the home-service port `8765`.
4. Leave `Overlay` empty. The panel creates its own Screen Space Overlay `Canvas`
   (`CanvasScaler` → Scale With Screen Size, 1920x1080, match 0.5) plus a
   `GraphicRaycaster`. To draw into a canvas you built yourself, set `Overlay` to
   that canvas's `VisionOverlayGraphic` instead.
5. Confirm the scene has **exactly one `EventSystem`**. The panel reuses an
   existing one and only creates one when the scene has none; it logs a warning
   when it finds duplicates, because two `EventSystem` objects silently break
   button input.
6. Press Play. The panel polls by itself; camera, room, monitoring and
   registration controls are all on the panel and need no extra wiring.

Optional: if you assign `Overlay` yourself, also assign its `Preview` field to
your preview `RawImage` so the overlay maps camera coordinates through the same
aspect-fit rectangle the image uses.

### Order of operations

Start `home-service` first (it creates the local vision token), then
`vision-service`, then Unity. If only Unity runs, the panel reports 未连接 and the
house shows everyone as 位置未知 — it never invents a room.

## State semantics the view must respect

- **Unknown location looks unknown.** A person whose room is not localized is
  drawn distinctly **and moved to an explicit unknown staging area** above the
  house. The marker is never left at a previous position, because one that stays
  behind reads as a live observation.
- **Camera observations drive positions.** Person `room_id` is published from
  confirmed camera identity, so the house follows the snapshot. `x`/`y` are
  normalized camera-image coordinates in `[0,1]` (`x` = body-box center,
  `y` = body-box bottom), mapped onto the room footprint by the single
  reviewable helper `CameraToRoomLocal`; they are not room-local offsets.
- **Pose is part of the label.** A localized person is labelled `姓名 · 姿态`
  (for example `爸爸 · 站立`). Missing or unknown pose degrades to `未知姿态`.
- **Offline devices look offline.** A device with `online == false` is greyed and
  its state is not presented as live.
- **Only one room broadcasts at a time.** The highlight follows the queue head;
  `played`, `failed` and `cancelled` tasks are skipped.
- **Room name travels with the task.** Highlighting uses `room_name` from the
  payload, so a label can never point at the wrong room.

已实现，尚未验证：以上代码未编译、未运行，也未做任何截图、PlayMode 或视觉验收。
