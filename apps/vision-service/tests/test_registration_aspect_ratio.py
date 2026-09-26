"""Registration direction ratios must be independent of frame aspect ratio."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import FaceQuality, FaceSample, NormalizedPoint  # noqa: E402
from registration import RegistrationSession  # noqa: E402


class RegistrationAspectRatioTest(unittest.TestCase):
    def make_sample(self, eye_to_mouth_pixels: int) -> FaceSample:
        width, height = 1280, 720

        def point(x: int, y: int) -> NormalizedPoint:
            return NormalizedPoint(x / width, y / height, 0.99)

        return FaceSample(
            bbox=(400 / width, 200 / height, 600 / width, 500 / height),
            landmarks=(
                point(440, 300), point(560, 300), point(500, 320),
                point(465, 300 + eye_to_mouth_pixels),
                point(535, 300 + eye_to_mouth_pixels),
            ),
            detection_confidence=0.99,
            quality=FaceQuality(True),
            embedding=(1.0, 0.0),
            frame_aspect_ratio=width / height,
        )

    def test_frontal_pose_from_wide_frame_is_not_misread_as_looking_down(self) -> None:
        width, height = 1280, 720
        # Eye-to-mouth distance is 90px and inter-eye distance is 120px: a
        # neutral 0.75 pitch ratio. Per-axis normalization makes its raw ratio
        # 1.33 in a 16:9 image unless the frame aspect ratio is applied.
        sample = self.make_sample(90)
        session = RegistrationSession("dad", SimpleNamespace(), engine=object())

        status = session.accept(np.zeros((height, width, 3), dtype=np.uint8), [sample], 100)

        self.assertEqual(status.quality_reason, None)
        self.assertEqual(status.accepted_samples, 1)
        self.assertEqual(status.step, "front")

    def test_neutral_front_pose_is_not_classified_as_looking_down(self) -> None:
        # Exercise a neutral pose near the upper end of the front tolerance.
        sample = self.make_sample(120)
        session = RegistrationSession("dad", SimpleNamespace(), engine=object())
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        session.accept(frame, [sample], 100)
        session.accept(frame, [sample], 200)
        direction, ratios = session._head_direction(sample)
        self.assertEqual(direction, "front")
        session._steps = ["front"]
        self.assertTrue(session._matches_step(direction, ratios))
        session._steps = ["look_down"]
        self.assertFalse(session._matches_step(direction, ratios, session._nose_position(sample)))

        # Merely moving the mouth farther from the eyes is not evidence of a nod.
        stretched_face = self.make_sample(156)
        down_direction, down_ratios = session._head_direction(stretched_face)
        self.assertFalse(session._matches_step(down_direction, down_ratios,
                                              session._nose_position(stretched_face)))


if __name__ == "__main__":
    unittest.main()
