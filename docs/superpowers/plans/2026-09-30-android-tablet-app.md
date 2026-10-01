# Android Tablet App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可用 Android Studio 打开、构建、安装和调试的“我的家”只读平板 APK。

**Architecture:** Capacitor 将现有 React/Three.js 前端打包进 Android APK。原生客户端仅从用户配置的私有 HTTPS 根地址读取 `/api/home/snapshot`，保留浏览器现有相对路径；电脑的只读端口可向同一私有网络提供调试 APK 下载。

**Tech Stack:** React、TypeScript、Vite、Capacitor 7、Android Gradle、Python 标准库。

**Spec:** [`../specs/2026-09-30-android-tablet-app-design.md`](../specs/2026-09-30-android-tablet-app-design.md)

## Global Constraints

- 首版只读：无摄像头、麦克风、定位或设备写入权限。
- 只接受用户配置的 HTTPS 根地址；不把当前 Tailscale 主机名、密钥或签名材料写入仓库。
- 过期、失败或断线快照不得展示成实时人物位置。
- 保留现有桌面 `/`、浏览器 `/tablet` 行为及未提交工作树改动。
- 新下载/缓存尽量放 E/D 盘；现有 Android SDK 无需搬迁。

## Review Focus

- 用户输入含路径、凭据、查询或明文 HTTP 的地址时必须拒绝。
- Android 首次启动无地址时显示设置，不访问桌面写接口。
- 503、超时或不完整快照使页面标为未同步。
- APK 下载路由只能返回固定构建产物，不能让路径参数读任意文件。
- Android 构建失败不得被报告为“APK 已可安装”。

---

### Task 1: Native snapshot adapter and connection settings

**Files:** Modify `apps/perception-console/web/src/TabletApp.tsx`, `apps/perception-console/web/src/main.tsx`, `apps/perception-console/web/src/styles.css`; create `apps/perception-console/web/src/tablet-connection.ts` and its test.

**Interfaces:** `normalizeTabletOrigin(input): string | null`; `loadTabletSnapshot(origin: string | null): Promise<HomeSnapshot>`; Android uses CapacitorHttp, browser uses current `json` helper.

- [x] Test URL normalization and failing snapshot behaviors with pure Node tests; connection setting is local to the native app.
- [x] Implement native-only setup/retry UI and native HTTP snapshot adapter without changing browser routing.
- [x] Run targeted tests and Vite production build.

### Task 2: Android Studio project and debug APK

**Files:** Modify `apps/perception-console/web/package.json`, lock file; create `capacitor.config.ts` and generated `android/` project; update `.gitignore` narrowly for Android build outputs and local files.

**Interfaces:** `npm.cmd run android:sync`; `npm.cmd run android:build`; output `android/app/build/outputs/apk/debug/app-debug.apk`.

- [x] Install pinned Capacitor 7 dependencies with npm cache on E; add Android platform and sync bundled `dist`.
- [x] Configure app name/id, HTTPS-only transport, required permissions and Android project source.
- [x] Run Gradle debug build; inspect manifest and APK output. First build hit JVM native memory exhaustion; limited Gradle workers/JVM cores and rebuilt successfully.

### Task 3: Private APK delivery and documentation

**Files:** Modify `apps/perception-console/src/main.py`, `apps/perception-console/tests/test_tablet_view.py`, `apps/perception-console/README.md`, roadmap/index.

**Interfaces:** exact GET `/tablet/app-debug.apk` on the tablet-only listener; fixed build path and `application/vnd.android.package-archive` response.

- [x] Add route tests for present/missing APK, GET-only boundary and path traversal rejection.
- [x] Stream only the fixed debug APK with no-store headers; keep desktop API isolation.
- [x] Document Android Studio opening, web build/sync, APK install, Tailscale, USB/wireless ADB and current read-only scope.

### Task 4: Final verification

- [x] Run web tests/build, perception-console tests, Gradle debug build, `git diff --check` and manifest inspection.
- [x] Smoke test private HTTPS `/tablet` and `/tablet/app-debug.apk` on PC; USB installation and app connection page opened on tablet.
- [x] Record concrete results and limitations in `docs/superpowers/reports/` and update plan checkboxes.

**剩余实机验收：** 按 README 在应用里输入当前电脑私有 HTTPS 根地址，确认三房间快照、断连提示、触摸与流畅度。完成前不把应用内联网展示写为已通过。

## 2026-10-01 第二版追加进度

用户确认电脑和平板均需手动控制设备，随后明确要求 iPhone 风格左右滑动开关与亮度滑轨、无需确认按钮，操作后自动同步。这是首版只读范围的后续扩展。

- 已实现：受限设备控制接口、HA 实际状态确认、幂等重试；灯光、空调与插座触控面板；连续设置合并并顺序执行；Android 2.0 构建和 USB 覆盖更新。
- 已验证：后端 90 项、前端 23 项；隔离界面模拟开关和连续亮度操作；真实页面显示与私有入口来源限制。
- 待用户验收：真实灯光/空调/插座响应和状态同步、平板触控手感与流畅度。
- 证据与命令：[第二版报告](../reports/2026-10-01-manual-device-control-v2.md)。
- 后续交互修正已实现并模拟验证：设备选择区置于控制面板上方并吸顶；提示限定当前设备、成功后 3 秒收起、切换设备/房间清除。已重新构建并更新平板，触控体验继续待用户验收。
- 终端整体视觉已按用户要求统一：电脑六页共享“我的家”的导航、边距、深蓝/暖金配色；1280、1024、768 宽度下逐页对齐检查通过。见[统一主题报告](../reports/2026-10-01-console-unified-theme.md)，观感待用户评价。
