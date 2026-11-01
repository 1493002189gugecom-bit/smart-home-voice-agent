# 2026-10-28 业务正确性与设备接口修复

## 后续修复：Agent 超时后的执行拦截

用户随后指定修复“模型返回超时后仍执行动作”。已将单轮截止时间提前到上下文准备前，并在模型返回后、批次中每项工具前、定位解析完成后交给执行器前重新检查。直接指令入口也使用相同截止时间。超时后不发布迟到的普通回复；部分操作已经完成时保留真实结果，并清理未完成的工具调用消息，保证下一轮上下文合法。

验证：5 个关键场景先复现失败，再修复通过；完整语音套件 **536 passed in 17.33s**，`git diff --check` 通过。本次没有真实 API/设备操作。截止时间拦截的是尚未交给执行器的新操作；已经提交的设备操作不能据此撤销，进行中的网络请求仍受其自身超时约束。下文 821 项为此前批次记录。

状态：本轮代码修改、离线回归与本地模拟设备 HTTP 联调已完成；真人语音、摄像头、真实设备和整套进程启动验收未执行。

## 修复后的行为

1. **改口不再沿用待执行需求。** “好热啊”后追问房间，仅“卧室”“我在卧室”等完整房间回答可以补全原需求。“别降温了，改为关闭卧室灯”会清除待执行降温需求，进入正常语义工具流程。退出对话也清除待执行需求。
2. **复合指令保留各自参数。** “客厅调低两度，卧室设为26度”以及不带标点、工具分轮返回的变体，不再把全句的相对温差套到所有空调上。相对温差纠偏只接受完整匹配的单条请求；显式目标名称来自设备目录。单条“再调低两度”或“卧室空调调低0.5度”仍能纠正模型猜测的绝对温度。
3. **启动时核对实际配置。** 新启动和复用服务都检查实际后端、监听模式、hybrid 意图就绪状态。模式省略时读取语音服务同一份本地配置。配置不匹配时明确报错，不自动终止已有进程。memory 与 HA 的 HTTP 健康接口都返回实际 backend。
4. **设备请求在 HTTP 入口校验。** 校验 Host/Origin、JSON Content-Type、唯一且合法的 Content-Length、1 MiB 大小上限、UTF-8 对象结构和有限数值；不接受 chunked、NaN、Infinity、数值溢出，读取正文最多等待五秒。拒绝后关闭连接，未消费正文不能被当作下一条请求。
5. **增加本机写入鉴权并打通调用方。** home-service 自动生成本地写入令牌，语音执行器、控制台代理和 HA 验收工具自动读取。令牌不进入前端、模型和业务响应。视觉入口继续使用独立的视觉令牌；该令牌不能控制设备。快照/SSE 只读接口不要求写入令牌。

## 本轮验证

以下均为当前工作树上的真实命令结果，退出码均为 0。测试中模型和设备外部依赖使用受控替身；HTTP 联调连接测试启动的随机端口内存服务。

| 命令（仓库根目录执行） | 结果 |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest apps/voice-service/tests -q -o addopts= --tb=short` | 532 passed |
| `.venv\Scripts\python.exe -m pytest apps/home-service/tests -q -o addopts= --tb=short` | 210 passed |
| `.venv\Scripts\python.exe -m pytest apps/perception-console/tests -q -o addopts= --tb=short` | 43 passed |
| `.venv\Scripts\python.exe -m pytest apps/tests tools/ha-check/tests/test_acceptance.py -q -o addopts= --tb=short` | 36 passed |
| PowerShell AST 解析两个启动/健康脚本 | 无语法错误 |
| `git diff --check`（本轮修改的已有业务文件与测试） | 通过；仅提示既有 Windows 换行转换 |

合计 **821 项测试通过**。新增回归先在旧逻辑上观察失败，再验证修复后通过。独立代码复核提出的无标点复合指令、memory 健康字段与非标准 JSON 常量三个问题，均补充失败用例后修复，并重跑相关完整套件。

## 实施取舍与限制

- 在当前工作树修改，保留用户已有的大量未提交代码；没有提交或重置。修改前源文件保存在 git 忽略的 `runtime/business-fixes-2026-10-28/before`，供与本轮改动区分；不把 HEAD 中较早的实现当作当前基线。
- 相对温差快捷纠偏采用正向完整匹配；复杂表达继续由语义模型按工具分别解释。这里验证的是业务路由与执行参数隔离，不能证明真实 API 对任意自然语言都判断正确。
- 没有调用付费 API，没有启动正式服务栈、麦克风或摄像头，没有更改 HA 设备、Unity/Blender 工程或现有密钥配置。真实环境延迟、误触发率、多人环境和设备回执仍需现场验收。
- 本轮没有新增音乐/情绪模型、云 API 预算管理或长期偏好记忆。

## 生效方式

在 `E:\smart-home` 运行：

```powershell
.\tools\stop-perception.ps1
.\tools\start-perception.ps1 -HomeBackend ha -ListeningMode hybrid
```

hybrid 保留唤醒词入口。希望使用模拟设备时显式传 `-HomeBackend memory`。
新增写入令牌在正式 home-service 下次启动时生成。自写的 POST 客户端需按 home-service README 传 `X-Home-Token`；已有项目语音与控制台已适配。修改令牌路径后，各进程须使用相同的 `HOME_SERVICE_WRITE_TOKEN_FILE` 并重启。
