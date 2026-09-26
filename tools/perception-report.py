"""Inspect bounded, redacted perception metrics without starting devices."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass
sys.path.insert(0, str(ROOT / "apps"))
from diagnostics import DEFAULT_DIRECTORY, DiagnosticWriter, read_records, summarize  # noqa: E402


def _watch(seconds: float, interval: float, directory: Path) -> None:
    try:
        import psutil
    except ImportError as exc:
        raise SystemExit("resource sampling requires psutil in the project .venv") from exc
    manifest = ROOT / "runtime" / "perception" / "processes.json"
    if not manifest.exists():
        raise SystemExit("No process manifest; start the perception stack first")
    processes = json.loads(manifest.read_text(encoding="utf-8-sig")).get("processes", [])
    tracked = []
    for entry in processes:
        name = entry.get("name")
        if name not in {"home", "vision", "voice", "console"}:
            continue
        try:
            process = psutil.Process(int(entry["pid"]))
            if abs(process.create_time() - int(entry["creationTicks"]) / 10_000_000 + 62135596800) > 2:
                continue
            process.cpu_percent(None)
            tracked.append((name, process))
        except (psutil.Error, KeyError, TypeError, ValueError):
            continue
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        time.sleep(min(interval, max(0.0, end - time.monotonic())))
        for name, process in tracked:
            try:
                children = [child for child in process.children(recursive=True) if child.is_running()]
                group = [process] + children
                rss = sum(item.memory_info().rss for item in group) / 1_048_576
                cpu = sum(item.cpu_percent(None) for item in group)
                DiagnosticWriter(directory / "resource-samples", name).emit("resource", resource_rss_mb=rss,
                                                       resource_cpu_percent=cpu)
            except psutil.Error:
                DiagnosticWriter(directory / "resource-samples", name).emit("resource", outcome="error",
                                                       error_code="process_exited")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--unity-directory", type=Path,
                        help="Optional Unity Application.persistentDataPath/smart-home-diagnostics")
    parser.add_argument("--since-ms", type=int, default=0,
                        help="Only include records at or after this Unix millisecond timestamp")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("report")
    watch = sub.add_parser("watch")
    watch.add_argument("--seconds", type=float, default=60.0)
    watch.add_argument("--interval", type=float, default=2.0)
    record = sub.add_parser("record")
    record.add_argument("--component", choices=["launcher", "home", "vision", "voice", "console", "unity"], required=True)
    record.add_argument("--stage", required=True)
    record.add_argument("--outcome", choices=["ok", "error", "cancelled", "skipped"], default="ok")
    record.add_argument("--duration-ms", type=float)
    record.add_argument("--error-code")
    record.add_argument("--run-kind", choices=["cold", "warm", "continuous"], default="continuous")
    args = parser.parse_args()
    if args.action == "record":
        DiagnosticWriter(args.directory, args.component).emit(
            args.stage, outcome=args.outcome, duration_ms=args.duration_ms,
            error_code=args.error_code, run_kind=args.run_kind,
        )
        return 0
    if args.action == "watch":
        if not 0 < args.interval <= args.seconds:
            parser.error("interval must be positive and no larger than seconds")
        _watch(args.seconds, args.interval, args.directory)
    rows = read_records(args.directory)
    if args.unity_directory:
        rows.extend(read_records(args.unity_directory))
    rows = [row for row in rows if isinstance(row.get("at_ms"), int) and row["at_ms"] >= args.since_ms]
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
