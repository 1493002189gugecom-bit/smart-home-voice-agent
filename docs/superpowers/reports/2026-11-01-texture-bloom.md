# 模型纹理核查与 Three.js 泛光

日期：2026-11-01。作业基于现有模型加载与 OrbitControls 页面继续完成。

## 已有纹理

核查运行资源 `web/public/assets/models/smart-home-gltf/smart-home.gltf`：60 个材质中有 15 个引用 baseColorTexture，15 个 texture 定义共享 `textures/Image_0.png`（49596 字节），80 个网格 primitive 均有 TEXCOORD_0。家具已有纹理与 UV，不需要重新覆盖所有材质；墙地板等部分沿用纯色材质。GLTFLoader 按 glTF 引用自动加载图片，泛光后仍保留原有贴图。

## 本次实现

- 在 Three.js 网页查看器加入 EffectComposer → RenderPass → UnrealBloomPass → OutputPass，HDR 高亮经阈值提取、模糊叠加后统一执行色调映射与 sRGB 输出。
- 灯罩上部添加静态暖色高亮，保留原几何体、UV 和纹理。灯具材质克隆后修改 shader，避免整张家具图集变亮。模型文件与 Blender 工程未修改。
- 泛光默认开启，右上角“泛光”按钮可关闭/开启，便于作业截图对比。GLB 和 glTF 入口均生效；OrbitControls 与原漫游继续可用。
- 限制像素比为 1.5，按交互事件绘制，静止时不增加持续动画循环，隐藏页面时停止绘制；卸载释放后处理通道、渲染目标、克隆材质和共享模型资源。
- 页脚明确“灯光仅作效果演示”。该静态查看器不接设备快照，不表示真实灯具开启。首页设备效果继续采用已有确认快照逻辑。

核心代码：`web/src/glb-walkthrough.ts`。按钮与界面状态：`web/src/ModelWalkthrough.tsx`；尺寸适配：`web/src/walkthrough.css`。没有安装新依赖。

## 查看方法

打开 <http://127.0.0.1:8770/walkthrough?model=gltf>，刷新页面，保持“全屋查看”。滚轮拉近灯具，再点击右上角“泛光”比较光晕。旧 GLB 入口 <http://127.0.0.1:8770/walkthrough> 同样支持。

## 验证

- TypeScript 与 Vite 正式构建通过，既有大于 500 KB 的 bundle 提示保留。
- 前端全套 Node 测试 39/39 通过。
- 新浏览器验收通过：可见的泛光开关、开关恢复稳定、1024×768 / 768×1024 / 360×800 适配及真实贴图加载；错误列表为空。
- glTF / OrbitControls 原浏览器 8 项回归通过；GLB 原漫游 7 项回归通过，三房间均能移动、松键停止、重置、拖动环视及返回全屋模式。
- 验收图片在 `output/playwright/texture-bloom-on.png`、`texture-bloom-off.png` 与三个尺寸截图；日志在 `runtime/perception/texture-bloom-acceptance.txt`、`bloom-orbit-regression.txt`、`bloom-walk-regression.txt`。

截图比较工具修正：画布截图包含覆盖其上的 DOM 工具栏，按钮边框有 0.2 秒 CSS 过渡。泛光测试先等待该过渡完成；漫游图像对比仅在截图瞬间隐藏工具栏覆盖层，同时独立检查真实按钮状态。图像等同性最多容忍每通道 1 级 GPU 量化差，不用整帧百分比掩盖运动。原严格 PNG 字节比较曾报告松键/重置失败，独立像素诊断和修正后的回归已排除该误判，未更改行走逻辑。

本次更新网页 dist，没有更新旧 Word、Android 资源或 APK。没有录制视频，没有真实设备或目标平板 GPU 性能实测；实际观感与平板性能留待用户验收。
