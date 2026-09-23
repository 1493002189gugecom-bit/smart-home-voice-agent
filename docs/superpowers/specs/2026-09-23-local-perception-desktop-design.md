# 本地感知中心与 Unity 只读展示设计

日期：2026-09-23  
状态：用户已批准直接实施，不要求逐段审阅

## 1. 目标

把摄像头、人脸注册、语音交互和未来声纹能力从 Unity 移到独立的 Windows 本地感知中心。感知中心采用 React + TypeScript 界面，既能作为 `127.0.0.1:8770` 的正式本地网页运行，也能由 Windows 桌面启动器以应用窗口打开。Unity 删除旧视觉控制、注册和预览逻辑，只通过 `home-service` 的只读事件显示人物房间、身份、姿态和家庭设备状态。

## 2. 范围

本阶段交付：

- `perception-console`：统一入口、静态 React 页面、服务状态与同源 API 代理；
- `voice-service` 状态接口：发布用户转写、助手回复、工具执行和语音状态；
- Windows 启动器：启动并监控 home、vision、voice、console 四个本地进程；
- React 页面：总览、视觉、语音、身份、服务；
- Unity 清理：移除摄像头面板、视觉服务客户端、预览叠加和启动按钮；
- 保留视觉服务向 home-service 发布人物位置的现有链路。

本阶段不实现声纹模型、声纹注册、跨模态身份融合、安装包签名、远程访问或云端媒体存储。界面为声纹保留明确的“尚未启用”入口，但不能伪造声纹状态。

## 3. 架构

```text
SmartHome Perception Windows launcher
  ├─ home-service        127.0.0.1:8765
  ├─ vision-service      127.0.0.1:8766
  ├─ voice-service API   127.0.0.1:8767
  └─ perception-console  127.0.0.1:8770
       ├─ React 静态资源
       ├─ /api/home/*   -> 8765
       ├─ /api/vision/* -> 8766
       └─ /api/voice/*  -> 8767

Unity -> home-service SSE（只读）
```

各模型服务继续独占各自设备和依赖。Console 不直接打开摄像头或麦克风，只代理命令和状态。页面只访问 8770，避免 CORS 和多端口错误处理分散。

## 4. 进程生命周期

Windows 启动器使用仓库中配置的 Python 解释器启动服务，并按端口健康检查判断服务是否已经存在。它只记录和关闭自己创建的子进程，不终止用户手动启动的同端口进程。启动顺序为 home、vision、voice、console；界面可在部分服务失败时打开并显示具体故障。退出时按 console、voice、vision、home 的顺序关闭自有进程。

开发阶段提供 PowerShell 启动器并使用 Edge/Chrome 的 app 模式打开 8770。React/Tauri 源码边界保留在 console 应用内；正式打包可在后续独立任务中增加，不阻塞本阶段把功能移出 Unity。

## 5. Voice API 与事件

Voice API 绑定 `127.0.0.1:8767`，至少提供：

- `GET /health`：进程、循环、输入输出设备和 Agent 状态；
- `GET /history`：当前进程内最近 200 条脱敏事件；
- `GET /events`：SSE 实时事件；
- `GET /devices`：可用输入与输出设备的安全摘要。

事件类型固定为：`voice_state`、`transcript`、`agent_reply`、`tool_result`、`playback_state` 和 `service_error`。事件包含时间、类型、状态和公开载荷。API 不返回 API Key、原始音频、模型绝对路径或设备敏感标识。对话历史默认只存在内存，进程退出即清空。

## 6. Console API

Console 绑定 `127.0.0.1:8770`。`/api/status` 并发读取三个服务的健康端点并返回 `up | down | degraded`。`/api/home/`、`/api/vision/`、`/api/voice/` 使用明确的允许路径代理请求，禁止任意 URL 转发。视觉 JPEG 保持二进制代理；语音和家庭事件流保持 SSE 语义。

服务不可用时返回统一错误：`service`、`error_code`、`message`，页面保留其他可用模块。所有响应增加 `Cache-Control: no-store`。

## 7. React 界面

技术栈为 React、TypeScript 和 Vite，不使用 Next.js。页面包括：

- 总览：四个服务状态、当前人物位置、最近设备变化；
- 视觉：实时画面、摄像头、房间、监控控制和识别结果；
- 语音：用户转写、助手回复、执行设备及结果、语音阶段、输入输出设备；
- 身份：爸爸、妈妈、孩子的人脸注册、删除、进度，以及禁用的声纹入口；
- 服务：端口、健康状态、可恢复错误和重启说明。

页面断线时指数退避重连，但不得清除最后一次明确标注为历史的对话；摄像头画面断线则立即隐藏，不能展示成实时画面。所有操作按钮在请求进行中禁用，服务确认后才更新成功状态。

## 8. Unity 边界

从 Unity 实际项目和可复制 payload 中删除 `VisionServiceClient`、`VisionCameraPanel`、`VisionCameraLauncher`、`VisionContracts` 和 `VisionOverlayGraphic`。场景先移除相应组件再删除脚本，避免 Missing Script。Unity 的 `HomeServiceClient`、SSE、人物/设备状态模型和房屋效果保持不变。

Unity 不连接 8766、8767 或 8770，不提供摄像头、注册、语音或服务管理按钮。

## 9. 隐私与安全

- 所有新端口只绑定 loopback；
- 不保存原始摄像头画面和原始音频；
- 当前会话转写和回复只驻留内存；
- API Key、完整模型路径和完整视频 URL 不进入页面、事件或日志；
- 代理采用路径白名单并限制请求体大小；
- 人脸特征继续由 vision-service 按现有 DPAPI 规则保存。

## 10. 验收约束

按用户要求，实施者不运行测试、构建、模型推理、摄像头验证、麦克风验证或截图验收。只允许静态文件检查、Git diff 检查、端口/进程只读检查和 Unity MCP 场景组件移除。功能运行验收由用户后续手动完成。
