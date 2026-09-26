# 感知终端交接说明（2026-09-25）

> 本文由用户口述的交接要点整理，并**由代理做只读核对后修正了路径**。凡标注「已核对」的内容是本次实际读文件/跑只读命令确认的；标注「用户报告」的是用户陈述、代理未独立复现。

## 一、本次任务目标

仓库 `E:\smart-home`，Unity 项目 `E:\unityroom\unityroom`。用户要四件事：

1. 注册完成后**明确提示成功**；
2. 支持**自填新增人物**（如姑姑/舅舅），并同步到 Unity 角色；
3. 识别到爸爸时，Unity 显示其位于**卧室**；
4. **终端设备状态改为中文**。

### 用户对第 3 条的进一步澄清（重要，决定实现方向）

> 「如果我把终端的摄像头场景变为客厅，那自然爸爸的位置也要改为客厅 —— 就是实现**实时显示摄像头里可视人物的位置**。」

也就是说：**人物的房间 = 当前摄像头的逻辑房间**，切换房间后人物位置必须跟着变。
这一点在结构上**已经成立**（见第二节「已核对」第 6 条），验收点是"切房间后快照是否跟着变"，而不是需要新写一套逻辑。

## 二、已核对的事实（代理实读确认）

1. **注册成功**：本次 `dad` 人脸注册成功，`/registration` 返回 `state: completed`（用户报告）。
2. **实时识别**：`/results` 曾返回 `dad`、`identity_state: confirmed`、房间 `bedroom`；家庭服务 `/snapshot` 也显示爸爸在卧室（用户报告）。
3. **注册页完成后变黑**的原因：预览只在采集中显示（用户报告）。
4. Unity 现有爸爸角色本来就会从家庭服务快照更新房间（用户报告）。
5. **路径纠正 1**：人物存储**不在** `apps/vision-service/src/visual_state.py`，实际是 **`apps/home-service/src/visual_state.py`**（已核对存在，且工作树中为已修改状态）。
6. **路径纠正 2**：`SmartHomeSceneController.cs` **不在** `apps/unity-house-payload/`（那是旧的 payload 目录），实际是
   **`E:\unityroom\unityroom\Assets\SmartHome\Runtime\SmartHomeSceneController.cs`**（已核对，12,356 字节，最后修改 2026-09-25 11:06:33）。
7. **人物房间跟随摄像头**：`apps/home-service/src/visual_state.py:564` 为 `view.room_id = track.camera_room_id`（已核对）——即人物对外发布的房间就是摄像头的逻辑房间。**用户澄清的第 3 条要求在该行已实现**，房间切换后会在观察 TTL（3.5 秒）内跟着变。
8. **动态人物**：同文件 `:184` 有 `PERSON_ID_PATTERN` 校验、`:223` 有 `create_person(display_name)`、`:230` 有按 `casefold` 的重名拒绝（已核对）→ 支持非 `dad/mom/child` 的新人物。
9. **`/persons` 接口**：`apps/home-service/src/server.py:228` 与 `apps/home-service/src/ha_service.py:335` 都有（已核对）；新建人物由新测试 `apps/home-service/tests/test_person_directory.py` 覆盖（`POST /persons`，已核对文件存在）。
10. **`/registrations`（复数）已录入名单**：`apps/vision-service/src/server.py:212`（已核对）。
11. **录入前向家庭服务核对**：`apps/vision-service/src/server.py:188` 会 `GET {home_service_url}/persons`（已核对）。
12. **`tools.py` 的 `observed` 变量**：`apps/home-service/src/tools.py:112` 为 `observed = visual.get(item.id) or {}`（已核对，定义存在）。
13. **网页终端**：`apps/perception-console/`（`web/src/App.tsx` 33,261 字节，最后修改 2026-09-25 11:09:29；另有新增 `registration.ts`、`device-labels.ts`、`audio-device-ui.ts` 及各自 `.test.mjs`）（已核对）。
14. **`tools/start-perception.ps1`** 存在（已核对）。
15. **仓库状态**：最新提交 `8661f21`（2026-09-24 `docs: plan perception UX and audio implementation`）；工作树有 25+ 个已修改文件、12 项未跟踪路径，**全部未提交**（已核对）。

## 三、本轮已写入、尚未提交的改动

家庭服务（`apps/home-service/src/`）：新增持久人物名单与 `/persons` 接口（`server.py`、`ha_service.py`、`visual_state.py`、`tools.py`）。

视觉服务（`apps/vision-service/src/`）：允许动态人物编号；提供 `/registrations`；在新人物开始录入前向家庭服务核对（`server.py`、`pipeline.py`、`contracts.py`、`registration.py`、`registry.py`、`face.py`、`capture.py`、`main.py`）。

网页（`apps/perception-console/`）：新增自填人物、录入状态、醒目的成功提示；设备状态改为中文（`web/src/App.tsx`、`styles.css`、新增 `registration.ts`、`device-labels.ts`、`audio-device-ui.ts`、`src/proxy.py`、`src/main.py`）。

Unity：`E:\unityroom\unityroom\Assets\SmartHome\Runtime\SmartHomeSceneController.cs` 增加根据快照**动态克隆人物标记和标签**。

另修复 `apps/home-service/src/tools.py` 中房间查询引用未定义变量 `observed` 的错误。

### 验证状态的诚实区分

- **用户报告已通过**：网页 `npm run build`；视觉服务测试 19 项；家庭服务测试（修 `observed` 后）；终端网页测试；新增人物目录的两个测试。
- **均未验证**：Unity 编译与运行（**代理至今没有编译过这批 Unity C#**）；**正在运行的服务尚未重启**，因此浏览器后端可能仍在跑旧代码。
- 当时未发现 Unity 编辑器进程，也未发现 `unity` CLI，但 `D:\unity\edition\2022.3.47f1c1\Editor\Unity.exe` 存在。

## 四、已知问题：切房间"看起来失败了"（根因已定位）

用户观察到：后端切换房间时会清除旧监控会话并用新房间继续识别，但网页随后又发一次"开始监控"，报"已经在监控"，于是**切换看起来失败**。

**根因链条（代理实读确认）**：

1. `apps/vision-service/src/pipeline.py` 的 `select_room` 在房间变化时自动重启监控（`was_monitoring` 分支）→ 模式保持 `MONITORING`。
2. `apps/vision-service/src/server.py:153-156`：`/monitor/start` 在模式已是 `MONITORING` 时返回
   **409 `already_monitoring`「当前已在监控」**。
3. 网页在切房间后又发一次 `/monitor/start` → 收到 409 → 界面显示错误，**尽管后端其实已经切好房间**。

同类问题：`/monitor/pause`（`server.py:169-170`）在未监控时返回 409 `not_monitoring`。

**两条可选修法**（用户已在 App.tsx 里做了 (a)，改动 +19/−9）：

- (a) 网页把 409 `already_monitoring` 当作**成功**（幂等），随后刷新状态并**明确显示当前生效的监控房间**；
- (b) 后端把 `/monitor/start` 改成幂等（已在同一房间/会话时返回 200 而不是 409）。

需要决定的是：`already_monitoring` 到底算不算错误？若要保留严格 409，客户端必须容忍它，并区分"已在我想要的房间监控"（正常）与"在别的房间监控"（需要切换）。

## 五、请优先继续

1. 检查**跨服务人物创建与注册链路的边界问题**（`POST /persons` → 视觉服务核对 → 注册 → 识别 → 快照）。
2. 验证 **Unity 动态角色脚本可编译、标签正确**（注意：脚本在 `E:\unityroom\unityroom`，不在仓库 payload 里）。
3. 检查**网页实际显示**（设备中文、录入成功提示、当前生效房间）。
4. 按 `tools/start-perception.ps1` 的进程清单**确认归属后**，谨慎重启受影响服务，并**恢复用户原来的卧室监控**。
5. 重启前**记录摄像头与房间状态**；**不要误删现有爸爸的人脸数据**。
6. 最终实际验证：新增人物名单、录入成功提示、爸爸在 Unity 的卧室显示。无法实测的部分如实说明。

## 六、注意事项

- **不要整体重置或覆盖工作树**：里面混有大量其他未提交改动（含 voice-service、perception-console、docs 的并行工作）。
- 人脸特征在 Git 忽略的 `runtime/vision` 下、用 DPAPI 绑定当前 Windows 用户加密；**删除该目录等于删除爸爸已注册的人脸**。
- 启动顺序有硬依赖：**home-service 先启动并创建 `runtime/vision/home-service.token`，视觉服务只读**。
- 端口：HA `127.0.0.1:8123`、home-service `127.0.0.1:8765`、vision-service `127.0.0.1:8766`。
- 解释器陷阱：项目必须用 `E:\smart-home\.venv\Scripts\python.exe`（Python 3.11.9）。裸 `python` 在本机是 `E:\py\python.exe`（3.13），两者互相看不见，混用会产生"一个说有、一个说没有"的假象。
- 本文件中标注「已核对」的结论来自只读检查；**代理未运行任何测试、编译、模型推理、摄像头或截图**。

---

## 七、解决记录（同一日，接手后完成）

### 已修复的三个真实缺陷

1. **监控中扫描摄像头会让视觉服务崩溃（最严重）**
   `runtime/vision/native-fault.log` 记录了一次真实的 Windows 访问违例，现场是**多个线程同时执行 `capture.py` 的 `enumerate_cameras`**（逐索引打开 `cv2.VideoCapture`），由 `GET /cameras` 触发。
   **修复**：枚举加互斥锁串行化；枚举时跳过正在被采集线程占用的设备索引（仅在 `mode` 为 `monitoring`/`registering` 时）。
   **实测**：监控中连续调用 `/cameras` 三次，每次只返回未被占用的设备（`device:2`），服务存活、仍是 `monitoring`/`bedroom`；修复前会杀进程。

2. **`runtime/vision/persons.json` 带 BOM 会让 home-service 启动即崩**
   接手过程中代理自己用 PowerShell `Set-Content -Encoding UTF8` 写该文件（PS 5.1 会写 UTF-8 BOM），home-service 用 `encoding="utf-8"` 读取 → `JSONDecodeError: Unexpected UTF-8 BOM` → 服务直接退出。这正是项目 `findings.md` 早已记录的坑。
   **修复**：文件改写为无 BOM；`visual_state.py` 改用 `utf-8-sig` 读取（Notepad/PowerShell 产生的 BOM 不再能阻止启动），并把裸 traceback 换成指明文件与行号的错误。

3. **感知终端的测试套件根本无法收集**
   `apps/perception-console/tests/test_status.py` 直接 `import main`，但该包 `pyproject.toml` 没有任何 pytest 路径配置 → `ModuleNotFoundError: No module named 'main'`。
   **修复**：新增 `apps/perception-console/tests/conftest.py` 把 `src` 加入 `sys.path`。

### 已实测通过的功能（真实摄像头 + 真实服务）

| 要求 | 证据 |
| --- | --- |
| 识别到爸爸时显示在卧室 | 快照 `dad room=bedroom location_known=True location_source=camera camera_id=device:0` |
| **房间跟随摄像头**（用户澄清的核心意图） | 真实调用 `/room/select`：卧室 → **客厅时 `dad room=living_room`** → 切回卧室又变 `bedroom`，全程 `mode=monitoring` |
| 过期诚实回落 | 观察超过 3.5 秒 TTL 后 `room_id=null`、`location_known=false`，不回退旧房间 |
| 自填新增人物 | `POST /persons` 创建**姑姑**、**舅舅**，返回与持久化的中文名均正确（UTF-8 字节核验），出现在 `/persons`、`/snapshot`（位置未知），视觉服务 `/registrations` 报告 dad 已录入 |
| 设备状态中文 | `device-labels.ts` 对 8 个真实设备状态逐项核对：灯→`开启 · 亮度 75%`、空调→`开启 · 制冷 · 目标 26°C`、插座→`开启`、传感器→`26°C` |
| 注册成功提示 | `App.tsx:344` 在 `state === "completed"` 时显示「✓ …的人脸已录入成功」，并把原会变黑的预览替换成「人脸录入已结束 · 摄像头画面已关闭」 |
| 重复 start monitoring 不再报错 | `App.tsx:200-205` 先读 `/config` 的 `mode`，仅在未监控时才发 `/monitor/start`；`/config` 已返回 `mode`，不会撞 409 |
| Unity 编译 | batchmode 退出 0，无 `error CS`；`SmartHome.Runtime.dll` 于 11:22 重建，确认改过的 `SmartHomeSceneController.cs` 已编入 |
| Unity PlayMode | **5/5 通过**（含加载真实场景并连接实时 home-service） |
| 各测试套件 | home-service 全通过；vision 19 项 + 14 子测试；console 1 项；网页单元 4+1+5；`npm run build` 通过 |

### 服务与现场状态（留给你）

- 四个服务均运行**新代码**：home 8765、vision 8766、voice 8767、console 8770。旧进程（Python311 那份与 `.venv`/`.venv-vision` 那份并存）已按命令行精确停止后用 `tools/start-perception.ps1` 重启。
- 摄像头 `device:0` 已恢复 **bedroom 监控**，`sync=synced`。
- **爸爸的人脸数据完好**：`runtime/vision/face_registry.bin` 仍是 10:52:55 的 20,834 字节，未被删除或改写。
- 本轮改动仅 3 个已跟踪文件（`apps/home-service/src/visual_state.py`、`apps/vision-service/src/capture.py`、`apps/vision-service/src/server.py`）+ 1 个新增文件（`apps/perception-console/tests/conftest.py`）。**未提交**。

### 仍需人工确认的两点

1. **网页的实际观感**：成功提示、设备中文、当前监控房间只在浏览器里能看见，代理无法渲染页面。
2. **姿态显示**：当前 `pose` 为 `unknown` 是**正确**的——摄像头此刻只拍到贴着画面底边的残缺人体（17 个关键点里仅 5 个达阈值，检测框宽高比 1.77）。若要看到站立/坐下/躺下，请让**整个身体完整入镜并保持光线充足**。注意：正是缺陷 B 的关键点门槛在阻止这个残缺框被误判成「躺下」。

### 另一处遗留（未处理）

语音服务疑似有两个实例：持有 8767 的是 PID 15236（系统 Python 3.11），而启动脚本 manifest 记录的是 PID 35276（`.venv`）。两者都活着，功能正常，但归属混乱，建议后续清理时一并理顺。
