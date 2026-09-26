# unity-check — compile the Unity parser without the editor

`StateParser.cs` and `MiniJson.cs` are pure C# with no Unity API calls beyond
`[Serializable]` and `[Tooltip]`. That means they can be compiled and executed by
the plain .NET SDK, which catches parser and contract bugs **before** opening the
editor.

This is a **logic check, not a Unity compile**. It cannot catch Unity-API mistakes
in `HomeServiceClient.cs` or `SceneStateApplier.cs` — those are verified by
opening the project in the editor.

## Run

```powershell
dotnet run --project tools/unity-check/unity-check.csproj
```

It compiles `apps/unity-house-payload/Assets/Scripts/StateParser.cs` and checks
it against `apps/unity-house-payload/Assets/Tests/snapshot.json`, a payload captured from
the live home service.

## What it already caught

Two real contract bugs that the Python tests had missed:

1. `location_known` is a derived property, so `asdict()` dropped it from the
   person payload. Unity had no way to tell "position unknown" from "position
   present".
2. `room_name` was missing from the snapshot's broadcast tasks, so a highlight
   could not be tied to a room name.

Both were fixed in `apps/home-service/src/state.py` and are now guarded by
`apps/home-service/tests/test_json_contract.py`.

## Refreshing the fixture

The fixture must match the current service output. Regenerate it after any
payload change:

```powershell
.\.venv\Scripts\python.exe -c "import json,sys,pathlib; sys.path.insert(0,'apps/home-service/src'); from state import build_default_state; s=build_default_state(pathlib.Path('apps/home-service/config/rooms.json')); s.set_light('living_room_light',on=True,brightness=50); s.set_ac('bedroom_ac',on=True,mode='cool',target_temp=26); s.enqueue_broadcast('bedroom','吃饭啦',['dad']); pathlib.Path('apps/unity-house-payload/Assets/Tests/snapshot.json').write_text(json.dumps(s.snapshot(),ensure_ascii=False,indent=2),encoding='utf-8')"
```

Then re-run both checks:

```powershell
dotnet run --project tools/unity-check/unity-check.csproj
.\.venv\Scripts\python.exe -m pytest apps/home-service/tests -q
```

## Notes

- `UnityStubs.cs` provides only `UnityEngine.TooltipAttribute`.
  `[Serializable]` must **not** be stubbed: it resolves to
  `System.SerializableAttribute` in Unity too, and stubbing it causes an
  ambiguous-reference compile error.
- The harness does not modify the Unity project; it only reads the sources.
