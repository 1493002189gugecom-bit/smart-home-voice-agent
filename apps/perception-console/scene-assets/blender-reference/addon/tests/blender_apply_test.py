import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT))

import smart_home_live  # noqa: E402


snapshot = {
    "version": 42,
    "backend": "ha",
    "generated_at_ms": 1,
    "rooms": [],
    "persons": [],
    "broadcasts": [],
    "broadcast_queue": [],
    "devices": [
        {
            "id": "living_room_light",
            "name": "客厅灯",
            "type": "light",
            "online": True,
            "state": {"on": True, "brightness": 50},
        },
        {
            "id": "bedroom_ac",
            "name": "卧室空调",
            "type": "ac",
            "online": True,
            "state": {"on": True, "mode": "cool", "target_temp": 24},
        },
        {
            "id": "desk_plug",
            "name": "智能插座",
            "type": "switch",
            "online": True,
            "state": {"on": False, "power": 0},
        },
        {
            "id": "indoor_temperature",
            "name": "室内温度",
            "type": "sensor",
            "online": True,
            "state": {"temperature": 26},
        },
    ],
}


count = smart_home_live.apply_snapshot(snapshot)
assert count == 8, f"expected eight exact device roots, got {count}"
assert bpy.data.objects["Label_living_room_light"].data.body == "客厅灯  开 50%"
assert bpy.data.objects["Label_bedroom_ac"].data.body == "卧室空调  制冷 24°C"
assert bpy.data.objects["Label_desk_plug"].data.body == "智能插座  关"
assert bpy.data.objects["Label_indoor_temperature"].data.body == "室内温度  26°C"
assert bpy.data.objects["Label_kitchen_light"].data.body == "厨房灯  暂无数据"
assert bpy.data.objects["Label_living_room_ac"].data.body == "客厅空调  暂无数据"
assert bpy.data.objects["living_room_light_Glow"].data.energy == 900.0
assert bpy.data.objects["living_room_light_Bulb"].data.materials[0].name == "M_StatusOnAmber"
assert bpy.data.objects["bedroom_ac_LED"].data.materials[0].name == "M_StatusOnTeal"
assert bpy.data.objects["kitchen_ac_LED"].data.materials[0].name == "M_StatusOffline"
assert bpy.context.scene["smart_home_stream_backend"] == "ha"
assert bpy.context.scene["smart_home_stream_version"] == 42
print("SMART_HOME_BLENDER_APPLY_TEST_OK")
