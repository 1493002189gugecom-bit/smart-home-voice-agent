# 2026-09-30 Android 平板应用设计

状态：**首版范围已由用户确认；Android 工程和调试 APK 已构建，平板实机安装与体验待验收。**

2026-10-01 更新：首版已 USB 安装；用户授权第二版增加受限设备控制与操作后自动同步，见[第二版报告](../reports/2026-10-01-manual-device-control-v2.md)。下文保留首版设计范围，第二版状态以新报告和计划追加进度为准。

## 目标与边界

用户希望得到能下载、安装、启动的 Android 应用，并能在 Android Studio 中打开工程，通过 USB 或无线调试连接平板、构建与安装调试 APK。首版沿用已有 React/Three.js“我的家”平板视图，展示设备状态和可信的人物房间位置；继续保持只读，不增加摄像头、麦克风、身份注册或设备写入权限。电脑上的 `home-service` 仍是数据权威层，Unity 工程不参与 Android 应用。

应用安装后仍需连接电脑服务：平板上的 Tailscale 与电脑上的私有 Serve HTTPS，或将来等价的已认证 HTTPS 通道。APK 不替代 Home Assistant、四项本地服务或网络通道。首版不发布应用商店、不生成正式签名包，也不承诺离线设备状态。

## 现有基础与已测边界

- 浏览器平板路由为 `/tablet`；电脑本机 `127.0.0.1:8771` 仅允许静态资源、健康检查和只读 `GET /api/home/snapshot`。快照按字段白名单投影，过期或上游失败时返回不可用，不沿用旧人物房间。
- 2026-09-30 电脑与小米平板已加入同一 Tailscale 网络；Serve 已提供私有 HTTPS。电脑侧 GET `/tablet` 和 `/api/home/snapshot` 均返回 200。**尚无平板界面目视验收，也没有 Android APK。**
- 电脑到平板的 Tailscale ping 当前经 DERP 中继，后两次约 0.5 秒；这只说明链路可达，不能代表最终页面帧率或稳定性。
- 本机 Node 24、Java 21、Android SDK 和 Android Studio 2024.2.1 已存在；Android Studio 安装在 D 盘。采用与现有 IDE 兼容的 Capacitor 7，Gradle 与 npm 缓存放 E 盘，不搬迁已有 SDK。

## 路线选择

| 方案 | 能力与代价 | 结论 |
| --- | --- | --- |
| **Capacitor + 内置网页资源** | 复用 React/Three.js；生成 Android Studio 可编辑工程和 APK；应用离线时仍可打开并显示断连状态；需增加原生 HTTP 读取及构建同步流程 | **首版采用** |
| Kotlin WebView 直接加载电脑网页 | 原生工程较小，网页更新即生效；电脑或隧道不可用时连应用界面也加载不出来 | 不选作首版 |
| PWA/TWA | 安装方便，但不满足用户明确要求的 Android Studio 工程和常规 APK 调试流程 | 暂不做 |

## 组件与数据流

1. 在 `apps/perception-console/web/` 集成 Capacitor Android 平台，保留现有网页终端。Android 原生运行时只显示 `TabletApp`；电脑浏览器的 `/` 与 `/tablet` 路由行为保持现状。APK 打包生产构建后的界面资源，不把 `server.url` 远程加载模式用于交付版。
2. 首次启动显示连接设置：用户输入电脑私有 HTTPS 站点的**根地址**，例如 `https://设备名.网络名.ts.net`。只接受 HTTPS origin；拒绝用户信息、查询、片段和非根路径。地址只存在平板本地设置，不写入 Git、APK 默认值或日志。应用只拼接并请求 `/api/home/snapshot`。
3. Android 端用 Capacitor 的原生 HTTP API 读取只读快照，以免本地界面 origin 与电脑 HTTPS origin 的浏览器跨域限制。网页端继续使用现有相对路径请求。请求超时、HTTP 非 200、Tailscale 断开、HA 失联或响应不符合预期时，视图立即标“未同步”，允许重试或修改地址，不显示旧位置为实时位置。
4. 原生工程只需联网权限。不申请摄像头、麦克风、定位和存储权限；禁止明文 HTTP。快照仍由服务端字段白名单和新鲜度检查约束，Android 客户端不得调用桌面终端 `8770` 或设备写接口。
5. 接口地址由用户配置，因此更换电脑名或认证 HTTPS 通道时无需重新编译 APK。Tailscale 仍由平板上的独立 Tailscale 应用维持；Android 应用不尝试在后台控制 VPN。

## 构建与开发体验

- 提供可直接在 Android Studio 打开的 `android/` 工程、固定依赖版本、构建说明和调试 APK 输出位置。Web 改动后执行前端构建与 Capacitor sync，再在 Android Studio 点击 Run 或用 Gradle/ADB 构建安装。调试包可本机分享给平板安装；正式分发签名属于后续阶段，签名密钥不进仓库。
- USB 调试和 Android 11 及以上的同网段无线 ADB 配对均可用于开发；无线配对由用户在平板开发者选项与 Android Studio 中完成，不把配对码写入项目。
- 适配当前小米平板横竖屏；先沿用已检查的 4:3、3:2、16:10 响应式布局，实机核查 Android WebView 的尺寸、触摸、返回键和重连体验。

## 验收与未测量项

1. 构建可安装的 debug APK；Android Studio 能打开工程并运行 Gradle sync。APK 启动显示连接设置，输入有效 HTTPS 根地址后看到三房间视图。
2. 模拟有效快照、断线、过期、HTTP 403/503、慢请求和切换地址；不能把未同步数据显示为实时人物位置，也不能触发任何设备写入。
3. 检查 APK 权限、明文流量禁用、地址不进入源码与日志；验证网页终端和现有 `/tablet` 不回归。
4. 在用户的小米平板上用 Android Studio/ADB 安装与目视验收；测量首次启动、页面交互、网络重连和真实设备快照耗时。当前这些实机指标一律标为**未测量**。

## 外部技术依据

- Capacitor 官方支持将现有网页项目生成 Android 平台并在 Android Studio 管理；`CapacitorHttp` 可经原生 HTTP 读取远端 API。版本与构建命令以实施时官方文档和锁文件为准。
- Android 官方支持 Android 11 及以上设备通过同一 Wi-Fi 的无线调试配对；也保留 USB 调试路径。
