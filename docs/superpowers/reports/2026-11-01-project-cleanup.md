# 项目目录清理记录

日期：2026-11-01。清理范围为智能家居主仓库 `E:\smart-home`。

## 已删除

共清理 29 处目录或文件、3565 个文件，项目目录减少 92064748 字节，约 87.8 MiB。

- 与智能家居无关的 DSH 技能管理工具源码：`dsh-client-ui-skill-explorer-source`、`dsh-skill-explorer-plus`、`dsh-skills-manager`、`upstream-src`。
- DSH 安装与故障排查文件：`diag`、根目录 `debug.log` 和 `install-fork.log`。
- 可重新生成的缓存与浏览器临时会话输出：根目录 `.playwright-cli`、`.pytest_cache`，以及 apps、tools 中的 Python `__pycache__` 和 pytest 缓存。

删除前核查了各目录用途、主项目引用、外部 DSH 配置和运行进程。独立 DSH Git 仓库工作树干净、无 stash、与 upstream 的 ahead/behind 均为零；未发现这些源码目录被当前进程或外部 DSH 配置及链接引用。

## 保留内容

- `apps`、`tools`、`infra`、`docs` 中的项目源码、配置和资料。
- 所有 Three.js / glTF / GLB / Blender 场景与家具资源、正式网页 dist、Android 工程及 APK。
- `.venv`、`.venv-vision`、前端 node_modules 等现有依赖环境。
- runtime 中的本地配置、凭据、识别数据、录音和运行记录。
- output 中的 Word 作业、结题文档、项目截图及验证结果。
- `.planning`、`.superpowers` 和根目录任务计划、发现、进度记录，均属于项目历史。
- `E:\unityroom` 下的 Unity 工程及其环境，本轮未清理。

没有通过“未被 Git 跟踪”判断文件是否无用，当前未提交和未跟踪的项目工作全部保留。

## 恢复备份

系统回收站 API 返回权限拒绝，因此先将无关工具源码及诊断文件备份并逐文件校验，再删除原目录。备份位于项目外：

`E:\smart-home-cleanup-backups\2026-11-01-unrelated-projects.zip`

包含 254 个原文件和内置清单，原内容 1080215 字节，ZIP 为 547431 字节。仅排除了可重新安装的 DSH node_modules；所有无关工具源码、独立仓库 Git 数据、锁文件和诊断文件均在备份中。Python / pytest / 浏览器缓存直接清理，不备份。

ZIP SHA-256：`51cadbd9bc55df1e7957dbd0877e6a5665b0bf9eac5f4c22ae0e64d0517e844a`。

如需恢复，可先将 ZIP 解压到项目外目录，按需取回原目录；DSH 的 node_modules 需按其锁文件重新安装。

## 清理后验证

- 965 个受保护的源码、模型、文档文件 SHA-256 均与清理前一致，没有文件缺失或内容变化。
- 新增本记录之前，主仓库 Git 状态与清理前完全一致；未重置、提交或推送任何原有改动。
- 29 个清理目标均已从项目目录移除。
- 正式网页 `/walkthrough?model=gltf`、glTF 模型及共享贴图均返回 HTTP 200，模型 114885 字节、贴图 49596 字节。

机器可读的删除清单、备份校验和完整性核对结果保存在 `runtime/perception/cleanup-2026-11-01/`。本次未改动应用逻辑，没有为清缓存重跑会生成缓存的测试或构建。
