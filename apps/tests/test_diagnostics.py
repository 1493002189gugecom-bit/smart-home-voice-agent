import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diagnostics import DiagnosticWriter, summarize


class DiagnosticTests(unittest.TestCase):
    def test_allowlist_rotation_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = DiagnosticWriter(Path(tmp), "voice", max_bytes=360, backups=2)
            for index in range(8):
                writer.emit("asr", trace_id="turn-123", duration_ms=index + 1,
                            outcome="ok", transcript="secret", token="secret")
            files = list(Path(tmp).glob("voice.jsonl*"))
            self.assertLessEqual(len(files), 3)
            self.assertTrue(all(path.stat().st_size <= 360 for path in files))
            contents = "".join(path.read_text(encoding="utf-8") for path in files)
            self.assertNotIn("secret", contents)
            self.assertNotIn("turn-123", contents)
            rows = [json.loads(line) for path in files for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(all(set(row) <= {"at_ms", "component", "stage", "outcome", "trace_id", "duration_ms", "run_kind"} for row in rows))
            report = summarize(rows)
            self.assertIn("voice.asr", report["stages"])
            self.assertIsNotNone(report["stages"]["voice.asr"]["p95_ms"])

    def test_missing_measurements_are_explicit(self):
        report = summarize([])
        self.assertEqual(report["resource_peak"], "未测量")
        self.assertEqual(report["run_kinds"]["cold"], "未测量")
        self.assertEqual(report["scenarios"]["cold"], "未测量")

    def test_cross_service_ids_match_after_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            DiagnosticWriter(directory, "voice").emit("device_operation", operation_id="op-abc")
            DiagnosticWriter(directory, "home").emit("request", operation_id="op-abc")
            DiagnosticWriter(directory, "vision").emit("home_sync", observation_id="vision-abc:123")
            DiagnosticWriter(directory, "home").emit("request", observation_id="vision-abc:123")
            rows = [json.loads(line) for path in directory.glob("*.jsonl")
                    for line in path.read_text(encoding="utf-8").splitlines()]
            voice = next(row for row in rows if row["component"] == "voice")
            vision = next(row for row in rows if row["component"] == "vision")
            home_operation = next(row for row in rows if row["component"] == "home" and "operation_id" in row)
            home_observation = next(row for row in rows if row["component"] == "home" and "observation_id" in row)
            self.assertEqual(voice["operation_id"], home_operation["operation_id"])
            self.assertEqual(vision["observation_id"], home_observation["observation_id"])
            self.assertNotIn("op-abc", repr(rows))

    def test_unity_slow_case_retains_bounded_trigger_hashes(self):
        report = summarize([{
            "component": "unity", "stage": "sse_arrival", "outcome": "ok",
            "run_kind": "continuous", "duration_ms": 120,
            "trigger_ids": ["a" * 24] * 20,
        }])
        self.assertEqual(len(report["slow_cases"][0]["trigger_ids"]), 16)

    def test_end_to_end_chains_join_voice_vision_home_sse_unity(self):
        rows = [
            {"component": "voice", "stage": "capture", "at_ms": 1000, "duration_ms": 100, "trace_id": "turn-a"},
            {"component": "voice", "stage": "device_operation", "at_ms": 1100, "trace_id": "turn-a", "operation_id": "op-a"},
            {"component": "home", "stage": "device_confirm", "at_ms": 1200, "operation_id": "op-a"},
            {"component": "home", "stage": "sse_snapshot", "at_ms": 1300, "trigger_ids": ["op-a"]},
            {"component": "unity", "stage": "snapshot_apply", "at_ms": 1400, "trigger_ids": ["op-a"]},
            {"component": "vision", "stage": "observation", "at_ms": 2000, "duration_ms": 50, "observation_id": "obs-a"},
            {"component": "home", "stage": "vision_ingress", "at_ms": 2020, "observation_id": "obs-a"},
            {"component": "home", "stage": "sse_snapshot", "at_ms": 2050, "trigger_ids": ["obs-a"]},
            {"component": "unity", "stage": "snapshot_apply", "at_ms": 2100, "trigger_ids": ["obs-a"]},
            {"component": "home", "stage": "device_confirm", "at_ms": 2200, "operation_id": "missing-source"},
        ]
        for row in rows:
            row.setdefault("outcome", "ok")
        chains = summarize(rows)["end_to_end"]
        self.assertEqual(chains["completed"], 2)
        self.assertEqual(chains["p50_ms"], 150)
        self.assertEqual(chains["p95_ms"], 500)
        self.assertEqual(chains["incomplete"], {"missing_source": 1})
        self.assertEqual(chains["by_kind"]["operation"]["p95_ms"], 500)
        self.assertEqual(chains["by_kind"]["observation"]["p95_ms"], 150)
        self.assertEqual(chains["slow_chains"][0]["home_to_sse_ms"], 100)


if __name__ == "__main__":
    unittest.main()
