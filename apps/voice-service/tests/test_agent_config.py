from __future__ import annotations

import pytest

import config
import loop

AGENT_VARIABLES = (
    "DEEPSEEK_API_KEY",
    "SMART_HOME_AGENT",
    "SMART_HOME_AGENT_ENV_FILE",
    "SMART_HOME_AGENT_BASE_URL",
    "SMART_HOME_AGENT_MODEL",
    "SMART_HOME_AGENT_TIMEOUT",
    "SMART_HOME_AGENT_DEADLINE",
    "SMART_HOME_AGENT_MAX_TOOL_ROUNDS",
    "SMART_HOME_SERVICE_URL",
)

SECRET = "sk-local-secret"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in AGENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield


def test_environment_wins_over_the_local_file(monkeypatch, tmp_path):
    env_file = tmp_path / "agent.env"
    env_file.write_text(f"DEEPSEEK_API_KEY={SECRET}-from-file\n", encoding="utf-8")
    monkeypatch.setenv("SMART_HOME_AGENT_ENV_FILE", str(env_file))
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET + "-from-env")

    assert config.agent_api_key() == SECRET + "-from-env"


def test_key_is_read_from_the_local_file_including_quotes(monkeypatch, tmp_path):
    env_file = tmp_path / "agent.env"
    env_file.write_text(f'DEEPSEEK_API_KEY="{SECRET}"\n', encoding="utf-8")
    monkeypatch.setenv("SMART_HOME_AGENT_ENV_FILE", str(env_file))

    assert config.agent_api_key() == SECRET


def test_missing_key_is_an_empty_string_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_HOME_AGENT_ENV_FILE", str(tmp_path / "absent.env"))

    assert config.agent_api_key() == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("", False), ("no", False)],
)
def test_agent_enabled_reads_the_switch(monkeypatch, value, expected):
    monkeypatch.setenv("SMART_HOME_AGENT", value)

    assert config.agent_enabled() is expected


def test_agent_defaults_match_the_approved_design():
    assert config.DEFAULT_AGENT_BASE_URL == "https://api.deepseek.com"
    assert config.DEFAULT_AGENT_MODEL == "deepseek-chat"
    assert config.DEFAULT_HOME_SERVICE_URL == "http://127.0.0.1:8765"
    assert config.agent_max_tool_rounds() == 4
    assert config.agent_timeout_seconds() == 20.0
    assert config.agent_deadline_seconds() == 20.0


@pytest.mark.parametrize("value", ["abc", "-5", "0", ""])
def test_invalid_numeric_overrides_fall_back_to_defaults(monkeypatch, value):
    monkeypatch.setenv("SMART_HOME_AGENT_TIMEOUT", value)
    monkeypatch.setenv("SMART_HOME_AGENT_MAX_TOOL_ROUNDS", value)

    assert config.agent_timeout_seconds() == config.DEFAULT_AGENT_TIMEOUT_SECONDS
    assert config.agent_max_tool_rounds() == config.DEFAULT_AGENT_MAX_TOOL_ROUNDS


def test_startup_check_reports_missing_agent_key_without_leaking_it(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SMART_HOME_AGENT_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.setattr("sys.argv", ["loop.py", "--agent", "--startup-check"])

    with pytest.raises(loop.VoiceStartupError, match="agent_key_missing"):
        loop.main()

    printed = capsys.readouterr().err
    assert SECRET not in printed


def test_loop_does_not_require_a_key_when_the_agent_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_HOME_AGENT_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.setattr("sys.argv", ["loop.py", "--startup-check", "--no-tts"])
    called = {}

    def fake_startup(*args, **kwargs):
        called["input"] = True
        raise KeyboardInterrupt

    monkeypatch.setattr(loop.audio_utils, "select_input_device", fake_startup)
    with pytest.raises(KeyboardInterrupt):
        loop.main()

    assert called["input"] is True
