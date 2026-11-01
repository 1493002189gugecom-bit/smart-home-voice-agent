# 修正后的智能家居静态场景

2026-10-12 导出，包含三个房间、21 件家具、8 个设备外形。贴图内嵌，所有家具保持 React/Three.js 当前修正后的坐标、朝向和比例。

- `smart-home-layout-2026-10-12.glb`：通用模型，可在 Blender 的 File → Import → glTF 2.0 导入；619564 字节。
- `smart-home-layout-2026-10-12.blend`：已通过 Blender MCP 导入并保存的工程，可直接打开。新场景名 `SmartHome_KayKit_2026_10_12`，原默认场景 `Scene` 保留。

场景保留房间与家具层级、可编辑网格、材质、UV、内嵌图集和设备 ID。GLB 有 151 个节点、80 个 mesh 资源、60 个材质、1 张内嵌贴图。Blender 实际导入为 151 个对象、82 个网格对象；房间 3、家具根节点 21、设备根节点 8 均已核对。

这是静态外形与示意布局，单位不是实际住宅测量。人物示意标记、屏幕小标签、地面光晕、动态粒子/流线及设备状态动画不写入模型；继续由网页快照驱动。文件不是与 HA/网页自动同步的 Blender 工程。编辑后的模型需要另行导回项目。

GLB SHA256：`f1a5412ca2d82ee7594ee0fe7494bac7a41d0179ec4c2e05355be6e9fa6c3a5e`。

导出调用当前真实 Three.js 场景构造与素材加载；开发适配器在 `../../web/tests/scene-export.html`、`scene-export-browser.js`，不修改线上场景 API，不进入网页构建或 APK。先在 web 目录启动 Vite 8785，再从仓库根目录运行 Playwright CLI 的 `run-code --filename=apps/perception-console/web/tests/scene-export-browser.js`。

KayKit/Kenney 为 CC0，来源与许可见父目录的素材说明。GLB 已验证没有外部图片/缓冲依赖，Blender 图集已打包。

视图检查时 Blender 5.2.1 的 EEVEE 离屏截图触发崩溃。工程已提前保存，随后后台重新打开验证通过，并保存为 Solid + Texture 默认预览，保留贴图与材质数据。GUI 重新打开仍在着色器编译阶段退出，日志包含 `draw_shader_shared.hh` 的 GLSL `namespace` / `_pad1` 编译错误。因此**当前 Blender 界面预览未通过，应用没有保持打开**；不能把 MCP 导入成功等同于图形预览成功。GLB 数据与 .blend 后台读取已验证。

本次未升级插件、Blender 或驱动。Blender MCP 接口的模型导入、对象检查与保存已经实际执行成功，后续图形预览属于单独的环境问题。错误记录：`runtime/perception/blender-export-reopen.stdout.log`、`blender-export-reopen.stderr.log` 和系统 Temp 下的 `smart-home-layout-2026-10-12.crash.txt`。

## 2026-10-20：Blender 导出 glTF 作业

本次现有 Blender 窗口打开了该 `.blend`，MCP 场景读取与 Solid 预览正常。通过 MCP 从家居活动场景实际导出 [glTF Separate 三件套](gltf-2026-10-20/README.md)，操作返回 FINISHED；未升级软件或覆盖源工程，未复测上述 EEVEE 离屏渲染。

新文件为 `gltf-2026-10-20/smart-home.gltf`、`smart-home.bin`、`textures/Image_0.png`。网页 `/walkthrough?model=gltf` 直接装载新 glTF，OrbitControls 鼠标操作已验证；[报告和自行录制说明](../../../../docs/superpowers/reports/2026-10-20-gltf-orbitcontrols.md)。
