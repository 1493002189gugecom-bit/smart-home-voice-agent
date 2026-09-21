# Home Assistant 数字孪生 SSE 推送设计

## 目标

让 `home-service` 被动接收 Home Assistant 状态变化，并把只读状态实时推送给 Blender 与 Unity。两种客户端都不持有 Home Assistant Token、不调用设备控制接口；现有 `/snapshot`、`/tool/room_status` 和控制工具保持兼容。

## 架构

数据流为：

`Home Assistant /api/websocket` → `home-service HA 事件订阅器` → `GET /events` SSE → `Blender / Unity`。

HA 后端使用现有 Python 环境中的 `aiohttp` 建立 WebSocket，按官方流程完成 Token 认证并订阅 `state_changed`。订阅器只接受 `ha_entities.json` 中列出的实体；匹配事件触发一次短暂合并后的标准化快照。Memory 后端在成功改变内存状态后使用同一个事件中心发布快照。

## SSE 协议

`GET /events` 只绑定现有 loopback 服务，不增加 LAN 暴露。响应使用 `text/event-stream; charset=utf-8`。

- 建立连接后立即发送 `snapshot` 事件。
- 相关设备变化时发送新的 `snapshot` 事件。
- HA 上游连接状态变化时发送 `status` 事件。
- 每 15 秒发送 SSE 注释心跳，避免空闲连接被关闭。
- 每条业务事件包含递增 `id`；重连后总是发送最新完整快照，不承诺历史事件重放。
- 每个慢客户端仅保留最新待发送状态，避免无界队列。
- 同一批 HA 连续事件在 100 毫秒窗口内合并。

统一 `snapshot` 数据采用现有 Unity 可理解的顶层结构：

```json
{
  "version": 42,
  "backend": "ha",
  "generated_at_ms": 1789488000000,
  "rooms": [],
  "devices": [],
  "persons": [],
  "broadcasts": [],
  "broadcast_queue": []
}
```

HA 数据中的时间字符串不直接写入 Unity 的整数 `version` 字段；事件中心使用自己的单调递增整数版本。现有 HTTP 端点的响应格式不改变。

## home-service 组件

新增独立事件中心，负责最新快照、序号、条件等待、心跳和客户端注销。HTTP Handler 只负责 SSE 编码与连接生命周期。

新增 HA WebSocket 订阅器，在后台 asyncio 线程运行：

1. 连接 `/api/websocket`；
2. 等待 `auth_required` 并发送 Token；
3. 确认 `auth_ok`；
4. 订阅 `state_changed`；
5. 过滤实体并发布快照；
6. 断线后按 1、2、4、8、16、30 秒退避重连。

日志和错误事件只包含固定错误码，不包含 Token、请求头或 HA 响应正文。服务停止时关闭订阅线程和 SSE 客户端；长连接线程设为 daemon，避免阻塞退出。

## Blender 客户端

项目源代码保存在 Unity 项目的 `Assets/SmartHome/BlenderAddon/smart_home_live`，并安装到 Blender 5.2 用户插件目录。

- 仅当场景包含 `read_only_visualization` 标记和 `SmartHomeDigitalTwin` 集合时自动连接。
- 后台 daemon 线程读取 SSE；`bpy.app.timers` 在 Blender 主线程应用更新。
- 通过设备根对象的 `smart_home_id` 匹配状态。
- 更新状态标签、状态材质、灯光启用与亮度、温度和离线/无数据视觉。
- 侧栏显示连接状态、最近更新时间，并提供连接、暂停、立即重连按钮。
- 插件只发出 `GET /events`，不包含任何 POST 或 HA 凭据配置。

当前 HA 仅提供四个实体：`living_room_light`、`bedroom_ac`、`desk_plug`、`indoor_temperature`。Blender 中额外的演示设备若未出现在快照中，显示为“暂无数据”，不会伪造状态。

## Unity 客户端

`HomeServiceClient` 改为 SSE 优先。使用 `UnityWebRequest` 与自定义 `DownloadHandlerScript` 接收分块数据，纯 C# SSE 解码器处理跨块、多个事件、注释心跳和 UTF-8。

- `snapshot` 事件继续触发现有 `SnapshotReceived`，不修改 `SmartHomeSceneController` 的颜色与标签逻辑。
- `status` 事件更新 HUD 连接信息。
- 断线后使用现有 `reconnectDelaySeconds` 重连。
- `/events` 返回 404 时才回退现有 `/snapshot` → `/tool/room_status` 轮询，兼容旧服务。
- 正常 SSE 工作期间不再周期请求状态端点。

## 错误处理

- HA WebSocket 断开：SSE 保持可用并发送 `upstream_disconnected`；恢复后立即发送最新快照。
- home-service 断开：Blender 与 Unity 显示未连接并退避重连，保留最后一次画面但标明数据陈旧。
- JSON 或 SSE 帧损坏：丢弃单个事件并记录不含敏感数据的错误，不终止客户端主循环。
- 未知设备：标记“暂无数据”，不创建设备、不映射到相似名称。
- 客户端退出或场景卸载：取消请求、停止线程并注销计时器。

## 测试与验收

home-service 测试覆盖事件中心、SSE 编码、首次快照、实体过滤、事件合并、心跳、慢客户端和 HA WebSocket 断线重连。使用本地假 HA WebSocket，不依赖真实设备。

Unity EditMode 测试覆盖 SSE 分块和统一快照解析；PlayMode 测试连接测试服务，验证事件触发现有场景颜色、灯光和标签更新，且无状态变化时不产生轮询请求。

Blender 测试覆盖 SSE 解码和设备 ID 映射；在当前 `.blend` 中连接真实只读 HA 流，核对四个已知设备的标签、材质和灯光。验收过程不调用任何设备控制接口。

最终验证包括：后端测试、Unity EditMode/PlayMode、Blender 插件加载、真实 HA 首次快照、一次由外部产生的状态事件或可控假 HA 事件、断线重连，以及保存后的 Blender 截图。

## 非目标

- 不让 Blender 或 Unity 控制设备。
- 不把 HA Token 写入客户端、场景或 Unity 资源。
- 不移除或改变现有 HTTP 查询与控制工具。
- 不引入 WebSocket Unity 包或更换 Unity 渲染管线。
