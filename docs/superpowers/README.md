# docs/superpowers 索引与状态登记

**这里是一切的入口。** 计划（plans）、设计（specs）、笔记（notes）、报告（reports）混在
这个目录里，文件名日期不等于内容日期，所以状态只以本文件为准。

目录约定：

| 位置 | 含义 |
| --- | --- |
| [`plans/2026-09-25-project-roadmap.md`](plans/2026-09-25-project-roadmap.md) | **当前唯一总体路线图**：现在该做什么只看这一份 |
| `plans/` 根下的其他文件 | 仍在推进的专项计划 |
| [`plans/completed/`](plans/completed/) | **已完成**的计划（文件头写明完成时间与证据） |
| [`plans/archive/`](plans/archive/) | **仅供历史参考**：架构已被取代，只作当时记录 |
| `specs/` `notes/` `reports/` | 设计、过程笔记、验收报告 |

两条读法：

1. **复选框不代表状态。** 本项目的惯例是“只有真人验收后才勾选”，所以多份**已完成**的计划里
   `- [ ]` 仍是空的。状态以本表和文件头的横幅为准。
2. **旧报告的验收结果不能当成当前工作树的结论**，只能作为当时版本的证据。凡是没做过真人
   麦克风/摄像头/Unity 验收的功能，一律写作“已实现，尚未验证”。

---

## 1. 当前路线图（唯一）

| 文件 | 内容 | 状态 | 完成进展 |
| --- | --- | --- | --- |
| [`plans/2026-09-25-project-roadmap.md`](plans/2026-09-25-project-roadmap.md) | P0-1…P2 共 11 项任务、技术栈决策、4 个实施批次；新增 09-30“我的家”视图方向 | 计划 2026-09-25；多数未实施 | P0-1、P0-2 与 P0-6 源码推进；P0-3、P0-4 有实现基础但问题待修复；09-30 网页示意视图源码与本地视觉检查已完成，真实设备验收待做（见路线图进展表） |

## 2. 进行中的专项计划

2026-10-01 提交前回归：终端/HA 90 项、语音 97 项、视觉 22 项、前端 23 项通过，网页构建及 193 个素材校验通过；真实设备与平板体验仍按各专项计划验收。见[GitHub 提交前检查](reports/2026-10-01-github-upload-check.md)。

2026-10-01 素材进度：用户已选择 KayKit 为主选，53 个家具模型和原始贴图已保存，53/53 在浏览器中加载/渲染成功并生成单品预览；18 个优先候选，Kenney 140 个模型保留为储备。当前 Three.js 只有灯开关效果，亮度、空调气流及家具替换仍待实施。见[素材与设备效果计划](plans/2026-10-01-threejs-assets-and-device-effects.md)和[家具库](../../apps/perception-console/scene-assets/README.md)。

终端视觉进度：2026-10-01 已将电脑六个页面统一为“我的家”的深色风格，导航、标题、边距在桌面与平板宽度下保持一致。见[统一主题报告](reports/2026-10-01-console-unified-theme.md)。

2026-10-01 新进度：“我的家”第二版已增加电脑/平板设备手动控制，采用胶囊开关、亮度滑轨和操作后自动同步；APK 已更新，真实设备效果与平板手感待验收。见[第二版报告](reports/2026-10-01-manual-device-control-v2.md)。

| 文件 | 计划时间 | 状态 | 下一步 / 卡在哪 |
| --- | --- | --- | --- |
| [`plans/2026-10-01-threejs-assets-and-device-effects.md`](plans/2026-10-01-threejs-assets-and-device-effects.md) | 2026-10-01 | KayKit 主选 53/53 浏览器加载渲染通过；**场景接入与设备效果扩展待实施** | 18 个 KayKit 优先候选；补齐厨房、空调/插座/传感器，按确认快照实现灯亮度和气流；平板性能与设备效果待真人验收 |
| [`plans/2026-09-26-voice-intent-and-audio-integrity.md`](plans/2026-09-26-voice-intent-and-audio-integrity.md) | 2026-09-26 | **计划与现场诊断已完成；本次修复未实施** | P0-3：舒适请求、一次性待续与本轮可信证据；P0-4：录音完整性和插话；模拟验证及真人验收待做 |
| [`plans/2026-09-30-android-tablet-app.md`](plans/2026-09-30-android-tablet-app.md) | 2026-09-30 | Android 工程与第二版 APK 已完成并 USB 更新；**真实设备与平板体验待验收** | 见[第二版报告](reports/2026-10-01-manual-device-control-v2.md)；核对胶囊开关、亮度滑轨、自动同步与响应速度 |
| [`plans/2026-09-25-speaker-bound-operation-target.md`](plans/2026-09-25-speaker-bound-operation-target.md) | 2026-09-25 | Phase A 已实现；**Phase B 未实现**（全仓无 `resolve_self_target`） | 做 Phase B 前先补 A9 的 FAR/FRR 实测（当前阈值是估的） |
| [`plans/2026-09-24-perception-console-ux-registration-audio.md`](plans/2026-09-24-perception-console-ux-registration-audio.md) | 2026-09-24 | 大部分完成（17/25 子项） | 375px 窄屏验收；验收后提交 |
| [`plans/2026-09-23-local-perception-desktop.md`](plans/2026-09-23-local-perception-desktop.md) | 2026-09-23 | Task 1–2 有记录，Task 3–5 无完成记录 | 启动器、Unity 视觉 UI 移除、静态复核 |
| [`plans/2026-09-21-local-face-pose-vision.md`](plans/2026-09-21-local-face-pose-vision.md) | 2026-09-21 | 已实现，尚未验证；Task 7/8 的 Unity 面板已被 09-23 计划删除 | 按新架构回填状态；真人摄像头验收 |
| [`plans/2026-09-17-ha-eight-device-sync.md`](plans/2026-09-17-ha-eight-device-sync.md) | 2026-09-17 | 主体可用，**2 项 important 缺陷被 defer 未修** | 见该文件开头的“遗留问题”摘录（原文在 git 忽略目录里） |
| [`plans/2026-09-17-unity-realtime-device-effects.md`](plans/2026-09-17-unity-realtime-device-effects.md) | 2026-09-17 | Task 1–4 完成，Task 5 无记录 | PlayMode 实测 + 5 张 720p 截图 + 性能 |

`specs/` 里对应的约束性设计（这些设计**仍然有效**，不是历史）：
[`2026-09-17-ha-unity-realtime-effects-design.md`](specs/2026-09-17-ha-unity-realtime-effects-design.md)、
[`2026-09-21-local-face-pose-vision-design.md`](specs/2026-09-21-local-face-pose-vision-design.md)、
[`2026-09-23-local-perception-desktop-design.md`](specs/2026-09-23-local-perception-desktop-design.md)、
[`2026-09-24-perception-console-ux-registration-audio-design.md`](specs/2026-09-24-perception-console-ux-registration-audio-design.md)、
[`2026-09-25-speaker-bound-operation-target-design.md`](specs/2026-09-25-speaker-bound-operation-target-design.md)。

## 3. 已完成的计划（含完成时间）

| 计划 | 计划时间 | 完成时间 | 完成证据 |
| --- | --- | --- | --- |
| [`plans/completed/2026-09-13-ha-closed-loop-completion.md`](plans/completed/2026-09-13-ha-closed-loop-completion.md) | 2026-09-13 | **2026-09-13** | [`reports/2026-09-13-ha-closed-loop-report.md`](reports/2026-09-13-ha-closed-loop-report.md)：301 单测 + 真实栈 14/14 |
| [`plans/completed/2026-09-16-digital-twin-sse.md`](plans/completed/2026-09-16-digital-twin-sse.md) | 2026-09-16 | **2026-09-17**（未提交） | 后端/HA 194 项、Unity EditMode 22/22、PlayMode 5/5、Blender 协议 3/3；汇总见根 `task_plan.md` |

## 4. 历史归档（仅供历史参考，不要再照着做）

| 文件 | 时间 | 为什么归档 |
| --- | --- | --- |
| [`plans/archive/2026-09-12-smart-home-voice-agent-implementation-plan.md`](plans/archive/2026-09-12-smart-home-voice-agent-implementation-plan.md) | 2026-09-12（v5，最后修改 2026-09-25） | §0–6 的架构假设已不成立（`apps/unity-house/` **不存在**，真实工程在 `E:\unityroom\unityroom`）；原附在文末的 09-25 路线图已迁出为当前路线图 |
| [`specs/2026-09-12-smart-home-voice-agent-design.md`](specs/2026-09-12-smart-home-voice-agent-design.md) | 2026-09-12 | §5“人物位置只由手动模式决定”被 09-21 视觉设计正式替换；§6“首版不支持插话打断”已被 `apps/voice-service/src/barge_in.py` 取代 |
| [`notes/2026-09-13-project-state-and-next-steps.md`](notes/2026-09-13-project-state-and-next-steps.md) | 2026-09-13 | “下一步：Ollama+Qwen2.5-VL-3B”方案从未实施，被 YOLO26n-pose + InsightFace 取代 |
| [`specs/2026-09-25-speaker-identity-voiceprint-evaluation.md`](specs/2026-09-25-speaker-identity-voiceprint-evaluation.md) | 2026-09-25 | §六“称呼 vs 控制”被 09-25 设计的后果分级表修正；方案对比仍可参考 |
| [`findings.md`](../../findings.md)（根） | 最后更新 2026-09-21 | 结论已被 09-25 真人验收覆盖 |

## 5. 已删除的废弃计划

| 曾存在的文件 | 删除时间 | 为什么 |
| --- | --- | --- |
| `plans/2026-09-12-virtual-devices-implementation.md` | 2026-09-25 | 目标与 [`plans/completed/2026-09-13-ha-closed-loop-completion.md`](plans/completed/2026-09-13-ha-closed-loop-completion.md) 逐字相同，后者已完成并验收；该文件 6 项里只勾了 1 项且再无推进，全仓无任何引用。需要时从 Git 历史取回 |

另有一份非计划的存档保留在原地：
[`notes/2026-09-12-future-building-safety-scenario.md`](notes/2026-09-12-future-building-safety-scenario.md)
（写字楼火灾救援想法，文件自述“未纳入当前计划、未承诺实现”）。

## 6. 已完成但不在 `completed/` 的成果（报告 / 笔记）

| 文件 | 时间 | 内容 |
| --- | --- | --- |
| [`reports/2026-09-12-phase-a-voice-report.md`](reports/2026-09-12-phase-a-voice-report.md) | 2026-09-12 | 阶段 A 语音验证：ASR 30/30；TTS 换 edge-tts；按用户决定放行（“决策通过”，非实测通过） |
| [`reports/2026-09-13-ha-closed-loop-report.md`](reports/2026-09-13-ha-closed-loop-report.md) | 2026-09-13 | HA 闭环验收：真实栈 14/14 |
| [`notes/2026-09-25-perception-handoff.md`](notes/2026-09-25-perception-handoff.md) | 2026-09-25 | 感知终端交接；**§七 是全仓唯一记录真实摄像头验证的表**（房间跟随、TTL 诚实回落、Unity batchmode 退出 0、PlayMode 5/5），应视为验收报告 |
| [`reports/2026-09-26-reliability-registration-progress.md`](reports/2026-09-26-reliability-registration-progress.md) | 2026-09-26 | P0-1/P0-2 本批进展、测试与尚未进行的真人验收；不是完成报告 |
| [`reports/2026-09-26-observability-reliability-baseline.md`](reports/2026-09-26-observability-reliability-baseline.md) | 2026-09-26 | P0-1/P0-6 关联链路、诊断报告、已有进程采样和未完成的实测边界；不是完成报告 |
| [`specs/2026-09-12-home-assistant-virtual-device-design.md`](specs/2026-09-12-home-assistant-virtual-device-design.md) | 2026-09-12 | 四设备底座已完成；范围后被 09-17 设计扩到八设备 |
| [`specs/2026-09-13-voice-agent-integration-design.md`](specs/2026-09-13-voice-agent-integration-design.md) | 2026-09-13 | 已实现（`agent_client`/`agent_tools`/`agent_session`） |
| [`specs/2026-09-13-ha-closed-loop-completion-design.md`](specs/2026-09-13-ha-closed-loop-completion-design.md) | 2026-09-13 | 已实现并有报告 |
| [`specs/2026-09-16-digital-twin-sse-design.md`](specs/2026-09-16-digital-twin-sse-design.md) | 2026-09-16 | 已实现（SSE） |
| [`reports/artifacts/README.md`](reports/artifacts/README.md) | 2026-09-12 | `tools/voice-check/*.py` 实际写入该目录 |

## 7. 待办：必要但还没做的

| 对象 | 说明 |
| --- | --- |
| [`notes/2026-09-12-model-review.md`](notes/2026-09-12-model-review.md) | 待办 4：KWS 与 Silero 缺随附许可证文本，对外分发前必须解决 |
| A9 FAR/FRR 实测 | 声纹阈值（0.55/0.08）目前是估的，没有实测报告；误判说话人会导致控制错房间 |
| `apps/voice-service/README.md` 里提到的 `docs/superpowers/notes/2026-09-25-speaker-far-report.md` | **该文件不存在**，那条命令只是用法示例，不要当成已有证据 |

## 8. 内容曾被埋没的地方（已处理）

| 内容 | 原来在哪 | 现在 |
| --- | --- | --- |
| 2026-09-25 全项目复审路线图（11 项 + 批次） | 09-12 计划文件的**最后 51 行**，零外部引用 | [`plans/2026-09-25-project-roadmap.md`](plans/2026-09-25-project-roadmap.md)，旧文件移入 `plans/archive/` 并留指针 |
| 真实摄像头/Unity 验收证据 | `notes/2026-09-25-perception-handoff.md` §七 | 本文件第 6 节标为“应视为验收报告” |
| 八设备 SSE 的两项 important 缺陷 | `.superpowers/sdd/2026-09-17-ha-eight-device-sync/final-review.md`（**git 忽略目录**，tracked 文档无索引） | 已摘录进 [`plans/2026-09-17-ha-eight-device-sync.md`](plans/2026-09-17-ha-eight-device-sync.md) 开头 |
| Kokoro→edge-tts、DirectSound“写入成功但没声音” | 阶段 A 报告中段 | 见该报告 §2.1/§4b |
| 通用教训（dotnet 编译 Unity C#、`asdict()` 不序列化 `@property`、**PowerShell 5.1 重写中文文件会毁内容**） | `findings.md:78-91`、`progress.md:61` | 新结论请写进 `progress.md` 并在此登记 |

## 9. Unity / Blender 的落点（避免再次猜错）

- 真实 Unity 工程：`E:\unityroom\unityroom`（Unity 2022.3.47f1c1；Blender 插件源在 `Assets/SmartHome/BlenderAddon/smart_home_live`）。
- 仓库内 `apps/unity-house/` **不存在**且被 `.gitignore` 忽略；09-12 计划把它当工程路径的说法已作废。
- 仓库内另有 `apps/unity-house-payload/`（只放可安装的 .cs payload）与 `apps/room/`（另一个被 git 忽略的 2022.3 工程）。
- Blender 相关文档只有 [`specs/2026-09-16-digital-twin-sse-design.md`](specs/2026-09-16-digital-twin-sse-design.md) 与其计划 Task 4。

## 10. 维护规则

1. 新计划放 `plans/` 根，命名 `YYYY-MM-DD-短名.md`，并**在本文件登记**。
2. 完成时移入 `plans/completed/`，在文件头补“计划时间 + 完成时间 + 证据”。
3. 被取代时移入 `plans/archive/`，文件头写清被谁取代；彻底无价值就删除，并记到第 5 节。
4. 本文件在 `.gitignore` 里是**显式放行**的（`!/docs/superpowers/README.md`），其余同级文件默认忽略。
