import sys
import time
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT))

import smart_home_live  # noqa: E402


if not hasattr(bpy.types.WindowManager, "smart_home_live"):
    smart_home_live.register()

assert smart_home_live._marked_scene(), "the digital twin scene is not marked for read-only live state"
assert smart_home_live._start_worker(force=True), "the read-only SSE worker did not start"

deadline = time.monotonic() + 8.0
while bpy.context.scene.get("smart_home_stream_version", 0) < 1 and time.monotonic() < deadline:
    smart_home_live._drain_messages()
    time.sleep(0.05)

smart_home_live._drain_messages()
smart_home_live._stop_worker()

assert bpy.context.scene.get("smart_home_stream_backend") == "ha"
assert bpy.context.scene.get("smart_home_stream_version", 0) >= 1
assert bpy.data.objects["Label_living_room_light"].data.body == "客厅灯  开 50%"
assert bpy.data.objects["Label_desk_plug"].data.body == "智能插座  关"
assert bpy.data.objects["Label_indoor_temperature"].data.body == "室内温度  26°C"
assert bpy.data.objects["Label_bedroom_ac"].data.body == "卧室空调  制冷 24°C"
assert bpy.data.objects["living_room_light_Glow"].data.energy == 900.0
print("SMART_HOME_BLENDER_LIVE_READONLY_OK")
