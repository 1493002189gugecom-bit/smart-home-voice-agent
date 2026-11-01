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
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Insertion order matters: each `insert(0, ...)` prepends, so the vision-service
# entry goes in first and the voice-service entry second to end up ahead of it.
# Both apps ship a module named `config`, so getting this backwards silently hands
# the vision config to the voice service.
sys.path.insert(0, str(REPO_ROOT / "apps" / "vision-service" / "src"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "voice-service" / "src"))

import config  # noqa: E402  (voice-service)
from registry import RegistryError  # noqa: E402  (vision-service)
from speaker import SpeakerEmbedder, SpeakerModelError  # noqa: E402
from speaker_registry import SpeakerRegistry  # noqa: E402

if not hasattr(config, "speaker_model_path"):  # pragma: no cover - import-order guard
    raise ImportError(f"the wrong `config` module was imported: {config.__file__}")

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


# ------------------------------------------------------- production-faithful scoring
#
# The functions below deliberately re-implement the *policy* that `speaker_identity`
# and the vision gallery apply, because measuring a different rule than the one that
# runs is how a threshold ends up tuned for a system nobody is using. The gallery
# ranks by the best single prototype per person and reports the runner-up margin;
# the voice service then requires both a score and a margin.



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


def live_impostor_trials(
    embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]],
    strategy: str,
    stored: dict[str, list[tuple[float, ...]]],
) -> list[Trial]:
    """Score every *other* person's recordings against each enrolled person's template.

    This is the false-accept risk that actually exists in production: someone the
    gallery does not know says something, and the matcher has to reject them. The
    legacy `score_trials` cohort cannot answer it — it compares strangers' recordings
    to each other, which is why its "FAR ≤ 1% needs threshold 0.77" figure says more
    about the toy cohort than about this household.
    """

    trials: list[Trial] = []
    for enrolled_id, prototypes in sorted(stored.items()):
        template = build_template(prototypes, strategy)
        for person_id, samples in sorted(embeddings.items()):
            if person_id == enrolled_id:
                continue
            for embedding, seconds, _name in samples:
                trials.append(Trial(enrolled_id, False, best_similarity(embedding, template), seconds))
    return trials


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


# ------------------------------------------------------------------ template policy

def build_template(vectors: list[tuple[float, ...]], strategy: str) -> list[tuple[float, ...]]:
    """The prototype list one person's gallery row would hold under `strategy`.

    ``max`` keeps every enrolment vector and the gallery scores against the closest
    one — the shipped behaviour. ``centroid`` collapses them to a single averaged
    vector. Averaging shrinks the effect of one odd recording, but also erases a
    speaker's genuine spread, so which one wins is a measurement, not a preference.
    """

    if not vectors:
        raise ValueError("cannot build a template from no vectors")
    if strategy == "max":
        return list(vectors)
    if strategy != "centroid":
        raise ValueError(f"unknown strategy {strategy!r}")
    total = [0.0] * len(vectors[0])
    for vector in vectors:
        for index, value in enumerate(vector):
            total[index] += value
    norm = math.sqrt(sum(value * value for value in total))
    if norm <= 0.0:
        raise ValueError("centroid has zero norm")
    return [tuple(value / norm for value in total)]


def best_similarity(query: tuple[float, ...], template: list[tuple[float, ...]]) -> float:
    """The gallery's own score: the closest prototype of one person."""

    return max(cosine(query, prototype) for prototype in template)


@dataclass(frozen=True)
class GenuineTrial:
    """One utterance scored against its own person's template."""

    person_id: str
    sample: str
    seconds: float
    score: float
    strategy: str

    @property
    def bucket(self) -> str:
        return bucket_of(self.seconds)


def genuine_trials(
    embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]],
    strategy: str,
    *,
    leave_one_out: bool,
) -> list[GenuineTrial]:
    """Score every utterance of a person against that person's enrolment template.

    ``leave_one_out`` removes the utterance from the template before scoring it, so a
    sample can never match itself. That is the honest estimate of a *new* sentence.
    Without it, the number is inflated by self-comparisons and every voice looks
    recognisable.
    """

    trials: list[GenuineTrial] = []
    for person_id, samples in sorted(embeddings.items()):
        for index, (embedding, seconds, name) in enumerate(samples):
            rest = [item[0] for position, item in enumerate(samples) if position != index]
            if leave_one_out and len(rest) < 2:
                # One remaining prototype is a single point, not a template; the
                # result would say more about that one recording than about the voice.
                continue
            vectors = rest if leave_one_out else [item[0] for item in samples]
            if not vectors:
                continue
            trials.append(
                GenuineTrial(person_id, name, seconds, best_similarity(embedding, build_template(vectors, strategy)), strategy)
            )
    return trials


def threshold_for_acceptance(trials: list[GenuineTrial], target_frr: float) -> float | None:
    """The highest threshold that still accepts at least ``1 - target_frr`` of them.

    Returns the largest observed score when nothing is ever rejected, which is the
    correct answer for a corpus with no failures and not a claim about unseen audio.
    """

    if not trials:
        return None
    scores = sorted(item.score for item in trials)
    allowed = math.floor(target_frr * len(scores))
    if allowed <= 0:
        return scores[0]
    return scores[min(allowed, len(scores) - 1)]


def decision_counts(trials: list[GenuineTrial], threshold: float) -> dict[str, float | int]:
    rejected = [item for item in trials if item.score < threshold]
    return {
        "total": len(trials),
        "rejected": len(rejected),
        "frr": (len(rejected) / len(trials)) if trials else None,
        "accepted": len(trials) - len(rejected),
    }


def score_spread(trials: list[GenuineTrial]) -> dict[str, float | None]:
    if not trials:
        return {"min": None, "p5": None, "median": None, "mean": None, "max": None}
    scores = sorted(item.score for item in trials)
    index = max(0, min(len(scores) - 1, int(round(0.05 * (len(scores) - 1)))))
    return {
        "min": scores[0],
        "p5": scores[index],
        "median": scores[len(scores) // 2],
        "mean": sum(scores) / len(scores),
        "max": scores[-1],
    }


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


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def distribution_report(embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]],
                        strategies: tuple[str, ...]) -> list[str]:
    """Genuine-score spread per template strategy, with and without leave-one-out."""

    lines = ["## 本人分数分布（决定 FRR）", ""]
    lines.append("| 模板策略 | 评估方式 | 试验数 | min | p5 | 中位 | 均值 | max |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for strategy in strategies:
        for leave_one_out, label in ((True, "留一（模拟新语句）"), (False, "含自身（乐观）")):
            trials = genuine_trials(embeddings, strategy, leave_one_out=leave_one_out)
            spread = score_spread(trials)
            lines.append(
                f"| {strategy} | {label} | {len(trials)} | {_fmt(spread['min'])} | {_fmt(spread['p5'])} | "
                f"{_fmt(spread['median'])} | {_fmt(spread['mean'])} | {_fmt(spread['max'])} |"
            )
    lines.append("")
    lines.append("留一列才是「这句新话会不会被认出来」的估计；含自身列只是上界，不要用它选阈值。")
    return lines


def threshold_report(embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]],
                     strategy: str, current: float) -> list[str]:
    """Sweep candidate thresholds against the leave-one-out genuine scores."""

    trials = genuine_trials(embeddings, strategy, leave_one_out=True)
    lines = [f"## 阈值扫描（策略 {strategy}，留一）", ""]
    if not trials:
        lines.append("样本不足，无法扫描：每人至少需要 3 条录音。")
        return lines
    spread = score_spread(trials)
    lines.append(f"- 最低分 {_fmt(spread['min'])}；p5 {_fmt(spread['p5'])}；中位 {_fmt(spread['median'])}")
    counts = decision_counts(trials, current)
    lines.append(f"- 当前阈值 **{current:.2f}**：接受 {counts['accepted']}/{counts['total']}，"
                 f"FRR **{_percent(counts['frr'])}**（这就是「认不出来」的比例）")
    lines.append("")
    lines.append("| 阈值 | 接受 | 拒绝 | FRR | 备注 |")
    lines.append("| --- | --- | --- | --- | --- |")
    candidates = sorted({0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, current,
                         *(round(item.score, 3) for item in trials)})
    for threshold in candidates:
        row = decision_counts(trials, threshold)
        note = ""
        if abs(threshold - current) < 1e-9:
            note = "当前配置"
        elif row["rejected"] == 0:
            note = "本语料零拒绝（不等于对新语音零拒绝）"
        lines.append(
            f"| {threshold:.3f} | {row['accepted']} | {row['rejected']} | {_percent(row['frr'])} | {note} |"
        )
    lines.append("")
    lines.append("> 阈值只能这样选：先定可接受的 FAR，再取最低分之上足够的安全余量。"
                 "单侧数据只能给出「上界」，不能证明外人不会被接受。")
    return lines


def per_utterance_report(embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]],
                         strategy: str, current: float) -> list[str]:
    """One row per recording, so a rejected sentence can be inspected directly."""

    lines = ["## 逐条明细（留一）", ""]
    lines.append("| 人物 | 录音 | 语音时长 | 分数 | 当前阈值下的判定 | 需要降到 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for person_id, samples in sorted(embeddings.items()):
        for index, (embedding, seconds, name) in enumerate(samples):
            rest = [item[0] for position, item in enumerate(samples) if position != index]
            if len(rest) < 2:
                continue
            score = best_similarity(embedding, build_template(rest, strategy))
            verdict = "接受" if score >= current else "拒绝→算陌生人"
            lines.append(
                f"| {person_id[:12]}… | {name} | {seconds:.2f}s | {score:.4f} | {verdict} | {score:.3f} |"
            )
    lines.append("")
    lines.append("「需要降到」是该条被接受所需的最高阈值；它的最大值就是本语料下不误拒的上限。")
    return lines


def enrolled_report(embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]],
                    current: float, config_path: Path) -> tuple[list[str], dict[str, list[float]]]:
    """Score every recording against the *live* DPAPI gallery prototypes.

    The leave-one-out tables answer "would a new sentence match the enrolment?", this
    one answers "would a new sentence match what is actually stored right now?" — the
    question the running service asks. A large gap between the two means the stored
    gallery is the weak link, not the threshold.
    """

    lines = ["## 与线上声纹库直接比对（不删样本）", ""]
    by_person: dict[str, list[float]] = {}
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    runtime_dir = Path(raw["runtime_dir"])
    if not runtime_dir.is_absolute():
        runtime_dir = REPO_ROOT / runtime_dir
    registry = SpeakerRegistry(runtime_dir, str(raw["speaker_pack"]))
    try:
        stored = {person_id: registry.prototypes(person_id) for person_id in registry.persons()}
    except RegistryError as exc:
        lines.append(f"无法读取声纹库：{type(exc).__name__} {exc}")
        return lines, by_person
    if not stored:
        lines.append("声纹库是空的：还没有人注册。")
        return lines, by_person
    lines.append(f"线上库：{len(stored)} 人，向量数 "
                 f"{', '.join(f'{pid[:12]}…={len(vecs)}' for pid, vecs in stored.items())}")
    lines.append("")
    lines.append("| 人物 | 录音 | 语音时长 | 对线上库最高分 | 当前阈值下的判定 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for person_id, samples in sorted(embeddings.items()):
        for embedding, seconds, name in samples:
            if person_id not in stored:
                lines.append(f"| {person_id[:12]}… | {name} | {seconds:.2f}s | 未注册 | - |")
                continue
            score = best_similarity(embedding, stored[person_id])
            by_person.setdefault(person_id, []).append(score)
            verdict = "接受" if score >= current else "拒绝→算陌生人"
            lines.append(
                f"| {person_id[:12]}… | {name} | {seconds:.2f}s | {score:.4f} | {verdict} |"
            )
    lines.append("")
    lines.append("> 线上库只有一个人时，margin 门槛恒为通过（没有第二名可比），"
                 "因此单人家族**无法**从这份数据得出 FAR。")
    return lines, by_person


def cross_session_report(embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]],
                         strategy: str, current: float, component: str) -> tuple[list[str], list[float]]:
    """Score each session against a template built only from the *other* sessions.

    This is the measurement that decides whether the shipped setup actually works,
    and it is the one thing a single-session recording cannot show. Enrolment audio
    and live audio come from different moments: same speaker, different distance,
    different room noise, sometimes a different day. Leave-one-out over one session
    hides that entirely, because every trial shares its session with its template.

    A person with only one session yields nothing here — which is exactly the point:
    a corpus recorded in one sitting is not evidence about the live service.
    """

    lines = [f"## 跨会话泛化（模板只取{component}）", ""]
    by_person: dict[str, list[float]] = {}
    if not any("/" in name for _e, _s, name in (item for items in embeddings.values() for item in items)):
        lines.append("录音没有会话子目录，无法区分会话；请按 `<人物>/<会话>/*.wav` 存放。")
        return lines, []
    lines.append("| 人物 | 录音 | 语音时长 | 分数 | 当前阈值下的判定 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for person_id, samples in sorted(embeddings.items()):
        for embedding, seconds, name in samples:
            session = name.split("/", 1)[0]
            others = [
                item[0] for item in samples
                if item[2].split("/", 1)[0] != session and item[0] is not embedding
            ]
            if len(others) < 2:
                continue
            score = best_similarity(embedding, build_template(others, strategy))
            by_person.setdefault(person_id, []).append(score)
            verdict = "接受" if score >= current else "拒绝→算陌生人"
            lines.append(
                f"| {person_id[:12]}… | {name} | {seconds:.2f}s | {score:.4f} | {verdict} |"
            )
    scores = [value for values in by_person.values() for value in values]
    lines.append("")
    if scores:
        ordered = sorted(scores)
        lines.append(
            f"跨会话 {len(scores)} 条：min {ordered[0]:.4f} / 中位 {ordered[len(ordered) // 2]:.4f} / "
            f"max {ordered[-1]:.4f}"
        )
    else:
        lines.append("没有跨会话试验：每个人至少需要两个会话的录音。")
    lines.append("")
    lines.append("> 这一节才代表下次说话的难度；只有一会话时它是空的，那是数据不足，不是通过。")
    return lines, scores



def _stored_prototypes(config_path: Path) -> dict[str, list[tuple[float, ...]]]:
    """Read the live gallery's prototypes, or an empty mapping when unreadable."""

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        runtime_dir = Path(raw["runtime_dir"])
        if not runtime_dir.is_absolute():
            runtime_dir = REPO_ROOT / runtime_dir
        registry = SpeakerRegistry(runtime_dir, str(raw["speaker_pack"]))
        return {person_id: registry.prototypes(person_id) for person_id in registry.persons()}
    except (OSError, ValueError, KeyError, RegistryError):
        return {}


def load_samples(root: Path) -> dict[str, list[Path]]:
    """Read ``<root>/<person_id>/**/*.wav``; unknown person ids are refused loudly.

    Session subdirectories are included, because the same list recorded on two
    different days is what exposes how much a score depends on the session.
    """

    found: dict[str, list[Path]] = {}
    for directory in sorted(item for item in root.iterdir() if item.is_dir()):
        if not PERSON_ID_PATTERN.match(directory.name):
            print(f"[skip] {directory.name} 不是合法人物编号", file=sys.stderr)
            continue
        wavs = sorted(path for path in directory.rglob("*.wav") if path.is_file())
        if wavs:
            found[directory.name] = wavs
    return found


def embed_samples(samples: dict[str, list[Path]], embedder: SpeakerEmbedder):
    import soundfile as sf

    embeddings: dict[str, list[tuple[tuple[float, ...], float, str]]] = {}
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
            # The stem carries the session directory, so two sessions of the same
            # phrase stay distinguishable in the per-utterance table.
            label = path.parent.name + "/" + path.name
            embeddings.setdefault(person_id, []).append((vector, quality.seconds, label))
    if skipped:
        print(f"[skip] {len(skipped)} 条被质量门拒收：{', '.join(skipped[:8])}", file=sys.stderr)
    return embeddings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=Path("runtime/voice-eval"),
                        help="目录结构：<samples>/<person_id>/<session>/*.wav")
    parser.add_argument("--model", type=Path, default=None, help="默认取 SMART_HOME_SPEAKER_MODEL")
    parser.add_argument("--target-far", type=float, default=DEFAULT_TARGET_FAR)
    parser.add_argument("--out", type=Path, default=None, help="把报告写到这个文件")
    parser.add_argument("--strategy", choices=("max", "centroid", "both"), default="both",
                        help="模板策略；both 会对两种都出报告")
    parser.add_argument("--current-threshold", type=float, default=None,
                        help="当前线上阈值，默认读 SMART_HOME_SPEAKER_MATCH_THRESHOLD")
    parser.add_argument("--min-seconds", type=float, default=None,
                        help="质量门下限，默认读配置；调小可测短句的真实代价")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "apps/vision-service/config/vision.json")
    parser.add_argument("--no-enrolled", action="store_true", help="跳过与线上声纹库的比对")
    parser.add_argument("--json", action="store_true", help="extra：把关键数字输出为 JSON")
    args = parser.parse_args()

    model = args.model or config.speaker_model_path()
    if model is None:
        print("未配置声纹模型：请设置 SMART_HOME_SPEAKER_MODEL 或用 --model 指定。", file=sys.stderr)
        return 2
    if not args.samples.is_dir():
        print(f"样本目录不存在：{args.samples}", file=sys.stderr)
        return 2

    samples = load_samples(args.samples)
    if not samples:
        print(f"{args.samples} 下没有找到任何 <人物编号>/<会话>/*.wav。", file=sys.stderr)
        return 2

    current = args.current_threshold if args.current_threshold is not None else config.speaker_match_threshold()
    min_seconds = args.min_seconds if args.min_seconds is not None else config.speaker_min_seconds()
    strategies = ("max", "centroid") if args.strategy == "both" else (args.strategy,)

    embedder = SpeakerEmbedder(model, min_seconds=min_seconds)
    embeddings = embed_samples(samples, embedder)
    if not embeddings:
        print("所有录音都被质量门拒收（语音太短/太静/削波/信噪比低），无法测量。", file=sys.stderr)
        return 2

    counted = {k: len(v) for k, v in embeddings.items()}
    two_sided = len(embeddings) >= 2
    lines = [
        f"# 声纹{'FAR/FRR' if two_sided else 'FRR'}实测报告",
        "",
        f"- 样本根目录：`{args.samples}`",
        f"- 每人合格样本数：{counted}",
        f"- 质量门下限：{min_seconds}s（短于它的语句根本不会被判定，也就无所谓阈值）",
        f"- 线上阈值：{current}",
        "",
    ]
    if not two_sided:
        lines += [
            "> **只有一个人有录音。** 因此这份报告只能回答「本人的话会不会被认成陌生人」"
            "（FRR），**不能**回答「外人的话会不会被认成本人」（FAR）："
            "没有第二个人的声纹，分母就是 0。任何声称已达标的说法都缺这一半证据。",
            "",
        ]
    lines += distribution_report(embeddings, strategies)
    lines += [""]
    for strategy in strategies:
        lines += threshold_report(embeddings, strategy, current)
        lines += [""]
    for strategy in strategies:
        section, cross_scores = cross_session_report(embeddings, strategy, current, "其他会话")
        lines += section
        lines += [""]
    lines += per_utterance_report(embeddings, strategies[0], current)
    lines += [""]
    if not args.no_enrolled:
        section, live_scores = enrolled_report(embeddings, current, args.config)
        lines += section
        lines += [""]
        for person_id, scores in sorted(live_scores.items()):
            ordered = sorted(scores)
            lines.append(
                f"- 人物 {person_id[:12]}… 对**线上库**的 {len(scores)} 条分数："
                f"min {ordered[0]:.4f} / 中位 {ordered[len(ordered) // 2]:.4f} / max {ordered[-1]:.4f}"
            )
        if live_scores:
            lines.append("")
            lines.append("> 这一组分数（而不是注册样本之间的 0.8）才是线上判定真实面对的分布。")

        # The false-accept risk that actually exists here: an unrecognised speaker
        # being matched against the stored gallery. Reported separately from the
        # leave-one-out cohort because that cohort cannot measure it.
        stored = _stored_prototypes(args.config)
        live_impostors = live_impostor_trials(embeddings, strategies[0], stored)
        lines.append("")
        lines.append("## 真实冒名风险（他人录音对线上库）")
        lines.append("")
        if not live_impostors:
            lines.append("语料里没有第二个人的录音，因此**无法测量 FAR**：分母为 0。")
            lines.append("上面 §3.5 那种「疑似非本人」的零散录音不能替代有身份标注的语料。")
        else:
            scores = sorted(item.score for item in live_impostors)
            accepted = sum(1 for value in scores if value >= current)
            lines.append(
                f"- 冒名试验 {len(scores)} 次：min {scores[0]:.4f} / 中位 "
                f"{scores[len(scores) // 2]:.4f} / **max {scores[-1]:.4f}**"
            )
            lines.append(f"- 阈值 {current} 下被误接受的次数：**{accepted}/{len(scores)}**")
            if accepted:
                lines.append("")
                lines.append("> **有冒名样本越过了阈值**：这说明当前阈值对该说话人不够安全，"
                             "应上调阈值或增加注册样本，不能只看 FRR。")
            if len(scores) < MIN_IMPOSTOR_TRIALS:
                lines.append("")
                lines.append(
                    f"> 冒名试验只有 {len(scores)} 次，分辨率上界是 {_percent(1 / len(scores))}，"
                    f"要证明 FAR ≤ {_percent(args.target_far)} 至少需要 {MIN_IMPOSTOR_TRIALS} 次。"
                    "**这个数字不能当达标证据。**"
                )

    real_trials = score_trials({pid: [(v, s) for v, s, _n in items] for pid, items in embeddings.items()})
    if real_trials:
        lines += ["", build_report(real_trials, args.target_far, counted)]

    report = "\n".join(lines) + "\n"
    print(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"报告已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
