# Unity Real-Time Device Effects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive subtle, smooth Unity lighting, air-conditioning airflow, plug LED, and temperature feedback from the eight-device HA SSE snapshot.

**Architecture:** Add a separate `SmartHomeEffectsController` beside the existing scene controller so labels and status colors keep their current behavior. Per-device effect components receive target values from snapshots and animate toward them on the Unity main thread; the editor generator creates every required light, particle system, mesh, and binding deterministically.

**Tech Stack:** Unity 2022.3.47f1c1, C# 9-compatible Unity runtime, Built-in Render Pipeline, Unity Test Framework, uGUI, ParticleSystem, Standard shader.

**Spec:** `E:/smart-home/docs/superpowers/specs/2026-09-17-ha-unity-realtime-effects-design.md`

## Global Constraints

- Keep `SmartHomeSceneController` and its current color/label semantics intact.
- Unity remains read-only and only consumes `GET /events` or legacy read polling.
- Stable IDs are exact; no fuzzy name matching.
- Airflow remains subtle: at most 30 particles per AC and no collision, volume fog, or post-processing.
- Lights affect their local rooms while the all-lights-off scene remains readable.
- Do not change the rendering pipeline or add third-party packages.
- Preserve existing HUD, camera controls, and distance-aware labels.
- The Unity project is not a Git checkout; do not create commits or modify Plastic metadata.

---

### Task 1: Implement and test deterministic effect mappings

**Files:**
- Create: `Assets/SmartHome/Runtime/SmartHomeEffectMath.cs`
- Create: `Assets/SmartHome/Tests/EditMode/SmartHomeEffectMathTests.cs`

**Interfaces:**
- Produces: `SmartHomeEffectMath.LightIntensity(int brightness)`, `CoolingStrength(float targetTemperature)`, `TemperatureColor(float temperature)`, and `Move(float current, float target, float speed, float deltaTime)`.
- Consumes: normalized brightness `0–100` and temperatures in Celsius from `DeviceStateDto`.

- [ ] **Step 1: Write failing mapping tests**

```csharp
[TestCase(0, 0.35f)]
[TestCase(100, 2.6f)]
public void LightIntensityUsesVisibleClampedRange(int brightness, float expected)
{
    Assert.That(SmartHomeEffectMath.LightIntensity(brightness), Is.EqualTo(expected).Within(0.001f));
}

[TestCase(30f, 0f)]
[TestCase(23f, 0.5f)]
[TestCase(16f, 1f)]
public void CoolingStrengthIncreasesAsTargetFalls(float temperature, float expected)
{
    Assert.That(SmartHomeEffectMath.CoolingStrength(temperature), Is.EqualTo(expected).Within(0.001f));
}

[Test]
public void TemperatureColorMovesFromBlueThroughTealToOrange()
{
    Color cold = SmartHomeEffectMath.TemperatureColor(18f);
    Color comfort = SmartHomeEffectMath.TemperatureColor(25f);
    Color hot = SmartHomeEffectMath.TemperatureColor(32f);
    Assert.That(cold.b, Is.GreaterThan(cold.r));
    Assert.That(comfort.g, Is.GreaterThan(comfort.r));
    Assert.That(hot.r, Is.GreaterThan(hot.b));
}
```

- [ ] **Step 2: Run the focused EditMode test and confirm red state**

Run:

```powershell
& 'D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe' -batchmode -projectPath 'E:\unityroom\unityroom' -runTests -testPlatform EditMode -testFilter 'SmartHome.Tests.SmartHomeEffectMathTests' -testResults 'E:\unityroom\unityroom\Logs\EffectMathEditMode.xml' -logFile 'E:\unityroom\unityroom\Logs\EffectMathEditMode.log' -quit
```

Expected: compilation failure because `SmartHomeEffectMath` does not exist.

- [ ] **Step 3: Implement the pure mapping class**

Use clamped deterministic mappings:

```csharp
public static float LightIntensity(int brightness)
{
    float t = Mathf.Clamp01(brightness / 100f);
    return Mathf.Lerp(0.35f, 2.6f, t * t);
}

public static float CoolingStrength(float targetTemperature)
{
    return Mathf.InverseLerp(30f, 16f, targetTemperature);
}

public static float Move(float current, float target, float speed, float deltaTime)
{
    return Mathf.MoveTowards(current, target, Mathf.Max(0f, speed) * Mathf.Max(0f, deltaTime));
}
```

Implement `TemperatureColor` with a blue-to-teal interpolation for `16–25°C` and teal-to-orange for `25–35°C`, clamped outside the range.

- [ ] **Step 4: Run the focused test**

Expected: all `SmartHomeEffectMathTests` pass.

- [ ] **Step 5: Review checkpoint**

Confirm the class is pure, allocation-free, and has no dependency on scene objects.

---

### Task 2: Implement the effect component contract and light behavior

**Files:**
- Create: `Assets/SmartHome/Runtime/SmartHomeDeviceEffect.cs`
- Create: `Assets/SmartHome/Runtime/SmartHomeLightEffect.cs`
- Create: `Assets/SmartHome/Tests/EditMode/SmartHomeLightEffectTests.cs`

**Interfaces:**
- Produces: abstract `SmartHomeDeviceEffect.StableId`, `Apply(DeviceDto device)`, and `MarkUnavailable()`.
- Produces: `SmartHomeLightEffect.TargetIntensity` and `Tick(float deltaTime)` for deterministic tests.
- Consumes: `SmartHomeView`, a serialized `Light`, and a serialized emissive `Renderer`.

- [ ] **Step 1: Write failing light behavior tests**

Create a GameObject with `SmartHomeView`, `Light`, renderer, and `SmartHomeLightEffect`. Verify:

```csharp
effect.Apply(new DeviceDto {
    id = "living_room_light", online = true, type = "light",
    state = new DeviceStateDto { on = true, brightness = 80 }
});
Assert.That(effect.TargetIntensity, Is.EqualTo(SmartHomeEffectMath.LightIntensity(80)));

effect.MarkUnavailable();
Assert.That(effect.TargetIntensity, Is.Zero);
```

Call `Tick(1f)` and assert the linked light approaches the target without overshoot. Assert an offline or off device targets zero.

- [ ] **Step 2: Run focused EditMode tests and confirm red state**

Expected: compilation failure because the effect types do not exist.

- [ ] **Step 3: Implement the base contract and light effect**

The base component resolves its stable ID from the sibling view:

```csharp
public abstract class SmartHomeDeviceEffect : MonoBehaviour
{
    [SerializeField] private SmartHomeView view;
    public string StableId => view == null ? string.Empty : view.stableId;
    public abstract void Apply(DeviceDto device);
    public abstract void MarkUnavailable();
}
```

`SmartHomeLightEffect.Apply` sets a target of zero unless the device is online and on. `Update` calls public `Tick(Time.deltaTime)`. Cache a runtime material once in `Awake`, enable `_EMISSION`, and update emission only when the value changes; destroy only the owned runtime material in `OnDestroy`.

- [ ] **Step 4: Run light tests and full EditMode suite**

Expected: new light tests and all existing label/SSE tests pass.

- [ ] **Step 5: Review checkpoint**

Confirm the component sends no network requests and performs no per-frame material instantiation.

---

### Task 3: Implement AC, plug, and temperature effects

**Files:**
- Create: `Assets/SmartHome/Runtime/SmartHomeAirConditionerEffect.cs`
- Create: `Assets/SmartHome/Runtime/SmartHomePlugEffect.cs`
- Create: `Assets/SmartHome/Runtime/SmartHomeTemperatureEffect.cs`
- Create: `Assets/SmartHome/Tests/EditMode/SmartHomeDeviceEffectsTests.cs`

**Interfaces:**
- Produces: AC `TargetEmissionRate`, `TargetSpeed`, `TargetAlpha`, and `Tick(float deltaTime)`.
- Produces: plug `TargetColor` and temperature `TargetColor`.
- Consumes: normalized `DeviceDto` objects and serialized effect renderers/ParticleSystem.

- [ ] **Step 1: Write failing AC state tests**

```csharp
[TestCase(30f, 2f)]
[TestCase(23f, 7f)]
[TestCase(16f, 12f)]
public void CoolModeMapsLowerTemperatureToMoreAirflow(float target, float emission)
{
    effect.Apply(Ac("cool", target, true, true));
    Assert.That(effect.TargetEmissionRate, Is.EqualTo(emission).Within(0.001f));
}

[Test]
public void OffAndUnavailableAirConditionersStopEmission()
{
    effect.Apply(Ac("off", 18f, false, true));
    Assert.That(effect.TargetEmissionRate, Is.Zero);
    effect.MarkUnavailable();
    Assert.That(effect.TargetEmissionRate, Is.Zero);
}
```

Assert `fan_only` uses a low neutral rate independent of target temperature, maximum particles remains 30, and shutdown stops emission without clearing living particles.

- [ ] **Step 2: Write failing plug and temperature tests**

Assert plug colors for on/off/offline and temperature color equality with `SmartHomeEffectMath.TemperatureColor`. Assert `MarkUnavailable()` targets gray for the sensor and amber for the plug.

- [ ] **Step 3: Run focused tests and confirm red state**

Expected: compilation failure for the three missing component classes.

- [ ] **Step 4: Implement the three components**

For AC cool mode calculate:

```csharp
float strength = SmartHomeEffectMath.CoolingStrength(state.target_temp);
TargetEmissionRate = Mathf.Lerp(2f, 12f, strength);
TargetSpeed = Mathf.Lerp(0.25f, 0.65f, strength);
TargetAlpha = Mathf.Lerp(0.12f, 0.32f, strength);
```

For `fan_only`, target `3` particles/second, speed `0.35`, and neutral alpha `0.10`. Smooth ParticleSystem emission, start speed, and start color in `Tick`; call `Play()` only when target emission is positive and `Stop(true, StopEmitting)` when it reaches zero.

Plug and temperature components cache one owned runtime material, enable Standard shader emission, and smooth their colors without flashing.

- [ ] **Step 5: Run focused and complete EditMode suites**

Expected: all effect tests and existing EditMode tests pass with no logged exceptions.

- [ ] **Step 6: Review checkpoint**

Confirm each AC caps `main.maxParticles` at 30 and none uses collision, trails, lights, or sub-emitters.

---

### Task 4: Route snapshots to effects without changing the existing scene controller

**Files:**
- Create: `Assets/SmartHome/Runtime/SmartHomeEffectsController.cs`
- Create: `Assets/SmartHome/Tests/EditMode/SmartHomeEffectsControllerTests.cs`
- Modify: `Assets/SmartHome/Editor/SmartHomeHouseGenerator.cs`
- Modify: `Assets/SmartHome/Tests/EditMode/SmartHomeScreenLabelTests.cs`

**Interfaces:**
- Produces: `SmartHomeEffectsController.client`, `RefreshBindings()`, and `Apply(HomeSnapshot snapshot)`.
- Consumes: all `SmartHomeDeviceEffect` components under the generated scene and `HomeServiceClient.SnapshotReceived`.

- [ ] **Step 1: Write failing routing tests**

Build a hierarchy with real effect components and stable IDs, call `RefreshBindings`, then apply a snapshot. Assert each component receives only its matching state. Apply a second snapshot with one device absent and assert that effect is unavailable instead of retaining an active target. Add a duplicate-ID test that logs an error and deterministically keeps the first binding.

- [ ] **Step 2: Run focused tests and confirm red state**

Expected: compilation failure because `SmartHomeEffectsController` does not exist.

- [ ] **Step 3: Implement the controller**

Mirror the subscription lifecycle of `SmartHomeSceneController`, but only manage effects:

```csharp
private readonly Dictionary<string, SmartHomeDeviceEffect> effects =
    new Dictionary<string, SmartHomeDeviceEffect>();

public void Apply(HomeSnapshot snapshot)
{
    foreach (KeyValuePair<string, SmartHomeDeviceEffect> pair in effects)
    {
        DeviceDto device;
        if (snapshot.Devices.TryGetValue(pair.Key, out device)) pair.Value.Apply(device);
        else pair.Value.MarkUnavailable();
    }
}
```

Subscribe/unsubscribe to the shared `HomeServiceClient`; do not create a second client or request.

- [ ] **Step 4: Extend the generator with deterministic effect objects**

Change `CreateDevice` to return `SmartHomeView`, then attach effects by type:

- Lights: keep their Point Light, add a small emissive bulb child, attach `SmartHomeLightEffect`.
- ACs: add a vent child and ParticleSystem oriented into the room, `maxParticles=30`, no collision/trails/lights, attach `SmartHomeAirConditionerEffect`.
- Plug: add a small LED sphere/cylinder child and attach `SmartHomePlugEffect`.
- Temperature sensor: add a thin indicator ring child and attach `SmartHomeTemperatureEffect`.

Add `SmartHomeEffectsController` to `Runtime · Home Service (read only)` and assign the existing `HomeServiceClient`. Lower ambient light and Sun intensity only enough to make per-room lights visible; keep the all-off scene readable.

- [ ] **Step 5: Add generated-scene structure assertions**

Open `Assets/SmartHome/Generated/SmartHomeHouse.unity` in EditMode and assert:

```csharp
Assert.That(Object.FindObjectsOfType<SmartHomeLightEffect>(true).Length, Is.EqualTo(3));
Assert.That(Object.FindObjectsOfType<SmartHomeAirConditionerEffect>(true).Length, Is.EqualTo(3));
Assert.That(Object.FindObjectsOfType<SmartHomePlugEffect>(true).Length, Is.EqualTo(1));
Assert.That(Object.FindObjectsOfType<SmartHomeTemperatureEffect>(true).Length, Is.EqualTo(1));
Assert.That(Object.FindObjectOfType<SmartHomeEffectsController>(), Is.Not.Null);
```

- [ ] **Step 6: Regenerate the scene in batch mode**

Close the interactive Unity Editor before writing the generated scene. Run:

```powershell
& 'D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe' -batchmode -projectPath 'E:\unityroom\unityroom' -executeMethod SmartHome.Editor.SmartHomeHouseGenerator.GenerateFromCommandLine -logFile 'E:\unityroom\unityroom\Logs\RealtimeEffectsGenerate.log' -quit
```

Expected: exit code 0, the log contains `Generated and validated Assets/SmartHome/Generated/SmartHomeHouse.unity`, and the saved scene contains zero missing scripts.

- [ ] **Step 7: Run all EditMode tests**

Write results to `Logs/RealtimeEffectsEditMode.xml`. Expected: all tests pass.

- [ ] **Step 8: Review checkpoint**

Inspect the generated YAML only for serialized references/counts; do not hand-edit scene YAML.

---

### Task 5: Verify live behavior, visuals, and performance

**Files:**
- Modify: `Assets/SmartHome/Tests/PlayMode/GeneratedSceneIntegrationTests.cs`
- Create: `Assets/SmartHome/Tests/PlayMode/SmartHomeEffectsIntegrationTests.cs`
- Generated evidence: `Logs/SmartHomeEffects-*.png`

**Interfaces:**
- Consumes: HA-backed SSE from the completed HA plan and the generated scene from Task 4.
- Produces: automated PlayMode evidence and 720p screenshots for the final handoff.

- [ ] **Step 1: Add PlayMode component and snapshot tests**

Load `SmartHomeHouse`, assert the 3/3/1/1 effect counts, obtain the existing `SmartHomeEffectsController`, and call `Apply` with controlled snapshots. Wait for transitions and assert actual light intensity, ParticleSystem emission, plug material color, and sensor color reach expected values within tolerances.

- [ ] **Step 2: Add the HA-backed integration assertion**

Extend `GeneratedSceneConnectsToLiveHomeService` to require all eight stable device IDs and `client.Backend == "Home Assistant"`. Assert every active light/AC effect target corresponds to `client.LatestSnapshot`, without making a control request.

- [ ] **Step 3: Run PlayMode tests**

Run:

```powershell
& 'D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe' -batchmode -projectPath 'E:\unityroom\unityroom' -runTests -testPlatform PlayMode -testResults 'E:\unityroom\unityroom\Logs\RealtimeEffectsPlayMode.xml' -logFile 'E:\unityroom\unityroom\Logs\RealtimeEffectsPlayMode.log' -quit
```

Expected: all controlled-effect tests pass; the live test passes when the HA plan’s service is running.

- [ ] **Step 4: Capture five 1280×720 visual states**

Use controlled test snapshots, not real-device control calls, to capture:

- all lights off/readable structure;
- one room light at low brightness;
- all room lights at high brightness without overexposure;
- AC cool at `16°C` with subtle visible airflow;
- plug on plus one unavailable device.

Save files as `Logs/SmartHomeEffects-Off.png`, `-LowLight.png`, `-Bright.png`, `-Cooling.png`, and `-Unavailable.png`.

- [ ] **Step 5: Inspect performance and console output**

Run Play Mode for at least 60 seconds with all three AC effects active. Expected: total particles remain at or below 90, no repeated material allocations are visible in the Profiler sample, and the Console has no missing-reference or exception messages.

- [ ] **Step 6: Perform final read-only HA comparison**

Read current HA state and `/events` first snapshot, enter Play Mode, and compare eight IDs with the Unity effect targets. Do not toggle entities. Record a sanitized table containing only IDs, normalized state values, and match/mismatch result.

- [ ] **Step 7: Final regression run**

Run the complete Unity EditMode and PlayMode suites. Expected: all deterministic tests pass; if a live integration test is skipped because HA is unavailable, report it separately rather than treating Memory data as success.
