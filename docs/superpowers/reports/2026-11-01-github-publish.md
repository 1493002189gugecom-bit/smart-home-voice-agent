# GitHub 提交前验证

日期：2026-11-01。提交目标为当前分支 `feat/kaykit-device-effects`，远端为 `1493002189gugecom-bit/smart-home-voice-agent`。

## 本次归档范围

- Three.js 家具、确认快照驱动的设备效果、glTF/GLB 装载、OrbitControls、漫游和 Bloom 泛光。
- Blender 工程、选中运行模型、独立 Blender 参考资料与原素材许可。
- 语音流程、取消与超时处理、设备接口鉴权、视觉身份注册和控制台更新。
- Unity 工程退役、专用代码删除、当前说明与计划状态更新。
- 用户当前保存的 Word 作业原样复制到 `attachments/`，用于 GitHub 下载；没有修改该文档内容。

运行配置、凭据、模型权重、识别注册数据、录音、缓存、node_modules、虚拟环境、生成的网页 dist 和 Android 构建输出不进入提交。新增忽略 `.dcc-mcp/` 本机元数据与 Blender 自动备份，文件在本地保留。

## 当前验证

所有命令在当前工作树执行，Python 套件按服务分别运行，避免各应用同名模块互相污染。

| 套件 | 结果 |
| --- | --- |
| voice-service | 536 passed |
| home-service | 210 passed |
| vision-service | 108 passed，另有 14 个 subtests passed |
| perception-console | 43 passed |
| apps 共享测试 | 24 passed |
| device-simulator | 94 passed |
| HA 检查工具 | 35 passed |
| 转存 Blender 协议 | 3 passed |
| 前端 Node 测试 | 39 passed |
| TypeScript 与 Vite 构建 | 通过 |

Python 共 1053 项通过，前端 39 项通过。构建仍有既有大于 500 KB 的 bundle 提示，不影响构建成功。

完整 HA 工具套件首次发现一项旧断言只接受客厅、卧室两个区域；已按现有三房间目录补上厨房并更正测试名称，重跑 35 项全部通过。没有修改或执行真实 Home Assistant 区域分配。

本轮为离线自动化和构建验证，没有启动麦克风、摄像头、真实云 API 或设备控制，不将其称为真人或真实设备验收。

日志与提交内容审查记录位于本地忽略目录 `runtime/perception/git-publish-2026-11-01/`。
