"""Bounded, allow-listed performance records shared by local services.

This module deliberately accepts no text payloads, names, paths, or exception
messages. A metric is useful for correlation without becoming a second voice
or camera history.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "runtime" / "perception" / "diagnostics"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_COMPONENTS = frozenset({"launcher", "voice", "vision", "home", "console", "unity"})
_RUN_KINDS = frozenset({"cold", "warm", "continuous"})
_WRITER_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_WRITER_LOCKS_GUARD = threading.Lock()


def _identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _code(value: Any) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


class DiagnosticWriter:
    def __init__(self, directory: Path = DEFAULT_DIRECTORY, component: str = "home",
                 *, max_bytes: int = 1_048_576, backups: int = 2):
        if component not in _COMPONENTS:
            raise ValueError("unknown diagnostic component")
        self.directory = Path(directory)
        self.component = component
        self.max_bytes = max(256, int(max_bytes))
        self.backups = max(0, min(8, int(backups)))
        with _WRITER_LOCKS_GUARD:
            self._lock = _WRITER_LOCKS.setdefault((str(self.directory.resolve()), component), threading.Lock())

    def emit(self, stage: str, *, outcome: str = "ok", trace_id: str | None = None,
             observation_id: str | None = None, operation_id: str | None = None,
             trigger_ids: Iterable[str] | None = None,
             duration_ms: float | None = None, error_code: str | None = None,
             resource_rss_mb: float | None = None, resource_cpu_percent: float | None = None,
             run_kind: str | None = None, **_discarded: Any) -> None:
        if self.directory == DEFAULT_DIRECTORY and "PYTEST_CURRENT_TEST" in os.environ:
            return
        safe_stage = _code(stage)
        if safe_stage is None or outcome not in {"ok", "error", "cancelled", "skipped"}:
            return
        record: dict[str, Any] = {
            "at_ms": int(time.time() * 1000), "component": self.component,
            "stage": safe_stage, "outcome": outcome,
        }
        kind = run_kind or os.environ.get("SMART_HOME_DIAGNOSTIC_RUN", "continuous")
        if kind in _RUN_KINDS:
            record["run_kind"] = kind
        for key, value in (("trace_id", trace_id), ("observation_id", observation_id),
                           ("operation_id", operation_id)):
            accepted = _identifier(value)
            if accepted is not None:
                record[key] = hashlib.sha256(accepted.encode("utf-8")).hexdigest()[:24]
        if trigger_ids is not None:
            safe_triggers = [_identifier(value) for value in list(trigger_ids)[:256]]
            record["trigger_ids"] = [hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
                                     for value in safe_triggers if value is not None]
        accepted_code = _code(error_code)
        if accepted_code is not None:
            record["error_code"] = accepted_code
        for key, value in (("duration_ms", duration_ms), ("resource_rss_mb", resource_rss_mb),
                           ("resource_cpu_percent", resource_cpu_percent)):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                record[key] = round(float(value), 3)
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > self.max_bytes:
            return
        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / f"{self.component}.jsonl"
                if path.exists() and path.stat().st_size + len(line) > self.max_bytes:
                    self._rotate(path)
                with path.open("ab") as stream:
                    stream.write(line)
        except OSError:
            # Diagnostics must not affect the device-control or perception path.
            return

    def _rotate(self, path: Path) -> None:
        if self.backups == 0:
            path.unlink(missing_ok=True)
            return
        oldest = path.with_name(path.name + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            previous = path.with_name(path.name + f".{index}")
            if previous.exists():
                previous.replace(path.with_name(path.name + f".{index + 1}"))
        path.replace(path.with_name(path.name + ".1"))


def read_records(directory: Path = DEFAULT_DIRECTORY) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    paths = list(Path(directory).glob("*.jsonl*")) + list((Path(directory) / "resource-samples").glob("*.jsonl*"))
    for path in sorted(paths):
        if not re.fullmatch(r"(?:launcher|voice|vision|home|console|unity)\.jsonl(?:\.[1-8])?", path.name):
            continue
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        except OSError:
            continue
    return rows


def _percentile(values: list[float], percent: float) -> float | str:
    if not values:
        return "未测量"
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)], 3)


def _chains(rows: list[dict[str, Any]]) -> dict[str, Any]:
    linked: dict[str, list[dict[str, Any]]] = {}
    turns: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        trace_id = _identifier(row.get("trace_id"))
        if trace_id is not None:
            turns.setdefault(trace_id, []).append(row)
        ids = [row.get("operation_id"), row.get("observation_id")]
        triggers = row.get("trigger_ids")
        if isinstance(triggers, list):
            ids.extend(triggers[:256])
        for identifier in set(value for value in ids if _identifier(value) is not None):
            linked.setdefault(identifier, []).append(row)

    completed: list[dict[str, Any]] = []
    incomplete: dict[str, int] = {}
    incomplete_by_kind: dict[str, dict[str, int]] = {"operation": {}, "observation": {}, "unknown": {}}
    for identifier, events in linked.items():
        operation = any(row.get("component") == "voice" and row.get("stage") == "device_operation"
                        for row in events) or any(row.get("stage") == "device_confirm" for row in events)
        observation = any(row.get("component") == "vision" and row.get("stage") in
                          {"observation", "capture_age", "pose", "face", "render", "frame", "home_sync"} for row in events) or any(
                              row.get("stage") == "vision_ingress" for row in events)
        if not operation and not observation:
            kind = "unknown"
        else:
            kind = "operation" if operation else "observation"
        source: list[dict[str, Any]] = []
        if kind == "operation":
            trace_ids = [row.get("trace_id") for row in events if row.get("stage") == "device_operation"]
            for trace_id in trace_ids:
                source.extend(turns.get(trace_id, []))
            source = [row for row in source if row.get("component") == "voice" and row.get("stage") == "capture"]
        elif kind == "observation":
            source = [row for row in events if row.get("component") == "vision" and row.get("stage") == "observation"]
            if not source:
                source = [row for row in events if row.get("component") == "vision" and row.get("stage") == "capture_age"]
        home_stage = "device_confirm" if kind == "operation" else "vision_ingress"
        home = [row for row in events if row.get("component") == "home" and row.get("stage") == home_stage]
        sse = [row for row in events if row.get("component") == "home" and row.get("stage") == "sse_snapshot"
               and row.get("outcome") == "ok"]
        unity = [row for row in events if row.get("component") == "unity" and row.get("stage") == "snapshot_apply"]
        if not source:
            reason = "missing_source"
        elif not home:
            reason = "missing_home"
        elif not sse:
            reason = "missing_sse"
        elif not unity:
            reason = "missing_unity"
        else:
            reason = None
        if reason:
            incomplete[reason] = incomplete.get(reason, 0) + 1
            by_kind = incomplete_by_kind[kind]
            by_kind[reason] = by_kind.get(reason, 0) + 1
            continue
        start_row = min(source, key=lambda row: row.get("at_ms", 0))
        start = start_row.get("at_ms")
        duration = start_row.get("duration_ms", 0)
        if not isinstance(start, int):
            incomplete["invalid_clock"] = incomplete.get("invalid_clock", 0) + 1
            by_kind = incomplete_by_kind[kind]
            by_kind["invalid_clock"] = by_kind.get("invalid_clock", 0) + 1
            continue
        if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
            start -= duration
        home_at = min((row.get("at_ms") for row in home if isinstance(row.get("at_ms"), int)), default=None)
        sse_at = min((row.get("at_ms") for row in sse if isinstance(row.get("at_ms"), int)
                      and (home_at is None or row["at_ms"] >= home_at)), default=None)
        unity_at = min((row.get("at_ms") for row in unity if isinstance(row.get("at_ms"), int)
                        and (sse_at is None or row["at_ms"] >= sse_at)), default=None)
        if home_at is None or sse_at is None or unity_at is None or not start <= home_at <= sse_at <= unity_at:
            incomplete["invalid_clock"] = incomplete.get("invalid_clock", 0) + 1
            by_kind = incomplete_by_kind[kind]
            by_kind["invalid_clock"] = by_kind.get("invalid_clock", 0) + 1
            continue
        completed.append({
            "kind": kind, "id": identifier,
            "total_ms": round(unity_at - start, 3),
            "source_to_home_ms": round(home_at - start, 3),
            "home_to_sse_ms": round(sse_at - home_at, 3),
            "sse_to_unity_ms": round(unity_at - sse_at, 3),
        })
    totals = [item["total_ms"] for item in completed]
    by_kind = {}
    for kind in ("operation", "observation"):
        values = [item["total_ms"] for item in completed if item["kind"] == kind]
        by_kind[kind] = {"completed": len(values), "p50_ms": _percentile(values, 50),
                         "p95_ms": _percentile(values, 95), "incomplete": incomplete_by_kind[kind]}
    return {
        "completed": len(completed), "p50_ms": _percentile(totals, 50),
        "p95_ms": _percentile(totals, 95), "incomplete": incomplete,
        "by_kind": by_kind,
        "slow_chains": sorted(completed, key=lambda item: item["total_ms"], reverse=True)[:10],
    }


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    stages: dict[str, dict[str, Any]] = {}
    run_counts = {kind: 0 for kind in sorted(_RUN_KINDS)}
    scenario_stages: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in sorted(_RUN_KINDS)}
    resource = {"rss_mb": [], "cpu_percent": []}
    resource_by_component: dict[str, dict[str, list[float]]] = {}
    slow: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        component, stage = row.get("component"), row.get("stage")
        if component not in _COMPONENTS or _code(stage) is None:
            continue
        key = f"{component}.{stage}"
        bucket = stages.setdefault(key, {"count": 0, "errors": 0, "durations": []})
        bucket["count"] += 1
        kind = row.get("run_kind")
        scenario_bucket = (scenario_stages[kind].setdefault(key, {"count": 0, "errors": 0, "durations": []})
                           if kind in scenario_stages else None)
        if scenario_bucket is not None:
            scenario_bucket["count"] += 1
        if row.get("outcome") == "error":
            bucket["errors"] += 1
            if scenario_bucket is not None:
                scenario_bucket["errors"] += 1
        value = row.get("duration_ms")
        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            bucket["durations"].append(float(value))
            if scenario_bucket is not None:
                scenario_bucket["durations"].append(float(value))
            identifiers = {name: row[name] for name in ("trace_id", "observation_id", "operation_id")
                           if _identifier(row.get(name)) is not None}
            trigger_ids = row.get("trigger_ids")
            if isinstance(trigger_ids, list):
                identifiers["trigger_ids"] = [item for item in trigger_ids[:16] if _identifier(item) is not None]
            slow.append({"stage": key, "duration_ms": value, "run_kind": kind, **identifiers})
        if row.get("run_kind") in run_counts:
            run_counts[row["run_kind"]] += 1
        for source, target in (("resource_rss_mb", "rss_mb"), ("resource_cpu_percent", "cpu_percent")):
            measured = row.get(source)
            if isinstance(measured, (int, float)) and math.isfinite(measured) and measured >= 0:
                resource[target].append(measured)
                resource_by_component.setdefault(component, {"rss_mb": [], "cpu_percent": []})[target].append(measured)
    output = {}
    def metrics(bucket: dict[str, Any]) -> dict[str, Any]:
        return {
            "count": bucket["count"],
            "failure_rate": round(bucket["errors"] / bucket["count"], 4),
            "p50_ms": _percentile(bucket["durations"], 50),
            "p95_ms": _percentile(bucket["durations"], 95),
        }
    for key, bucket in sorted(stages.items()):
        output[key] = metrics(bucket)
    peaks = {key: round(max(values), 3) if values else "未测量" for key, values in resource.items()}
    return {
        "stages": output,
        "run_kinds": {key: count if count else "未测量" for key, count in run_counts.items()},
        "scenarios": {kind: {key: metrics(bucket) for key, bucket in sorted(scenario_stages[kind].items())}
                      if scenario_stages[kind] else "未测量" for kind in sorted(_RUN_KINDS)},
        "resource_peak": peaks if any(resource.values()) else "未测量",
        "resource_peak_by_component": {
            component: {key: round(max(values), 3) if values else "未测量" for key, values in samples.items()}
            for component, samples in sorted(resource_by_component.items())
        } if resource_by_component else "未测量",
        "slow_cases": sorted(slow, key=lambda item: item["duration_ms"], reverse=True)[:10],
        "end_to_end": _chains(rows),
    }
