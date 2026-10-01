# Local Perception Console

The console is the formal loopback UI for home, vision, and voice status. The
Python gateway binds to `127.0.0.1:8770`, serves the built React assets, and
proxies only explicit allow-listed routes to ports 8765–8767.
`GET /health` checks only the console process; `GET /api/status` aggregates
backend status and can take longer when a backend is slow.

## Source layout

- `src/main.py`: static host, unified health, and HTTP/SSE proxy.
- `src/proxy.py`: service map, route allow-list, and 1 MiB request limit.
- The vision view uses a continuous `/api/vision/preview.mjpeg` stream; the gateway
  relays each JPEG part without saving frames.
- `web/`: React + TypeScript + Vite source.
- `web/dist/`: generated production assets; intentionally not authored by hand.

## Build the UI

Build the checked-in React source with:

```powershell
cd E:\smart-home\apps\perception-console\web
npm ci
npm run build
```

Then run the gateway from the repository root:

```powershell
.\.venv\Scripts\python.exe apps\perception-console\src\main.py
```

Before `web/dist` exists, `/api/*` remains available while `/` returns an honest
`frontend_not_built` response instead of a blank or misleading page.

For normal Windows use, run `tools\start-perception.ps1`. It starts missing
services, reports each failure independently, and opens the console in Edge or
Chrome app mode when port 8770 is healthy even if another service failed.
The Services page separates HTTP availability, model readiness and recent camera
frames; it also shows the last successful backend response. `tools\stop-perception.ps1` stops this repository's four
services, including manually started instances on their designated ports, and
fails if a port remains occupied.

For source UI development, run the gateway on 8770 and `npm run dev`; Vite
proxies `/api` to the gateway. All services remain loopback-only.

## 平板查看（需主动开启）

平板不能使用 `127.0.0.1:8770`：那个地址指向平板自身，而且桌面终端只监听电脑本机。
平板入口提供“我的家”三房间视图与所选设备的手动控制；它读取电脑家庭服务快照，只额外开放受限的 `/api/home/device/control`，不能调用摄像头预览、语音或身份注册。电脑与平板上都安装并登录同一私有 Tailscale 网络后，用 Tailscale Serve 提供 HTTPS；不要把桌面 `8770` 直接开放到局域网或公网。

1. 在电脑上构建前端并启动服务：

   ```powershell
   cd E:\smart-home
   cd apps\perception-console\web
   npm.cmd run build
   cd E:\smart-home
   .\tools\stop-perception.ps1  # 如果此前已启动普通终端
   .\tools\start-perception.ps1 -TabletView
   ```

   启动器仍在电脑打开 `8770` 窗口，并额外在 `127.0.0.1:8771` 开启平板入口。`stop-perception.ps1` 会一并关闭它。若平板入口未启动，先停止旧终端进程，再以 `-TabletView` 重启。

2. 在电脑上确认没有已有的 Serve 配置，再运行（只代理整个 `8771` 站点；不要只代理 `/tablet` 单一路径）：

   ```powershell
   & 'D:\tail\tailscale.exe' serve status
   & 'D:\tail\tailscale.exe' serve --bg 8771
   ```

   后台 Serve 会持续运行。Tailscale 打印的 HTTPS 地址后面加 `/tablet`，例如 `https://电脑名.网络名.ts.net/tablet`；在已登录同一私有网络的平板浏览器中打开。需要结束共享时运行 `& 'D:\tail\tailscale.exe' serve reset`。

数据路径：平板浏览器 → Tailscale 私有 HTTPS → 电脑本机 `8771` → `GET /api/home/snapshot` → 本机 `home-service:8765`。摄像头和麦克风仍属于电脑；平板仅显示家庭服务确认的设备和人物房间状态。电脑关机、家庭服务断开或快照请求失败时，页面显示未同步，不把旧位置当成实时数据。

若已有其他内网穿透服务，可把**电脑上的隧道客户端**目标设为 `http://127.0.0.1:8771`，代理整个站点，并在外层提供 HTTPS 与设备/用户认证。`8771` 自身没有登录验证，因此不要直接做路由器公网端口映射，也不要穿透 `8770`、`8765`、`8766` 或 `8767`。

2026-10-01 电脑和平板已登录同一 Tailscale 网络，电脑已启用后台 Serve；电脑侧私有 HTTPS 的 `/tablet`、快照和 APK 下载接口返回 200。应用已通过 USB 安装到小米平板并打开连接设置；应用内读取真实快照与操作体验待按下述步骤验收。

## Android 平板应用（调试版）

### 现在完整跑一遍

**本机当前要填的地址：`https://iphone16promax.tail285617.ts.net`**。这是电脑的 Tailscale 私有 HTTPS **根地址**，不是浏览器页面地址。应用输入框里不要加 `/tablet`、`/api/home/snapshot`、`:8771` 或末尾空格。以后电脑的 Tailscale 主机名变化时，以 `& 'D:\tail\tailscale.exe' serve status` 显示的 HTTPS 地址为准。

1. **电脑：启动 Home Assistant 和本地服务。** 打开 Docker Desktop，等 Home Assistant 容器就绪；然后在 PowerShell 运行：

   ```powershell
   cd E:\smart-home
   .\tools\start-perception.ps1 -TabletView
   ```

   看到 `home`、`vision`、`voice`、`console` 均 ready，且提示 `Tablet home view is ready at http://127.0.0.1:8771/tablet`。如果脚本提示“已有 console 没有 tablet view”，运行 `.\tools\stop-perception.ps1`，再运行上面的启动命令。这个启动命令也会打开电脑上的 `8770` 终端；平板应用使用的是额外的 `8771` 平板入口。

2. **电脑：确认私有连接。** 在同一 PowerShell 窗口运行：

   ```powershell
   & 'D:\tail\tailscale.exe' status
   & 'D:\tail\tailscale.exe' serve status
   curl.exe -f https://iphone16promax.tail285617.ts.net/api/home/snapshot
   ```

   `status` 应显示本机和 `xiaomi-pad-7` 在线；`serve status` 应显示 HTTPS 地址代理到 `http://127.0.0.1:8771`；最后一条应返回含 `rooms`、`devices`、`persons` 的 JSON。若 Serve 没有配置，运行 `& 'D:\tail\tailscale.exe' serve --bg 8771` 后重试。

3. **平板：连接并查看。** 打开平板 Tailscale，确认已连接同一账号的网络；打开已安装的 **“小屋·我的家”**。在“HTTPS 地址”框准确输入 `https://iphone16promax.tail285617.ts.net`，点“连接并查看”。首次进入应显示“我的家”三房间视图；点击房间可查看该房间的设备和摄像头确认的人物房间状态。顶部应显示“家庭服务已同步”和更新时间。第二版可以手动调空调、灯光和插座，操作步骤见下节；摄像头和语音仍在电脑终端使用。

4. **若显示“家庭服务未同步”。** 先在平板浏览器打开 `https://iphone16promax.tail285617.ts.net/tablet`：若页面也打不开，检查平板和电脑的 Tailscale，以及第 2 步的 Serve；若网页能打开但没有设备数据，检查 Docker Desktop、Home Assistant 和电脑 `8770` 的服务状态。回到应用点右上角“连接设置”，核对地址；应用会自动重试，不必重新安装。电脑关机或停止服务后，应用应显示未同步，不应继续把旧人物位置当实时位置。

已下载 APK 的用户可直接从第 1 步开始；首次还没安装时，在平板浏览器下载 `https://iphone16promax.tail285617.ts.net/tablet/app-debug.apk` 并按系统提示安装。2026-10-01 已用 USB 调试在小米平板安装并启动此调试包；完整联网画面与触控效果仍需在平板上核对。

### 用 Android Studio 开发和更新应用

Android Studio 已安装在 `D:\Android s\bin\studio64.exe`。打开项目时选 `E:\smart-home\apps\perception-console\web\android`，等待 Gradle 同步后可选已开启 USB 调试的平板点 Run。Android 11 及以上也可在开发者选项中开启无线调试并在 Android Studio 配对；同一 Wi-Fi 下完成配对。项目使用 Capacitor 7、Java 21、现有 Android SDK；首次 Gradle 构建需下载依赖。

在 Android Studio 中按这个顺序运行：

1. 打开上面的 `android` 目录。首次自动进行 Gradle Sync，是正常的项目导入过程；同步完成后选运行配置 `app`。
2. 平板保持 USB 调试与“USB 安装”开启，允许电脑调试；在顶部设备列表选小米平板，点击绿色 Run 三角。Android Studio 会构建、安装并启动应用。
3. 此应用的 3D 场景和界面源码在上一级 `web/src`：`Home3D.tsx`、`home-scene.ts`、`TabletApp.tsx`。修改这些网页源码后，先在 `web` 目录运行 `npm.cmd run android:sync`，再回 Android Studio 点 Run。Android 原生代码则在 `android/app/src/main`，可直接在 Android Studio 修改并运行。

如果仍看到旧的 `Using flatDir should be avoided…` 提示，点击 **File → Sync Project with Gradle Files** 刷新。此提示源自 Capacitor 模板的本地依赖仓库；工程现已自动移除没有 JAR/AAR 的空仓库，且 `cap sync` 后重新构建也已确认不再出现该提示。实际存在本地 JAR/AAR 时保留对应仓库。

为让 Android Studio 本身也把新增 Gradle 缓存放 E 盘，在 **Settings → Build, Execution, Deployment → Build Tools → Gradle** 中把 **Gradle user home** 设为 `E:\smart-home\runtime\gradle-cache`，Gradle JDK 使用现有的 `D:\Android s\jbr`（Java 21）。下面 PowerShell 的缓存变量只影响该命令窗口，不会自动改变已经运行的 Android Studio 设置。

修改网页后，在 PowerShell 运行：

```powershell
cd E:\smart-home\apps\perception-console\web
npm.cmd run android:sync
$env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
$env:GRADLE_USER_HOME = 'E:\smart-home\runtime\gradle-cache'
cd android
.\gradlew.bat assembleDebug --no-daemon --max-workers=2
```

生成的调试包在 `android\app\build\outputs\apk\debug\app-debug.apk`。更新时可用 Android Studio 的 Run 安装，也可在平板 Tailscale 浏览器重新下载上面的 APK；此包是开发用 debug 签名，不用于应用商店分发。平板上的 Tailscale 和电脑服务都需保持运行。

应用显示家庭快照并支持受限的设备控制；不申请摄像头、麦克风、定位权限。断线或旧快照会标“未同步”并禁止修改。第二版已构建并通过 USB 更新，真实设备效果和触控手感待用户验收。

### 第二版：电脑和平板手动控制设备

电脑刷新 `http://127.0.0.1:8770/` 并进入“我的家”；平板打开更新后的应用，连接地址保持不变。先选房间，再点设备卡片。

| 设备 | 触控操作 |
| --- | --- |
| 灯光 | 点按或左右滑动胶囊开关；拖动亮度滑轨，或点 25%／50%／75%／100% 快捷档位 |
| 空调 | 胶囊开关控制电源；点制冷/送风；用 −／+ 每次调整 0.5°C，范围 16–30°C |
| 插座/开关 | 点按或左右滑动胶囊开关 |
| 传感器 | 查看读数 |

**没有确认按钮。** 开关、快捷亮度、模式和温度按钮直接发送；亮度拖动时立即显示数值，松手自动同步。键盘调整在松开按键时发送。正在控制设备时仍可继续调整同一设备，连续操作合并为最新设置并顺序发送；设备未确认前显示“正在同步”，不把界面预览当成成功。设备没有温度数据时显示“设为 26°C”，点击即发送该明确温度。

结果只有在 Home Assistant 确认实际状态后才显示成功，3D 灯光与设备卡片随最新快照更新。未确认时先查看设备状态，再用“重试本次操作”重试同一个请求；等待期间避免重复操作。离线、过期状态与本地模拟后端不能用于这条真实设备写入链路。服务端限制设备类型、参数范围和请求来源，平板仍不能访问其他写接口。

设备列表保持在控制面板上方，选中后滚动时会吸顶，便于切换设备。操作提示只出现在对应设备控制区内，切换设备/房间时清除；成功提示 3 秒后收起，失败或未确认可点“收起提示”关闭。

手动验收：调一次灯光亮度、切换开关、改变空调温度，核对实际设备与电脑/平板状态一致；停止家庭服务后应禁止修改。此轮自动验证没有控制真实灯光或空调。

## Runtime behavior

电脑终端的“我的家、总览、视觉、语音、身份、服务”已统一为深蓝与暖金风格，切换页面时导航栏和标题边距保持一致。修改后若仍看到旧浅色页面，可在 `8770` 按 Ctrl+F5 刷新。

- Identity enrollment shows the selected camera's live preview while a session is
  active. Cancel the session before changing cameras; the preview stream is
  relayed in memory and camera frames are not saved by the console.
- Audio starts in automatic mode and follows the Windows system default when it
  is usable. If it is unavailable, the service reports its fallback. A manual
  choice is matched by device name and host API and stored in the ignored local
  file `runtime/voice-agent/audio-devices.json`; device indexes are not saved.
- After applying an audio choice, wait for the UI to confirm that the service
  applied it or report a failure. A pending request is not shown as active.
- The console does not persist raw audio, transcripts, or assistant replies.
