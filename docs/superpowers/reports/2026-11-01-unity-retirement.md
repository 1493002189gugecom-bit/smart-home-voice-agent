# Unity 工程退役与 Blender 资料转存

日期：2026-11-01。用户明确当前只使用 Blender + Three.js，本次在项目目录清理基础上删除旧 Unity 工程和专用工具。

## 已移除的目录

- `E:\unityroom\unityroom`：旧 Unity 工作区。
- `E:\smart-home\apps\room`：另一份旧 Unity 工程。
- `E:\smart-home\apps\unity-house-payload`：旧 Unity C# 导入包。
- `E:\smart-home\tools\unity-check`：旧 Unity 校验与导入工具。

共移除 4 个目录、31896 个文件、509342283 字节，约 486 MiB。包含 Unity Library、Temp、Logs、UserSettings 等生成文件；没有卸载系统软件，也没有删除外层聊天工作目录 `E:\unityroom`。

主 README 改为当前 Blender + Three.js 展示架构，移除 Unity 启动和导入入口；Git allowlist 移除退役包与工具的配置。历史 Unity 计划、报告继续作为项目过程资料，并在当前状态入口说明其已退役。

## Blender 文件保留位置

旧 Unity 工作区中独有的以下 9 个文件已复制到：

`E:\smart-home\apps\perception-console\scene-assets\blender-reference`

- `SmartHomeDigitalTwin.blend` 和 `.blend1`。
- 两张 Blender 场景预览 PNG。
- `addon/smart_home_live` 下的 2 个 Python 插件文件。
- `addon/tests` 下的 3 个协议和 Blender 验证脚本。

转存文件逐个 SHA-256 校验一致，目录结构不再依赖 Unity Assets。纯协议测试在新位置运行，3 项通过；没有运行需要 Blender 与真实后端的实时测试。

当前 Three.js 作业仍使用 `scene-assets/exports/smart-home-layout-2026-10-12.blend` 与原 glTF/GLB 运行模型。转存的旧 Blender 场景仅作参考，不覆盖当前家具布局、纹理和泛光。

## 项目外备份

源资料已打包到：

`E:\smart-home-cleanup-backups\2026-11-01-retired-unity-sources.zip`

备份含 230 个源文件及内置校验清单，包括 Assets、Packages、ProjectSettings、Plastic 工作区元数据、原计划记录、C# payload 和工具源码。仅排除可重新生成的 Unity 缓存、构建输出和日志。ZIP 4711166 字节，已逐项读回校验。

SHA-256：`37de4cb7f5c600fa3d9708decb611225a5b85855698d047983bba9ab411275b4`。

## 验证

- 除明确要删除的 Unity 文件和本次要更新的说明文件外，944 个受保护的主项目源码、模型与 Word 文档校验一致，没有意外缺失或修改。
- 主仓库有 14 个预期的 Unity 跟踪文件删除；没有提交、推送或重置原有工作。
- 4 个退役目录均不存在，9 个 Blender 转存文件均存在且哈希一致。
- 当前模型网页、glTF JSON 和共享贴图均 HTTP 200。
- 转存插件的协议测试 3 项通过，README 与 Git 配置差异检查通过。

机器可读记录保存在 `runtime/perception/cleanup-2026-11-01/unity-before.json`、`unity-deleted.json`、`unity-verification.json`。当前 home-service、视觉、语音和网页服务代码未作调整。
