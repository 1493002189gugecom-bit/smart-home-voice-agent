# GitHub 提交前检查：Android、终端与家具素材

日期：2026-10-01。提交目标为现有分支 `feat/android-tablet-app`。

## 范围

提交当前项目中已完成的 Android 平板工程、Three.js 房屋示意视图、电脑/平板设备手动控制、统一终端主题、语音结果话术、摄像头房间切换修正、启停脚本、项目计划与素材库。

KayKit 53 个模型为主选，Kenney 140 个模型保留为储备；素材和预览已保存，但尚未替换运行场景或实现新增设备效果。

## 本轮检查

| 检查 | 结果 |
| --- | --- |
| 终端与 HA 控制回归 | 90 项通过 |
| 语音会话、事件字段和声纹接线回归 | 97 项通过 |
| 视觉健康、故障处理与房间切换回归 | 22 项通过 |
| 前端纯逻辑回归 | 23 项通过 |
| TypeScript / Vite 网页构建 | 通过；既有主 JS 包约 858 KB，仍有大包提示，后续按性能计划处理 |
| 启停脚本 PowerShell 语法 | 2/2 通过；未为提交检查启动摄像头或麦克风 |
| 素材目录、预览与模型校验值 | 193 个模型通过 |
| 本地配置与产物排除 | Android Studio `.idea/`、SDK 本地配置、缓存、APK、构建产物未纳入提交 |
| 凭据模式检查 | 候选文件未发现私钥、GitHub Token、云端 API Key 或长 Bearer 值；不输出候选值 |

最初把三个 Python 服务放入同一 pytest 进程时，其扁平模块 `config` 重名导致收集冲突。按服务独立运行后通过；不为解决测试进程的模块隔离问题修改运行服务。

## 复现命令

在 `E:\smart-home` 分别运行：

```powershell
.\.venv\Scripts\python.exe -m pytest apps/perception-console/tests apps/home-service/tests/test_ha_control.py -q
.\.venv\Scripts\python.exe -m pytest apps/voice-service/tests/test_agent_session.py apps/voice-service/tests/test_event_contract.py apps/voice-service/tests/test_speaker_wiring.py -q
.\.venv\Scripts\python.exe -m pytest apps/vision-service/tests/test_health_contract.py apps/vision-service/tests/test_runtime_failure_handling.py apps/vision-service/tests/test_room_monitor_lifecycle.py -q
```

在 `E:\smart-home\apps\perception-console\web` 运行：

```powershell
node --experimental-strip-types --test src/*.test.mjs
npm.cmd run build
```

## 验收边界

本轮为提交前回归，没有重装 APK、操作真实设备、启动摄像头或麦克风。既有 Android 构建与 USB 安装证据见[第二版报告](2026-10-01-manual-device-control-v2.md)。真人语音打断、人脸注册、实际设备效果和平板性能仍按原计划验收。
