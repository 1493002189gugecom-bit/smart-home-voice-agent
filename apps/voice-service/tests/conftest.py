from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
TOOLS = Path(__file__).resolve().parents[3] / "tools" / "voice-check"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(TOOLS))

import pytest


@pytest.fixture(autouse=True)
def deterministic_audio_processing(monkeypatch):
    # Loop fixtures encode control markers as PCM; native DSP is tested separately.
    monkeypatch.setenv("SMART_HOME_AUDIO_PROCESSING", "off")
