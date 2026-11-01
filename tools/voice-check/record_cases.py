"""Record Phase A ASR/KWS cases from the physical Realtek microphone.

The script explicitly selects a Realtek input by name and never changes the
Windows default device. Press Enter to record each case; audio is written as
16 kHz mono PCM16 WAV. Audio files are globally ignored by git.

Examples:
  python tools/voice-check/record_cases.py --cases tools/voice-check/cases/asr-30.tsv
  python tools/voice-check/record_cases.py --phrases tools/voice-check/cases/wake-20.txt \
      --out-dir tools/voice-check/cases/audio/wake

Speaker-evaluation corpus (one directory per person, one subdirectory per session):

  python tools/voice-check/record_cases.py \
      --phrases tools/voice-check/cases/speaker-phrases-dad.txt \
      --out-dir runtime/voice-eval/dad/s1 \
      --seconds 6 --trim --min-speech 1.2

Record the same list a second time into ``.../dad/s2`` on another day, and again
from a different distance: speaker scores are dominated by session conditions, so
a corpus recorded in one sitting flatters whatever threshold is chosen from it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "voice-service" / "src"))
import audio_utils  # noqa: E402
import config  # noqa: E402

# Frame energy below this is a room, not a voice. Matches the peak threshold the
# recorder's level warning uses, so "too quiet to use" means one thing.
MIN_SIGNAL_ENERGY = 0.01 ** 2


def list_persons() -> int:
    """Print the person ids the voiceprint gallery actually knows.

    A speaker corpus is read as ``<root>/<person_id>/<session>/*.wav``, and the
    tooling refuses any other directory name. Writing "爸爸" instead of the stored id
    therefore gets skipped with a one-line warning while the report still looks
    healthy — a silent way to record twenty clips for nothing. The stored ids are
    random handles, so they cannot be guessed; this prints them.
    """

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "vision-service" / "src"))
    from speaker_registry import SpeakerRegistry  # noqa: PLC0415 - optional dependency

    config_path = Path(__file__).resolve().parents[2] / "apps" / "vision-service" / "config" / "vision.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    runtime_dir = Path(raw["runtime_dir"])
    if not runtime_dir.is_absolute():
        runtime_dir = config_path.parents[3] / runtime_dir
    registry = SpeakerRegistry(runtime_dir, str(raw["speaker_pack"]))

    cases_dir = Path(__file__).resolve().parent / "cases"
    print("声纹库里已注册的人物编号（录音目录必须用这一列作为名字）：")
    print()
    print(f"  {'person_id':<40}{'向量数':>6}  {'可用的朗读清单'}")
    persons = registry.persons()
    if not persons:
        print("  （声纹库是空的：还没有人注册）")
    counts = registry.counts()
    for person_id in persons:
        lists = sorted(path.name for path in cases_dir.glob(f"speaker-phrases-{person_id}.txt"))
        shown = ", ".join(lists) if lists else "（无：可在 cases/ 下按此编号新建清单）"
        print(f"  {person_id:<40}{counts.get(person_id, 0):>6}  {shown}")
    print()
    print("用法：")
    print(f"  --out-dir runtime/voice-eval/<person_id>/s1")
    print(f"  --phrases {cases_dir}\\speaker-phrases-<清单名>.txt")
    if not any((cases_dir / f"speaker-phrases-{person_id}.txt").exists() for person_id in persons):
        print()
        print("提示：仓库自带的清单按角色命名（speaker-phrases-dad/mom/child.txt），")
        print("      而库里的编号是随机串；请把清单复制成 speaker-phrases-<上面的编号>.txt，")
        print("      或者继续用角色清单、但把 --out-dir 写成上面的 person_id。")
    return 0


def list_usable_inputs() -> int:
    """Print input devices that can open as mono float32 at 16 kHz."""
    import sounddevice as sd

    hostapis = sd.query_hostapis()
    print("可用输入设备（支持 16 kHz 单声道）：")
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"]) <= 0:
            continue
        try:
            sd.check_input_settings(device=index, channels=1, samplerate=config.SAMPLE_RATE, dtype="float32")
        except sd.PortAudioError:
            continue
        host = str(hostapis[int(device["hostapi"])]["name"])
        print(f"  #{index:>2} [{host}] {device['name']}")
    print()
    print("当前优先列表（SMART_HOME_INPUT_DEVICE 可覆盖）：" + ", ".join(config.input_device_candidates()))
    try:
        chosen = audio_utils.select_input_device(None, config.SAMPLE_RATE)
        print(f"实际将使用：#{chosen.index} {chosen.name} [{chosen.hostapi}]")
    except RuntimeError as exc:
        print(f"当前无法选定输入设备：{exc}")
        return 2
    return 0


def load_tsv(path: Path) -> list[dict]:
    cases = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            raise ValueError(f"{path}:{lineno}: expected id<TAB>wav[<TAB>expected]")
        wav = Path(parts[1].strip())
        # Optional fourth field is the exact phrase to read; the third field is
        # reserved for ASR key-information tokens.
        prompt = parts[3].strip() if len(parts) > 3 else (parts[2].strip() if len(parts) > 2 else parts[0].strip())
        cases.append({"id": parts[0].strip(), "wav": wav, "prompt": prompt})
    return cases


def load_phrases(path: Path, out_dir: Path) -> list[dict]:
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        rows.append({"id": f"{lineno:03d}", "wav": out_dir / f"{lineno:03d}.wav", "prompt": text})
    return rows


def record(device_index: int, seconds: float) -> np.ndarray:
    frames = int(seconds * config.SAMPLE_RATE)
    audio = sd.rec(
        frames,
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device_index,
        blocking=True,
    )[:, 0]
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 1.0:
        audio = audio / peak
    return audio


def _voiced_frames(audio: np.ndarray, frame_ms: float = 20.0, margin_db: float = 15.0) -> np.ndarray:
    """Frame energies that carry speech, as booleans over fixed-size frames.

    Three rules, because each alone is wrong on real recordings:

    * the reference loudness is a high **percentile** of frame energy, never the
      maximum. Speech is peaky: one plosive or a click on the microphone is tens of
      dB above the vowels, and a threshold relative to that single frame declares
      most of a perfectly audible sentence to be silence;
    * the threshold sits `margin_db` below that reference rather than on top of it;
    * the clip must reach an absolute floor, because flat noise has no dynamic range
      and would otherwise clear any purely relative threshold from end to end.

    The absolute floor matches the level `--check-level` and the recorder already
    treat as "no real signal", so the two cannot disagree about a bad microphone.
    """

    import numpy as np

    frame = max(1, int(config.SAMPLE_RATE * frame_ms / 1000.0))
    usable = (len(audio) // frame) * frame
    if usable == 0:
        return np.zeros(0, dtype=bool)
    energies = np.square(audio[:usable].reshape(-1, frame)).mean(axis=1)
    if float(energies.max()) < MIN_SIGNAL_ENERGY:
        return np.zeros(energies.size, dtype=bool)
    reference = float(np.percentile(energies, 90))
    if reference <= 0.0:
        return np.zeros(energies.size, dtype=bool)
    return energies >= reference * (10.0 ** (-margin_db / 10.0))


def speech_seconds(audio: np.ndarray, frame_ms: float = 20.0, margin_db: float = 15.0) -> float:
    """Duration of the frames that actually carry speech.

    A speaker embedding is computed over whatever is handed to it, so a 6-second
    file holding a 1-second utterance is not a 6-second sample. Measuring the
    voiced part is what makes a corpus's "short utterance" bucket meaningful.
    """

    voiced = _voiced_frames(audio, frame_ms, margin_db)
    if voiced.size == 0:
        return 0.0
    return int(np.count_nonzero(voiced)) * frame_ms / 1000.0


def trim_silence(audio: np.ndarray, frame_ms: float = 20.0, margin_db: float = 15.0,
                 pad_ms: float = 150.0):
    """Drop leading and trailing non-speech, keeping a short pad on both sides.

    Stored samples stay comparable to the live path, which forwards the whole
    captured buffer including a little silence; the pad keeps the waveform from
    starting or ending mid-phoneme.
    """

    voiced = _voiced_frames(audio, frame_ms, margin_db)
    indices = np.flatnonzero(voiced)
    if indices.size == 0:
        return audio
    frame = max(1, int(config.SAMPLE_RATE * frame_ms / 1000.0))
    pad = max(1, int(config.SAMPLE_RATE * pad_ms / 1000.0))
    start = max(0, int(indices[0]) * frame - pad)
    stop = min(len(audio), (int(indices[-1]) + 1) * frame + pad)
    return audio[start:stop]


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--cases", type=Path, help="TSV: id, wav path, expected words")
    source.add_argument("--phrases", type=Path, help="one phrase per line")
    parser.add_argument("--out-dir", type=Path, default=Path("tools/voice-check/cases/audio"))
    parser.add_argument("--device-contains", default=None, help="name filter; default uses the configured preference list")
    parser.add_argument("--list-devices", action="store_true", help="print usable inputs and exit")
    parser.add_argument("--list-persons", action="store_true",
                        help="print the person ids in the voiceprint gallery and exit")
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--start", type=int, default=1, help="1-based first case")
    parser.add_argument("--force", action="store_true", help="overwrite existing WAV files")
    parser.add_argument("--auto", action="store_true", help="record consecutively without Enter prompts")
    parser.add_argument("--check-level", action="store_true", help="measure one second and warn if the input looks silent")
    parser.add_argument("--trim", action="store_true", help="drop leading/trailing silence before saving")
    parser.add_argument("--min-speech", type=float, default=0.0,
                        help="refuse/flag a recording whose voiced part is shorter than this many seconds")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="how many times a too-short case may be re-recorded in one run")
    args = parser.parse_args()

    if args.list_persons:
        return list_persons()
    if args.list_devices:
        return list_usable_inputs()
    if not args.cases and not args.phrases:
        parser.error("one of --cases or --phrases is required")

    device = audio_utils.select_input_device(args.device_contains, config.SAMPLE_RATE)
    print(f"Input: #{device.index} {device.name} [{device.hostapi}]")
    print(f"Format: {config.SAMPLE_RATE} Hz, mono, PCM16; duration: {args.seconds:.1f}s")

    if args.check_level:
        print("测量 1 秒输入电平，请说话……")
        probe = record(device.index, 1.0)
        probe_peak = float(np.max(np.abs(probe))) if len(probe) else 0.0
        print(f"电平峰值 {probe_peak:.4f}")
        if probe_peak < 0.02:
            print(
                "警告：几乎没有声音。请检查耳机静音键、Windows 输入音量，"
                "并确认选择的是你正在使用的麦克风；未解决前不要开始正式录制。",
                file=sys.stderr,
            )
            return 3

    cases = load_tsv(args.cases) if args.cases else load_phrases(args.phrases, args.out_dir)
    cases = cases[max(0, args.start - 1):]
    if not cases:
        print("no cases to record", file=sys.stderr)
        return 2

    def resolve(wav: Path) -> Path:
        return wav if wav.is_absolute() else Path.cwd() / wav

    pending = [c for c in cases if not resolve(c["wav"]).exists()]
    print()
    print(f"待录 {len(pending)} 条，已存在 {len(cases) - len(pending)} 条")
    if not pending:
        print("全部已录制。若要重录，加 --force；若要续录，用 --start N。")
        return 0
    print("录音时请保持正常语速；输入 s 跳过当前，输入 q 退出。")
    print()

    for position, case in enumerate(cases, args.start):
        wav = resolve(case["wav"])
        wav.parent.mkdir(parents=True, exist_ok=True)
        if wav.exists() and not args.force:
            print(f"[{position}/{len(cases)+args.start-1}] SKIP existing {wav}")
            continue

        print(f"\n[{position}/{len(cases)+args.start-1}] 请朗读：{case['prompt']}")
        if not args.auto:
            answer = input("按 Enter 开始（输入 s 跳过，q 退出）：").strip().lower()
            if answer == "q":
                break
            if answer == "s":
                continue
        for attempt in range(1, max(1, args.max_attempts) + 1):
            print("3...", end=" ", flush=True)
            time.sleep(0.5)
            print("2...", end=" ", flush=True)
            time.sleep(0.5)
            print("1... 开始")
            audio = record(device.index, args.seconds)
            peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
            rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
            voiced = speech_seconds(audio)
            print(f"  峰值 peak={peak:.4f} rms={rms:.4f} 语音时长={voiced:.2f}s")
            if args.min_speech > 0 and voiced < args.min_speech:
                print(f"  太短（需要 ≥{args.min_speech:.1f}s），本条不保存。")
                if attempt < args.max_attempts and not args.auto:
                    answer = input("  按 Enter 重录，输入 s 保留这条，q 退出：").strip().lower()
                    if answer == "q":
                        return 0
                    if answer == "s":
                        break
                    continue
                if args.auto and attempt < args.max_attempts:
                    print("  自动重录…")
                    continue
                print("  仍太短，跳过本条（如确实想说这么短，请降低 --min-speech）。")
                continue
            break
        else:
            continue

        if args.trim:
            audio = trim_silence(audio)
            voiced = speech_seconds(audio)
        sf.write(str(wav), audio, config.SAMPLE_RATE, subtype="PCM_16")
        print(f"已保存 {wav}  语音时长={voiced:.2f}s  总时长={len(audio)/config.SAMPLE_RATE:.2f}s")
        if peak < 0.01:
            print("警告：录音电平很低，请检查麦克风或隐私权限。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
