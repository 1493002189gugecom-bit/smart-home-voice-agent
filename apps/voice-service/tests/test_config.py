from __future__ import annotations

from pathlib import Path

import pytest

import config


def test_default_model_root_is_d_drive(monkeypatch):
    monkeypatch.delenv("SMART_HOME_MODELS_DIR", raising=False)
    assert config.models_dir() == Path(r"D:\smart-home-models")


def test_model_root_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_HOME_MODELS_DIR", str(tmp_path))
    assert config.models_dir() == tmp_path


def test_wake_command_merge_defaults_on_and_can_be_disabled(monkeypatch):
    monkeypatch.delenv("SMART_HOME_WAKE_MERGE", raising=False)
    assert config.wake_command_merge_enabled() is True
    monkeypatch.setenv("SMART_HOME_WAKE_MERGE", "0")
    assert config.wake_command_merge_enabled() is False


@pytest.mark.parametrize("raw, expected", [("", 0.7), ("1.0", 1.0), ("0", 0.7), ("3", 0.7), ("nan", 0.7), ("inf", 0.7)])
def test_command_end_silence_is_bounded(monkeypatch, raw, expected):
    monkeypatch.setenv("SMART_HOME_COMMAND_END_SILENCE", raw)
    assert config.command_end_silence_seconds() == expected


def test_require_reports_model_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_HOME_MODELS_DIR", str(tmp_path))
    missing = tmp_path / "missing.onnx"
    with pytest.raises(FileNotFoundError) as exc:
        config.require(missing)
    assert str(missing) in str(exc.value)
    assert "SMART_HOME_MODELS_DIR" in str(exc.value)


@pytest.mark.skipif(not config.DEFAULT_MODELS_DIR.exists(), reason="local model directory is not installed")
def test_downloaded_model_paths_exist(monkeypatch):
    monkeypatch.delenv("SMART_HOME_MODELS_DIR", raising=False)
    assert all(path.exists() for path in config.asr_paths().values())
    assert all(path.exists() for key, path in config.tts_paths().items() if key != "lexicon")
    assert all(path.exists() for path in config.kws_paths().values())
    assert config.vad_model().exists()
