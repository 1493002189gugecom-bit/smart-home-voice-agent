from __future__ import annotations

import sys
from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
CONFIG = Path(__file__).resolve().parents[1] / "config" / "rooms.json"


@pytest.fixture(autouse=True)
def isolated_write_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME_SERVICE_WRITE_TOKEN_FILE", str(tmp_path / "home-write.token"))
