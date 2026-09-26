from __future__ import annotations

import device_preferences


def test_preferences_round_trip_without_device_indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(device_preferences, "PREFERENCES_PATH", tmp_path / "devices.json")
    expected = {"input": {"name": "Realtek microphone", "hostapi": "Windows WASAPI"}, "output": None}
    device_preferences.save(expected)
    assert device_preferences.load() == expected
    assert "index" not in (tmp_path / "devices.json").read_text()
