# Android 平板应用构建与交付记录（2026-10-01）

状态：**Android Studio 工程、调试 APK、私有下载入口已实现；小米平板已安装并启动连接页；应用内真实快照与触摸体验待用户按 README 验收。**

## 已完成

- 用现有 Android Studio 2024.2.1 对应的 Capacitor 7 建立 `apps/perception-console/web/android` 工程。前端资源内置在 APK，Android 端仅用原生 HTTP 读取用户配置的私有 HTTPS 快照；应用没有设备写入功能。
- 首次启动显示连接设置。地址必须是 HTTPS 根地址；过期或缺失快照标为未同步。包内 `INTERNET` 是唯一主动申请的 Android 权限；关闭明文流量与应用备份。
- 平板专用 `8771` 入口增加固定 `/tablet/app-debug.apk` 下载路径；电脑端 Serve 保持代理到 `127.0.0.1:8771`，桌面 `8770` 未开放到私有网络。
- README 增加当前电脑的确切连接地址及电脑、Tailscale、平板、Android Studio 的完整步骤；根 README 提供入口。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| 前端纯数据测试与构建 | 19/19 通过；Vite 构建成功。约 848 kB 单 JS 包有尺寸警告，未影响构建；平板实际性能未测。 |
| 平板入口 Python 测试 | 12/12 通过，覆盖只读边界、快照投影与新 APK 路由。 |
| Android Gradle | `npm.cmd run android:build` 成功，`assembleDebug` 通过；APK 约 4.36 MB。首次构建因 Gradle JVM 原生内存耗尽失败，限制工作线程与可见核心后成功。 |
| APK 内容 | `aapt` 识别包名 `com.smarthome.tablet`、名称“小屋·我的家”、minSdk 23/targetSdk 35；未请求摄像头、麦克风或定位权限；`allowBackup=false`、`usesCleartextTraffic=false`。 |
| 私有 HTTPS | 电脑端 `/tablet`、`/api/home/snapshot`、`/tablet/app-debug.apk` 均返回 200；下载 APK 与本地产物 SHA-256 一致。 |
| 小米平板 | ADB 识别设备；用户开启“USB 安装”后 `adb install -r` 返回 `Success`，`am start` 启动 MainActivity，截图可见首次连接设置页。 |
| 应用内真实快照、触摸与性能 | **待用户按 README 实机验收**；不以电脑端 HTTP 200 或截图替代。 |

## 继续验收

按 [`apps/perception-console/README.md`](../../../apps/perception-console/README.md#现在完整跑一遍) 的顺序启动电脑服务、确认 Tailscale，并在平板应用里填当前电脑的 HTTPS 根地址。检查“家庭服务已同步”、三房间视图、设备/人物房间显示；断开网络后检查“未同步”。记录平板截图、触摸和渲染体验，再更新本报告。

## Android Studio 导入警告复核

- 用户打开 `web/android` 后看到 app 与 capacitor-cordova-android-plugins 的 `flatDir` 警告。IDE 日志显示工程同步完成，使用 D 盘 Android Studio 的 Java 21；用相同 JDK 执行 Gradle `help` 时复现了两条警告，命令仍成功。
- 当前本地依赖目录无 JAR/AAR。根 `android/build.gradle` 在模块配置结束后仅移除空的平面仓库，有本地依赖的仓库保留。修正写在不会被 Capacitor 同步覆盖的根脚本中。
- 重新执行 Gradle `help` 及包含 `cap sync android` 的 `npm.cmd run android:build`，两者成功且没有 `flatDir` 警告。Android Studio 的旧提示需要重新同步才能刷新；本次未把命令行构建成功当成 IDE 已重新同步。
- README 补充 IDE 的 Sync、选择 app/平板、Run 和前端资源更新流程，并说明 IDE Gradle 缓存单独设为 E 盘。
