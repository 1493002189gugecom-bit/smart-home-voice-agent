# Three.js 场景浏览器验证

2026-11-01 纹理与泛光：在仓库根目录运行 `playwright-cli.cmd -s=texture-bloom open http://127.0.0.1:8770/walkthrough?model=gltf`，随后 `playwright-cli.cmd -s=texture-bloom run-code --filename=apps/perception-console/web/tests/texture-bloom-browser-check.js`。检查实际贴图加载、泛光开关与画面变化、恢复稳定、三个尺寸下按钮不重叠及横向不溢出。仅截图，不录视频或控制设备。

模拟确认快照，仅用于开发验证，不发送真实设备控制。HTML 不是 Vite 正式构建入口，不进入 dist/APK。

在 web 目录运行 `npm.cmd run dev -- --port 8785`，另一个终端使用已安装的 Playwright CLI：

```powershell
playwright-cli.cmd -s=effects open http://127.0.0.1:8785/tests/scene-check.html
playwright-cli.cmd -s=effects run-code --filename=tests/scene-furniture-browser-check.js
playwright-cli.cmd -s=effects run-code --filename=tests/scene-effects-browser-check.js
playwright-cli.cmd -s=effects reload
playwright-cli.cmd -s=effects run-code --filename=tests/scene-inventory-browser-check.js
playwright-cli.cmd -s=effects run-code --filename=tests/scene-resources-browser-check.js
playwright-cli.cmd -s=effects run-code --filename=tests/scene-zoom-browser-check.js
# 最新网页构建由 8771 提供；布局脚本会模拟 /api/home/snapshot，结束时撤销
playwright-cli.cmd -s=effects goto http://127.0.0.1:8771/tablet
playwright-cli.cmd -s=effects run-code --filename=tests/scene-layout-browser-check.js
```

效果脚本覆盖亮度、重复快照、气流、离线、过期、隐藏和卸载；资源脚本覆盖加载失败、加载中卸载、重复挂载。隐藏使用 document.hidden getter + visibilitychange 验证应用处理器，不能替代实际 Android 后台生命周期测试。纹理计数的单个 DFG_LUT 是 Three.js 内部共享资源；应用贴图均应释放并结束 WebGL 上下文。

家具脚本调用真实加载器与已保存模型，检查床头靠墙、厨房正面朝操作通道、柜体靠墙、椅子朝桌、台灯落在承托面、挂画高度、边界与实体碰撞。房间边界来自当前示意场景的尺寸，通道以场景单位计算，不代表真实住宅测量。`layout-fixed-wide.png` 是修正后的默认视角。2026-10-12 修正前 11 项均能复现失败，修正后 11 项通过。

截图输出到运行目录的 output/playwright，JSON 返回值可保存到 runtime/perception。CLI 工具调用失败可能只有 Error 文本，应核查输出包含 Result 和完整 checks，不能只看进程退出码。

最终静态资源/APK 一致性检查：在仓库根目录运行 `.venv/Scripts/python.exe apps/perception-console/web/tests/verify-artifacts.py`。服务未开时可以加 `--offline` 仅核对本地包内容。

静态场景 GLB 导出：Vite 8785 运行时，在仓库根目录使用 `run-code --filename=apps/perception-console/web/tests/scene-export-browser.js`。导出调用真实构造与素材加载，等待 21 件家具全部就绪，只输出静态房屋/家具/设备外形，内嵌贴图；输出到 `scene-assets/exports/`。开发适配器不进入正式网页/APK。

GLB 漫游验证（从仓库根目录运行）：

```powershell
playwright-cli.cmd -s=walkthrough open http://127.0.0.1:8785/tests/walkthrough-check.html
playwright-cli.cmd -s=walkthrough run-code --filename=apps/perception-console/web/tests/walkthrough-browser-check.js
playwright-cli.cmd -s=walkthrough run-code --filename=apps/perception-console/web/tests/walkthrough-final-browser-check.js
```

开发观察器读取真实控制器/组件的渲染相机，没有替换渲染器或模拟模型。21 项覆盖模型、键盘、拖动、屏幕方向键、Chrome CDP 双指触控、隐藏、释放、失败重试与四比例。最终入口脚本验证实际 8770/8771/CSP 下的纹理与可见行走，以及“我的家”入口。CDP 触控与 document.hidden 检查不是 Android 真机验收。

课程要求验收仅依赖正式 8770 页面，无需启动 Vite 或平板端口。在仓库根目录执行：

```powershell
playwright-cli.cmd -s=course-check open http://127.0.0.1:8770/walkthrough --headed
playwright-cli.cmd -s=course-check run-code --filename=apps/perception-console/web/tests/walkthrough-course-browser-check.js
```

脚本验证真实 GLB 请求与文件头、场景显示、三房间行走/停止/重置、拖动环视和返回全屋模式，截图保存到 `output/playwright/course-*.png`。本次报告为 `docs/superpowers/reports/2026-10-12-threejs-course-acceptance.md`。

2026-10-20 glTF / OrbitControls 作业验收，在仓库根目录运行：

```powershell
playwright-cli.cmd -s=gltf-orbit-check open "http://127.0.0.1:8770/walkthrough?model=gltf"
playwright-cli.cmd -s=gltf-orbit-check run-code --filename=apps/perception-console/web/tests/orbit-controls-browser-check.js
```

8 项检查覆盖页面真实 `.gltf` 请求、外部 bin/PNG 响应、场景显示、左键旋转、滚轮缩放、右键平移、上下文菜单阻止和无浏览器错误。三种操作均检查释放稳定和重置恢复；PNG 解码后逐通道只容忍 1 级舍入，图片响应体的 CLI 提取为空时记录同次页面请求的 Resource Timing 大小，不补发请求。输出须同时确认 `passed:true`、8 条 checks 和空 errors；CLI 进程退出 0 不能代替该判断。只保存 `output/playwright/orbit-gltf-*.png` 截图，不录视频。
