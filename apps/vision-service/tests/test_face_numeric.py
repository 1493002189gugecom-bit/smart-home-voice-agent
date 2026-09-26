"""Regression coverage for InsightFace's NumPy scalar outputs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from face import FaceEngine, _float_or_none  # noqa: E402


class FaceNumericTest(unittest.TestCase):
    def test_numpy_detection_values_are_numeric(self) -> None:
        self.assertAlmostEqual(_float_or_none(np.float32(0.9)), 0.9, places=6)
        engine = FaceEngine(SimpleNamespace())
        detected = SimpleNamespace(bbox=np.array([78, 140, 700, 850], dtype=np.float32))
        self.assertEqual(engine._normalized_bbox(detected, 780, 1134), (
            78 / 780, 140 / 1134, 700 / 780, 850 / 1134,
        ))


if __name__ == "__main__":
    unittest.main()
