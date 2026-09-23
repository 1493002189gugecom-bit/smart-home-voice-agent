# Voice Service — Phase A Local Validation

Voice stack: local KWS (wake word), Silero VAD, SenseVoice int8 ASR, plus
**network-backed Edge neural TTS for announcements**.

Announcement synthesis uses Microsoft Edge neural voices (`edge-tts`) because
they sound markedly more natural than the local 82M Kokoro model.

**Consequence: announcements require network access.** There is deliberately no
automatic local substitution — if synthesis fails, the announcement is reported
as failed rather than silently replaced, which keeps the `queued → playing →
played | failed` contract honest.

The local Kokoro model remains available as an optional backend
(`--provider kokoro`) for offline experiments, but it is not used as a fallback.

Models are stored outside the repository. On this machine the default root is
`D:\smart-home-models`; override it with `SMART_HOME_MODELS_DIR`.

## Voice Agent (cloud LLM + real device control)

With `--agent`, a transcript is sent to a cloud LLM (DeepSeek, OpenAI-compatible)
which may call four **restricted** tools on the local `home-service`:

| Tool | Effect |
| --- | --- |
| `query_room_status` | rooms, devices, room temperature |
| `query_device_status` | one device's current state |
| `set_light` | `on`, `brightness` 0–100 |
| `set_ac` | `on`, `mode` (`off`/`cool`/`fan_only`), `target_temp` 16–30 |
| `set_switch` | `on` |
| `adjust_ac` | `direction` = `cooler`/`warmer` |
| `run_scene` | one phrase that drives several devices |

The device list and the scene list are both read from `home-service` at startup,
so a device or scene added to its configuration becomes speakable with no code
change. The model only ever sees closed enums.

### Scenes

| Say | Scene | Devices |
| --- | --- | --- |
| 我出门了 / 都关掉 | `leave_home` | light off, AC off, plug off |
| 我回来了 | `arrive_home` | light on 80%, AC cool 26, plug on |
| 我要睡觉了 / 晚安 | `good_night` | light off, plug off, AC cool 26 |

Define more in `apps/home-service/config/scenes.json`. Each step must name a
catalogued device and may only pass parameters that device type accepts; the file
is validated at startup, so a typo fails there rather than when you say the phrase.

**Partial success is reported as partial.** With the plug offline, 我出门了 speaks
"客厅灯已关闭；卧室空调已关闭；智能插座设备当前离线" — it never claims the whole
house is off. Each device is confirmed individually, and a device that failed is
left untouched.

### Temperatures come from configuration, never from the model

`apps/home-service/config/ha_entities.json` carries the AC comfort policy —
`comfort_temp`, `temp_step`, `min_temp`, `max_temp`. The model is never the source
of those numbers:

| You say | What happens |
| --- | --- |
| 有点热 (AC off) | turns on cooling at `comfort_temp` |
| 好热啊 | one `temp_step` cooler |
| 再低一点 | one more step |
| 有点冷 | one step warmer |
| 空调调到 24 度 | exactly 24, because you named it |

`set_ac` also fills in `comfort_temp` when the request cools without naming a
temperature, and `adjust_ac` does the arithmetic server-side. Identical intents
therefore behave identically, and a made-up 22 °C can no longer reach the device.
The spoken sentence for an adjust comes from the service, because the model
tended to narrate "turned it on at 26" as "lowered one step".

### Ending the conversation

The model decides when the user is done, through the `end_conversation` tool, and
the loop returns to standby after the farewell is spoken. A keyword list in the
loop would always miss something — "我先去忙了" is not on any list. Unmistakable
phrases ("退出", "结束对话") remain a hard fallback for when the model is
unavailable, but they are checked *after* the agent so a goodbye can still be
answered out loud.

Without `--agent` the loop keeps its original behaviour and touches no device.

### Configure the key

The key lives in a local **git-ignored** file — never in the repository, a log,
or a command line:

```powershell
New-Item -ItemType Directory -Force runtime\voice-agent | Out-Null
Set-Content runtime\voice-agent\agent.env "DEEPSEEK_API_KEY=<your-key>"
```

`DEEPSEEK_API_KEY` in the environment overrides the file. If the agent is
requested but no key is configured, the loop exits with code 2 **before** loading
any model, rather than silently falling back to a fixed reply.

Override the rest with `SMART_HOME_AGENT_MODEL`, `SMART_HOME_AGENT_BASE_URL`,
`SMART_HOME_SERVICE_URL`, `SMART_HOME_AGENT_TIMEOUT`, `SMART_HOME_AGENT_DEADLINE`
and `SMART_HOME_AGENT_MAX_TOOL_ROUNDS`.

### Run

```powershell
# Text-driven acceptance: real LLM + real home-service, no microphone.
.\.venv\Scripts\python.exe tools/voice-check/agent_acceptance.py

# Full voice loop with the agent.
.\.venv\Scripts\python.exe apps/voice-service/src/loop.py --agent
```

Or set `SMART_HOME_AGENT=1` to enable the agent without the flag.

### Honesty rules the agent follows

- **Tool results, not model prose, decide what is spoken.** If any write failed,
  the model's sentence is discarded, so a hallucinated "已经打开了" can never be
  spoken aloud.
- A `confirmation_timeout` is spoken as "已提交但没有确认到设备状态" — neither
  success nor failure.
- Device ids come from a closed enum, so the model cannot invent a device or a
  path. Writes carry a locally generated `operation_id`; a repeated identical
  write inside one request replays instead of commanding twice.
- The tool loop is bounded (4 rounds, 20 s), and the conversation context is
  bounded and cleared when the session returns to standby, so "再低一度" cannot
  leak across sessions.
- Only the transcript text is uploaded; audio never leaves the machine.

## Environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps/voice-service/requirements.lock.txt
```

## Local status API

The normal voice loop also exposes a loopback-only control surface at
`http://127.0.0.1:8767`. `GET /health`, `/history`, `/events` (SSE), and
`/devices` let the local perception console show wake state, transcripts,
assistant replies, tool outcomes, playback state, and safe audio-device names.
The last 200 public events live only in memory. Raw audio, API keys, model paths,
and credentials are never returned. Use `--no-control-api` only for isolated
command-line diagnostics.

## Choose the TTS voice

```powershell
# Audition the bundled Chinese candidates (network required).
.\.venv\Scripts\python.exe tools/voice-check/audition_voices.py

# Compare providers side by side: kokoro (local) vs edge (online).
.\.venv\Scripts\python.exe tools/voice-check/compare_tts.py --options kokoro,edge
```

Available Chinese Edge voices:

| Voice | Character |
| --- | --- |
| `zh-CN-XiaoxiaoNeural` | female, warm (default) |
| `zh-CN-XiaoyiNeural` | female, lively |
| `zh-CN-YunxiNeural` | male, lively |
| `zh-CN-YunyangNeural` | male, professional |
| `zh-CN-YunjianNeural` | male, passionate |

Override the voice or speech rate for the current shell:

```powershell
$env:SMART_HOME_TTS_VOICE = "zh-CN-YunyangNeural"
$env:SMART_HOME_TTS_RATE  = "+10%"
```

Online synthesis retries transient failures and suspiciously slow responses
(3 attempts by default) so a network hiccup does not silently drop an
announcement.

## Verify devices and models without opening the microphone

```powershell
.\.venv\Scripts\python.exe apps/voice-service/src/loop.py --startup-check
```

The selected input must contain `Realtek`; the Windows default NetEase virtual
input is intentionally not used.

## Choose the input and output devices

Device names change when a headset is plugged in, so devices are chosen from an
ordered preference list rather than a hard-coded name. The default order is
`HyperX, Realtek`; the first candidate that opens as mono float32 at 16 kHz wins.
If none of them work, the tool fails loudly instead of silently recording silence
from a virtual device.

Playback uses a stricter check: `select_playback_target()` also verifies the
sample rate and channel count, preferring **WASAPI** over the legacy
DirectSound/MME paths. That matters because a DirectSound endpoint can accept
`stream.write()` and still produce no audible output — which is exactly what
happened on this machine.

Inspect what is available and what will actually be used:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/record_cases.py --list-devices
.\.venv\Scripts\python.exe tools/voice-check/test_target_playback.py
```

Override the preference list for the current shell (comma-separated substrings):

```powershell
$env:SMART_HOME_INPUT_DEVICE  = "HyperX"        # record through the headset mic
$env:SMART_HOME_OUTPUT_DEVICE = "HyperX"        # play the reply through the headset
```

If audio still cannot be heard, compare the Windows-native player against
PortAudio, then measure the system output meter:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/test_windows_audio.py
.\.venv\Scripts\python.exe tools/voice-check/test_output_meter.py
```

Before recording a whole corpus, confirm the microphone actually picks up your
voice. This measures one second and aborts if it looks silent:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/record_cases.py --check-level --phrases tools/voice-check/cases/mic-smoke.txt
```

For a per-device comparison with dBFS readings and playable WAV files:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/diagnose_mic.py --seconds 6 --candidates HyperX
```

Endpoint state and Windows volume levels can be read (never modified) with:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/inspect_endpoints.py HyperX Realtek
```

## Record the 30 ASR cases

```powershell
.\.venv\Scripts\python.exe tools/voice-check/record_cases.py `
  --cases tools/voice-check/cases/asr-30.tsv --seconds 4
```

Then evaluate them:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/check_asr.py `
  --cases tools/voice-check/cases/asr-30.tsv `
  --out docs/superpowers/reports/artifacts/asr-results.json
```

The Phase A gate is at least 27/30 cases with all required key information.

## Generate the 20 TTS samples

```powershell
.\.venv\Scripts\python.exe tools/voice-check/check_tts.py `
  --cases tools/voice-check/cases/tts-20.tsv `
  --out-dir docs/superpowers/reports/artifacts/tts
```

The WAV files require human listening; generation success is not a listening
pass. Use the interactive scorer (it resumes from saved progress). The gate is
at least 18/20, while all 20 must receive a score:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/listen_tts.py
```

## Record wake-word positives

Record the two bundled 20-line candidate sets:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/record_cases.py `
  --phrases tools/voice-check/cases/wake-xiaowu-20.txt `
  --out-dir tools/voice-check/cases/audio/wake/xiaowu-xiaowu `
  --seconds 3

.\.venv\Scripts\python.exe tools/voice-check/record_cases.py `
  --phrases tools/voice-check/cases/wake-nihao-20.txt `
  --out-dir tools/voice-check/cases/audio/wake/nihao-xiaowu `
  --seconds 3
```

Capture the required negative corpus and speaker-to-microphone acoustic loop:

```powershell
# Records 30 minutes locally as six five-minute files.
.\.venv\Scripts\python.exe tools/voice-check/capture_negative.py

# Audibly plays the fixed reply “收到” and records it through Realtek.
.\.venv\Scripts\python.exe tools/voice-check/capture_self_trigger.py
```

Evaluate positives, actual negative duration, and captured playback:

```powershell
.\.venv\Scripts\python.exe tools/voice-check/check_wake.py `
  --positives 20 --min-positive-hits 18 `
  --negatives-dir tools/voice-check/cases/noise-30min `
  --self-trigger-out tools/voice-check/cases/self-trigger `
  --out docs/superpowers/reports/artifacts/wake-results.json
```

Each candidate requires at least 18/20 detections. Negative audio must total at
least 1800 seconds; its false-wake count is reported without a hard threshold.
Playback captures must be non-empty and have zero KWS detections and zero ASR
forbidden-command terms.

## Run the minimal wake-once continuous loop

```powershell
.\.venv\Scripts\python.exe apps/voice-service/src/loop.py
```

Say “小屋小屋” or “你好小屋”; a short two-note prompt confirms wake-up,
then speak commands continuously. The full 20-second waiting window starts only
after the reply finishes. Microphone blocks are discarded during processing and
playback. Events are appended as timestamped JSON lines to
`docs/superpowers/reports/artifacts/loop-session.log`. This Phase A loop does
not call a cloud LLM and cannot control devices.

## Automated checks

```powershell
.\.venv\Scripts\python.exe -m pytest apps/voice-service/tests -q
.\.venv\Scripts\python.exe -m compileall -q apps/voice-service/src tools/voice-check
```

Audio, model weights, logs and `.venv` are ignored by git.
