# Blender 历史参考场景

2026-11-01 从退役 Unity 工作区转存，模型与插件内容按 SHA-256 校验一致。此目录可以独立于 Unity 保存和使用。

- `SmartHomeDigitalTwin.blend`：旧版数字孪生场景，保留其 `.blend1` 备份和两张预览图。
- `addon/smart_home_live/`：Blender 只读家庭状态插件，包含 SSE 解码与设备标签更新。
- `addon/tests/`：插件对应的协议单元测试和 Blender 运行脚本。

当前 Three.js 作业使用的 KayKit 家居工程仍为 `../exports/smart-home-layout-2026-10-12.blend`，运行模型仍在网页 `public/assets/models/`；本参考场景不替换当前网页模型。

协议测试可从主仓库运行：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider apps/perception-console/scene-assets/blender-reference/addon/tests/test_protocol.py -q
```

`blender_apply_test.py` 和 `blender_live_readonly_test.py` 需要在 Blender Python 中运行。后者要求实际后端与特定测试状态，本次迁移没有运行实时设备测试。
