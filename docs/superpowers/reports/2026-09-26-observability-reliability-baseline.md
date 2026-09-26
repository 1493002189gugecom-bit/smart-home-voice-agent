# 2026-09-26 启停状态与跨服务诊断基线

对应路线图 P0-1、P0-6；P0-2 仍等待真人注册验收。本报告区分源码验证、已有进程资源采样和未进行的真人操作。

## 本次实现

- `start-perception.ps1` 将终端不可用定为退出码 2，终端可用但组件或窗口异常定为 1，全部就绪定为 0；仍会在单服务失败时打开可用的终端。启动耗时按冷/热启动记录。
- home-service `/health` 额外给出 HA 上游连接状态与最后连接时间；终端把“HTTP 在线但 HA 未连接”显示为需要处理，同时保留各服务最后一次 HTTP 响应时间和视觉最后出帧时间。
- 语音每轮生成请求 ID，记录采集、ASR、说话人判断、Agent、设备操作、TTS 合成、首音估计和整轮耗时。设备操作 ID 贯穿语音与 home-service。视觉观察由会话 ID 与帧时间标识，记录采集排队、姿态、人脸、预览和 home 同步耗时。home-service 记录请求及 SSE 快照耗时，合并快照保留最多 256 个触发 ID。Unity 客户端记录 SSE 快照到达滞后与场景回调耗时。
- Python 诊断按组件保留 1 MiB 当前文件及两个备份；Unity 保留 1 MiB 当前文件及一个备份。诊断采用字段白名单，关联 ID 落盘前哈希，不存音视频、转写、回复、人物称呼、路径或异常正文。报告命令统计阶段 p50/p95、失败率、分场景结果、资源峰值与慢例，也按哈希 ID 拼接语音/视觉→home→SSE→Unity 的完整耗时，并列出未闭合链路的缺失阶段。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| home-service 测试 | 单独运行 `python -m pytest -q apps/home-service/tests`，退出码 0；覆盖设备操作 ID 进入 SSE、合并视觉触发 ID、HA 最近连接时间。 |
| console 与诊断测试 | `python -m pytest -q apps/perception-console/tests apps/tests`，16 通过；覆盖 HA 断联降级、日志轮转、字段脱敏、跨服务 ID 哈希一致性、完整链路与缺失指标。 |
| voice-service 测试 | 单独运行 `python -m pytest -q apps/voice-service/tests`，退出码 0。 |
| vision-service 测试 | 单独运行 `python -m pytest -q apps/vision-service/tests`，67 通过、14 个子测试通过；覆盖合成注册帧的阶段计时，并修复诊断代码遇到伪帧无时间戳时的异常。 |
| 构建与语法 | 前端 `npm run build`、Python `py_compile`、启动脚本 PowerShell AST 解析均退出码 0。 |
| 资源采样 | 对**已经运行的旧进程**只读采样 5 秒：console 43.066 MiB、home 56.832 MiB、vision 1942.0 MiB、voice 506.684 MiB 为各自 RSS 峰值；CPU 采样为 0%，时间太短且进程空闲，不能据此判断推理性能。 |
| 已有进程热启动路径 | 四个端口已在监听的前提下运行 `tools\start-perception.ps1 -NoWindow -TimeoutSeconds 5`，退出码 0，四项均显示 ready；记录的 home/vision/voice/console 检查耗时分别为 3207.745/2561.213/2670.083/2605.988 ms，每项只有 1 次样本。没有新开设备或窗口。 |

四套 Python 测试不能放进同一个 pytest 进程：各服务存在同名 `config.py`、`server.py`，会在收集阶段相互覆盖。应按上表分别运行。`.venv-vision` 未安装 pytest，视觉测试使用项目根 `.venv`。

`runtime/perception/diagnostics/` 中的首次 home 记录含本次合成测试事件，不能作为真人时延基线。尝试删除该临时文件被自动策略拦截，保留原文件；本次资源基线单独保存在 `runtime/perception/baseline-2026-09-26/`，后续报告可使用 `--since-ms` 排除旧记录。

## 仍待实测

- 未停止或重启正在运行的四项服务，因为当前启动 voice 会占用麦克风；因此**冷启动、部分失败退出码 1/2**、HA 断线后重连和默认终端窗口打开仍待手动验证。现有进程未加载本轮 Python 代码。
- 热启动仅测了已有进程路径的一次，尚不足以计算可靠的 p50/p95。真实语音各阶段、设备确认率、视觉处理和同步时延、Unity 到达时延及真实端到端 p50/p95 均为**未测量**。本次仅验证指标结构与合成链路；5 秒资源样本不是连续运行稳定性结论。Unity 工程当前未打开，C# 运行时编译也未验证。
- 真实摄像头注册、人脸识别率和语音打断体感仍由用户手动验收；未采集或存储真人音视频。

## 复现命令

```powershell
cd E:\smart-home
.\.venv\Scripts\python.exe .\tools\perception-report.py --directory runtime/perception/baseline-2026-09-26 report
.\.venv\Scripts\python.exe .\tools\perception-report.py watch --seconds 60 --interval 2
```

如需冷/热启动数据，在方便占用麦克风的时间运行 `tools\stop-perception.ps1`，随后运行一次 `tools\start-perception.ps1`（冷启动）并再次运行启动脚本（热启动）。Unity 运行后的诊断目录见 `HomeServiceClient.DiagnosticsPath`；将其作为 `--unity-directory` 传给报告命令。

实施裁定：在已有大量未提交变更的当前工作树上增量修改，不创建无法携带这些变更的空白 worktree，也不提交或推送无关改动。
