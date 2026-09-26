# Home Assistant 虚拟设备闭环验收报告

日期：2026-09-13
状态：**自动化、真实栈与网页人工确认全部完成**

对应规格：`docs/superpowers/specs/2026-09-13-ha-closed-loop-completion-design.md`
对应计划：`docs/superpowers/plans/completed/2026-09-13-ha-closed-loop-completion.md`（计划 2026-09-13，完成 2026-09-13）

## 1. 结论

四类虚拟设备已通过 MQTT Discovery 注册进现有 Home Assistant，`home-service` 的
HA 后端可以在**观察到真实状态**之后才报成功，并且离线、注入失败、延迟超时与
重复操作都按设计给出诚实语义。真实栈验收 14/14 通过。

本轮未接入云端 LLM，未改动 Unity 与空间模拟，符合规格范围。

## 2. 目标与实际环境的偏差（重要）

| 计划假设 | 实测结果 | 处置 |
| --- | --- | --- |
| `object_id` 可固定 entity_id | HA 2026.6.3 对 MQTT Discovery **忽略** `object_id` | 改为让实体名 slug 恰好等于稳定 ID |
| 有 `device` 块可兼顾分组与稳定 ID | HA 把设备名 slug 前缀拼进 entity_id，产生 `light.ke_ting_deng_shv_living_room_light` | 移除 `device` 块，实体 ID 才是可预测的稳定 ID |
| 命令行 PowerShell 为 7.x | 实为 **Windows PowerShell 5.1**，原生 stderr 会变终止错误 | 脚本改用 `Invoke-Native` 包装并显式检查退出码 |
| 删除主机文件即可让容器重新生成 | Docker Desktop 绑挂载仍报「文件已存在」 | 先生成 `password_file.new`，再在主机侧改名就位 |

关键点：这些偏差都是**在真实栈验证中暴露**的，不是靠假设通过的。若只跑单测，
闭环会在「实体 ID 不匹配」这一步静默失败。

## 3. 自动化验证

| 套件 | 结果 |
| --- | --- |
| `apps/home-service/tests` | **144 passed** |
| `apps/device-simulator/tests` | **78 passed** |
| `apps/voice-service/tests` | **51 passed**（无回归） |
| `tools/ha-check/tests` | **28 passed** |
| 合计 | **301 passed** |
| `compileall`（三个服务 + 工具） | exit 0 |
| `git diff --check` | 无输出 |

## 4. 真实栈验收

命令：

```powershell
$env:HA_ENV_FILE='E:\smart-home\runtime\home-assistant\ha.env'
.\.venv\Scripts\python.exe tools\ha-check\acceptance.py
```

结果：`"ok": true`，14/14：

| 检查 | 结果 |
| --- | --- |
| `service_reachable` | PASS（HA 后端，127.0.0.1:8765） |
| `four_entities_discovered` | PASS |
| `entities_available` | PASS |
| `light_50_confirmed` | PASS，`status=confirmed`，亮度 50 |
| `ac_cool_24_confirmed` | PASS，`mode=cool`，`temp=24.0` |
| `duplicate_operation_id_replays` | PASS，两次同为 200 |
| `operation_id_conflict_detected` | PASS，409 |
| `offline_device_is_rejected` | PASS，`offline` |
| `offline_device_recovers` | PASS |
| `injected_failure_is_not_reported_as_success` | PASS，`unconfirmed`/`confirmation_timeout` |
| `injected_failure_keeps_device_state` | PASS，`on -> on` |
| `delay_reports_unconfirmed_not_failure` | PASS，`confirmation_timeout` |
| `delayed_command_completes_late` | PASS，亮度最终到 90（证明超时≠失败） |
| `service_health_after_faults` | PASS |

延迟用例尤其重要：超时后命令**迟到完成**，正好证明 `unconfirmed` 的语义是
「未确认」而不是「失败」。

## 5. 实体与控制契约

稳定 ID 与 HA entity_id 一一对应，且 `shv_` 前缀唯一：

| 稳定 ID | entity_id | 实测状态 |
| --- | --- | --- |
| `living_room_light` | `light.shv_living_room_light` | on |
| `bedroom_ac` | `climate.shv_bedroom_ac` | cool |
| `desk_plug` | `switch.shv_desk_plug` | off |
| `indoor_temperature` | `sensor.shv_indoor_temperature` | 26.0 |

灯光亮度换算经实测确认：HA 侧 128 → `home-service` 归一为 50（0–255 → 0–100）。

## 6. 安全边界实测

| 项目 | 实测 |
| --- | --- |
| HA 端口 | 仅 `127.0.0.1:8123`，非 `0.0.0.0` |
| MQTT 1883 / 9001 | **未发布到主机**，仅 Compose 内部网络 |
| 模拟器容器 | 无任何宿主端口 |
| 容器用户 | `simulator`（uid 10001，非 root） |
| Mosquitto | `allow_anonymous false`，两套独立凭据 |
| 凭据文件 | `password_file` 0644（仅哈希），位于 ignored `runtime/` |
| 已提交秘密 | `git grep` 只命中占位符、变量名与测试夹具，无真实值 |

## 7. 本轮修复的真实缺陷

按发现顺序：

1. `password_file` 权限 0600 → broker 以非 root 运行，无法读取密码文件，容器反复重启。
2. 模拟器在**从未连上 broker** 时关闭会抛 `RuntimeError` 崩溃；`graceful_shutdown`
   现在容错且只执行一次。
3. `prepare_stack.ps1` 在 PowerShell 5.1 下把 Docker 的 stderr 当终止错误，**成功也报失败**。
4. HA 忽略 `object_id`：entity_id 变成拼音（`light.ke_ting_deng_ke_ting_deng`），
   与实体目录完全不匹配 → 控制全部 `not_found`。这是本轮最大的隐性故障。
5. 凭据轮换路径不可用：`mosquitto_passwd -c` 拒绝覆盖，且绑挂载让容器仍看到已删除文件。
6. 验收脚本自身把「等待状态」helper 写成返回布尔，导致后续下标取状态时报 `TypeError`。

第 4 项特别说明：**单元测试无法发现它**，因为 Mock 网关不会做 slug 化。
只有真实 HA 验证才能暴露，这也是坚持做真实栈验收的价值。

## 8. 运行态与运维入口

- 已启动：`homeassistant`、`mosquitto`、`shv-device-simulator`（均 `Up`）。
- `home-service` 以 `HOME_SERVICE_BACKEND=ha` 在 127.0.0.1:8765 运行。
- 凭据在验收过程中**已整体轮换一次**（因为本地密码值曾出现在会话输出中），
  轮换后重新完成 HA 重配置，并**重新跑通 14/14 验收**。

## 9. 尚未验证的项目（不声称通过）

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| HA 网页人工确认 | **已验证（用户确认）** | 用户在 `http://127.0.0.1:8123` 看到四个实体且状态正常 |
| 容器重启后自动重新注册 | **未单独复现** | 模拟器在多次 `restart`/`--force-recreate` 后均重发 Discovery 并被 HA 重新接纳（本轮实际操作多次），但未做整机 `down/up` 的一次性复现 |
| `home-service` 崩溃恢复的现场复现 | **部分** | `accepted`/`submitted`/`unconfirmed` 恢复策略有单测；未在真实栈制造未终结操作后重启验证 |

未完成项均为复现类加固，不含未实现功能。规格要求的实现、自动化验收、真实栈
验收与网页确认均已通过。

## 10. 提交记录

```
e4c79cf fix(ha): make credential rotation and generation reliable
50ebb48 fix(simulator): pin predictable entity ids and reset tooling
8ab986c test(ha): add repeatable closed-loop acceptance
f830005 feat(home): select Home Assistant backend explicitly
d63540f feat(home): confirm Home Assistant device operations
2814290 build(ha): add secure simulator stack override
66589b3 feat(simulator): publish MQTT virtual devices
940f2e8 feat(home): add persistent operation state machine
5be7971 feat(simulator): add validated device state model
d2ebb05 docs: bind Home Assistant to loopback
```
