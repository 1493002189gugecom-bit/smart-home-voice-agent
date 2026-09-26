"""Measure speaker-identification FAR/FRR before voiceprints are allowed to target anything.

Why this exists: a wrong "confident" match is the only failure mode that can adjust
the wrong room without the user noticing, so the operating threshold must come from
measurements on this household's own voices and microphones, not from a paper.

Protocol (leave-one-out, no gallery and no running service required):

  * samples are read from ``<root>/<person_id>/*.wav``;
  * a genuine trial scores one sample against that person's *other* samples, which
    is what the enrolled gallery would do with a fresh utterance;
  * an impostor trial scores one sample against every other person's samples;
  * every trial is bucketed by how long the utterance was, because short speech is
    where voiceprints actually fail.

Output is a markdown table plus the threshold that meets a target FAR. The script
needs the speaker model and real recordings, so it is run by a person, not by an
agent, and it never touches device control.

Usage:
    .venv\\Scripts\\python.exe tools/voice-check/measure_speaker.py \
        --samples runtime/voice-eval --model D:/smart-home-models/speaker.onnx
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "voice-service" / "src"))

import config  # noqa: E402
from speaker import SpeakerEmbedder, SpeakerModelError  # noqa: E402

BUCKETS = ("under_1s", "1_to_2s", "over_2s")
DEFAULT_TARGET_FAR = 0.01
# A 1% target is simply not measurable on a handful of impostor trials: the
# smallest non-zero rate is 1/n. Reported rather than silently rounded away.
MIN_IMPOSTOR_TRIALS = 100
# Duplicated from the vision service's contracts on purpose: importing that app
# here would pull its whole dependency set into a measurement script for two lines
# of shape checking.
PERSON_ID_PATTERN = re.compile(r"^(dad|mom|child|person_[0-9a-f]{32})$")


@dataclass(frozen=True)
class Trial:
    """One scored comparison. `genuine` means the two voices are the same person."""

    person_id: str
    genuine: bool
    score: float
    seconds: float

    @property
    def bucket(self) -> str:
        return bucket_of(self.seconds)


def bucket_of(seconds: float) -> str:
    if seconds < 1.0:
        return "under_1s"
    if seconds < 2.0:
        return "1_to_2s"
    return "over_2s"


def cosine(left, right) -> float:
    """Unit-length cosine similarity in plain Python, so the report is easy to audit."""

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise ValueError("cannot score a zero vector")
    return dot / (left_norm * right_norm)


def _ratio(items, predicate) -> float | None:
    """None for an empty set: no trials is not the same as no errors."""

    if not items:
        return None
    return sum(1 for item in items if predicate(item)) / float(len(items))


def rates(trials, threshold: float) -> tuple[float | None, float | None]:
    """``(FAR, FRR)``: impostors accepted, and genuine speakers rejected."""

    impostors = [item for item in trials if not item.genuine]
    genuine = [item for item in trials if item.genuine]
    far = _ratio(impostors, lambda item: item.score >= threshold)
    frr = _ratio(genuine, lambda item: item.score < threshold)
    return far, frr


def equal_error_rate(trials) -> tuple[float | None, float | None]:
    """The observed threshold where FAR and FRR are closest, and that rate."""

    scores = sorted({item.score for item in trials})
    best: tuple[float, float, float] | None = None
    for threshold in scores:
        far, frr = rates(trials, threshold)
        if far is None or frr is None:
            return None, None
        gap = abs(far - frr)
        if best is None or gap < best[0]:
            best = (gap, (far + frr) / 2.0, threshold)
    if best is None:
        return None, None
    return best[1], best[2]


def threshold_for_far(trials, target_far: float) -> tuple[float | None, float | None]:
    """The lowest threshold whose impostor acceptance still meets the target.

    FAR alone chooses the threshold: accepting a stranger as a household member is
    what would move the wrong room, while rejecting the right person only costs one
    clarifying question.
    """

    impostor_scores = [item.score for item in trials if not item.genuine]
    if not impostor_scores:
        return None, None
    # The candidate set must include one threshold above every impostor score: that
    # is the only point where the impostor rate is zero, and sweeping the observed
    # scores alone can never reach it, which would report "unreachable" for almost
    # every real household.
    candidates = sorted({*impostor_scores, max(impostor_scores) + 1e-9})
    for threshold in candidates:
        far, frr = rates(trials, threshold)
        if far is not None and far <= target_far:
            return threshold, frr
    return None, None


def bucket_report(trials) -> dict[str, dict[str, float | int | None]]:
    """Per-duration-bucket counts and rates at the equal-error threshold."""

    _eer, threshold = equal_error_rate(trials)
    report: dict[str, dict[str, float | int | None]] = {}
    for bucket in BUCKETS:
        subset = [item for item in trials if item.bucket == bucket]
        if not subset:
            continue
        far, frr = (None, None) if threshold is None else rates(subset, threshold)
        report[bucket] = {
            "genuine": sum(1 for item in subset if item.genuine),
            "impostor": sum(1 for item in subset if not item.genuine),
            "far": far,
            "frr": frr,
        }
    return report


def score_trials(embeddings: dict[str, list[tuple[tuple[float, ...], float]]]) -> list[Trial]:
    """Leave-one-out genuine trials plus every cross-person impostor trial.

    A person with a single sample contributes no genuine trial, which is reported
    rather than hidden: one recording cannot prove a voice is recognisable.
    """

    trials: list[Trial] = []
    for person_id, samples in sorted(embeddings.items()):
        for index, (embedding, seconds) in enumerate(samples):
            # Unordered pairs: a comparison has one score, and counting it twice
            # would inflate the trial count without adding evidence.
            for other_index in range(index + 1, len(samples)):
                other, _other_seconds = samples[other_index]
                trials.append(Trial(person_id, True, cosine(embedding, other), seconds))
            for other_id, others in embeddings.items():
                if other_id == person_id:
                    continue
                for other, _other_seconds in others:
                    trials.append(Trial(person_id, False, cosine(embedding, other), seconds))
    return trials


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def build_report(trials, target_far: float, samples_per_person: dict[str, int]) -> str:
    lines = ["# 声纹 FAR/FRR 实测报告", ""]
    lines.append(f"每人样本数：{samples_per_person}")
    genuine = sum(1 for item in trials if item.genuine)
    impostor = sum(1 for item in trials if not item.genuine)
    lines.append(f"真实试验 {genuine} 次，冒名试验 {impostor} 次。")
    lines.append("")
    eer, eer_threshold = equal_error_rate(trials)
    threshold, frr = threshold_for_far(trials, target_far)
    lines.append(f"- EER：{_percent(eer)}（阈值 {eer_threshold}）")
    lines.append(f"- 满足 FAR ≤ {_percent(target_far)} 的阈值：{threshold}（此时 FRR {_percent(frr)}）")
    lines.append("")
    lines.append("| 语句长度 | 真实 | 冒名 | FAR | FRR |")
    lines.append("| --- | --- | --- | --- | --- |")
    for bucket, values in bucket_report(trials).items():
        lines.append(
            f"| {bucket} | {values['genuine']} | {values['impostor']} | "
            f"{_percent(values['far'])} | {_percent(values['frr'])} |"
        )
    lines.append("")
    if impostor < MIN_IMPOSTOR_TRIALS:
        lines.append(
            f"> 冒名试验只有 {impostor} 次，无法证明 {_percent(1 / max(1, impostor))} 以下的分辨率；"
            f"要验证 FAR ≤ {_percent(target_far)} 至少需要 {MIN_IMPOSTOR_TRIALS} 次冒名试验。"
        )
    if threshold is None:
        lines.append(
            "> 用现有数据无法达成目标 FAR：最严格的阈值仍会接受至少一次冒名样本，"
            "说明有两人声音过于接近，不应放开声纹自动定位。"
        )
    lonely = [person_id for person_id, count in samples_per_person.items() if count < 2]
    if lonely:
        lines.append(f"> 以下人物只有一条样本，无法产生真实试验：{', '.join(lonely)}")
    return "\n".join(lines) + "\n"


def load_samples(root: Path) -> dict[str, list[Path]]:
    """Read ``<root>/<person_id>/*.wav``; unknown person ids are refused loudly."""

    found: dict[str, list[Path]] = {}
    for directory in sorted(item for item in root.iterdir() if item.is_dir()):
        if not PERSON_ID_PATTERN.match(directory.name):
            print(f"[skip] {directory.name} 不是合法人物编号", file=sys.stderr)
            continue
        wavs = sorted(directory.glob("*.wav"))
        if wavs:
            found[directory.name] = wavs
    return found


def embed_samples(samples: dict[str, list[Path]], embedder: SpeakerEmbedder):
    import soundfile as sf

    embeddings: dict[str, list[tuple[tuple[float, ...], float]]] = {}
    skipped: list[str] = []
    for person_id, paths in samples.items():
        for path in paths:
            audio, rate = sf.read(str(path), dtype="float32")
            try:
                vector, quality = embedder.embed(audio, rate)
            except SpeakerModelError as exc:
                raise SystemExit(f"声纹模型不可用（{exc.code}），无法测量。") from exc
            if vector is None:
                skipped.append(f"{path.name}:{quality.reason}")
                continue
            embeddings.setdefault(person_id, []).append((vector, quality.seconds))
    if skipped:
        print(f"[skip] {len(skipped)} 条被质量门拒收：{', '.join(skipped[:8])}", file=sys.stderr)
    return embeddings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=Path("runtime/voice-eval"),
                        help="目录结构：<samples>/<person_id>/*.wav")
    parser.add_argument("--model", type=Path, default=None, help="默认取 SMART_HOME_SPEAKER_MODEL")
    parser.add_argument("--target-far", type=float, default=DEFAULT_TARGET_FAR)
    parser.add_argument("--out", type=Path, default=None, help="把报告写到这个文件")
    args = parser.parse_args()

    model = args.model or config.speaker_model_path()
    if model is None:
        print("未配置声纹模型：请设置 SMART_HOME_SPEAKER_MODEL 或用 --model 指定。", file=sys.stderr)
        return 2
    if not args.samples.is_dir():
        print(f"样本目录不存在：{args.samples}", file=sys.stderr)
        return 2

    samples = load_samples(args.samples)
    if len(samples) < 2:
        print("至少需要两位人物的录音：真实试验和冒名试验都要有。", file=sys.stderr)
        return 2

    embedder = SpeakerEmbedder(model, min_seconds=config.speaker_min_seconds())
    embeddings = embed_samples(samples, embedder)
    trials = score_trials(embeddings)
    if not trials:
        print("没有产生任何试验，请检查录音数量。", file=sys.stderr)
        return 2

    report = build_report(trials, args.target_far, {k: len(v) for k, v in embeddings.items()})
    print(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"报告已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
