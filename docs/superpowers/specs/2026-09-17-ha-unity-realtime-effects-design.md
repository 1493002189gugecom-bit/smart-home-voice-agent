# Home Assistant 与 Unity 场景实时效果设计

## 目标

让 Home Assistant 中八个数字孪生设备的状态，通过现有 `home-service` SSE 流可靠地驱动 Unity 场景中的设备反馈，并修复当前 HA 与场景状态不一致的问题。效果应当清晰但克制：灯光真正影响对应房间明暗，空调产生轻量气流，插座有状态指示灯，温度传感器提供局部温度色彩提示。

Unity 继续保持只读：不保存 Home Assistant Token，不调用 HA 服务，也不发送任何设备控制请求。新增的四个设备是本机 MQTT 演示实体，不对应真实设备。

## 当前状态与根因

Unity 场景包含三个房间、八个设备、三个人物和一个全局方向光。设备 ID 为：

- 灯：`living_room_light`、`bedroom_light`、`kitchen_light`
- 空调：`living_room_ac`、`bedroom_ac`、`kitchen_ac`
- 插座：`desk_plug`
- 温度传感器：`indoor_temperature`

当前 HA 只注册了 `living_room_light`、`bedroom_ac`、`desk_plug` 和 `indoor_temperature` 四个 MQTT 实体；`ha_entities.json` 也只包含这四项。当前运行在 `127.0.0.1:8765` 的 `home-service` 使用 Memory 后端，因此 `/snapshot` 返回内存演示状态，而不是 HA 的实时状态。这是 HA 页面与 Unity 场景显示不一致的直接原因。

修复后必须明确使用 HA 后端启动 `home-service`，并用 `/health`、`/events` 首帧以及 Unity 最终效果逐层核对同一个实体状态。系统不得在 HA 后端失败时静默回退到 Memory 数据。

## 数据流与职责

统一数据流为：

`MQTT 演示设备 / 现有 HA 设备` → `Home Assistant state_changed` → `home-service /events` → `Unity HomeServiceClient` → `SmartHomeEffectsController` → `设备效果组件`。

- MQTT 模拟器负责演示实体的发现、保留状态、可用性和命令回显。
- Home Assistant 是展示状态的权威来源。
- `home-service` 只把 `ha_entities.json` 中的实体标准化为统一快照并通过 SSE 推送。
- `HomeServiceClient` 维持现有 SSE 优先和旧服务轮询回退行为。
- 现有 `SmartHomeSceneController` 继续负责颜色、标签、人物和房间基础状态，不改变其现有语义。
- 新增 `SmartHomeEffectsController` 订阅同一个 `SnapshotReceived`，只把设备数据转换为视觉目标值。
- 每个效果组件在 Unity 主线程中平滑逼近目标值，避免 HA 连续事件导致闪烁或突变。

## 八个 HA 实体映射

保留现有四个实体，并新增四个 MQTT Discovery 实体：

| 场景设备 ID | HA entity_id | 类型 | 区域 |
|---|---|---|---|
| `living_room_light` | `light.shv_living_room_light` | light | 客厅 |
| `living_room_ac` | `climate.shv_living_room_ac` | climate | 客厅 |
| `desk_plug` | `switch.shv_desk_plug` | switch | 客厅 |
| `indoor_temperature` | `sensor.shv_indoor_temperature` | sensor | 客厅 |
| `bedroom_light` | `light.shv_bedroom_light` | light | 卧室 |
| `bedroom_ac` | `climate.shv_bedroom_ac` | climate | 卧室 |
| `kitchen_light` | `light.shv_kitchen_light` | light | 厨房 |
| `kitchen_ac` | `climate.shv_kitchen_ac` | climate | 厨房 |

三个灯使用 `0–100` 标准化亮度。三个空调支持 `off`、`cool` 和 `fan_only`，目标温度范围保持 `16–30°C`。新增实体沿用 `shv_` 唯一 ID、独立 availability topic 和 retained state，不向宿主机暴露 MQTT 端口。

`apps/device-simulator` 的持久状态结构、Discovery 消息、命令订阅和完整状态重发扩展到全部八个设备。`ha_entities.json` 和房间目录同步增加四项，稳定 ID 必须与 Unity 场景完全一致；禁止依靠名称模糊匹配。

## Unity 效果架构

新增一个效果协调器以及按设备类型划分的轻量组件：

- `SmartHomeEffectsController`：缓存设备 ID 到效果组件的绑定，接收完整快照，设置目标状态，并统一处理缺失数据。
- `SmartHomeLightEffect`：控制房间点光源、灯体自发光和过渡。
- `SmartHomeAirConditionerEffect`：控制气流粒子、颜色、速度和发射率。
- `SmartHomePlugEffect`：控制插座 LED 的颜色与自发光。
- `SmartHomeTemperatureEffect`：控制温度传感器指示环的颜色。

效果组件由场景生成器创建并序列化到生成场景，禁止依赖运行时名称搜索。`SmartHomeView.stableId` 仍是唯一绑定键。新增效果组件不替换现有 `SmartHomeView.renderers`、`linkedLight`、中文标签或状态颜色逻辑。

所有材质实例必须由效果组件显式持有或在初始化时创建，避免每帧访问 `Renderer.material` 产生隐式实例。组件禁用或场景卸载时停止粒子并释放运行时材质。

## 灯光效果

每盏灯继续使用对应房间内的实时 Point Light，并增加一个可发光的灯体表面。

- 关闭或离线时目标强度为 `0`。
- 开启时把 HA 亮度 `0–100` 映射到视觉强度，采用轻微非线性曲线，使低亮度仍可辨认，高亮度不过曝。
- 建议点光强度范围为 `0.35–2.6`，范围约 `4.5–5.5` 米；最终参数以 720p Game View 截图为准。
- 灯体使用暖白色自发光，发光强度与点光同步。
- 强度和颜色在约 `0.5` 秒内平滑变化。

为使灯光变化真实可见，全局环境光和方向光会适度降低，但必须保证所有房间灯关闭时仍能辨认房屋结构、设备和文字。灯光只影响对应房间附近，不使用全屏曝光或整场景闪变。

## 空调气流效果

每台空调出风口创建一个局部 Particle System，使用少量半透明短条或柔和粒子表示气流。

- 仅当设备在线、开启且模式为 `cool` 或 `fan_only` 时发射。
- `cool` 模式使用低饱和冷青色；`fan_only` 使用更淡、更中性的颜色。
- 制冷目标温度越低，发射率、速度和粒子可见度逐步增强。映射以 `30°C` 为最弱、`16°C` 为最强，并使用平滑曲线。
- 建议发射率 `2–12` 个/秒、最大粒子数不超过 `30`、速度约 `0.25–0.65`、生命周期约 `1.2–2.0` 秒。
- 关闭、离线或缺失数据时停止继续发射，让已有粒子自然消散。
- 不使用碰撞、体积雾或屏幕空间后处理，避免夸张效果和额外性能负担。

## 插座与温度反馈

智能插座增加一个独立的小型 LED 网格：

- 开启：柔和绿色常亮；
- 关闭：暗灰，不发光；
- 离线或无数据：低亮琥珀色；
- 不使用快速闪烁；功率仍由现有中文状态标签显示。

温度传感器增加一个局部指示环：

- 低温逐渐偏冷蓝，舒适温度约 `24–26°C` 为青绿色，高温逐渐偏橙色；
- 颜色变化限制在传感器附近，不改变全屋色调；
- 无数据时为灰色。

房间、家具和人物保持稳定，防止过多动态元素干扰空间识别。房间整体明暗由灯光状态驱动，空调和传感器只提供局部反馈。

## 状态一致性与异常处理

- `home-service /health` 必须明确报告 `backend: "ha"`；若不是 HA，Unity HUD 显示后端信息且验收失败。
- SSE 首帧必须与同一时间点 HA 中八个 entity_id 的标准化状态一致。
- Unity 断线时保留最后画面，但连接状态显示断开；重连后的首个完整快照覆盖全部目标状态。
- 设备离线或缺失时，停止对应动态效果并使用离线反馈，不沿用“在线开启”的粒子或灯光。
- 多个状态同时变化时，完整快照可一次更新多个目标；平滑组件不排队播放过时状态。
- 不在 Unity 内推算或伪造设备开关状态。视觉强度可以由真实亮度和温度派生，但开关、模式、目标温度与功率必须来自快照。
- 新增 MQTT 演示实体不绑定真实设备，不复用用户已有的非 `shv_` 实体。

## 测试

### device-simulator 与 home-service

- 测试八个设备的默认状态、持久化校验、命令 topic、Discovery payload、availability 和重连完整重发。
- 测试新增四个 HA catalog 映射的标准化结果。
- 测试 SSE 首帧和 `state_changed` 更新包含新增设备，未配置实体仍被过滤。
- 使用假 HA 网关完成自动测试，不通过测试代码控制当前真实 HA。

### Unity EditMode

- 灯亮度到目标光强的边界值和单调映射。
- 空调关闭、送风、制冷以及 `16/23/30°C` 的粒子目标值。
- 插座开启、关闭、离线三种 LED 状态。
- 温度颜色低温、舒适、高温和无数据状态。
- 完整快照、缺失设备、离线设备和第二次快照覆盖旧目标。
- 效果绑定必须覆盖八个稳定 ID，且不存在重复 ID。

### Unity PlayMode 与视觉验收

- 生成场景包含三盏房间灯、三套空调粒子、一个插座 LED 和一个温度指示环。
- 从测试 SSE 服务推送不同状态，验证最终灯光、发光、粒子和指示颜色。
- 在 1280×720 Game View 捕获灯全关、分房间开灯、强制冷、插座开启、离线状态截图，确认效果清晰且不过曝。
- 检查粒子总量、材质实例和控制台错误，确保空闲状态无持续分配。

### 只读实机验收

1. 启动八设备 MQTT 模拟器并确认 HA 注册新增四个实体；
2. 使用现有本地凭据启动 HA 后端 `home-service`；
3. 只读获取 HA 八个实体状态；
4. 读取 `/events` 首帧并逐项比较；
5. 启动 Unity Play Mode，比较 HUD 标签和视觉目标；
6. 状态变化由用户在 HA UI 或测试 MQTT 演示设备侧触发，Unity 不发送控制请求；
7. 验证断线和恢复后的完整快照重新同步。

## 非目标

- 不让 Unity 或 Blender 控制 HA 设备。
- 不把 HA Token、MQTT 密码或其他凭据写入 Unity、场景、日志或截图。
- 不把新增演示实体伪装为真实硬件。
- 不迁移渲染管线，不引入体积雾、第三方粒子插件或重型后处理。
- 不重做现有 HUD、相机控制、距离标签和 `SmartHomeSceneController` 的状态颜色逻辑。
