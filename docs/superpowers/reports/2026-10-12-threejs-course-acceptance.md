# Three.js 场景装载、显示与漫游验收

验收时间：2026-10-12 20:40（Asia/Shanghai）。验收依据为老师原话：“用 Three.js 装载、显示场景模型，能实现一定的漫游。”

## 结论

**当前实现符合上述三项要求。** 已检查源码、实际 GLB 和正式构建页面，并在浏览器验证装载、显示、房间内行走、停止、重置与环视。此次无需修改运行功能，补充了可复现的验收脚本与本报告。

代码使用 TypeScript 编写，构建后运行 JavaScript。三维场景直接调用 Three.js；React 管理页面按钮、提示与挂载生命周期。因此当前采用的 React + TypeScript + Three.js 结构满足“用 Three.js”的技术要求。

## 要求与实现

| 老师要求 | 当前实现与源码 | 本次验证 |
| --- | --- | --- |
| 用 Three.js 装载场景模型 | [glb-walkthrough.ts](E:/smart-home/apps/perception-console/web/src/glb-walkthrough.ts:132) 使用 `GLTFLoader.loadAsync()`，随后将 `gltf.scene` 加入 Three.js 场景 | 正式页面请求完整 GLB，HTTP 200，619564 字节，文件头为 `glTF` |
| 显示场景模型 | 同文件创建 `Scene`、`PerspectiveCamera`、`WebGLRenderer`、灯光和阴影，并调用 `renderer.render(scene,camera)` | 实际截图可见三个房间、家具、设备外形及纹理；没有浏览器脚本、模型纹理或 CSP 错误 |
| 实现一定的漫游 | 同文件接收键盘与拖动输入；[walk-navigation.ts](E:/smart-home/apps/perception-console/web/src/walk-navigation.ts:6) 计算相机位移与简化碰撞；[ModelWalkthrough.tsx](E:/smart-home/apps/perception-console/web/src/ModelWalkthrough.tsx:8) 提供模式、房间、重置和触控按钮 | 客厅、卧室、厨房分别按 W 均产生可见移动，松键停止，重置恢复入场画面；拖动改变环视方向，能够返回全屋查看 |

完整模型文件：[smart-home-layout-2026-10-12.glb](E:/smart-home/apps/perception-console/web/public/assets/models/smart-home-layout-2026-10-12.glb)。实际解析为 glTF 2、1 个场景、151 个节点、80 个网格，包含三个房间的导航元数据。`public` 与正式 `dist` 中的文件 SHA256 一致：`f1a5412ca2d82ee7594ee0fe7494bac7a41d0179ec4c2e05355be6e9fa6c3a5e`。

## 验证证据

- 正式页面浏览器验收 **7 项通过**：GLB 装载、场景显示、客厅行走、卧室行走、厨房行走、拖动环视、返回全屋模式；错误列表为空。测试比较实际画面变化，各房间同时检查松键停止及重置。
- 移动与碰撞单元测试 **7 项通过**：朝向、斜向速度、恢复时位移限制、地板边界、薄障碍、碰撞滑动、俯仰角限制。
- TypeScript `tsc --noEmit --pretty false` 退出码为 0。
- 另经独立只读源码/GLB 核查，三个房间地板符合导航约定，入场点均未落入家具障碍。

浏览器输出：[glb-walkthrough-course-result.txt](E:/smart-home/runtime/perception/glb-walkthrough-course-result.txt)。验收脚本：[walkthrough-course-browser-check.js](E:/smart-home/apps/perception-console/web/tests/walkthrough-course-browser-check.js)。

实际画面：[全屋查看](E:/smart-home/output/playwright/course-overview.png)、[客厅](E:/smart-home/output/playwright/course-客厅.png)、[卧室](E:/smart-home/output/playwright/course-卧室.png)、[厨房](E:/smart-home/output/playwright/course-厨房.png)、[环视](E:/smart-home/output/playwright/course-look.png)。

## 演示步骤与范围

1. 打开 [场景漫游页面](http://127.0.0.1:8770/walkthrough)，等待模型显示。
2. 全屋模式拖动旋转、滚轮缩放，展示场景整体。
3. 点击“进入漫游”或房间按钮，使用 WASD / 方向键移动，Shift 快走，按住画面拖动环视。
4. 点击“重置视角”恢复当前房间入口，点击“全屋查看”返回全景。

当前属于固定视线高度的房间内漫游，跨房间通过按钮切换；模型没有连通门洞，尚未实现穿门连续走遍全屋。碰撞使用家具包围盒，位置和尺度属于示意模型。此范围符合提供的“一定的漫游”要求。

本次验收使用电脑 Chrome，未把结果视为真实平板性能或真实设备运行验收。静态 GLB 漫游页与原实时设备控制视图的用途分别记录在[原实现报告](2026-10-12-glb-walkthrough.md)。

## 复现

在 `E:/smart-home` 中执行（8770 服务应已运行）：

```powershell
playwright-cli.cmd -s=course-check open http://127.0.0.1:8770/walkthrough --headed
playwright-cli.cmd -s=course-check run-code --filename=apps/perception-console/web/tests/walkthrough-course-browser-check.js
```

在 `E:/smart-home/apps/perception-console/web` 中执行：

```powershell
node --test src/walk-navigation.test.mjs
./node_modules/.bin/tsc.cmd --noEmit --pretty false
```
