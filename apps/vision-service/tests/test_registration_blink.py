"""Regressions for blink geometry and stale registration quality messages."""

from __future__ import annotations

import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import FaceQuality, FaceSample, NormalizedPoint
from registration import RegistrationSession


class LandmarkEngine:
    """Only replace model inference; exercise the real session and geometry."""

    def __init__(self):
        self.points = ()

    def landmarks_106(self, bbox):
        return self.points


def eye_points(aspect=1.0, opening=0.03, roll=0.0):
    # Official InsightFace 2d106det markup: corners 35/39 and 89/93;
    # upper lids 41/42, 95/96; lower lids 36/37, 90/91.
    # Pixel-equivalent eye width is .10 and open lid separation .03: EAR .30.
    # Keep the central lower lids 33/87 to expose the old wrong-corner mapping.
    points = [(0.0, 0.0, 0.0)] * 106
    for center_x, indices in ((0.35, (35, 41, 42, 39, 37, 36, 33)),
                              (0.65, (89, 95, 96, 93, 91, 90, 87))):
        offsets = ((-.05, 0), (-.025, -opening / 2), (.025, -opening / 2),
                   (.05, 0), (.025, opening / 2), (-.025, opening / 2),
                   (0, opening / 2))
        for index, (x, y) in zip(indices, offsets):
            rx = x * math.cos(roll) - y * math.sin(roll)
            ry = x * math.sin(roll) + y * math.cos(roll)
            points[index] = (center_x + rx, .40 + ry * aspect, 1.0)
    return tuple(points)


class RegistrationBlinkTest(unittest.TestCase):
    def setUp(self):
        self.engine = LandmarkEngine()
        self.engine.points = eye_points()
        self.session = RegistrationSession("dad", SimpleNamespace(), engine=self.engine)
        self.session._steps = ["blink"]
        self.frame = np.zeros((1000, 1000, 3), dtype=np.uint8)
        self.sample = FaceSample(
            bbox=(.25, .20, .75, .80),
            landmarks=tuple(NormalizedPoint(x, y, 1.0) for x, y in
                            ((.35, .40), (.65, .40), (.50, .50), (.40, .65), (.60, .65))),
            detection_confidence=.99, quality=FaceQuality(True), embedding=(1.0, 0.0),
            frame_aspect_ratio=1.0,
        )

    def test_eyelid_ratio_uses_both_lids_and_is_invariant_to_aspect_and_roll(self):
        for aspect in (1.0, 16 / 9, 9 / 16):
            for roll in (0.0, math.pi / 6):
                with self.subTest(aspect=aspect, roll=roll):
                    self.engine.points = eye_points(aspect, roll=roll)
                    sample = replace(self.sample, frame_aspect_ratio=aspect)
                    self.assertAlmostEqual(self.session._eyelid_ratio(sample), .30, places=6)

    def test_valid_blink_frame_clears_previous_blur_warning(self):
        blurred = replace(self.sample, quality=FaceQuality(False, "blurred"), embedding=None)
        self.assertEqual(self.session.accept(self.frame, [blurred], 100).quality_reason, "blurred")
        status = self.session.accept(self.frame, [self.sample], 200)
        self.assertIsNone(status.quality_reason)
        self.assertEqual(status.state, "collecting")

    def test_recovered_eye_landmarks_clear_previous_missing_landmarks_warning(self):
        self.engine.points = ()
        self.assertEqual(self.session.accept(self.frame, [self.sample], 100).quality_reason,
                         "landmarks_incomplete")
        self.engine.points = eye_points()
        self.assertIsNone(self.session.accept(self.frame, [self.sample], 200).quality_reason)

    def calibrate(self):
        for at_ms in (100, 200, 300, 400, 500):
            status = self.session.accept(self.frame, [self.sample], at_ms)
            self.assertEqual(status.state, "collecting")

    def test_open_closed_open_cycle_advances_after_open_eye_calibration(self):
        self.calibrate()
        self.engine.points = eye_points(opening=.004)
        self.assertEqual(self.session.accept(self.frame, [self.sample], 600).state, "collecting")
        self.engine.points = eye_points()
        status = self.session.accept(self.frame, [self.sample], 800)
        self.assertEqual(status.state, "confirming")
        self.assertIsNone(status.quality_reason)
        self.assertEqual(self.session.prototypes(), [])  # Blink never stores a closed-eye prototype.

    def test_closed_then_open_without_calibration_does_not_advance(self):
        self.engine.points = eye_points(opening=.004)
        self.session.accept(self.frame, [self.sample], 100)
        self.engine.points = eye_points()
        self.assertEqual(self.session.accept(self.frame, [self.sample], 200).state, "collecting")

    def test_interrupted_cycle_requires_a_new_blink(self):
        self.calibrate()
        self.engine.points = eye_points(opening=.004)
        self.session.accept(self.frame, [self.sample], 600)
        self.engine.points = eye_points()
        self.assertEqual(self.session.accept(self.frame, [self.sample], 1500).state, "collecting")

    def test_low_quality_frame_is_still_rejected(self):
        blurred = replace(self.sample, quality=FaceQuality(False, "blurred"), embedding=None)
        self.assertEqual(self.session.accept(self.frame, [blurred], 100).quality_reason, "blurred")
        self.assertEqual(self.session._calibration.open_samples, 0)


if __name__ == "__main__":
    unittest.main()
