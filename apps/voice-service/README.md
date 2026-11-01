# Voice Service — Hybrid Conversation and Local Audio

Voice stack: local KWS (wake word), Silero VAD, SenseVoice int8 ASR, plus
**network-backed Edge neural TTS for announcements**.

The microphone now passes through native WebRTC AEC3 and noise suppression when
the bundled `pywebrtc-audio` wheel is available. Edge replies play sentence by
sentence. Official CosyVoice PCM streaming and Fun-ASR realtime input are optional
providers; the local ASR and original wake-word route remain available.

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

### 无唤醒词与唤醒词共存

2026-10-28 业务修复：等待补充房间时，只有“卧室”“我在卧室”等纯房间回答
可以补全此前的舒适需求。“别降温了，改为关闭卧室灯”等改口会清除待执行需求，
进入正常语义规划。单一相对调温仍纠正模型猜测的绝对温度；复合指令按各工具
自身的参数执行，避免把“客厅调低两度”串到“卧室设为26度”上。

`start-perception.ps1` 现在核对服务实际返回的后端和监听模式；已有进程与本次
参数不一致、或 hybrid 的意图模块没有就绪时，会明确报错。修改 API 配置或模式
后先停止旧进程再启动：`tools\stop-perception.ps1`，然后
`tools\start-perception.ps1 -HomeBackend ha -ListeningMode hybrid`。
未显式传入模式时，启动器读取与语音服务一致的本地配置。设备写入令牌由本机
服务自动创建和读取，无需增加新的云 API。

启用 Agent 后默认使用 `hybrid`：本地 VAD 分句，SenseVoice 得到完整文本，
再由独立的意图 API 判断是否需要助手介入。`小屋小屋`、`你好小屋` 仍可直接
进入原有对话路径。未启用 Agent 时保持仅唤醒词模式。

```powershell
# 在 E:\smart-home 下运行；家居服务应已启动。
.\.venv\Scripts\python.exe apps/voice-service/src/loop.py --agent --listening-mode hybrid
# 原有仅唤醒词模式
.\.venv\Scripts\python.exe apps/voice-service/src/loop.py --agent --listening-mode wake
# 原有整套启动入口也支持显式选模式；省略参数时读取配置，默认 hybrid。
.\tools\start-perception.ps1 -ListeningMode hybrid
.\tools\voice-check\start_agent.ps1 -ListeningMode hybrid
```

启动脚本会复用已经运行的服务；修改模式或源码后，需要先正常停止旧语音进程再启动。
本次实现不自动重启服务。语音 `/health` 返回实际 `listening_mode`、
`requested_listening_mode`、`intent_provider`、`intent_model`、`intent_status` 和固定错误码；`voice_state` 的
`listening` 表示等待自然表达，`active` 表示连续对话中，`standby` 表示仅等待唤醒词。

| 场景 | 行为 |
| --- | --- |
| “今天天气真热啊”“卧室好热” | 判断为当前舒适需求后，按本地空调策略降温并播报确认结果 |
| “屋里好暗”“太刺眼了” | 先查灯光状态，再按本地配置开灯或调节亮度 |
| 不知道说话人或当前位置 | 追问房间；下一句“卧室”可完成原来的舒适调节 |
| “再低一点”“别开灯，改开空调” | 连续对话中结合上下文进入已有 Agent 工具流程 |
| “这样就行”或静默 20 秒 | 结束本轮，继续等待自然表达和唤醒词 |
| 普通闲聊、否定、引用、历史或假设 | 意图模块应忽略，不触发设备、不播报、不公开这段转写 |
| 短时间重复吐槽同一设备同一方向 | 默认 60 秒内抑制重复动作及播报；明确指令仍可继续调节 |
| 意图 API 超时、返回非法 JSON 或配置不完整 | 不放行自然控制；唤醒词入口仍可使用 |

本轮声纹与新鲜摄像头定位只用于解析本轮 `self` 目标，不复用上一句的身份或位置。
短句声纹失败可保留对话内容，但不能据此猜测说话人位置。明确说出的房间优先。
舒适动作限定为空调温度和灯光，不由意图 API 自由调用工具；实际成功播报来自家居服务。
灯光默认每档 20%、关灯后需要变亮时打开到 70%，亮度限制在 1–100%；
空调档位、舒适温度和范围继续由家居服务配置决定。

意图模块只返回 `kind`（`ignore/comfort/command/follow_up/end`）、`need`
（`cooler/warmer/brighter/dimmer` 或 null）、`room`（当前句中明确出现的目录房间或 null）。
本地严格验证字段，拒绝额外参数、设备 ID、温度等越权数据。

### 在控制台打字对话

打开感知控制台的“语音”栏目，在“实时对话”底部输入文字并发送。每条消息视为
已经完成的语音转写，走相同的意图分类、舒适动作策略和 Agent 工具校验。
文字输入不调用录音、ASR 或声纹；“播报回复”默认勾选，取消即可安静打字。
播报使用当前选定的输出设备，与麦克风回复串行播放；文字播报期间暂停麦克风输入，
避免扬声器回声触发新一轮。原有麦克风语音入口和语音回复的打断功能继续工作。

例如输入“好热啊”，房间不明确时会追问；下一条输入“卧室”可承接调节。
输入“这样就行”结束本轮，再发新消息即可开始下一轮。文字上下文与麦克风上下文
独立，闲置 60 秒后清空，不复用麦克风识别出的身份或位置。开头明确输入唤醒词
仍可进入原 Agent 对话路径。闲聊、意图服务故障、重复需求抑制会在对话区显示原因。

`Enter` 发送，`Shift+Enter` 换行，中文输入法选词回车不会发送；最多 1500 字。
每次只处理一条文字消息，结果通过现有 `/history` 和 `/events` 返回并显示在同一对话区。
`POST /conversation/text` 接收严格的 `{request_id, text, speak?}`（布尔值，省略为 false），返回 202 只表示接收，
`text_turn.turn_status` 的 `completed/ignored/failed` 才表示本轮结束；设备成功与否
以 `agent_reply` 和 `tool_result` 中的实际确认结果为准。同一进程内最近 200 条请求
按编号去重，避免响应丢失后重试导致重复控制。服务重启后记录和去重缓存清空。

更新后需重启语音服务和控制台后端，并刷新网页；旧进程不会自动加载新增接口。
启动仍使用 `tools/start-perception.ps1`。本入口使用原来的 API 配置，无需另买 API。

### 意图 API 配置项

默认 `SMART_HOME_INTENT_PROVIDER=agent`：复用现有 Agent 的 API Key、地址和模型，
创建独立的意图客户端，不改动原聊天客户端。通常无需购买第二个 API。
也可为意图识别独立配置供应商，聊天模型仍保持原配置：

| 配置 | 默认 / 含义 |
| --- | --- |
| `SMART_HOME_LISTENING_MODE` | `hybrid`；也可设 `wake` |
| `SMART_HOME_INTENT_PROVIDER` | `agent`；可选 `deepseek`、`dashscope`、`volcengine`、`compatible` |
| `SMART_HOME_INTENT_API_KEY` | 独立意图 Key；未设时读取对应 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY` 或 `ARK_API_KEY` |
| `SMART_HOME_INTENT_BASE_URL` | 独立兼容 API 地址；前三个供应商有默认地址 |
| `SMART_HOME_INTENT_MODEL` | DeepSeek 默认 `deepseek-chat`，DashScope 默认 `qwen3.7-flash`；Volcengine/compatible 必填 |
| `SMART_HOME_INTENT_RESPONSE_FORMAT` | `json_object`；模型支持时可选 `json_schema`，不支持格式参数时可选 `none`，本地仍校验 JSON |
| `SMART_HOME_INTENT_TIMEOUT` | 8 秒，允许 1–30 秒 |
| `SMART_HOME_COMFORT_COOLDOWN` | 60 秒，允许 1–3600 秒 |
| `SMART_HOME_COMFORT_LIGHT_STEP` | 20，允许 1–100 |
| `SMART_HOME_COMFORT_LIGHT_BRIGHTNESS` | 70，允许 1–100 |

这些配置读取环境变量或本地忽略文件 `runtime/voice-agent/agent.env`。
`agent` 模式忽略独立 Key/地址/模型配置，始终复用聊天客户端；换意图供应商时应明确选供应商。
DashScope 关闭思考，Volcengine 使用 `thinking.type=disabled`；所选模型需支持对应参数和流式接口。
Key 应保存在本地配置中，不写入源码、命令行、日志或提交。

无唤醒词模式会把每段有效语音的转写及最多六条短期上下文交给所选意图 API，
原始音频仍留在本地。被忽略的对话不进入公开事件或诊断文本日志，短期上下文仅在内存中保留。
默认仍使用本地 SenseVoice 整句转写；可按下方配置切换 Fun-ASR 实时识别。SenseVoice 的情绪、
声音事件作为辅助数据交给意图分类器；被模型标为音乐/非语音事件时，无唤醒词路径保守忽略，
混合人声可能因此被误判，显式唤醒仍保留独立通路。语气标签不能保证识别反问或讽刺，
音乐标签也不能证明电视人声来源。
真实语义准确率、延迟和扬声器回声仍需实际验收，合成测试不能证明真实房间效果。
自然语音超过 15 秒仍未结束时整句忽略并等到静音后恢复，避免拿尚未说完的前半句执行设备动作。

### 语音可靠性、实时 API 和效果检查

“语音”页可查看转写、意图判断、设备处理、合成、播放的状态与耗时；点击“停止回复”
可取消尚未完成的意图/Agent 请求、合成和播放。已经确认的设备操作仍然有效，停止回复不回滚设备。
文字播报仍暂停麦克风输入，使用页面按钮停止；麦克风回复保留语音打断。

Edge 使用一个合成时限约束全部重试，403/401等错误立即失败；返回慢但成功的音频直接接受。
长回复按句播放以缩短首次等待。CosyVoice 使用真正的 WebSocket PCM 增量输出。
失败不会自动重新执行家居操作，不会自动切换到另一家付费服务。

以下均可通过环境变量或本地忽略的 `runtime/voice-agent/agent.env` 配置（不要提交密钥）：

| 配置 | 默认 / 含义 |
| --- | --- |
| `SMART_HOME_AUDIO_PROCESSING` | `auto`；原生库可用时启用 AEC3+降噪；`off`关闭，`webrtc`要求原生库可用 |
| `SMART_HOME_SPEECH_TIMEOUT` | 30秒，允许0.1–120；网络语音请求时限 |
| `SMART_HOME_TTS_PROVIDER` | `edge`；可选 `cosyvoice`、`kokoro` |
| `DASHSCOPE_API_KEY` | 仅选择云端语音服务时需要，不与 DeepSeek Key 混用 |
| `SMART_HOME_COSYVOICE_MODEL` | `cosyvoice-v3-flash` |
| `SMART_HOME_COSYVOICE_VOICE` | `longanyang`，需与模型兼容 |
| `SMART_HOME_SPEECH_WS_URL` | 北京兼容域名；可换为对应业务空间/地域的官方 WebSocket 地址 |
| `SMART_HOME_ASR_PROVIDER` | `local`；`funasr`启用云端实时转写 |
| `SMART_HOME_FUNASR_MODEL` | `fun-asr-realtime` |

Fun-ASR 在说话过程中发送音频，仅最终句子进入意图/控制路径。中间结果会被修订，绝不据此执行。
显式唤醒后的识别中字幕可实时显示；被动环境语音在语义接纳前不公开文字。云端识别会上传语音，
本地 SenseVoice 继续提取声音事件辅助信息，原始音频不落盘。选择云端后必须正确配置密钥；
配置缺失会明确报错，不会悄悄回退。TTS的密钥/模型问题也不会自动切换付费供应商。

“检查意图识别效果”提供24个内置场景和单句试判。它只运行独立分类器，不能操作设备。
结果仅保留在服务内存，刷新可查看；调用现有模型会产生对应 API 用量。CLI可保存仅含内置文本的报告：

```powershell
.\.venv\Scripts\python.exe tools/voice-check/evaluate_intent.py --limit 24 --output output/voice-evaluation/intent.json
```

服务接口：`GET /intent/evaluation`、`POST /intent/evaluation/start`（`{limit:24}`或`{text:"..."}`）、
`POST /intent/evaluation/cancel`（空对象）、`POST /conversation/stop`（空对象）。均经过控制台白名单转发。
只重载受本项目管理的语音/控制台进程后刷新页面；不要在仍运行旧后端时只刷新网页。

协议参考：[CosyVoice](https://help.aliyun.com/zh/model-studio/cosyvoice-websocket-api/)、
[Fun-ASR](https://help.aliyun.com/zh/model-studio/fun-asr-client-events)、
[WebRTC Python 绑定](https://github.com/strands-labs/pywebrtc-audio)。

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

When a command such as “空调调高一度” omits the room, the current utterance's
confirmed voiceprint is joined to a fresh camera location. The local resolver
selects the unique AC in that room and speaks the room name in its confirmation.
If the speaker, location, or device is uncertain, it asks for the room. An
explicitly named room always takes priority. This target decision is made by
local code, not by the chat model.

Two things keep this from turning into “which room did you mean?”:

- The room the resolver found is written into that turn's prompt as
  `本轮可信定位：说话人在卧室（0.8 秒前的摄像头观察）…`, with the instruction to act
  on `target: "self"` instead of asking. When the location is missing, the same
  line says so and names the reason, so the model asks only when there is
  genuinely nothing to act on.
- The most ordinary room-less imperatives (开灯 / 关灯 / 开空调 / 关空调 /
  空调调高一度 and their common variants) are executed by local code with no
  model call at all. Anything naming a room or a specific lamp ("打开客厅灯",
  "开台灯"), a negation, or a second clause still goes to the model.

### Interrupting a spoken reply

You can talk over the assistant. Sustained speech stops the current reply, and what
you said is kept and used as the next command — “空调调高一度” said over the reply is
both the interruption and the instruction:

- **While it is thinking** (waiting for the cloud model, or synthesizing): the
  microphone is watched by a worker thread. Speech cancels the request, and the
  reply that was being prepared is never played at all.
- **While it is speaking**: detection is checked between 50 ms audio chunks. Once
  confirmed, playback aborts its buffered output immediately; the captured portion
  of your speech starts the next utterance. The wake word still interrupts too.
- **Echo**: the known TTS waveform is aligned with microphone audio and a matching
  copy is removed before VAD. This lets a quieter user speak over a louder reply in
  the simulated case. If the waveform cannot be matched reliably, the detector
  falls back to measuring leakage for `SMART_HOME_BARGE_ECHO_CALIBRATION` seconds
  (0.6 s) and requiring `SMART_HOME_BARGE_ECHO_RATIO` (1.5×) more loudness. Speech
  must last `SMART_HOME_BARGE_MIN_SPEECH` (0.3 s). In the fallback window, the
  first 0.6 s cannot interrupt.
- **Keep the beginning**: up to 0.64 s of onset context is retained. During
  playback it is echo-reduced or filtered against measured leakage, not blindly
  copied from the microphone; small gaps during confirmation are retained too.
- **Allow hesitation**: a sentence is not committed on the first negative VAD block.
  `SMART_HOME_COMMAND_END_SILENCE` defaults to 0.7 s of additional continuous
  VAD-negative audio (valid range 0.1–2.0 s). Silero's existing ~0.6 s hangover
  remains, so the physical end-of-speech wait is roughly 1.3 s. This trades a
  slightly slower response start for keeping a hesitant command in one utterance.
  Long silence still ends the turn, and the 15 s utterance cap remains.
- **Honest limits**: this is not full acoustic echo cancellation. Room reflections,
  device sound effects and an unusually loud speaker can still hide an interruption;
  a loud unrelated sound may stop playback. `SMART_HOME_BARGE_IN=0` restores
  wake-word-only interruption. A real microphone and speaker check is still required.

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

## Speaker identification (voiceprints, optional and off by default)

Voiceprints exist for exactly two things: addressing the speaker as "你", and letting
an omitted room default to the speaker's own camera-confirmed room. They are **not**
used to decide whether an action is allowed — that is a static table in
`src/agent_tools.py` (`TARGET_TIERS`), never a model's judgement — and they never
provide a location: position always comes from the camera.

Nothing here is active until a model file is configured, so an un-enrolled household
behaves exactly as before.

**Prerequisites: one model file, no new install**

`sherpa-onnx` is already a dependency of this service, and sherpa-onnx runs the
feature frontend (fbank) internally, so `speaker.py` hands it a raw 16 kHz waveform.
Nothing needs `pip install`, and no model is ever downloaded automatically — a
missing file is reported as `speaker_model_missing`.

Download one Chinese 16 kHz speaker-embedding model. These live in the upstream
`sherpa-recongition-models` release (the tag really is spelled that way):

```powershell
# In E:\smart-home — pick ONE. Sizes are for choosing between CPU latency and capacity.
$model = "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx"   # 37.8 MB, recommended start
# $model = "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"           # 27.0 MB, fastest
# $model = "3dspeaker_speech_eres2net_large_sv_zh-cn_3dspeaker_16k.onnx"  # 110.7 MB, largest ERes2Net

$dest = "D:\smart-home-models\$model"
Invoke-WebRequest `
  -Uri "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/$model" `
  -OutFile $dest
"downloaded: $((Get-Item $dest).Length / 1MB) MB -> $dest"
```

Then point the service at it. Either export it in the shell that launches the service,
or — better, because it survives a restart — put it in the Git-ignored local env file
`runtime/voice-agent/agent.env`, which this service already reads for its LLM key:

```powershell
# In E:\smart-home: append the model path to the local env file
Add-Content -Encoding UTF8 runtime\voice-agent\agent.env `
  "SMART_HOME_SPEAKER_MODEL=D:/smart-home-models/3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx"
```

Every speaker setting below can live in that file. A real environment variable always
wins over the file.

All six Chinese models in that release are `zh-cn` + `16 kHz`. Do **not** use the
`en` (English) models for Chinese speech. Swap models freely — it is one variable —
but re-measure after swapping, because the threshold depends on the model.

**Configuration** (all optional; the defaults are shown)

| Variable | Default | Meaning |
| --- | --- | --- |
| `SMART_HOME_SPEAKER_MODEL` | unset | Path to the model. Unset turns the feature off entirely. |
| `SMART_HOME_VISION_URL` | `http://127.0.0.1:8766` | The gallery owner; matching never happens locally. |
| `SMART_HOME_SPEAKER_MIN_SECONDS` | `1.5` | Shorter utterances are refused rather than guessed at. |
| `SMART_HOME_SPEAKER_MATCH_THRESHOLD` | `0.55` | Below this the result is `uncertain` **and the name is discarded**. |
| `SMART_HOME_SPEAKER_MARGIN_THRESHOLD` | `0.08` | Two people scoring this close together are not guessed between. |
| `SMART_HOME_SPEAKER_PROVIDER` | `cpu` | `cpu` or `cuda` (CUDA needs a CUDA-enabled sherpa build). |
| `SMART_HOME_SPEAKER_THREADS` | `1` | Threads for one embedding. |
| `SMART_HOME_SPEAKER_ENROLL_SAMPLES` | `5` | Accepted utterances required per enrolment (minimum 4). |

The two galleries live side by side under `runtime/vision/`, both DPAPI-encrypted for
the current Windows user: `face_registry.bin` and `speaker_registry.bin`. Voiceprints
never leave the machine, exactly like faces, and every enrolled embedding is bound to
a `person_id` from the same directory the faces use, so "the dad in the face gallery"
and "the dad in the voice gallery" are the same person by construction.

**Measure before trusting it.** Run the measurement harness on your own voices and
microphone, because the threshold in the table above is only a starting point:

```powershell
# Put at least two people's recordings in <root>/<person_id>/*.wav,
# with at least 100 impostor trials before believing a 1% target.
.\.venv\Scripts\python.exe tools/voice-check/measure_speaker.py `
  --samples runtime/voice-eval `
  --model D:/smart-home-models/3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx `
  --out docs/superpowers/notes/2026-09-25-speaker-far-report.md
```

The report gives EER, FAR and FRR overall and bucketed by utterance length. It needs
the model and real recordings, so a person runs it; it never touches device control.

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

Online synthesis retries transient failures (3 attempts maximum) within a shared
deadline, accepts slow successful audio, and does not retry authentication errors.

If the console reports `tts_service_rejected` (403), update the locked voice
dependencies and restart the voice process. This project pins `edge-tts 7.3.1`;
the older 7.2.8 client was rejected by the upstream service in the observed
2026-10-22 failure. Synthesis and output failures are shown separately, and
failed speech does not discard the text reply or repeat an executed operation.

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
after the reply finishes. With interruption enabled, microphone audio is watched
during model processing and playback, and speech becomes the next utterance.
Public events remain in memory for the current process. For explicit
diagnostics only, `--log-file <path>` writes timestamped JSON lines that may
include conversation text. This Phase A loop does
not call a cloud LLM and cannot control devices.

## Automated checks

```powershell
.\.venv\Scripts\python.exe -m pytest apps/voice-service/tests -q
.\.venv\Scripts\python.exe -m compileall -q apps/voice-service/src tools/voice-check
```

`tests/test_barge_in.py` covers sustained speech, known-waveform echo rejection,
the fallback calibration window, and the pre-roll that must not reach ASR. Startup
and buffered-output abort are covered by `test_loop.py` and `test_playback.py`.
These scripted checks cannot prove how your own speakers and microphone interact;
the real check above is still required.

无唤醒词的关键回归位于 `test_ambient_intent.py`、`test_comfort_actions.py`、
`test_ambient_loop.py`：覆盖真实主循环的自然触发、追问房间、结束/超时、重复抑制、
否定结果不公开、唤醒词共存、当前声纹边界、模型等待期间完整和未确认插话、
取消不写设备、JSON/供应商接口限制及灯光状态边界。模型与设备边界均使用受控替身，
不调用收费 API、不打开麦克风、不操作真实设备；语义例句的分类结果不代表真实模型质量评测。

Audio, model weights, logs and `.venv` are ignored by git.
