"""Only aggregate quality reason counts; never retain rejected image data."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import FaceQuality, FaceSample
from registration import RegistrationSession


def test_session_reports_a_distribution_of_rejected_frames():
    session = RegistrationSession("dad", SimpleNamespace(), engine=object())
    frame = SimpleNamespace(shape=(720, 1280, 3))
    blurred = FaceSample(
        bbox=(0.2, 0.2, 0.8, 0.8), landmarks=(),
        detection_confidence=0.9, quality=FaceQuality(False, "blurred"),
        embedding=None,
    )

    session.accept(frame, [], 1000)
    session.accept(frame, [], 1100)
    result = session.accept(frame, [blurred], 1200).to_dict()

    assert result["rejection_counts"] == {"no_face": 2, "blurred": 1}
    assert "frame" not in result
