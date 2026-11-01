# Blender glTF 导出与 OrbitControls 作业

日期：2026-10-20（Asia/Shanghai）。本次在已有 Three.js 页面与家居场景基础上完成模型导出、加载和鼠标控制的作业补齐。

用户已明确不需要代录视频，本次只提供自行录制的操作步骤。

## 当前结果

- 已通过 Blender MCP 从正确的 `.blend` 活动场景导出 glTF Separate，包含独立 `.gltf`、`.bin` 与共享贴图。
- 已增加正式网页入口 [glTF 模型页面](http://127.0.0.1:8770/walkthrough?model=gltf)，默认进入使用 OrbitControls 的“全屋查看”。
- 已明确编程配置左键旋转、中键/滚轮缩放、右键平移，页面有对应提示；原 GLB 入口与漫游保留。
- 浏览器鼠标验收 **8 项通过**，没有浏览器错误；按本次作业要求，模型导出、装载和常用鼠标控制已完成。

## 作业要求与代码位置

| 要求 | 实现 |
| --- | --- |
| 从 Blender 导出 glTF | 从 `smart-home-layout-2026-10-12.blend` 的 `SmartHome_KayKit_2026_10_12` 活动场景调用 Blender glTF 导出操作，格式 `GLTF_SEPARATE`，返回 `FINISHED` |
| 装载 glTF 模型 | [glb-walkthrough.ts:141](E:/smart-home/apps/perception-console/web/src/glb-walkthrough.ts:141) 调用 `GLTFLoader.loadAsync(modelUrl)`，加载完成后把 `gltf.scene` 加入 Three.js 场景 |
| OrbitControls 常用鼠标操作 | [glb-walkthrough.ts:50](E:/smart-home/apps/perception-console/web/src/glb-walkthrough.ts:50) 创建官方 OrbitControls 并设置鼠标映射，`change` 事件触发场景渲染 |
| HTML 页面显示 | [ModelWalkthrough.tsx:13](E:/smart-home/apps/perception-console/web/src/ModelWalkthrough.tsx:13) 按 `?model=gltf` 选择固定本地模型，将 Three.js 画布挂入现有网页 |

核心鼠标控制代码：

```javascript
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = false;
controls.enableRotate = true;
controls.enableZoom = true;
controls.enablePan = true;
controls.mouseButtons = {
  LEFT: THREE.MOUSE.ROTATE,
  MIDDLE: THREE.MOUSE.DOLLY,
  RIGHT: THREE.MOUSE.PAN,
};
```

相机围绕模型中心 `target` 旋转，缩放与平移均由官方控制器处理。未启用阻尼，静止时按需绘制，无需为鼠标控制保持持续动画循环。TypeScript 只提供类型检查，实际三维调用为 Three.js，构建输出是 JavaScript。

鼠标定义对应 [Three.js 官方 OrbitControls 文档](https://threejs.org/docs/pages/OrbitControls.html)。

## Blender 工程与模型文件

- [Blender 源工程](E:/smart-home/apps/perception-console/scene-assets/exports/smart-home-layout-2026-10-12.blend)。
- [smart-home.gltf](E:/smart-home/apps/perception-console/scene-assets/exports/gltf-2026-10-20/smart-home.gltf)：114885 字节，glTF 2.0 JSON。
- 同目录 `smart-home.bin`：473840 字节；`textures/Image_0.png`：49596 字节。
- [导出说明](E:/smart-home/apps/perception-console/scene-assets/exports/gltf-2026-10-20/README.md)。

三个文件必须按目录结构一起提交；只提交 `.gltf` 会缺少网格和贴图。模型含 3 个房间、21 个家具根节点和 8 个设备根节点，151 个节点、80 个网格、60 个材质。生成器记录为 `Khronos glTF Blender I/O v5.2.40`。模型采用已有 KayKit/Kenney 素材，来源和许可保留在素材目录说明中。

导出时包含 Custom Properties，保留导航需要的 `roomId`、`asset`、`deviceId` 与地板名称；只导出活动家居场景，没有导出原默认 `Scene`，没有覆盖源 `.blend`。

运行副本在 `E:/smart-home/apps/perception-console/web/public/assets/models/smart-home-gltf/`，构建后复制到 `web/dist/assets/models/smart-home-gltf/`。source/public/dist 三份资源逐字节一致，8770、8771 的三件套均 HTTP 200 且字节匹配。

## 自己录制时怎么操作

建议录制约 1 分钟，可按以下顺序演示：

1. 若要展示 Blender 导出过程，先打开上面的 `.blend` 工程，在 File → Export → glTF 2.0 中选择 **glTF Separate**，展示导出的 `.gltf`、`.bin` 和 `textures` 目录。保留 Custom Properties，并只导出当前家居场景。
2. 打开 [作业 glTF 页面](http://127.0.0.1:8770/walkthrough?model=gltf)，刷新并等待模型显示，页面副标题应为“Blender 导出的 glTF 模型”。
3. 保持 **全屋查看**，在模型画面空白处按住**鼠标左键拖动**，先左右、再上下，展示旋转。
4. **向上、向下滚动滚轮**，展示放大和缩小。
5. 按住**鼠标右键拖动**，展示场景平移，右键菜单不会盖住画面。
6. 点击**重置视角**，恢复完整场景，结束录制。

录 OrbitControls 作业时保持“全屋查看”。“进入漫游”会关闭轨道控制，改用 WASD/方向键行走和拖动环视，属于另一个演示功能。可另外展示 `new GLTFLoader()` 和 `new OrbitControls(...)` 两处代码，方便说明技术实现。

若网页服务未运行，可在 `E:/smart-home` 终端执行以下命令后再打开页面：

```powershell
.\.venv\Scripts\python.exe apps/perception-console/src/main.py --tablet-port 8771
```

## 验证证据

- TypeScript + Vite 网页构建成功：`index-Bphoy8iq.js`，955.12 KB / gzip 263.21 KB。既有大于 500 KB 的打包提示仍保留。
- 前端全量测试 **37/37** 通过。
- 原 GLB 入口浏览器回归 **7 项通过**：装载、显示、三房间移动/停止/重置、环视及模式切换；错误列表为空。
- 新 glTF 鼠标浏览器验收 **8 项通过**（11:10，Asia/Shanghai）：真实 glTF JSON 请求、外部资源、场景显示、左键旋转、滚轮缩放、右键平移、右键菜单阻止、无浏览器错误。三种鼠标操作分别检查画面变化、释放后稳定与重置恢复。
- 独立只读复核：新旧模型房间边界、27 个障碍和三个入场点兼容，模型包围盒仅有约 `1e-7` 的浮点导出误差。

证据位于 `E:/smart-home/runtime/perception/`：

- `blender-gltf-export-2026-10-20.txt`：实际 Blender MCP 导出日志。
- `blender-gltf-inventory.json`：glTF 内容与资源库存。
- `gltf-orbit-artifacts.json`：三份本地模型与两个端口资源一致性。
- `orbit-gltf-before.txt`：旧页面未请求 `.gltf` 的预期失败。
- `orbit-gltf-after.txt`：新入口鼠标控制验收。
- `orbit-gltf-glb-regression.txt`：原 GLB 入口回归。

测试脚本在 `web/tests/orbit-controls-browser-check.js`，只操作真实网页和截图，不录制视频、不发送设备控制。

实际截图：[初始全景](E:/smart-home/output/playwright/orbit-gltf-initial.png)、[左键旋转](E:/smart-home/output/playwright/orbit-gltf-left-drag-rotate.png)、[滚轮缩放](E:/smart-home/output/playwright/orbit-gltf-wheel-zoom.png)、[右键平移](E:/smart-home/output/playwright/orbit-gltf-right-drag-pan.png)。

验收脚本修正记录：CLI 的执行环境不提供全局 URL，URI 解析放在页面上下文；图片响应体读取偶尔为空，采用同一次真实页面请求的 Resource Timing 字节数并检查传输完成与 image/*，没有补发 fetch 来掩盖缺失资源。重置画面对比仅容忍 RGBA 每通道 1 级量化误差；独立诊断证实严格比较失败只涉及 35/744182 像素差 1 级。以上均为验证工具适配，没有更改真实产品交互。

本次更新了网页构建；原 Android 2.2 APK 保留为 10-12 版本，没有把新增 glTF 入口声称为已安装到平板。当前场景仍为静态示意模型，房间之间通过按钮切换。
