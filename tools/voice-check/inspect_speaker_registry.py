"""Read the enrolled voiceprint store and report what is actually in it.

Why this exists: "声纹不准" has several very different causes — too few enrolment
samples, a template that does not represent the speaker, prototypes built by a
different embedding model, or simply a threshold nobody ever measured. Guessing
between them wastes the user's recording effort, so this script answers the first
question before any new audio is collected.

It is strictly read-only: the registry is loaded through the vision service's own
decryption path, and only aggregate numbers are printed. No embedding vector, no
person name and no audio ever reaches stdout, so the output is safe to paste into a
report. `replace`/`delete` are never called, so the stored gallery cannot change.

Usage:
    .venv\\Scripts\\python.exe tools/voice-check/inspect_speaker_registry.py
    .venv\\Scripts\\python.exe tools/voice-check/inspect_speaker_registry.py --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "vision-service" / "src"))

from registry import RegistryCorrupt, RegistryError, similarity  # noqa: E402
from speaker_registry import SpeakerRegistry  # noqa: E402

DEFAULT_CONFIG = REPO_ROOT / "apps" / "vision-service" / "config" / "vision.json"


def _pair_similarities(vectors: list[tuple[float, ...]]) -> dict[str, float | None]:
    """Within-one-person cosine spread; None when fewer than two prototypes exist."""

    scores = [
        similarity(vectors[left], vectors[right])
        for left in range(len(vectors))
        for right in range(left + 1, len(vectors))
    ]
    if not scores:
        return {"min": None, "max": None, "mean": None, "pairs": 0}
    return {
        "min": min(scores),
        "max": max(scores),
        "mean": sum(scores) / len(scores),
        "pairs": len(scores),
    }


def _cross_similarities(
    left: list[tuple[float, ...]], right: list[tuple[float, ...]]
) -> dict[str, float]:
    scores = [similarity(a, b) for a in left for b in right]
    return {"max": max(scores), "mean": sum(scores) / len(scores), "pairs": len(scores)}


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def collect(config_path: Path, pack_override: str | None) -> dict[str, object]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    runtime_dir = Path(raw["runtime_dir"])
    if not runtime_dir.is_absolute():
        # The vision config resolves relative runtime paths against its own module
        # depth; the repository root is the only place that exists in practice.
        runtime_dir = REPO_ROOT / runtime_dir
    pack = pack_override or str(raw["speaker_pack"])

    report: dict[str, object] = {
        "config": str(config_path),
        "runtime_dir": str(runtime_dir),
        "pack": pack,
        "file": str(runtime_dir / "speaker_registry.bin"),
    }
    registry = SpeakerRegistry(runtime_dir, pack)
    report["persistent"] = registry.persistent
    try:
        counts = registry.counts()
    except RegistryCorrupt as exc:
        report["error"] = "registry_corrupt"
        report["message"] = str(exc)
        return report
    except RegistryError as exc:
        report["error"] = type(exc).__name__
        report["message"] = str(exc)
        return report

    report["embedding_dim"] = registry.embedding_dim
    report["persons"] = len([count for count in counts.values() if count])
    report["prototypes_total"] = sum(counts.values())

    per_person: list[dict[str, object]] = []
    for person_id in registry.persons():
        vectors = registry.prototypes(person_id)
        entry: dict[str, object] = {
            # The id is already a one-way handle; the display name lives elsewhere.
            "person": person_id[:14] + "…",
            "prototypes": len(vectors),
            "dims": sorted({len(vector) for vector in vectors}),
            "within": _pair_similarities(vectors),
        }
        if vectors:
            centroid = _centroid(vectors)
            entry["centroid_self_similarity"] = [
                _round(similarity(vector, centroid)) for vector in vectors
            ]
        per_person.append(entry)
    report["per_person"] = per_person

    cross: list[dict[str, object]] = []
    persons = registry.persons()
    for left in range(len(persons)):
        for right in range(left + 1, len(persons)):
            values = _cross_similarities(
                registry.prototypes(persons[left]), registry.prototypes(persons[right])
            )
            cross.append({
                "pair": f"{persons[left][:10]}…/{persons[right][:10]}…",
                "max": _round(values["max"]),
                "mean": _round(values["mean"]),
                "pairs": values["pairs"],
            })
    report["cross_person"] = cross
    return report


def _centroid(vectors: list[tuple[float, ...]]) -> tuple[float, ...]:
    total = [0.0] * len(vectors[0])
    for vector in vectors:
        for index, value in enumerate(vector):
            total[index] += value
    norm = math.sqrt(sum(value * value for value in total))
    if norm <= 0.0:
        raise ValueError("centroid has zero norm")
    return tuple(value / norm for value in total)


def render(report: dict[str, object]) -> str:
    lines = ["# 声纹库现状（只读检查）", ""]
    lines.append(f"- 配置文件：`{report['config']}`")
    lines.append(f"- 声纹库文件：`{report['file']}`")
    lines.append(f"- DPAPI 持久化：{report['persistent']}")
    lines.append(f"- 记录中的模型 pack：`{report['pack']}`")
    if "error" in report:
        lines.append("")
        lines.append(f"**读取失败：{report['error']}** —— {report.get('message', '')}")
        return "\n".join(lines) + "\n"

    lines.append(f"- 已注册人数：**{report['persons']}**")
    lines.append(f"- 声纹向量总数：**{report['prototypes_total']}**")
    lines.append(f"- 向量维度：{report['embedding_dim']}")
    lines.append("")
    lines.append("| 人物 | 向量数 | 维度 | 同人两两相似度 min / mean / max | 与自身质心相似度 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for entry in report["per_person"]:  # type: ignore[union-attr]
        within = entry["within"]
        if within["pairs"]:
            spread = f"{_fmt(within['min'])} / {_fmt(within['mean'])} / {_fmt(within['max'])}"
        else:
            spread = "只有 1 条，无法计算"
        selfs = entry.get("centroid_self_similarity") or []
        lines.append(
            f"| {entry['person']} | {entry['prototypes']} | {','.join(str(d) for d in entry['dims'])} | "
            f"{spread} | {' / '.join(_fmt(value) for value in selfs) if selfs else 'n/a'} |"
        )
    cross = report["cross_person"]  # type: ignore[assignment]
    lines.append("")
    if cross:
        lines.append("| 人物对 | 跨人两两相似度 mean / max |")
        lines.append("| --- | --- |")
        for entry in cross:  # type: ignore[union-attr]
            lines.append(f"| {entry['pair']} | {_fmt(entry['mean'])} / {_fmt(entry['max'])} |")
    else:
        lines.append("只有一个人注册，因此没有任何跨人相似度：**无法测量 FAR**，"
                     "任何阈值都不能证明外人不会被认成这个人。")
    lines.append("")
    lines.append("读法：同人两两相似度越低，说明注册样本越分散（模板越不可靠）；"
                 "跨人 max 越接近同人 min，说明这两个人越难分。")
    return "\n".join(lines) + "\n"


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pack", default=None, help="覆盖配置文件里的 speaker_pack")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()

    if not args.config.is_file():
        print(f"配置文件不存在：{args.config}", file=sys.stderr)
        return 2
    report = collect(args.config, args.pack)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
