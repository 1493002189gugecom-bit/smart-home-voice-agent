# 第二版：电脑与平板设备手动控制

状态：源码、网页构建和 APK 构建完成；已通过 USB 更新到小米平板。真实设备响应速度、平板触控手感和设备效果待用户验收。

## 已实现

- 电脑“我的家”、平板浏览器和 Android 应用共享控制面板。传感器只读；灯光支持电源/亮度，空调支持电源/制冷/送风/16–30°C，插座支持电源。
- 按用户最终要求采用 iPhone 风格绿色胶囊开关，支持点按与左右滑动；白色亮度滑轨，拖动显示、松手自动同步；快捷档位、模式、温度按钮直接执行。取消确认按钮。
- 连续操作最多保留一个最新待发设置，顺序执行，避免同时写入和丢失最后设置。没有编辑的滑块失焦不发送；后台快照更新实际读数，发送与拖动期间不覆盖本地调整。
- 控制面板置于所选房间详情顶部，扩大横屏控制列；控件高度 48–64px。保留 3D 房间视图和实时状态。
- 唯一新增控制入口为 `POST /api/home/device/control`；先核查 HA 后端、快照新鲜度、在线状态、类型及范围，使用既有 operation_id 幂等设备工具。身份注册、语音和摄像头接口未开放给平板。
- 只有操作 ID、设备 ID 和实际观察值全部匹配才返回成功；超时、断线和未确认显示对应结果，同一次重试保留 operation_id。诊断结果不透出上游凭据或内部错误。

## 验证与复现

```powershell
cd E:\smart-home
.venv\Scripts\python.exe -m pytest apps/perception-console/tests apps/home-service/tests/test_ha_control.py -q
cd apps\perception-console\web
node --experimental-strip-types --test src/*.test.mjs
$env:JAVA_HOME = 'D:\Android s\jbr'
$env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
$env:GRADLE_USER_HOME = 'E:\smart-home\runtime\gradle-cache'
npm.cmd run android:build
```

- 后端与既有 HA 控制回归：90 项通过；覆盖过期/离线/模拟后端零写入、参数范围、严格确认、超时、来源和请求大小限制。
- 前端测试：23 项通过；TypeScript/Vite 与 Gradle debug 构建成功。APK versionCode 2、versionName 2.0，同应用 ID 覆盖安装。
- 真实电脑 `/api/home/device/control` 与 Tailscale HTTPS 入口以空对象验证返回 400；外部 Origin 返回 403。没有用这些请求控制真实设备。
- 隔离模拟服务器验证实际界面：开关点按关闭/开启、左滑关闭、键盘亮度调整；连续点 75%→25%→100%，实际仅顺序发送 75% 和最新 100%，该轮最终状态 100%，并发峰值 1。随后拖动滑轨，松手自动发送并确认 49%。记录位于忽略目录 `runtime/perception/manual-control-ui-check.json`，模拟服务器在验证后关闭。
- 真实页面只检查布局、显示与读数；截图在本地忽略目录 `runtime/perception/tablet-control-v2.jpg`。布局截图不等于真实平板性能或设备控制验收。

## 本日交互修正：设备选择与操作提示

用户反馈控制面板排在设备列表前方，切换设备需要向下滚动，旧操作提示跨设备/房间残留。已将设备选择区排在控制面板上方，选中设备后使用两列紧凑卡片并在滚动时吸顶；操作提示移到对应设备的控制区内。切换房间、切换或收起设备即清除提示；成功提示 3 秒自动收起，失败/未确认提示可手动收起并保留重试入口。

验证：隔离模拟界面确认设备选择区位于控制区上方且为 sticky；确认提示属于对应设备、切换设备及房间后提示数量均为 0；成功提示自动消失。前端 23 项回归通过，TypeScript/Vite 与 Android debug 构建通过，更新到 USB 平板。未操作真实设备。

## 待用户体验

打开更新后的平板应用并连接 README 地址，点房间和设备，检查胶囊开关、亮度拖动、温度加减的操作手感，核对真实设备及两端状态。模拟事件结果不代替真实设备效果。语音打断、人脸注册质量和识别成功率仍按原计划待真人验收。
