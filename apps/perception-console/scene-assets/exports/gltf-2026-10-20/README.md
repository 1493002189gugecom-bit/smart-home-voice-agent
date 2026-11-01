# Blender 导出的 glTF 模型

2026-10-20 通过 Blender MCP，从已打开的 `smart-home-layout-2026-10-12.blend` 中活动场景 `SmartHome_KayKit_2026_10_12` 实际导出，操作返回 `FINISHED`。没有导出另一个默认场景 `Scene`，没有覆盖源 `.blend`。

导出格式为 Blender 的 `glTF Separate`，生成器为 `Khronos glTF Blender I/O v5.2.40`：

| 文件 | 字节 | 用途 |
| --- | ---: | --- |
| `smart-home.gltf` | 114885 | glTF 2.0 JSON，记录场景层级、材质和资源引用 |
| `smart-home.bin` | 473840 | 网格和顶点数据 |
| `textures/Image_0.png` | 49596 | 家具共享贴图 |

三个文件必须保持相对位置一起复制、提交。只复制 `.gltf` 会丢失网格或贴图。

包含 151 个节点、80 个 mesh、60 个材质；保留 3 个房间、21 个家具根节点、8 个设备根节点，以及 `roomId`、`asset`、`deviceId` 元数据和 `<roomId>_floor` 地板名称。

运行副本在 `E:/smart-home/apps/perception-console/web/public/assets/models/smart-home-gltf/`。模型来源与 CC0 许可沿用上级素材目录说明；这是静态示意布局。

手动复现：在 Blender 打开上级 `.blend`，切到 `SmartHome_KayKit_2026_10_12` 场景，File → Export → glTF 2.0，选择 glTF Separate，导出活动场景、包含 Custom Properties，关闭动画、相机和灯光导出，贴图子目录设为 `textures`。网页中的相机、灯光和 OrbitControls 由 Three.js 代码建立。

导出原始日志与资源库存位于 `E:/smart-home/runtime/perception/blender-gltf-export-2026-10-20.txt`、`blender-gltf-inventory.json`。使用说明和本次作业验收结果在 `docs/superpowers/reports/2026-10-20-gltf-orbitcontrols.md`。
