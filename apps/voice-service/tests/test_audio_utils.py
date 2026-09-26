from __future__ import annotations

import pytest
import sounddevice as sd

import audio_utils
import config
from audio_utils import AudioDevice


def device(index: int, hostapi: str, name: str = "Realtek microphone") -> AudioDevice:
    return AudioDevice(index=index, name=name, hostapi=hostapi, channels=2, default_samplerate=48000)


def test_select_input_prefers_wasapi(monkeypatch):
    devices = [device(3, "MME"), device(12, "Windows DirectSound"), device(24, "Windows WASAPI")]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    assert audio_utils.select_input_device("Realtek").index == 24


def test_select_input_falls_back_when_wasapi_rejects_rate(monkeypatch):
    devices = [device(12, "Windows DirectSound"), device(24, "Windows WASAPI")]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)

    def check(**kwargs):
        if kwargs["device"] == 24:
            raise sd.PortAudioError("Invalid sample rate")

    monkeypatch.setattr(sd, "check_input_settings", check)
    assert audio_utils.select_input_device("Realtek").index == 12


def test_select_input_never_uses_virtual_default(monkeypatch):
    devices = [
        device(1, "MME", "NetEase virtual audio"),
        device(12, "Windows DirectSound", "Realtek microphone"),
    ]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    assert audio_utils.select_input_device("Realtek").index == 12


def test_select_input_fails_loudly_without_matching_device(monkeypatch):
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: [device(1, "MME", "virtual")])
    with pytest.raises(RuntimeError, match="No usable input"):
        audio_utils.select_input_device("Realtek")


def test_system_default_input_wins_over_headset_preference(monkeypatch):
    devices = [
        device(16, "Windows DirectSound", "麦克风阵列 (Realtek(R) Audio)"),
        device(13, "Windows DirectSound", "耳机式麦克风 (HyperX Virtual Surround Sound)"),
    ]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    monkeypatch.setattr(sd.default, "device", (16, -1))
    assert audio_utils.select_input_device().index == 16


def test_invalid_system_default_input_falls_back_to_available_device(monkeypatch):
    devices = [device(13, "Windows DirectSound", "HyperX microphone"), device(16, "Windows WASAPI", "Realtek microphone")]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd.default, "device", (13, -1))

    def check(**kwargs):
        if kwargs["device"] == 13:
            raise sd.PortAudioError("unavailable")

    monkeypatch.setattr(sd, "check_input_settings", check)
    assert audio_utils.select_input_device().index == 16


def test_system_default_output_wins_when_usable(monkeypatch):
    devices = [device(6, "MME", "HyperX headphones"), device(29, "Windows WASAPI", "Realtek speakers")]
    monkeypatch.setattr(audio_utils, "list_output_devices", lambda: devices)
    monkeypatch.setattr(sd.default, "device", (-1, 29))
    monkeypatch.setattr(sd, "check_output_settings", lambda **kwargs: None)
    assert audio_utils.select_playback_target().index == 29


def test_manual_input_selection_uses_stable_name_and_hostapi(monkeypatch):
    devices = [device(13, "Windows DirectSound", "HyperX microphone"), device(31, "Windows WASAPI", "HyperX microphone")]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd.default, "device", (13, -1))
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    assert audio_utils.select_input_device(preferred={"name": "HyperX microphone", "hostapi": "Windows WASAPI"}).index == 31


def test_onboard_microphone_used_when_headset_is_absent(monkeypatch):
    devices = [device(16, "Windows DirectSound", "麦克风阵列 (Realtek(R) Audio)")]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    assert audio_utils.select_input_device().index == 16


def test_input_device_override_by_environment(monkeypatch):
    monkeypatch.setenv("SMART_HOME_INPUT_DEVICE", "Steam Streaming,HyperX")
    assert config.input_device_candidates() == ["Steam Streaming", "HyperX"]
    devices = [
        device(13, "Windows DirectSound", "耳机式麦克风 (HyperX Virtual Surround Sound)"),
        device(14, "Windows DirectSound", "麦克风 (Steam Streaming Microphone)"),
    ]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    assert audio_utils.select_input_device().index == 14


def test_unusable_headset_falls_back_to_next_preference(monkeypatch):
    devices = [
        device(13, "Windows DirectSound", "耳机式麦克风 (HyperX Virtual Surround Sound)"),
        device(16, "Windows DirectSound", "麦克风阵列 (Realtek(R) Audio)"),
    ]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)

    def check(**kwargs):
        if kwargs["device"] == 13:
            raise sd.PortAudioError("Invalid sample rate")

    monkeypatch.setattr(sd, "check_input_settings", check)
    assert audio_utils.select_input_device().index == 16


# ------------------------------------------------- following the system device


def test_a_virtual_system_default_never_takes_the_microphone(monkeypatch):
    """Regression: this machine's default really was a virtual endpoint, and it
    reported itself as usable while producing silent garbage."""
    devices = [
        device(1, "MME", "麦克风阵列 (网易虚拟音频设备)"),
        device(12, "Windows DirectSound", "麦克风阵列 (Realtek(R) Audio)"),
    ]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    monkeypatch.setattr(sd.default, "device", (1, -1))

    assert audio_utils.select_input_device().index == 12


def test_a_virtual_device_is_still_used_when_nothing_real_exists(monkeypatch):
    """Degrading to a poor microphone beats refusing to start at all."""
    devices = [device(1, "MME", "麦克风阵列 (网易虚拟音频设备)")]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    monkeypatch.setattr(sd.default, "device", (1, -1))

    assert audio_utils.select_input_device().index == 1


def test_pinning_a_virtual_device_still_works(monkeypatch):
    """The filter only governs automatic choice; an explicit pick is explicit."""
    devices = [
        device(1, "MME", "麦克风阵列 (网易虚拟音频设备)"),
        device(12, "Windows DirectSound", "麦克风阵列 (Realtek(R) Audio)"),
    ]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)

    chosen = audio_utils.select_input_device(
        preferred={"name": "麦克风阵列 (网易虚拟音频设备)", "hostapi": "MME"}
    )

    assert chosen.index == 1


def test_the_current_system_device_beats_a_name_fallback(monkeypatch):
    """What the user asked for: prefer whatever the system is using right now."""
    devices = [
        device(9, "Windows WASAPI", "麦克风 (USB Audio Device)"),
        device(12, "Windows DirectSound", "麦克风阵列 (Realtek(R) Audio)"),
    ]
    monkeypatch.setattr(audio_utils, "list_input_devices", lambda: devices)
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None)
    monkeypatch.setattr(sd.default, "device", (9, -1))

    assert audio_utils.select_input_device().index == 9


@pytest.mark.parametrize(
    "name",
    [
        "麦克风阵列 (网易虚拟音频设备)",
        "Microsoft 声音映射器 - Input",
        "主声音捕获驱动程序",
        "立体声混音 (Realtek HD Audio Stereo input)",
        "麦克风 (Steam Streaming Microphone)",
        "CABLE Output (VB-Audio Virtual Cable)",
    ],
)
def test_virtual_endpoints_are_recognised_by_name(name):
    assert audio_utils.is_virtual_endpoint(device(0, "MME", name)) is True


@pytest.mark.parametrize(
    "name",
    [
        "麦克风阵列 (Realtek(R) Audio)",
        "耳机式麦克风 (HyperX Virtual Surround Sound)",
        "Microphone (USB Audio Device)",
    ],
)
def test_real_endpoints_are_not_mistaken_for_virtual_ones(name):
    """A bare "virtual" hint would drop the HyperX headset, which is a real mic."""
    assert audio_utils.is_virtual_endpoint(device(0, "MME", name)) is False


def test_order_candidates_puts_real_endpoints_before_virtual_ones():
    devices = [
        device(1, "MME", "麦克风阵列 (网易虚拟音频设备)"),
        device(5, "Windows DirectSound", "麦克风阵列 (Realtek(R) Audio)"),
        device(9, "Windows WASAPI", "麦克风 (HyperX)"),
    ]

    ordered = audio_utils.order_candidates(devices, 1, ["HyperX", "Realtek"])

    # Pattern order is preserved inside each group; the virtual default sinks last.
    assert [item.index for item in ordered] == [9, 5, 1]
