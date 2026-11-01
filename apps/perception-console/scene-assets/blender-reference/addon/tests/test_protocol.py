import json
import sys
from pathlib import Path


ADDON = Path(__file__).resolve().parents[1] / "smart_home_live"
sys.path.insert(0, str(ADDON))

from protocol import SSEDecoder, devices_by_id, format_device_state  # noqa: E402


def device(name, kind, state, online=True):
    return {"id": "device", "name": name, "type": kind, "online": online, "state": state}


def test_decoder_handles_utf8_split_across_chunks_and_multiple_events():
    decoder = SSEDecoder()
    raw = (
        'id: 4\nevent: snapshot\ndata: {"devices":[{"name":"客厅灯"}]}\n\n'
        ': heartbeat\n\n'
        'id: 5\nevent: status\ndata: {"state":"upstream_connected"}\n\n'
    ).encode("utf-8")
    chinese_byte = raw.index("客".encode("utf-8")) + 1

    events = []
    events.extend(decoder.feed(raw[:chinese_byte]))
    events.extend(decoder.feed(raw[chinese_byte : chinese_byte + 1]))
    events.extend(decoder.feed(raw[chinese_byte + 1 :]))

    assert [item.event for item in events] == ["snapshot", "status"]
    assert json.loads(events[0].data)["devices"][0]["name"] == "客厅灯"
    assert events[1].id == "5"


def test_device_index_requires_exact_nonempty_ids():
    indexed = devices_by_id(
        {
            "devices": [
                {"id": "living_room_light", "name": "客厅灯"},
                {"id": "", "name": "坏数据"},
                {"name": "无标识"},
                "invalid",
            ]
        }
    )
    assert indexed == {"living_room_light": {"id": "living_room_light", "name": "客厅灯"}}


def test_format_device_state_covers_live_offline_and_no_data_states():
    assert format_device_state(device("客厅灯", "light", {"on": True, "brightness": 50})) == "客厅灯  开 50%"
    assert format_device_state(device("卧室空调", "ac", {"on": True, "mode": "cool", "target_temp": 24})) == "卧室空调  制冷 24°C"
    assert format_device_state(device("智能插座", "switch", {"on": False, "power": 0})) == "智能插座  关"
    assert format_device_state(device("室内温度", "sensor", {"temperature": 26})) == "室内温度  26°C"
    assert format_device_state(device("卧室空调", "ac", {}, online=False)) == "卧室空调  离线"
    assert format_device_state(None, fallback_name="厨房灯") == "厨房灯  暂无数据"
