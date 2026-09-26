# 阶段 A 本地语音验证报告

日期：2026-09-12
状态：**已放行。** 自动化基础验证完成；人工语音/试听门槛按用户 2026-09-12 的决定免测
（记为“决策通过”，不是实测通过），因此阶段 A 于当日放行进入阶段 B，结论见本文末节。
下文表格中的“未完成/待人工验证”字样保留为当时的事实记录，不再表示阶段未放行。

## 1. 环境

| 项目 | 实测 |
| --- | --- |
| 操作系统 | Windows 11 家庭中文版 |
| CPU / 内存 | Ryzen 9 7945HX，16C/32T；15.7 GiB RAM |
| GPU | RTX 4060 Laptop，8188 MiB VRAM；本阶段模型全部显式使用 CPU provider |
| Python | `.venv` Python 3.11.9 |
| 运行时 | sherpa-onnx 1.13.8 / sherpa-onnx-core 1.13.8 |
| 模型目录 | `D:\smart-home-models`（用户要求放 D 盘，可用 `SMART_HOME_MODELS_DIR` 覆盖） |
| 麦克风 | 按优先列表自动选择；当前插着耳机时选中 `耳机式麦克风 (HyperX Virtual Surround Sound)`，否则回退 `麦克风阵列 (Realtek(R) Audio)`；不使用默认的网易虚拟音频设备 |
| 扬声器 | 按同一优先列表选择；当前为 `头戴式耳机 (HyperX Virtual Surround Sound)` |

依赖完整版本见 `apps/voice-service/requirements.lock.txt`。`pip check` 输出 `No broken requirements found.`。

## 2. 模型与许可证

| 用途 | 模型 | 本机验证 | 许可证状态 |
| --- | --- | --- | --- |
| KWS | sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20 | 模型初始化及官方样例推理成功 | 包内无 LICENSE；本机开发可用，对外分发前必须向上游确认 |
| VAD | silero_vad.onnx | 语音→静音转换返回正常 | 独立 ONNX 无随附许可证文本；分发前确认 |
| ASR | SenseVoiceSmall int8 2024-07-17 | 官方中/英/日/韩/粤五条样例推理成功；真实录音 30/30 | 包内链接到 FunASR；当前上游 LICENSE 为 MIT |
| TTS（现用） | **Microsoft Edge 神经语音（edge-tts，在线）** | 20/20 生成成功，延迟 1.1–3.6 秒 | ⚠️ 非公开接口，条款未明确允许服务化调用；自用可，分发前须评估 |
| TTS（可选） | Kokoro int8 multi-lang v1.1（本地） | 25 条生成成功 | 包内 Apache-2.0 |

详细来源、文件大小与许可证证据见 `docs/superpowers/notes/2026-09-12-model-review.md`。D 盘模型目录（压缩包＋解压目录）约 905 MB。

### 2.1 TTS 选型变更（2026-09-12）

用户试听本地 Kokoro 后反馈“音色像旧年代、含底噪”。实测与决策：

- 底噪测量：静音段中位 **−60.5 dBFS**、高频能量占比中位 −24.7 dB，属于“基本不可闻”边缘，**不是主要问题**；主要问题是 82M 模型的音色与韵律。
- 官方 RTF 表显示 Kokoro 中英版 RTF 3.19（4 线程），是所列中文模型里最慢的之一（aishell3 仅 0.156）。
- 改用 **edge-tts**：延迟从 3.4 秒降到 **1.1–3.6 秒**，音质明显更自然，无需本地模型。
- **明确取舍**：播报改为依赖网络。合成失败时如实报 `failed`，**不做本地自动替换**，保持播报状态机诚实。用户明确不需要本地后备。
- Kokoro 保留为可选后端（`--provider kokoro`）供离线实验，不作为后备路径。
- **TTS 试听门槛处置**：20 条已用 edge 音色重新生成（20/20 成功）。用户听过后明确表示满意并决定不做逐条评分，因此本报告**不声称 18/20 通过**，而是记为“用户接受、正式评分免除”。这属于用户对门槛的显式豁免，需在阶段结论中如实体现。
- 曾出现一次“评分对象错位”隐患：7 条旧评分是在 Kokoro 音频上打的，而音频随后被 edge 覆盖。已删除该失效评分，并在 `listen_tts.py` 中记录 `provider`/`voice`，音色变化时自动作废旧评分，避免今后再次误用。

## 3. 自动化验证结果

### 3.1 代码与配置

- `python -m compileall -q apps/voice-service/src tools/voice-check`：通过。
- `python -m pytest apps/voice-service/tests -q`：**27 passed**（含静音块持续到达时仍触发超时、回复播放结束后才启动完整等待窗口、播放抑制丢弃输入、ASR 数字等价写法、空语料不得通过等回归测试）。
- `loop.py --startup-check`：Realtek 输入/输出及 KWS、VAD、ASR、TTS 全部初始化成功。
- `loop.py --no-tts --run-seconds 3`：实时输入流运行 3 秒，`audio queue overflows: 0`。
- `loop.py --no-tts --run-seconds 1`：生成 JSONL 会话日志，含 ISO 8601 时间戳、状态、设备、超时参数和停止事件；真人运行时还会记录 wake、ASR 转写/耗时、TTS 开始/结束、首帧估算和异常。
- `.venv`、模型、WAV、日志均由 `.gitignore` 排除。

### 3.2 ASR 冒烟

使用 SenseVoice 包内五条官方 WAV：

| 语言 | 结果 |
| --- | --- |
| 英文 | 成功转写 |
| 日文 | 成功转写 |
| 韩文 | 成功转写 |
| 粤语 | 成功转写 |
| 中文 | `开饭时间早上9点至下午5点。` |

5/5 管线成功；单条推理约 0.22–0.44 秒。该结果只证明模型和脚本可运行，**不替代真实麦克风的 30 条中文验收**。

### 3.3 TTS 生成

- `tts-20.tsv` 共 20 条，**20/20 成功生成** 24 kHz PCM16 WAV。
- 自动检查 20 个文件均存在、时长大于 0、峰值大于 0、采样率为 24000 Hz。
- 生成耗时约 2.49–6.50 秒/条（句长不同）。
- **听感尚未判定**；必须用 `listen_tts.py` 逐条播放并评分。

### 3.4 KWS 与自触发冒烟

- 官方 KWS 测试音频可产生关键词结果，证明模型与 token 管线可运行。
- 用 Kokoro 合成两个候选作非门槛冒烟：`小屋小屋` 命中；`你好小屋` 未命中。
- 把 20 条生成的普通家居播报 WAV 直接输入 KWS：**0 次检测**（补充冒烟，不算声学回环）。
- `check_wake.py` 现会对每段真实回录**同时运行 KWS 与 ASR**，报告唤醒命中、完整转写及禁止指令词命中；回录目录为空或总时长不足时不会通过。
- 负例文件按真实音频时长求和，少于 1800 秒或空目录不会通过；误唤醒次数本身按设计只报告、不设硬阈值。
- 已用直接合成的固定回复“收到”验证双管线可运行：KWS 0 次、ASR 转写“收到。”、禁止词 0 次；这不是扬声器→空气→麦克风的声学回环，故**不能替代真实播放自触发门槛**。

### 3.5 延迟与资源（CPU，官方中文样例，5 次）

| 指标 | 结果 |
| --- | --- |
| KWS 初始化 | 0.890 s |
| VAD 初始化 | 0.007 s |
| ASR 初始化 | 1.340 s |
| TTS 初始化 | 1.747 s |
| ASR 首次 / 热启动中位数 | 0.176 s / 0.171 s |
| TTS 完整同步合成首次 / 热启动中位数 | 3.418 s / 3.460 s |
| 进程峰值工作集 | 797.5 MB |
| VAD 语音后回到静音 | 是 |
| GPU provider | 未使用（全部显式 `cpu`） |

上表 TTS 数值是**整句完整生成耗时，不是首次出声时间**。真人运行 `loop.py` 时，会在 `loop-session.log` 写入从合成开始到输出流提交首帧并叠加 PortAudio 延迟的 `tts_first_output_estimate`；实际听到首声仍需用户复现并确认。基准期间整机 `nvidia-smi` 显存占用约 1549/8188 MiB；WDDM 模式未提供逐进程显存数字，进程列表未出现该 Python 基准进程。完整机器可读结果在 `voice-benchmark.json`。

## 4. 麦克风诊断

- Windows 麦克风隐私权限 HKCU/HKLM 与“桌面应用”两级均为 `Allow`。
- 插入耳机后 Windows 默认输入变为 HyperX，因此输入设备改为**有序优先列表**（`HyperX, Realtek`，可用 `SMART_HOME_INPUT_DEVICE` 覆盖），避免写死设备名。
- 端点注册表实测：HyperX 耳机麦克风 `ACTIVE`、音量 84/100；板载麦克风阵列 `ACTIVE`、音量 76/100。软件侧没有静音或零音量。
- 但两路麦克风在 6 秒录音窗口内都只有噪声底（HyperX 约 −91 dBFS、板载约 −97 dBFS），**峰值仅 −64 / −84 dBFS，没有任何语音能量**；MME、DirectSound、WDM-KS 三种接口和两个通道结果一致。
- 结论：现象是“录制期间没有声音进入系统”，而不是采样率或设备选择错误。可能原因是耳机物理静音键、麦克风未插到底，或录制时未实际发声。需要用 `--check-level` 在正式录音前确认。
- **后续已确认可用**：用户解除静音后，30 条 ASR 录音 peak 中位 0.208，输入路径正常。
- 正式录制入口：`record_cases.py --check-level`（静音会直接退出并报警）、`diagnose_mic.py`（逐设备 dBFS 与可回放 WAV）、`inspect_endpoints.py`（只读端点状态）。

## 4b. 播放路径缺陷与修复（TTS 试听无声）

首次运行 `listen_tts.py` 时用户报告完全听不到声音，排查过程与结论：

| 检查 | 结果 |
| --- | --- |
| Python 目标设备 | `#18 头戴式耳机 (HyperX)` [DirectSound] |
| 与 Windows 默认端点是否同一 | 是（端点 ID `{18976a29-…}`） |
| 端点状态 / 音量 | ACTIVE / 253（满） |
| `stream.write()` | 5 条 PortAudio 路径全部“成功”且无异常 |
| Windows 输出电平表 | **0.0000** |
| 三路对照（winsound 提示音 / winsound WAV / sounddevice） | 用户反馈：**只有 sounddevice 没声音** |

**根因**：DirectSound 主机接口能接受写入却不产生可听输出，而 Windows 原生播放（WASAPI）正常。

**修复**：
1. 新增 `audio_utils.select_playback_target()`，返回经校验的设备＋采样率＋声道组合，主机接口优先级改为 **WASAPI > DirectSound > MME > WDM-KS**，并在单声道不被支持时回退立体声、在 24 kHz 不被支持时回退 48 kHz。
2. 新增 `apps/voice-service/src/playback.py`：按目标重采样、必要时复制为多声道，写入后等待缓冲排空。
3. `loop.py`、`listen_tts.py`、`capture_self_trigger.py` 全部改用该目标；`listen_tts.py` 保留 `--player winsound` 原生回退。
4. 修复后实测目标为 `#24 头戴式耳机 (HyperX) [Windows WASAPI] 48000 Hz x2`，用户确认**能听到测试音**。

WDM-KS 仍排在最后：PortAudio 对它报 `Blocking API not supported yet`，不适合作为采集主路径。

## 5. 门槛状态

| 门槛 | 状态 | 证据强度 |
| --- | --- | --- |
| 1 | ASR 真实录音 ≥27/30 | ✅ 通过 | **实测 30/30** |
| 2 | 录音质量（peak ≥0.1） | ✅ 通过 | **实测**：中位 0.208，29/30 ≥0.1 |
| 3 | 麦克风可用性 | ✅ 通过 | **实测**：耳机麦克风 ACTIVE、音量 84/100 |
| 4 | 播放路径可用性 | ✅ 通过 | **实测**：WASAPI 48 kHz，用户确认可听到 |
| 5 | TTS 音色可接受 | ✅ 通过 | **用户确认满意**（免逐条评分） |
| 6 | 唤醒/负例/回环/连续会话 | ✅ 决策通过 | ⚠️ **用户决定免测**，非实测 |

### 5.1 第 6 项的如实说明（重要）

用户明确决定“不再测、全部通过、有问题再回来解决”。本报告尊重该决定，但必须记录实际证据状态，避免把决策当成实测：

| 未实测项 | 已知的间接证据 | 风险 |
| --- | --- | --- |
| 两个候选唤醒词各 20 次 | KWS 模型与 token 管线可运行；合成音“小屋小屋”可命中 | **唤醒成功率未知**，这是语音入口的第一环 |
| 30 分钟负例误唤醒 | 20 条普通播报 WAV 直接输入 KWS 为 0 次检测（非声学回环） | 真实环境误唤醒率未知 |
| 播放自触发声学回环 | 播放期间通过代码丢弃麦克风输入（有回归测试） | 声学回环是否真会自触发未知 |
| 20 秒连续会话真人操作 | 超时/播放后计时/播放抑制均有单元测试与回归测试 | 真人端到端未复现 |

因此**放行阶段 B 是基于用户决策，而非基于完整实测**。若后续真人使用时发现唤醒不灵、频繁误唤醒或播报自触发，应回到本报告的第 4–7 步补测；相关命令与工具均已就绪。

## 6. 用户执行入口

```powershell
# 0) 查看还有哪些语料没录
.\.venv\Scripts\python.exe tools/voice-check/recording_status.py

# 1) 录 30 条 ASR（--check-level 会先测电平，静音直接报警退出）
.\.venv\Scripts\python.exe tools/voice-check/record_cases.py --cases tools/voice-check/cases/asr-30.tsv --seconds 4 --check-level

# 2) 跑 ASR 门槛
.\.venv\Scripts\python.exe tools/voice-check/check_asr.py --cases tools/voice-check/cases/asr-30.tsv --out docs/superpowers/reports/artifacts/asr-results.json

# 2b) 核查录音电平与时长
.\.venv\Scripts\python.exe tools/voice-check/check_recording_quality.py

# 3) 试听并逐条评分 TTS
.\.venv\Scripts\python.exe tools/voice-check/listen_tts.py

# 4) 录两个唤醒词各 20 次
.\.venv\Scripts\python.exe tools/voice-check/record_cases.py --phrases tools/voice-check/cases/wake-xiaowu-20.txt --out-dir tools/voice-check/cases/audio/wake/xiaowu-xiaowu --seconds 3
.\.venv\Scripts\python.exe tools/voice-check/record_cases.py --phrases tools/voice-check/cases/wake-nihao-20.txt --out-dir tools/voice-check/cases/audio/wake/nihao-xiaowu --seconds 3

# 5) 录制 30 分钟负例（本地分成 6 个 5 分钟 WAV；--yes 跳过确认）
.\.venv\Scripts\python.exe tools/voice-check/capture_negative.py --yes

# 6) 播放固定回复并用物理麦克风做声学回录（会发出声音）
.\.venv\Scripts\python.exe tools/voice-check/capture_self_trigger.py --count 3

# 7) 检查真人唤醒、负例时长/误唤醒、回录 KWS 与 ASR 禁止词
.\.venv\Scripts\python.exe tools/voice-check/check_wake.py --positives 20 --negatives-dir tools/voice-check/cases/noise-30min --self-trigger-out tools/voice-check/cases/self-trigger --out docs/superpowers/reports/artifacts/wake-results.json

# 8) 启动真实连续语音环（事件写入本地 loop-session.log）
.\.venv\Scripts\python.exe apps/voice-service/src/loop.py
```

**ASR 结果（2026-09-12 实测）：30/30 通过**，门槛 27/30。

- 单条识别耗时 0.11–0.22 秒；全部为 16 kHz 单声道 4 秒。
- 录音电平：peak 中位 0.208、最大 0.709、最小 0.095。
- 用例 `asr-06` 最初因“关掉”被写成只接受“关闭”而误判，已改为接受 `关闭|关掉` 两种同义说法后命中；这属于用例写法问题，不是识别错误。

## 7. 阶段结论

**按用户决定放行进入阶段 B。**

已实测通过：真实中文语音 ASR **30/30**、录音电平合格、耳机麦克风与 WASAPI 播放路径确认可用、TTS 音色获用户认可。

用户明确决定免除唤醒词、30 分钟负例、声学回环与连续会话真人测试，并指示“全部通过、有问题再回来解决”。因此这四项**记为决策通过而非实测通过**，对应的采集与检查工具全部就绪，需要时可直接补测。

已完成的自动验证：四模型 CPU 初始化与推理、ASR/KWS/VAD 管线、设备优先选择与 WASAPI 播放、在线 TTS 含重试、连续环骨架，以及 **51 项单元测试**。
