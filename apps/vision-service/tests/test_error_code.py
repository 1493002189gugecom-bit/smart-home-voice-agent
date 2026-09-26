"""Runtime errors must remain stable, human-readable API codes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipeline import _safe_error_code  # noqa: E402


class SafeErrorCodeTest(unittest.TestCase):
    def test_native_numeric_error_does_not_leak_into_status(self) -> None:
        error = RuntimeError("native failure")
        error.code = -4
        self.assertEqual(_safe_error_code(error, "model_error"), "model_error")

    def test_named_service_error_is_preserved(self) -> None:
        error = RuntimeError("camera disconnected")
        error.code = "camera_disconnected"
        self.assertEqual(_safe_error_code(error, "model_error"), "camera_disconnected")


if __name__ == "__main__":
    unittest.main()
