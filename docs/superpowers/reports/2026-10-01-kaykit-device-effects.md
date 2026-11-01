# KayKit 家具与设备视觉效果实施报告

后续更新：2026-10-12 已修正床头、厨房、桌椅等布局，最新构建为 Android 2.1.1。以下保留 10-01 实施记录；当前验证和包信息见[布局修正报告](2026-10-12-furniture-layout-fix.md)。

状态：源码、最终网页和 Android 2.1（versionCode 3）已完成，自动回归和浏览器模拟检查通过。真实设备、目标平板性能及观感待用户验收。

## 当前完成结果

- 分支 `feat/kaykit-device-effects`，起点 `989f389`；修改保留在当前工作树，尚未提交或推送。
- KayKit 15 种家具与 Kenney 4 种厨房模型，共 21 个家具实例，包含沙发、扶手椅、床、柜、桌椅、灯具、地毯、画框、绿植和厨房设备。按包围盒统一比例，保留 KayKit 原始图集配色。运行子集 401566 字节，放在 `web/public/assets/furniture/`，符合 8771 的 `/assets/` 白名单；完整 193 种储备素材不进入 APK。
- 同种模型实例共享几何、材质和场景内 Promise 缓存，KayKit 共用一张图集。设备发光使用局部发光面，避免灯杆、底座和整件模型一起发亮。模型下载失败保留简化家具。
- 灯：KayKit 灯罩局部发光、暖色局部照明和渐变地面柔光，按确认亮度平滑过渡。
- 空调：风口处 24 个渐隐柔和粒子、单次绘制的 5 条低透明流线，制冷淡青、送风淡白，关闭后自然消散。三个房间合计 72 个粒子；固定动画只表示运行状态，不声称测到风速、空气坐标或真实冷量。
- 插座：开关指示灯。传感器：有效温度读数与克制色彩提示；已确认 HA 来源标为“实测”、memory 来源标为“模拟”、缺失来源标为“读数”。平板投影当前不保留 backend，来源未知时使用保守标签。目标温度明确标“设定”，备用 simulated_temp 标“模拟”。
- 全部状态来自 home 确认快照，提交中滑轨和操作草稿不改变场景运行状态。缺失、离线、未知或超过 5 秒的快照停止有效运行提示；过期也停用人物位置与控制面板。
- 设备按稳定 ID 保存状态、过渡和位置槽，清单增删不会把新设备挤进已有位置；同 ID 的房间/类型变化重建设备节点。重复快照不会重置过渡。
- **按用户最终要求，小标签只在近景显示。** 默认 zoom=1 的远景隐藏，zoom≤1.15 隐藏，1.15–1.5 平滑渐显，≥1.5 完整显示；缩小后反向淡出。标签保持屏幕字号并轻量避让，灯光和气流不受显隐影响。参考旧 Unity `SmartHomeScreenLabel.ApplyZoomVisibility` 的近景细节展示方式。
- 页面隐藏取消 RAF，恢复重新核验快照；静止按需绘制，气流或过渡才持续动画。卸载取消动画、定时器、监听并释放控件、模型、材质、纹理和 WebGL 上下文；加载途中卸载后，晚到模型直接释放。
- 已满意的 React 面板布局保留；人物仍只表示可信房间级位置，示意家具与气流不代表实测户型或坐标。

## 最新验证证据

1. 前端纯数据/控制测试 **30/30**：确认亮度、离线/未知/过期、温度来源、稳定位置槽，以及远景隐藏/近景渐显。
2. 后端既有回归 **90 项通过**：终端、平板投影、控制确认与 HA 控制测试。未用测试请求控制真实设备。
3. TypeScript/Vite、Capacitor sync、Gradle assembleDebug 成功；最终主 JS `index-CD-CvOzW.js`，944.12 KB，gzip 258.98 KB。Vite 的 >500 KB 体积提示仍在，目标平板加载耗时与帧率待实测。
4. 浏览器模拟效果脚本 19 项检查通过：21 实例加载、25/50/100% 独立亮度、重复快照、制冷/送风、关闭消散、离线/未知、多 ID、隐藏取消 RAF、过期/恢复与卸载。
5. 最新清单/来源脚本 8 项检查通过：`[a,b] → [b,c]` 保持 b 位置且 c 不重叠，缺失 a 不可用，换房间和换类型重建，HA/memory/未知来源温度标签准确。
6. 滚轮缩放脚本 5 项检查通过：默认远景无标签、放大渐显、近景完整可见、拉远再隐藏，确认状态和气流保持不变。
7. 资源脚本通过：阻断椅模型后余下 19 实例正常，两张椅保留回退；加载途中卸载、连续 3 次挂载/卸载均没有持续 RAF，GPU 场景几何计数归零。纹理计数 1 已通过 dispose 注册跟踪定位为 Three.js 内部 DFG_LUT（16×16）；应用贴图和标签已释放，forceContextLoss 清除上下文。
8. 最终网页检查 1280×800、1200×800、1024×768 和 768×1024：无横向溢出、无 WebGL 回退；截图均明确标记模拟确认快照。检查了最终远景干净和近景标签。隐藏测试为 document.hidden + visibilitychange 处理器模拟，不能替代真机 Android 后台测试。
9. 8770 首页、8771 平板页和 glTF/bin/PNG/GLB GET 全部 200，HTML 指向最新 bundle。APK 中全部 **40 个 dist 文件逐字节匹配**，仅包含 19 种选中模型，测试页面未打入包。8771 下载的 APK 与本地 SHA256 一致。

最终 APK：`apps/perception-console/web/android/app/build/outputs/apk/debug/app-debug.apk`，4800358 字节，构建时间 2026-10-01T16:19:34.525564+08:00。

SHA256：`8eba4f4b3981bc8e42213080d700f4f7565dcb9f3f663b6d33bf1e80f182ceaa`。

本地证据（忽略目录）：`runtime/perception/scene-*-browser-result.txt`、`scene-final-artifacts.json`，截图 `output/playwright/kaykit-tablet-*.png` 和 `kaykit-zoom-wide/close.png`。可复现脚本已整理到 `apps/perception-console/web/tests/`，临时 web 根目录验证页已移走。

## 查看与平板更新

- 电脑：[我的家](http://127.0.0.1:8770/)。刷新加载最终 dist，用滚轮放大查看设备标签。
- 本机平板浏览器入口：[平板视图](http://127.0.0.1:8771/tablet)。跨设备继续使用已有 Tailscale 私有 HTTPS 地址。
- Tailscale serve status 已核实 `https://iphone16promax.tail285617.ts.net` 代理到本机 8771，限 tailnet。
- 在已连接 Tailscale 的平板下载 [Android 2.1 APK](https://iphone16promax.tail285617.ts.net/tablet/app-debug.apk) 并覆盖安装；或者 Android Studio Run / `adb install -r <APK路径>`。保持原连接设置。此轮已构建并同步资源，**未执行真实平板安装**。

## 待用户验收

1. 真实灯光、亮度、制冷/送风、关闭和插座操作，核对 HA、面板及场景一致；控制未确认时不播放虚假成功效果。
2. 小米平板的横竖屏、触控缩放、远景隐藏/近景渐显阈值、家具比例与粒子观感。
3. 目标平板冷加载耗时、活跃/静止帧率、内存和后台行为；没有真机性能数据，不宣称 60 FPS。
4. 更多 AC 实例的总透明预算、真实户型和房内坐标属于后续工作；本轮静态三个房间为示意。

## 复现

```powershell
cd E:\smart-home
.venv\Scripts\python.exe -m pytest apps/perception-console/tests apps/home-service/tests/test_ha_control.py -q
cd apps\perception-console\web
node --experimental-strip-types --test src/*.test.mjs
$env:JAVA_HOME = 'D:\Android s\jbr'
$env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
$env:GRADLE_USER_HOME = 'E:\smart-home\runtime\gradle-cache'
npm.cmd run android:build
cd E:\smart-home
.venv\Scripts\python.exe apps/perception-console/web/tests/verify-artifacts.py
```

浏览器运行方法见 `web/tests/README.md`。模拟不连接真实控制接口。

## 暂停与续作记录

第一次暂停时：源码 29 项通过，14:36 首轮网页/APK 不包含最后审查修复，最终回归与重建尚未做。用户要求继续后，本报告以上完成状态已替代暂停快照：两项修复完成浏览器回归，补了缩放渐显与标签避让，前端 30 项通过，最终网页/Android 重建及资源一致性检查全部完成。没有重做素材下载，也没有修改 Unity 工程、凭据或 .env。

参考：[GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html)、[PointsMaterial](https://threejs.org/docs/pages/PointsMaterial.html)、[资源释放指南](https://threejs.org/manual/pages/how-to-dispose-of-objects.html)。
