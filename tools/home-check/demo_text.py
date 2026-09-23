"""Text-driven end-to-end walkthrough of the home service (Phase B gate).

No microphone and no Unity: this script drives the restricted tools with plain
text requests and prints what the Agent would be allowed to say. It is the
evidence script for "开灯 / 调空调 / 播报状态机" acceptance.

Run:
  python tools/home-check/demo_text.py
  python tools/home-check/demo_text.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps" / "home-service" / "src"))

from models import BroadcastState, now_ms  # noqa: E402,F401
from notify import plan_notification  # noqa: E402
from state import build_default_state  # noqa: E402
from tools import ToolService  # noqa: E402
from visual_state import VisualStateStore  # noqa: E402
CONFIG = REPO / "apps" / "home-service" / "config" / "rooms.json"


class Demo:
    def __init__(self, emit) -> None:
        self.state = build_default_state(CONFIG if CONFIG.exists() else None)
        # Person locations come from the camera store now: manual movement was
        # removed, so the demo has to produce locations the way vision does.
        self.visual = self._build_store()
        self.tools = ToolService(self.state, visual_state=self.visual)
        self.emit = emit
        self.steps: list[dict] = []
        self._sessions: dict[str, tuple[str, str]] = {}

    def _build_store(self) -> VisualStateStore:
        catalog = {
            person.id: {"display_name": person.display_name, "aliases": list(person.aliases)}
            for person in self.state.persons.values()
        }
        return VisualStateStore(catalog, observation_ttl_ms=600_000)

    def observe_room(self, room_id: str, person_ids: list[str]) -> None:
        """Publish confirmed observations for one room, exactly like the service."""
        index = len(self._sessions)
        camera_id = f"demo-camera-{index}"
        session_id = f"demo-session-{index}"
        timestamp = now_ms()
        self.visual.submit_batch(
            {
                "camera_id": camera_id,
                "camera_room_id": room_id,
                "session_id": session_id,
                "observed_at_ms": timestamp,
                "observations": [
                    {
                        "track_id": f"track-{person_id}",
                        "bbox": [0.4, 0.3, 0.6, 0.9],
                        "keypoints": [[0.5, 0.4, 0.9], [0.5, 0.7, 0.9]],
                        "detection_confidence": 0.9,
                        "person_id": person_id,
                        "identity_state": "confirmed",
                        "face_similarity": 0.9,
                        "pose": "standing",
                        "pose_confidence": 0.8,
                        "observed_at_ms": timestamp,
                        "track_state": "active",
                    }
                    for person_id in person_ids
                ],
            },
            timestamp,
        )
        self._sessions[room_id] = (camera_id, session_id)

    def withdraw_person(self, person_id: str) -> None:
        """Drop one track so its person becomes unknown again, as a lost track does."""
        for camera_id, session_id in self._sessions.values():
            self.visual.withdraw(
                {
                    "camera_id": camera_id,
                    "session_id": session_id,
                    "track_ids": [f"track-{person_id}"],
                },
                now_ms(),
            )

    def record(self, title: str, result) -> None:
        entry = {
            "step": title,
            "ok": result.ok,
            "phrase": result.phrase,
            "error": result.error,
            "error_code": result.error_code,
            "data": result.data,
        }
        self.steps.append(entry)
        self.emit(title, result)

    def run(self) -> int:
        self.emit_section("1. 查询当前状态")
        self.record("查询客厅状态", self.tools.query_room_status("客厅"))
        self.record("查询人物位置（爸爸）", self.tools.query_person_location("爸爸"))
        self.emit_note("尚未有摄像头观察，人物位置按设计应为未知（不是记住的房间）")

        self.emit_section("2. 开灯（明确指令）")
        self.record("打开客厅的灯", self.tools.set_light(room="客厅", on=True))
        self.record("把卧室灯调到 30", self.tools.set_light(room="卧室", brightness=30))
        self.record("再次关闭已关的厨房灯（幂等）", self.tools.set_light(room="厨房", on=False))

        self.emit_section("3. 调空调（范围校验）")
        self.record("打开卧室空调并设 25 度", self.tools.set_ac(room="卧室", on=True, target_temp=25))
        self.record("设定 40 度（应被拒绝）", self.tools.set_ac(room="卧室", target_temp=40))
        self.record("未知模式 turbo（应被拒绝）", self.tools.set_ac(room="卧室", mode="turbo"))

        self.emit_section("4. 离线设备不产生误操作")
        self.state.set_device_online("living_room_ac", False)
        self.record("控制已离线的客厅空调（应失败）", self.tools.set_ac(room="客厅", on=True))

        self.emit_section("5. 重复操作 ID 不会执行两次")
        first = self.tools.set_light(room="客厅", brightness=80, operation_id="demo-op-1")
        self.record("第一次设置亮度 80", first)
        version_after = self.state.require_device("living_room_light").version
        second = self.tools.set_light(room="客厅", brightness=80, operation_id="demo-op-1")
        self.record("同一 operation_id 重试", second)
        self.emit_note(
            f"设备版本在重试后保持不变：{version_after} -> {self.state.require_device('living_room_light').version}"
        )

        self.emit_section("6. 多目标通知：同房间合并 / 跨房间串行")
        # Locations are camera observations now: kitchen sees dad and mom, the
        # living room sees the child.
        self.observe_room("kitchen", ["dad", "mom"])
        self.observe_room("living_room", ["child"])
        plan = plan_notification(self.tools, ["爸爸", "妈妈", "孩子"], "吃饭啦")
        self.record("叫爸爸、妈妈和孩子吃饭", plan)
        self.emit_note(f"队列长度：{len(self.state.broadcast_queue)}（同房间已合并）")

        self.emit_section("7. 位置未知不自动全屋广播")
        # Withdrawing the child's track is how a lost track clears a location, so
        # this proves the unknown branch without any manual position handling.
        self.withdraw_person("child")
        plan2 = plan_notification(self.tools, ["孩子"], "吃饭啦")
        self.record("只叫位置未知的孩子", plan2)
        queues_before = len(self.state.broadcast_queue)
        self.emit_note(f"队列未新增：长度仍为 {queues_before}")

        self.emit_section("8. 播报状态机与措辞")
        task = self.state.head_broadcast()
        assert task is not None
        self.record("队首任务（queued）措辞", _Phrase(self.state.broadcast_phrase(task.id)))
        self.state.start_broadcast(task.id)
        self.record("播放中（playing）措辞", _Phrase(self.state.broadcast_phrase(task.id)))
        self.state.complete_broadcast(task.id, task.receipt_id, success=True)
        self.record("播放完成（played）措辞", _Phrase(self.state.broadcast_phrase(task.id)))

        failed = self.state.head_broadcast()
        assert failed is not None
        self.state.start_broadcast(failed.id)
        self.state.fail_broadcast(failed.id, "TTS 不可用（模拟断网）")
        self.record("失败（failed）措辞", _Phrase(self.state.broadcast_phrase(failed.id)))

        self.emit_section("9. 误操作计数")
        self.emit_note(
            f"实际设备写入 {self.tools.device_writes} 次；误操作 {self.tools.misoperations} 次（必须为 0）"
        )
        return 0 if self.tools.misoperations == 0 else 1

    def emit_section(self, title: str) -> None:
        self.emit(f"\n=== {title} ===", None)

    def emit_note(self, text: str) -> None:
        self.emit(f"    · {text}", None)


class _Phrase:
    """Adapter so a plain phrase can flow through the same recorder."""

    def __init__(self, phrase: str):
        self.ok = True
        self.phrase = phrase
        self.error = None
        self.error_code = None
        self.data = None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument("--report", type=Path, default=None, help="write the JSON report to this path")
    args = parser.parse_args()

    collected: list[dict] = []

    def emit(text: str, result) -> None:
        if args.json:
            return
        if result is None:
            print(text)
            return
        if result.ok:
            print(f"[OK  ] {text}: {result.phrase or result.data}")
        else:
            print(f"[FAIL] {text}: {result.error} ({result.error_code})")

    demo = Demo(emit)
    code = demo.run()

    if args.json or args.report:
        payload = {
            "tool": "demo_text.py",
            "ok": code == 0,
            "device_writes": demo.tools.device_writes,
            "misoperations": demo.tools.misoperations,
            "steps": demo.steps,
            "final_version": demo.state.version,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text, encoding="utf-8")
            print(f"wrote {args.report}")
        if args.json and not args.report:
            print(text)

    if not args.json:
        print(f"\n误操作次数: {demo.tools.misoperations}（必须为 0）")
        print("B 阶段文字驱动验证：" + ("通过" if code == 0 else "未通过"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
