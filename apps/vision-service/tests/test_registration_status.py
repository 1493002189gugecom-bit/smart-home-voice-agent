"""An idle registration poll is normal, so the client must not back off."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import VisionApp


class RegistrationStatusTest(unittest.TestCase):
    def test_no_session_returns_success_with_null_registration(self):
        runtime = SimpleNamespace(registration_status=lambda: None)
        status, payload = VisionApp(runtime).handle("GET", "/registration", None)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "registration": None})


if __name__ == "__main__":
    unittest.main()
