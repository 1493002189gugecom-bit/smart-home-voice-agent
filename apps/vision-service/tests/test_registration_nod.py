"""A real downward rotation foreshortens the face; it need not make it taller."""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from contracts import FaceQuality, FaceSample, NormalizedPoint
from registration import RegistrationSession


def projected_face(pitch_degrees=0, nose_depth=-30, width=1280, height=720):
    # Camera looks along +Z; a protruding nose has negative Z. Positive X-axis
    # rotation moves the nose downward. Perspective projection supplies an
    # independent fixture, rather than encoding the registration implementation.
    angle = math.radians(pitch_degrees)
    points = []
    for x, y, z in ((-60, -40, 0), (60, -40, 0), (0, 0, nose_depth),
                    (-40, 50, 0), (40, 50, 0)):
        ry = y * math.cos(angle) - z * math.sin(angle)
        rz = y * math.sin(angle) + z * math.cos(angle) + 650
        points.append(NormalizedPoint((width / 2 + 800 * x / rz) / width,
                                      (height / 2 + 800 * ry / rz) / height, 1))
    return FaceSample(bbox=(.25, .15, .75, .90), landmarks=tuple(points),
                      detection_confidence=.99, quality=FaceQuality(True),
                      embedding=(1.0, 0.0), frame_aspect_ratio=width / height)


class RegistrationNodTest(unittest.TestCase):
    def setUp(self):
        self.session = RegistrationSession("dad", SimpleNamespace(), engine=object())
        self.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for stamp in (100, 200):
            self.session.accept(self.frame, [projected_face()], stamp)

    def test_downward_nod_passes_without_unrealistic_eye_to_mouth_distance(self):
        self.session._steps = ["look_down", "blink"]
        down = projected_face(20)
        # Geometry really compresses the face below the old fixed 1.25 cutoff.
        self.assertLess(self.session._head_direction(down)[1][1], 1.25)
        first = self.session.accept(self.frame, [down], 300)
        self.assertEqual(first.accepted_samples, 1)
        self.assertIsNone(first.quality_reason)
        self.assertEqual(self.session.accept(self.frame, [down], 400).step, "blink")

    def test_upward_nod_is_not_accepted_as_downward(self):
        self.session._steps = ["look_down", "blink"]
        for sample in (projected_face(), projected_face(-20)):
            status = self.session.accept(self.frame, [sample], 300)
            self.assertEqual(status.accepted_samples, 0)
            self.assertEqual(status.quality_reason, "wrong_action")

    def test_upward_nod_is_measured_relative_to_front_sample(self):
        self.session._steps = ["look_up", "look_down", "blink"]
        status = self.session.accept(self.frame, [projected_face(-20)], 300)
        self.assertEqual(status.accepted_samples, 1)
        self.assertIsNone(status.quality_reason)

    def test_nods_work_with_different_neutral_angles_and_nose_depths(self):
        for neutral in (-8, 8):
            for depth in (-20, -40):
                for step, delta in (("look_down", 20), ("look_up", -20)):
                    with self.subTest(neutral=neutral, depth=depth, step=step):
                        session = RegistrationSession("dad", SimpleNamespace(), engine=object())
                        for stamp in (100, 200):
                            session.accept(self.frame, [projected_face(neutral, depth)], stamp)
                        session._steps = [step, "blink"]
                        status = session.accept(self.frame, [projected_face(neutral + delta, depth)], 300)
                        self.assertEqual(status.accepted_samples, 1)
                        self.assertEqual(status.action_progress, 1.0)


if __name__ == "__main__":
    unittest.main()
