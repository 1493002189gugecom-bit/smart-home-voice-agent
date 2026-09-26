# Unity 智能家庭语音 Agent — 实现计划

日期：2026-09-12（修订版 v5）
状态：**历史归档（2026-09-25 移入 `plans/archive/`）。** §0–6 是 2026-09-12 的方案与放行校验记录，其中的架构假设（`apps/unity-house/` 工程、拖动决定人物位置、Unity 承担摄像头与语音）已不成立；2026-09-25 的全项目复审路线图原本追加在本文件末尾，现已迁出为
[`../2026-09-25-project-roadmap.md`](../2026-09-25-project-roadmap.md)。当前状态看
[`docs/superpowers/README.md`](../README.md)。
对应设计：`docs/superpowers/specs/2026-09-12-smart-home-voice-agent-design.md`

## 修订说明

**v2（相对 v1）修正四个问题：**

1. **验收循环依赖**：v1 在阶段 A（只有离线脚本）就要求验收“进入对话、播报自触发、设备误操作”，但对话状态机与设备链路尚未实现；阶段 C 又要求自然语言端到端找人通知，而 Agent 在阶段 D。v2 把实时语音验证限定为“固定回复、不接家居状态”，自然语言端到端验收移到 D。
2. **缺少可执行细节**：v1 只有目录级描述。v2 为阶段 A 写出文件清单、依赖锁定、逐条运行命令与证据格式。
3. **计划引用的路径被 Git 忽略**：加入精确放行步骤与验证命令。
4. **Unity 版本处理不当**：v1 说“不兼容就退回 2022.3 LTS”。跨大版本降级 Unity 工程有风险，v2 改为先做兼容性验证再固定版本。

**v3（相对 v2）修正独立审查发现的九个问题：**

1. **场景 7 无归属**：v2 让 C 覆盖场景 1/3/4、D 覆盖 2/5/6/8/9/10，却同时要求“全部十条场景可复现”，自相矛盾。v3 把场景 7 明确归入 D，并新增“十条场景归属总表”。
2. **设计门槛缺失**：负例 0 设备误操作、报告中“误操作次数”、10 次播报回听在 v2 全篇缺失，且 v2 自称“已移到 B/C”与实际不符。v3 新增“设计门槛覆盖总表”并逐项落到阶段。
3. **音频仍会入库**：v2 只忽略了 `cases/audio/`，实测 `cases/wake-pos-*.wav`、`cases/noise-30min/*.wav`、`reports/artifacts/tts/*.wav` 的 `check-ignore` 退出码均为 1（会被提交）；且 v2 所称“音频后缀已忽略”不成立（`.gitignore` 当时没有音频后缀规则）。v3 在 `.gitignore` 增加 `*.wav/*.pcm/*.mp3/*.m4a/*.flac` 全局忽略。
4. **§3 定性错误**：P1 规则已由 `4886870` 生效，v3 把该节从“修改”改为“校验”，并更新过期的实测证据与仓库现状。
5. **P3 目录清单不全**：补齐 `cases/audio`、`cases/noise-30min`、`reports/artifacts/tts`。
6. **自动化/故障测试无归属**：设计第 10 节要求的会话单测、模型服务失败、播报回执、替身＋真实模型验证、TTS 失败在 v2 没有阶段产出，v3 新增对应表。
7. **E 阶段无通过门槛**：v3 补上不崩溃、不显存耗尽与端到端冷/热启动记录。
8. **播报链路未定义**：C 门槛要求场景 3，但没有任何阶段定义 Unity↔TTS 播报接口与串行队列语义。v3 新增“播报链路与回执语义”一节。
9. **措辞含糊**：“再换候选模型”“冲突”“语料未定义”等已改为可判定表述。

**v4（相对 v3）修正第二轮独立审查发现的九项问题：**

1. **Unity 工程无路径且被忽略（高）**：新增 **3.0 项目路径定义**，把 Unity 工程固定为 `apps/unity-house/`；`.gitignore` 放行 `Assets/`、`Packages/`、`ProjectSettings/`、项目 `.gitignore` 与 `README.md`，并再次排除生成目录。
2. **放行面过窄（高）**：补充 `apps/voice-service/tests/`、`apps/home-service/config/`、两个服务的 `pyproject.toml` 与 `README.md`；P2 清单从 22 条扩到 44 条，覆盖这些路径，避免“文件写了却静默不入库”。
3. **自然语言跨房间通知无验收（中）**：设计称其为核心体验，但 v3 只在 C 面板层验证机制。v4 在 D 新增第 8 条，端到端验收“叫爸爸和孩子吃饭”，含位置未知询问、不自动全屋广播、禁止“对方听到了/来了”。
4. **六处锚点错（中）**：修正 2.2、2.3 中对 B/C/D 子项的引用与 C 子节编号（C6/C7/C8 → C8/C9/C10）。
5. **显存判据无效（中）**：定义统一阈值 **6 GiB**（为 Unity 与检测留约 2 GiB），A10 改为“确认未意外占用 GPU，若走 GPU 则峰值 < 6 GiB”，E 阶段以 30 分钟采样峰值为判据。
6. **“期望 0”过弱（低）**：改为“必须为 0，否则该阶段不通过”。
7. **C9 措辞冲突（低）**：“10 个不同房间”与“只有三个房间”矛盾，改为“10 个房间与内容组合”。
8. **A9 无阈值（低）**：补“≥ 18/20 且误触发少于成功次数，否则判未通过”。
9. **其他（低）**：边界表加注以 2.1 为准；§1 增列 `88cc477`；P3 增建 `models/` 与 `cases/self-trigger/`；A8 自触发录音输出改到独立子目录，避免与用例混放。

**v5（相对 v4）收尾第三轮独立复核指出的文档一致性问题（均不阻塞执行）：**

1. **P2 计数与清单不符**：v4 文中写 44 条，脚本字面实际是 41 条。v5 补上漏列的 `apps/unity-house/Build/`、`Obj/`（应忽略）与 `apps/home-service/README.md`、`apps/unity-house/.gitignore`、`apps/unity-house/README.md`（应放行），并据实改为 **26 应忽略 + 20 应放行 = 46 条**；实测 46/46 通过。
2. **悬空引用**：`3.2 的模式` 指向不存在的小节，改为“下方 `/apps/*/*` 的放行模式”。
3. **规则片段与真实文件不一致**：3.1 片段补上 8 条 Unity 生成目录排除。
4. **§1 未含最新提交**：补 `b911f39`，并说明本文档当前修订将在下一次提交记录。
5. **显存采样基准混淆**：明确 A 阶段用 A7 单进程环路采样，E 阶段才用 30 分钟并行峰值。
6. **A9 阈值偏弱**：由“误触发少于成功次数”收紧为**误触发必须为 0**，数据引用 A8 负例。

第三轮复核结论为「可以照着执行」，上述均为文档一致性问题。

## 0. 执行原则

- 每个任务必须有“运行什么命令、看到什么算通过”；未验证不得声明完成。
- 前一阶段门槛未通过，不进入下一阶段。
- 失败如实记录到报告，不删用例、不降低门槛、不用演示代替测试。
- 密钥、模型权重、日志、录制的音视频不进入 Git。
- 涉及安装依赖与下载模型的任务，执行前需用户确认当前阶段。

## 1. 现状（只读检查，2026-09-12）

| 项目 | 结果 |
| --- | --- |
| 仓库 | `main` 跟踪 `origin/main`；`dd03a75` 设计、`1ac0619` 计划 v1、`4886870`/`152b406` 计划 v2、`88cc477` 计划 v3、`b911f39` 计划 v4；工作树干净 |
| 默认 Python | 3.13.2（`E:\py\python.exe`），已有 numpy、onnxruntime、sounddevice、funasr、torch、opencv 等 |
| 备用 Python | 3.11.9（`C:\Users\1\AppData\Local\Programs\Python\Python311\python.exe`），仅有 pip 24.0 |
| 音频 | PortAudio V19.7.0 可用；默认输入为「麦克风阵列（网易虚拟音频设备）」，默认输出为 Realtek 扬声器；Realtek 物理麦克风可选 |
| Unity | `2022.3.47f1c1` 与 `6000.0.23f1c1` 均在 `D:\unity\edition`，两者都有 `windowsstandalonesupport` |
| 其他 | FFmpeg 8.0.1、.NET SDK 9.0.305 已装；空闲内存约 5.4 GiB，空闲显存约 6.4 GiB |

尚未安装或运行任何候选模型，识别率、延迟、显存占用均未实测。

## 2. 阶段划分与依赖

| 阶段 | 目标 | 依赖 | 关键产物 |
| --- | --- | --- | --- |
| A | 本地语音验证（离线模型＋最小实时语音环） | 无 | 语音验证报告＋实测数据 |
| B | 家庭状态与受限工具服务（纯文字） | A 通过 | 可独立测试的本地服务＋测试报告 |
| C | 最小 Unity 小屋（测试面板驱动） | B 通过 | 可拖动人物、设备随指令变化 |
| D | 云端 Agent 与人物检测（自然语言端到端） | C 通过 | 完整语音—决策—执行链路 |
| E | 并行与故障验收 | D 通过 | 验收报告 |

**各阶段验收边界（避免提前验收未实现的功能）**

| 能力 | 最早可验收阶段 |
| --- | --- |
| 离线识别、离线合成、唤醒正负例 | A |
| 实时环路（固定回复、无家居状态） | A |
| 自触发防护（声学回环离线判定） | A |
| 设备误操作、离线设备、版本冲突 | B |
| 场景 1/3/4（经测试面板或文字指令）、10 次播报回听 | C |
| 场景 2/5/6/7/8/9/10（自然语言端到端） | D |

> 本表是边界速览，完整逐条归属以 **2.1** 为准。

### 2.1 十条场景归属总表（设计第 10 节）

| # | 场景 | 归属阶段 | 判定方式 |
| --- | --- | --- | --- |
| 1 | “打开客厅灯”只改变指定设备，Unity 与权威状态一致 | C | 测试面板按钮 |
| 2 | “有点热”查正确房间状态后有限调整或追问 | D | 自然语言 |
| 3 | 跨房间找人通知；同房间去重，不同房间依次播报 | C | 测试面板触发播报任务，验证队列与回执 |
| 4 | 移动人物后同一请求使用新位置 | C | 拖动后重复测试面板指令 |
| 5 | 澄清“哪个房间？”→“卧室”完成原请求；同会话承接；退出后不沿用 | D | 自然语言 |
| 6 | 设备离线、人物位置未知、云端失败不假报成功 | D | 自然语言＋故障注入（离线路径已在 B 单元/集成测试覆盖） |
| 7 | **唤醒后连续两轮交流；超时/退出后再次操作需要唤醒** | **D** | 一次唤醒内完成两轮；等待超时后确认需重新唤醒 |
| 8 | 播报不自触发；保护间隔后的正常讲话可识别 | D | 实时回环（离线判定见 A8） |
| 9 | 普通闲聊、环境噪声与电视负例记录误唤醒/误操作 | D | 负例会话；离线部分见 A8，设备误操作计数在 D |
| 10 | 手动视觉绑定在轨迹丢失后解除，摄像头观察不改写模拟房间 | D | 摄像头＋拖动交叉验证 |

场景 7 在 v2 中无归属，是本次修订修正的主要矛盾点。A7 的“可连续复现 3 轮”指重复整轮会话，**不等价于同一唤醒内连续两轮**，因此不能替代场景 7。

### 2.2 设计门槛覆盖总表

| 设计门槛（设计第 10 节） | 落实位置 |
| --- | --- |
| 30 条语句关键信息正确 ≥ 27 | A5（关键信息命中 ≥ 27/30） |
| 20 条 TTS 文本试听 | A6（另收紧为可理解且读音正确 ≥ 18） |
| 20 次唤醒 ≥ 18 成功 | A8 |
| 30 分钟无意图音频负例 | A8（语料来源见 A8；设备误操作计数在 B/C/D 各阶段记录） |
| 10 次播报回听 | **C9**（v2 缺失，v3 补上） |
| 报告误唤醒、误操作、自触发次数 | A11（离线部分）＋ B、C、D、E 报告（在线部分） |
| 有限负例不得出现设备误操作 | B（阶段门槛末尾）＋ C10 ＋ D（第 9 条）＋ E4；**判据：误操作次数必须为 0，否则该阶段不通过** |
| 播报自触发 0 次 | A8（声学回环）＋ D（第 9 条，会话内）；判据同上，必须为 0 |
| Unity 1080p ≥ 30 FPS | C10 |
| 并行 30 分钟；RAM/VRAM 峰值、帧率、识别耗时、TTS 首次出声 | E1 |
| 端到端冷/热启动延迟 | E2（各 ≥ 5 次取中位数） |
| 不得崩溃或显存耗尽 | A10（进程级）＋ E1（整机并行，阈值见 A10/E1） |

### 2.3 自动化与故障测试归属（设计第 10 节“自动化边界”）

| 设计要求 | 归属 |
| --- | --- |
| 状态/策略单测（目标解析、去重、范围、版本冲突、操作去重） | B5 |
| 会话单测（唤醒、20 秒超时、退出、澄清承接、播放期间禁识别） | D6（纯逻辑部分用替身先行） |
| 集成测试（Unity 连接、快照/增量/重连） | C3 |
| 集成测试（模型服务失败、播报回执） | C8（回执）＋ D7（模型失败） |
| 外部模型用可控替身测试程序逻辑，另做真实模型验证 | D7 |
| 故障测试（离线、重复请求、服务重启） | B6 |
| 故障测试（超时提交前/后） | B6（提交前）＋ D7（提交后，跨服务链路） |
| 故障测试（TTS 失败） | C8（合成失败即 failed）＋ D7（会话内反馈） |

## 3. 项目路径与放行校验

### 3.0 项目路径定义（本计划后续引用均以此为准）

| 用途 | 路径 |
| --- | --- |
| 离线检查脚本、测试用例 | `tools/voice-check/`（音频在 `cases/` 下的 audio、noise-30min、self-trigger 子目录） |
| 语音服务（唤醒/VAD/ASR/TTS/会话环） | `apps/voice-service/` |
| 家庭状态与工具服务 | `apps/home-service/` |
| Unity 小屋工程 | `apps/unity-house/` |
| 设计、计划、核验记录、报告 | `docs/superpowers/` |
| 模型权重（不入库） | **默认 `D:\smart-home-models\`**（用户要求放 D 盘），可用环境变量 `SMART_HOME_MODELS_DIR` 覆盖 |

**模型路径约定**：模型文件一律不入库，且默认存放在 D 盘而非仓库所在盘。脚本通过 `SMART_HOME_MODELS_DIR` 读取根目录，默认值为 `D:\smart-home-models`；仓库内 `models/` 只作为回退位置（若该盘不可用），并且在 `.gitignore` 中已被忽略。下载前先确认目标盘可用空间。

**Unity 工程必须是 `apps/unity-house/`。** `Library/`、`Temp/`、`Obj/`、`Build/`、`Builds/`、`Logs/`、`UserSettings/`、`.vs/` 是生成物，不入库；`Assets/`、`Packages/`、`ProjectSettings/` 与 `.meta` 必须入库。

### 3.1 规则状态

允许列表、音频忽略与 Unity 工程规则已随 `4886870`、`152b406`、`88cc477`、`b911f39` 及本次修订生效。本节是回归校验，**正常执行阶段不需要再改 `.gitignore`**；但若新增目录（例如把测试放到 `apps/voice-service/tests/` 之外的新位置），必须先按下方 `/apps/*/*` 的放行模式添加规则，并把该路径加入 P2 清单，否则会出现“文件写了却静默不入库”。

规则现状（截至本次修订）：

```gitignore
!/tools/
/tools/*
!/tools/voice-check/
/tools/voice-check/cases/audio/
/tools/voice-check/cases/noise-30min/
/tools/voice-check/cases/self-trigger/

!/apps/
/apps/*

!/apps/voice-service/
/apps/voice-service/*
!/apps/voice-service/src/
!/apps/voice-service/tests/
!/apps/voice-service/requirements.lock.txt
!/apps/voice-service/pyproject.toml
!/apps/voice-service/README.md
/apps/voice-service/.venv/

!/apps/home-service/
/apps/home-service/*
!/apps/home-service/src/
!/apps/home-service/tests/
!/apps/home-service/config/
!/apps/home-service/requirements.lock.txt
!/apps/home-service/pyproject.toml
!/apps/home-service/README.md

!/apps/unity-house/
/apps/unity-house/*
!/apps/unity-house/Assets/
!/apps/unity-house/Packages/
!/apps/unity-house/ProjectSettings/
!/apps/unity-house/.gitignore
!/apps/unity-house/README.md
# Re-assert generated-folder exclusions for this project.
/apps/unity-house/Library/
/apps/unity-house/Temp/
/apps/unity-house/Obj/
/apps/unity-house/Build/
/apps/unity-house/Builds/
/apps/unity-house/Logs/
/apps/unity-house/UserSettings/
/apps/unity-house/.vs/

# Local diagnostics, recordings and audio artifacts.
*.log
*.wav
*.pcm
*.mp3
*.m4a
*.flac
recordings/
.superpowers/
```

要点：
- `/apps/*/*` 这类规则会忽略目录内所有文件，因此每个需要入库的子目录都要单独放行；音频则按**后缀全局忽略**，覆盖所有子目录。
- 若将来某个音频确实要入库，必须写显式 `!` 规则并在此记录原因。
- `cases/audio/`、`cases/noise-30min/`、`cases/self-trigger/`、`reports/artifacts/tts/` 这些子目录本身不影响忽略结果，但需要在 P3 创建，便于脚本直接写入。

### P2. 校验命令与预期结果（必须用 `-q` 的退出码判断）

`git check-ignore` 的退出码：`0` = 被忽略，`1` = 未被忽略。

**必须加 `-q`。** 本机实测（同一条已放行路径）：

```text
git check-ignore -q -- apps/voice-service/requirements.lock.txt   → exit=1（正确：未忽略）
git check-ignore    -- apps/voice-service/requirements.lock.txt   → exit=1
git check-ignore -v -- apps/voice-service/requirements.lock.txt   → exit=0 且打印 !.gitignore:27:…
```

`-v` 只要匹配到任意规则就返回 0，命中否定规则（`!`）时同样如此，会让人把“已放行”误判成“被忽略”。因此 `-v` 只用于**查看是哪条规则匹配**，不能用于判断是否被忽略。

```powershell
$mustIgnore = @(
 '.env','models/x.onnx','recordings/a.wav','debug.log','upstream-src/README.md','task_plan.md',
 'apps/voice-service/__pycache__/x.pyc','apps/voice-service/config.env',
 'tools/voice-check/cases/wake-pos-001.wav','tools/voice-check/cases/noise-30min/a.wav',
 'tools/voice-check/cases/audio/asr-001.wav','docs/superpowers/reports/artifacts/tts/tts-001.wav',
 'apps/voice-service/src/probe.wav','docs/superpowers/reports/artifacts/loop-session.log',
 'apps/voice-service/.venv/pyvenv.cfg',
 # Unity generated folders and 3D/model build output
 'apps/unity-house/Library/x.dat','apps/unity-house/Temp/x','apps/unity-house/UserSettings/x.dwlt',
 'apps/unity-house/Build/x','apps/unity-house/Obj/x','apps/unity-house/Builds/x.exe','apps/unity-house/Logs/x.log',
 # old/root Unity layouts must not silently become tracked
 'unity/Assets/x.cs','SmartHome/Assets/x.cs','Assets/x.cs','ProjectSettings/ProjectVersion.txt'
)
$mustAllow = @(
 'tools/voice-check/cases/asr-30.tsv','tools/voice-check/cases/tts-20.tsv',
 'apps/voice-service/src/loop.py','apps/voice-service/requirements.lock.txt',
 'apps/voice-service/tests/test_session.py','apps/voice-service/pyproject.toml',
 'apps/voice-service/README.md',
 'apps/home-service/src/state.py','apps/home-service/tests/test_state.py',
 'apps/home-service/config/comfort.json','apps/home-service/pyproject.toml','apps/home-service/README.md',
 'apps/unity-house/Assets/Scenes/Main.unity','apps/unity-house/Assets/Scenes/Main.unity.meta',
 'apps/unity-house/Packages/manifest.json','apps/unity-house/ProjectSettings/ProjectVersion.txt',
 'apps/unity-house/.gitignore','apps/unity-house/README.md',
 'docs/superpowers/notes/n.md','docs/superpowers/reports/artifacts/r.json'
)
foreach ($p in $mustIgnore) { git check-ignore -q -- $p; if ($LASTEXITCODE -eq 0) { "OK ignored: $p" } else { "FAIL should-ignore: $p" } }
foreach ($p in $mustAllow)  { git check-ignore -q -- $p; if ($LASTEXITCODE -eq 1) { "OK allowed: $p" } else { "FAIL should-allow: $p" } }
```

**通过门槛**：**26 条应忽略与 20 条应放行全部输出 `OK`，`FAIL` 数为 0**（共 46 条）；随后 `git add -A -- apps tools docs/superpowers`，`git diff --cached --name-only` 中不出现 `__pycache__`、`config.env`、`.env`、模型、音频或 Unity 生成目录。

**实测结果**：46/46 通过，`FAILURES=0`（第三轮独立复核复跑一致）。特别确认 `apps/voice-service/tests/`、`apps/home-service/config/`、`apps/unity-house/Assets|Packages|ProjectSettings` 均可入库，而 `unity/`、根 `Assets/`、`ProjectSettings/` 等旧布局仍被忽略。

### P3. 生成计划所需目录

```powershell
New-Item -ItemType Directory -Force -Path 'tools/voice-check/cases/audio','tools/voice-check/cases/noise-30min','tools/voice-check/cases/self-trigger','apps/voice-service/src','apps/voice-service/tests','apps/home-service/src','apps/home-service/tests','apps/home-service/config','models','docs/superpowers/notes','docs/superpowers/reports/artifacts/tts' | Out-Null
```

`docs/superpowers/reports/artifacts/` 已随计划提交说明文件，保证目录存在于仓库中。`models/` 已被忽略（只放本地权重）；`tts/`、`audio/`、`noise-30min/`、`self-trigger/` 里的音频由音频后缀规则忽略。Unity 工程目录在阶段 C 由 Unity Hub 创建，不预先建空目录。

---

## 阶段 A：本地语音验证

阶段 A 只回答一个问题：**这台电脑上，本地唤醒、识别、合成能不能达到设计门槛。** 不接家居状态、不接云端、不做自然语言决策。

### A1. 独立环境

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

**通过门槛**：`.\.venv\Scripts\python.exe -V` 输出 `Python 3.11.9`；`.venv/` 未被 Git 跟踪。

### A2. 依赖安装与锁定

安装到 `.venv`，不触碰全局 3.13：

```powershell
.\.venv\Scripts\python.exe -m pip install sherpa-onnx sounddevice soundfile numpy
.\.venv\Scripts\python.exe -m pip freeze > apps/voice-service/requirements.lock.txt
```

`sherpa-onnx` 同时提供 KWS、VAD、ASR、TTS，首轮不需要 PyTorch/CUDA 依赖。

**通过门槛**：`pip check` 无冲突；`.\.venv\Scripts\python.exe -c "import sherpa_onnx, sounddevice, soundfile"` 无报错；锁定文件已生成。

### A3. 模型核验与下载（先核验，再下载）

| 用途 | 候选 | 默认唤醒词 |
| --- | --- | --- |
| 唤醒 | sherpa-onnx KWS zipformer 中文 3.3M | 待定，见 A9 |
| 端点检测 | Silero VAD | — |
| 识别 | SenseVoiceSmall int8 | — |
| 合成 | Kokoro 中英 v1.1（int8 优先） | — |

**备选（仅在主选不达标时启用，不预先下载）**：

| 用途 | 备选 |
| --- | --- |
| 识别 | faster-whisper small / medium（CTranslate2，CPU int8） |
| 合成 | CosyVoice2-0.5B（资源占用更高，仅在 Kokoro 中文听感不达标时评估） |
| 唤醒 | sherpa-onnx KWS zipformer 中英 3M 2025-12-20 |

核验清单（每项都要有结论）：
1. 下载地址可访问，文件大小与官方文档一致。
2. 许可证名称、是否允许预期用途、是否要求署名。
3. Windows + Python 3.11 + sherpa-onnx 所需 API 存在（KWS 的 `text2token` 可用）。

核验结论写入 `docs/superpowers/notes/2026-09-12-model-review.md`，含来源链接与核验日期。

**下载位置（用户要求）**：模型默认下载到 `D:\smart-home-models\`，不放仓库所在盘。运行时通过环境变量 `SMART_HOME_MODELS_DIR` 指定，脚本默认值即该路径；仓库内 `models/` 仅作回退，且已被忽略。下载前用 `Get-PSDrive` 确认 D 盘可用空间，并记录每个模型的实际大小与总占用。

**通过门槛**：四个模型的地址、大小、许可证均有明确结论；许可证不清晰的不下载，并在报告中说明替代方案。

### A4. 录音协议（A5/A6 共用的前提）

A4/A5/A6 都需要真实录音，必须先统一录音与文件格式：

- 录音工具：`tools/voice-check/record_wav.py`（同一脚本也用于 A8 唤醒正例）。
- 设备：显式指定 Realtek 物理麦克风，**不使用当前默认的网易虚拟音频设备**，也不修改系统默认。设备全名已实测存在：「麦克风阵列 (Realtek(R) Audio)」与「扬声器 (Realtek(R) Audio)」，各 1 个，可唯一定位。
- 格式：16 kHz、单声道、16-bit PCM WAV（与 KWS/ASR 输入要求一致，避免重采样引入差异）。
- 命名：`tools/voice-check/cases/audio/asr-001.wav` 依次编号；唤醒正例放 `tools/voice-check/cases/wake-pos-001.wav`（A8 用）；唤醒词对比 `tools/voice-check/cases/wake-<词>-001.wav`。
- 每次录音前打印所选用设备名、采样率与时长，录音后回放确认无静音、无削波。

命令：

```powershell
.\.venv\Scripts\python.exe tools/voice-check/record_wav.py --input-device "麦克风阵列 (Realtek(R) Audio)" --seconds 4 --out tools/voice-check/cases/audio/asr-001.wav
```

**通过门槛**：30 条语句全部录制完成，逐条回放可听清；`cases/asr-30.tsv` 的 `wav路径` 与实际文件名一一对应。音频不入库——由 `.gitignore` 的 `*.wav` 等后缀规则全局忽略（见第 3 节），不需要逐目录配置。

### A5. 离线识别验证（门槛项）

脚本：`tools/voice-check/check_asr.py`

- 输入：`tools/voice-check/cases/asr-30.tsv`（制表符分隔：`id`、`wav路径`、`期望关键信息`）。
- 行为：逐个 wav 调用 sherpa-onnx 识别，输出 `id`、实际转写、期望、关键信息是否命中。
- 输出：`docs/superpowers/reports/artifacts/asr-results.json` 与控制台表格。

用例至少 30 条，必须覆盖：否定（“不要开灯”）、人名（“叫爸爸吃饭”）、房间（“客厅”“卧室”）、数字（“二十六度”）、模糊意图（“有点热”）。

命令：

```powershell
.\.venv\Scripts\python.exe tools/voice-check/check_asr.py --cases tools/voice-check/cases/asr-30.tsv --out docs/superpowers/reports/artifacts/asr-results.json
```

**通过门槛**：关键信息命中 ≥ 27/30。未达标时按顺序处理：(1) 排查麦克风、距离与录音格式；(2) 改用 A3 备选列的模型（faster-whisper small 或 medium）；(3) 仍不达标则如实记录未通过，并附失败条目，不降低此门槛。

### A6. 离线合成验证（门槛项）

脚本：`tools/voice-check/check_tts.py`

- 输入：`tools/voice-check/cases/tts-20.tsv`，**由文字撰写，不需要录音**。
- 行为：逐条合成到 `docs/superpowers/reports/artifacts/tts/`。
- 人工试听：确认人名、数字、多音字读音与可理解性，逐条记录通过/不通过及原因。

**通过门槛**：20 条中可理解且关键字段读音正确 ≥ 18 条；不通过项列出具体问题字词。

### A7. 最小实时语音环（固定回复）

脚本：`apps/voice-service/src/loop.py`

显式选择设备（不修改系统默认）：

```powershell
.\.venv\Scripts\python.exe apps/voice-service/src/loop.py --input-device "麦克风阵列 (Realtek(R) Audio)" --output-device "扬声器 (Realtek(R) Audio)"
```

行为（**不接家居状态、不接云端**）：
1. 待机：持续 KWS 唤醒检测。
2. 唤醒：播放提示音，进入听取。
3. 听取：VAD 分句 → 识别 → 打印转写 → 播放**固定测试回复**（如“收到”）。
4. 播报期间：丢弃麦克风输入，不喂给 KWS 与识别。
5. 退出：20 秒无新语音回到待机。
6. 日志：每条事件写入 `docs/superpowers/reports/artifacts/loop-session.log`，含时间戳、状态、转写、耗时。

**通过门槛**：唤醒 → 说一句 → 听到固定回复 → 20 秒后回到待机，可连续复现 3 轮；终端可见状态与转写。

### A8. 唤醒与自触发验证

脚本：`tools/voice-check/check_wake.py`

- 输入：`--positives N`（实时唤醒次数）、`--negatives-dir`（负例音频目录）、`--self-trigger-out`（声学回环录音输出目录）。
- 行为：正例逐次调用 KWS 并记录是否触发；负例对目录内每个音频文件离线运行 KWS，累计触发次数；自触发录音再离线跑 KWS 与 ASR，输出是否出现唤醒词或指令文字。
- 输出：`docs/superpowers/reports/artifacts/wake-results.json`，含正例成功数、负例触发数、自触发命中文本。

1. **正例**：连续 20 次主动唤醒，录音存 `tools/voice-check/cases/wake-pos-###.wav`，记录成功次数。
2. **负例**：连续播放 30 分钟无意图音频（对话、视频），**语料来源须在报告中写明具体来源与文件清单**（本机播放的视频/播客，或公开音频）；文件放 `tools/voice-check/cases/noise-30min/`，并记录实际总时长。
3. **自触发（声学回环）**：正常音量播放固定回复，同时用麦克风录制；对录制文件离线运行 KWS 与识别，检查是否出现唤醒或指令文字。

命令：

```powershell
.\.venv\Scripts\python.exe tools/voice-check/check_wake.py --positives 20 --negatives-dir tools/voice-check/cases/noise-30min --self-trigger-out tools/voice-check/cases/self-trigger --out docs/superpowers/reports/artifacts/wake-results.json
```

**通过门槛**：20 次唤醒中 ≥ 18 次成功；负例 30 分钟误唤醒次数记录并报告（无硬门槛，用于评估）；自触发录制中不出现指令文字。**注意**：A 阶段尚无家居设备，因此本阶段不产生“设备误操作”数据，该计数在 B/C/D 记录，见 B 阶段门槛、C10 与 D 阶段门槛。

### A9. 唤醒词选择

对候选唤醒词各录 20 次正例，比较成功次数与误触发。**候选清单固定为**：「小屋小屋」与「你好小屋」；两者都不达标时，再在报告中提出第三候选并说明理由。选定一个写入 `apps/voice-service/src/config.py`，对比数据写入报告。

**达标阈值**（与 A8 一致）：所选唤醒词需 ≥ 18/20 成功；**在其正例采集期间不得出现误触发（误触发次数必须为 0）**，误触发数据以 A8 的负例结果为准。若两个候选都不达标，本项判为未通过并如实报告，不得直接沿用示例词“小屋小屋”而不做说明。**唤醒词不是身份认证**，报告中要写明这一点。

### A10. 延迟与资源

在实时环路中记录：冷启动首次识别耗时、热启动识别耗时、TTS 首次出声时间、进程峰值内存（RSS）、峰值显存、GPU 是否被占用。

**通过门槛**：
- 不崩溃、不出现 OOM 或驱动重置。
- 首轮语音链路按设计选用 **CPU**，因此需记录并确认**没有意外占用 GPU**（若实测走了 GPU，则峰值显存必须 < 6 GiB，为 Unity 与检测留出余量）。
- 数据完整记录。识别与合成的具体秒数不设门槛，数值交由用户判断是否可接受。

**显存/内存采样基准（区分阶段）**：
- **A 阶段（本步）用 A7 实时环路的单进程运行采样**：此时没有 Unity 并行，因此记录的是语音链路的独立占用。
- **E 阶段才用 30 分钟 Unity 并行运行的 `nvidia-smi` 采样峰值**。
- **阈值**：显存 6 GiB。本机显存约 8 GiB，扣除系统与显示占用后保留约 2 GiB 给 Unity 场景与人物检测；超过即视为余量不足，判为不通过。以采样峰值为准，不用单次瞬时值。

### A11. 阶段 A 产出与放行

报告：`docs/superpowers/reports/2026-09-12-phase-a-voice-report.md`

必须包含：环境与依赖版本、四个模型核验结论、30 条转写逐条结果、20 条 TTS 试听结果、唤醒正负例（含负例语料来源与总时长）与自触发结果、延迟与资源表（含峰值显存）、失败案例、误唤醒次数、明确的“通过/不通过”结论。

**未通过时不得进入阶段 B。**

---

## 阶段 B：家庭状态与受限工具服务

纯文字驱动，不接语音，不接 Unity。

1. 数据模型：房间、人物、设备、视觉观察、会话、操作（字段见设计文档第 5 节）。
2. 权威状态服务：快照、增量、版本号、重连接口。
3. 六个工具：查询房间、查询人物位置、查询设备状态、设置灯光、设置空调、房间播报。
4. 服务端校验：对象存在、参数范围、房间归属、在线状态、前置版本、操作 ID 去重。
5. 单元测试：目标解析、房间去重、范围校验、版本冲突、操作去重。
6. 集成测试：模拟设备离线、超时、部分成功、重复请求、服务重启。
7. 接口默认绑定 `127.0.0.1`，不开放局域网。
8. 代码放在 `apps/home-service/`（放行规则已生效，见第 3 节）。

**“播报”在本阶段的判定**：B 只产生播报**任务**，不播放音频。因此本阶段的通过标准是任务层面的可判定状态，而不是“听到声音”：

- 任务状态机：`queued` → `playing` → `played` → `failed`（第五个终态 `cancelled` 可选）。
- 按房间去重：同一房间多个目标合并为一个任务。
- 不同房间：按接收顺序串行排队，一次只有一个任务处于 `playing`。
- 只有 `played` 才允许回复“已播报”；`queued` 只能说“已安排播报”；`failed` 必须报失败。
- 真实音频回执在 C 阶段接上 Unity 播放后验证。

**通过门槛**：`pytest` 全绿且离线、冲突、去重路径均有对应用例；用文字请求脚本可复现“开灯”“调空调”，并产生符合上述状态机与去重规则的播报任务（含同房间合并、跨房间串行、`failed` 不冒充成功）。本阶段记录**设备误操作次数，必须为 0，否则本阶段不通过**。另需满足设计 139 的“不自动全屋广播”：位置未知的目标不得触发全屋播报任务（有对应用例）。

---

## 阶段 C：最小 Unity 小屋（测试面板驱动）

1. **版本决策（已提前固定为 2022.3）**：用户明确要求优先使用 **Unity 2022.3**，且本机 `D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe` 与 `windowsstandalone` 模块已确认存在。因此工程**直接用 2022.3.47f1c1 创建**，不再走“先用 Unity 6 再回退”的路径；这是用户选定的目标版本，不是降级。
   - 仍需在 2022.3 下验证：JSON 序列化（`JsonUtility` 或手写解析）、WebSocket 客户端、以及 API 兼容级别下能否编译目标 .NET 代码。
   - 若 2022.3 下编译或连接失败，**先排查配置**（API Compatibility Level 设为 .NET Standard 2.1、包版本与编辑器匹配），不擅自改换编辑器版本。
   - 工程入库范围见第 3.0 节。
2. 三个房间、灯、空调、房间播报点；人物用圆点＋名字。
3. 连接状态服务：初始快照、增量同步、断线重连。
4. 拖动人物提交位置；模拟室温由测试面板修改。
5. 语音区、摄像头区、执行记录区界面骨架（摄像头先留空）。
6. 用单个扬声器按房间播报并高亮目标房间。
7. 本阶段通过**测试面板按钮与文字输入**验证场景，不要求自然语言。

### C8. 播报链路与回执语义（本节是场景 3 可判定的前提）

```text
状态服务（播报任务 queued）
        ↓ 串行队列取队首
Unity 请求 TTS 服务合成该条文本
        ↓ 返回音频（或失败）
Unity 播放，房间播报点高亮，界面显示“正在播报：<房间>”
        ↓ 播放结束回调
Unity 上报 played/failed + 任务 ID
        ↓
状态服务更新任务终态；仅 played 允许“已播报”措辞
```

- 队列由状态服务维护，Unity 只消费队首任务，**不允许并行播放**。
- Unity 上报必须携带任务 ID；ID 不匹配或超时上报视为失败，不自动重播。
- TTS 合成失败 → 任务直接 `failed`，不播放、不重试、不声称成功。
- 高亮与界面文本必须与实际播放的房间一致；单个扬声器无法物理分区，界面须标注“模拟分区播报”。
- **回复措辞约束（设计 139/141）**：位置未知的目标只说明“未通知该目标并询问”，不得触发全屋广播；任何阶段都不得说“对方听到了”或“对方来了”，验收时逐条检查实际回复文本。

### C9. 播报回听（设计门槛项）

按设计第 10 节要求做 **10 次播报回听**：覆盖 10 个不同的**房间与内容组合**（房间只有三个，因此组合按“房间 × 内容 × 多目标去重/串行”构造，例如同一房间去重播报、两个不同房间串行播报、位置未知目标不播报），人工确认实际播放内容与目标房间一致、高亮正确、状态从 `queued` 正确走到 `played`。

**通过门槛**：10 次回听全部内容与目标房间一致，任务状态流转正确；出现任意一次错房间、错内容或状态不推进即为不通过。本阶段记录**设备误操作次数**与**播报回执错误次数，两者必须为 0，否则本阶段不通过**。

### C10. 阶段 C 通过门槛

设计文档「必须通过的场景」第 **1、3、4** 条可复现（含移动人物后使用新位置）；C9 十次回听通过；设备误操作与播报回执错误均为 0；Unity 1080p ≥ 30 FPS。

---

## 阶段 D：云端 Agent 与人物检测（自然语言端到端）

1. 用户选定云端 LLM 厂商并开通 API；设置预算告警与应用侧限额。
2. 密钥写入 `.env`（已忽略），不进入 Unity 包与日志。
3. Agent 编排：工具选择、超时、工具轮数上限、澄清状态、操作 ID 复用。
4. 接入语音会话状态机：静音、待机、听取、处理、播报、20 秒退出。
5. 接入本地人物检测与手动身份绑定；轨迹丢失解除绑定；视觉不覆盖模拟位置。
6. **会话单测先行**（纯逻辑，可用替身）：唤醒、20 秒超时、退出、澄清承接、播放期间禁识别。测试放 `apps/voice-service/tests/`。
7. **故障测试**：TTS 失败、模型服务失败、超时提交后；外部模型先用可控替身测程序逻辑，再做真实模型验证。
8. **自然语言跨房间找人通知**（设计称为核心体验，C 只在面板层验证过机制）：用真实语音说“叫爸爸和孩子吃饭”，验证按目标人物位置选择播报房间、同房间去重、不同房间串行；位置未知时说明未通知并询问；不得自动全屋广播，不得说“对方听到了/来了”。
9. 验证「必须通过的场景」第 **2、5、6、7、8、9、10** 条，其中第 9 条同时统计负例中的设备误操作次数。

**通过门槛**：D 归属的全部场景（2、5、6、7、8、9、10）可复现，加上 C 已验收的 1、3、4，合计十条；第 8 条自然语言跨房间通知端到端通过；含失败与澄清路径；会话单测与故障测试全绿；TTS 失败时保留文字反馈且不重复执行设备操作。本阶段记录**设备误操作次数**、**误唤醒次数**、**播报自触发次数，三者必须为 0，否则本阶段不通过**。

---

## 阶段 E：并行与故障验收

1. Unity ＋ ASR ＋ TTS ＋ 检测并行运行 ≥ 30 分钟，`nvidia-smi` 与进程监控采样记录 RAM/VRAM 峰值、帧率、识别耗时、TTS 首次出声。
2. **端到端延迟必须区分冷启动与热启动**（各测 ≥ 5 次并给出中位数），云端延迟单独测量并报告；由用户确认是否可接受。
3. 汇总报告：`docs/superpowers/reports/2026-09-12-acceptance-report.md`，汇总各阶段记录的误唤醒、**误操作**、自触发次数与 10 次播报回听结果。
4. 未通过项如实列出。

**通过门槛**：
- 30 分钟并行**不崩溃、无 OOM、无显示驱动重置**。
- **峰值显存 < 6 GiB**（阈值定义见 A10），且 1080p 帧率 ≥ 30 FPS。
- 上述指标与冷/热启动延迟齐全；报告中列表完整，未通过项有明确说明。
- **误操作次数、播报自触发次数必须为 0**；误唤醒次数如实报告（负例环境无法保证为 0，不作为门槛）。

---

## 4. 证据与命名约定

| 类型 | 位置 | 说明 |
| --- | --- | --- |
| 测试用例 | `tools/voice-check/cases/` | `.tsv`，含期望关键信息 |
| 机器可读结果 | `docs/superpowers/reports/artifacts/*.json` | 脚本输出 |
| 合成音频 | `docs/superpowers/reports/artifacts/tts/` | 试听用 |
| 会话日志 | `docs/superpowers/reports/artifacts/*.log` | 时间戳、状态、耗时 |
| 阶段报告 | `docs/superpowers/reports/YYYY-MM-DD-*.md` | 结论与失败案例 |
| 模型核验 | `docs/superpowers/notes/YYYY-MM-DD-*.md` | 地址、大小、许可证 |

音频、模型、日志不入库；报告与 JSON 结果入库。

## 5. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 16 GiB 内存不足 | 模型分批加载；先只跑 ASR＋TTS，再逐步加入检测 |
| 默认麦克风是虚拟设备 | 脚本显式选择 Realtek 物理麦克风；不改系统默认 |
| 唤醒误触发偏高 | 调整 boosting score 与触发阈值；仍不达标则如实报告 |
| 模型许可证不清 | 停止下载，先核验许可证再决定 |
| Unity 版本冲突 | 已固定 **2022.3.47f1c1**（用户选定，本机已确认可执行文件与 windowsstandalone 模块存在）；C 阶段只需在 2022.3 下验证 JSON/WebSocket/编译，不再做跨版本回退 |
| 云端账单超预期 | 厂商预算告警＋应用侧请求数与轮数上限 |
| 实时环路回声 | 播报期间丢弃输入；用 A8 离线回环验证 |

## 6. 待用户确认

1. 是否按 A → B → C → D → E 顺序执行。
2. 阶段 A 是否允许安装 Python 3.11 虚拟环境依赖并下载四个模型。
3. Unity 先用 `6000.0.23f1c1` 做兼容性验证吗（冲突则用 2022.3 重建）。
4. 唤醒词候选固定为“小屋小屋”与“你好小屋”，对比后选定。
5. 云端 LLM 厂商留到阶段 D 再定。

## 7. 2026-09-25 全项目复审与优化路线图 → 已移到独立文件

该节于 2026-09-25 追加在本文件末尾，但文件名日期是 09-12，很容易被当成历史内容忽略。
**2026-09-25 已原样迁出**，现址与唯一权威版本：

- [`../2026-09-25-project-roadmap.md`](../2026-09-25-project-roadmap.md)

本文件自 2026-09-25 起移入 `plans/archive/`，只作为 2026-09-12 的历史方案与放行校验记录；
§0–6 的架构假设多数已不成立，当前架构、优先级与完成进展一律以路线图文件和
[`docs/superpowers/README.md`](../README.md) 索引为准。
