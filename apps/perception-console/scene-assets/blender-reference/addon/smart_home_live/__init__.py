from __future__ import annotations

bl_info = {
    "name": "Smart Home Live (Read Only)",
    "author": "Smart Home Project",
    "version": (1, 0, 0),
    "blender": (4, 3, 0),
    "location": "3D View > Sidebar > Smart Home",
    "description": "Read-only live device state from local home-service SSE",
    "category": "3D View",
}

import json
import queue
import threading
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import bpy
from bpy.app.handlers import persistent

from .protocol import SSEDecoder, devices_by_id, format_device_state


_messages: queue.Queue = queue.Queue(maxsize=32)
_worker = None


def _offer(message):
    try:
        _messages.put_nowait(message)
    except queue.Full:
        try:
            _messages.get_nowait()
        except queue.Empty:
            pass
        try:
            _messages.put_nowait(message)
        except queue.Full:
            pass


class _SSEWorker:
    def __init__(self, base_url):
        self.url = base_url.rstrip("/") + "/events"
        self.stop_event = threading.Event()
        self.response = None
        self.thread = threading.Thread(target=self._run, name="smart-home-live", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        delay = 1
        while not self.stop_event.is_set():
            decoder = SSEDecoder()
            try:
                request = Request(self.url, method="GET", headers={"Accept": "text/event-stream"})
                self.response = urlopen(request, timeout=30)
                content_type = self.response.headers.get_content_type()
                if content_type != "text/event-stream":
                    raise RuntimeError("unexpected_content_type")
                _offer(("connection", "已连接（只读 SSE）"))
                delay = 1
                while not self.stop_event.is_set():
                    read_available = getattr(self.response, "read1", self.response.read)
                    chunk = read_available(4096)
                    if not chunk:
                        raise RuntimeError("stream_closed")
                    for event in decoder.feed(chunk):
                        if event.event not in {"snapshot", "status"}:
                            continue
                        try:
                            payload = json.loads(event.data)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        _offer((event.event, payload))
            except HTTPError as exc:
                _offer(("connection", f"连接失败 HTTP {exc.code}"))
            except (URLError, TimeoutError, OSError, RuntimeError):
                _offer(("connection", "连接中断，正在重试"))
            finally:
                response = self.response
                self.response = None
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
            if self.stop_event.wait(delay):
                break
            delay = min(delay * 2, 30)
        _offer(("connection", "已暂停"))


def _settings():
    manager = getattr(bpy.context, "window_manager", None)
    return getattr(manager, "smart_home_live", None) if manager else None


def _marked_scene(scene=None):
    scene = scene or getattr(bpy.context, "scene", None)
    return bool(scene and scene.get("read_only_visualization") and bpy.data.collections.get("SmartHomeDigitalTwin"))


def _material(name):
    return bpy.data.materials.get(name)


def _assign_material(obj, material_name):
    material = _material(material_name)
    if material is None or not hasattr(obj.data, "materials"):
        return
    if len(obj.data.materials):
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)


def _device_visual(device):
    if device is None:
        return "M_StatusOffline", "M_LabelWhite", False, 0.0, "no_data"
    if device.get("online") is False:
        return "M_StatusOffline", "M_LabelRed", False, 0.0, "offline"
    state = device.get("state") if isinstance(device.get("state"), dict) else {}
    kind = device.get("type")
    if kind == "sensor":
        return "M_StatusOnTeal", "M_LabelTeal", False, 0.0, "online"
    if state.get("on") is True:
        brightness = state.get("brightness")
        level = float(brightness) if isinstance(brightness, (int, float)) else 100.0
        status_material = "M_StatusOnAmber" if kind == "light" else "M_StatusOnTeal"
        label_material = "M_LabelAmber" if kind == "light" else "M_LabelTeal"
        return status_material, label_material, kind == "light", level, "on"
    if state.get("on") is False:
        return "M_StatusOff", "M_LabelWhite", False, 0.0, "off"
    return "M_StatusOffline", "M_LabelWhite", False, 0.0, "no_data"


def apply_snapshot(snapshot, scene=None):
    """Apply one canonical snapshot using exact smart_home_id matches only."""
    scene = scene or bpy.context.scene
    indexed = devices_by_id(snapshot)
    roots = [
        obj for obj in scene.objects
        if isinstance(obj.get("smart_home_id"), str)
        and obj.get("read_only") is True
        and obj.get("device_type")
    ]
    for root in roots:
        device_id = root.get("smart_home_id")
        device = indexed.get(device_id)
        fallback = root.get("display_name") or device_id
        text = format_device_state(device, fallback_name=fallback)
        status_material, label_material, light_on, level, state_name = _device_visual(device)

        root["state"] = state_name
        root["online"] = bool(device is not None and device.get("online") is not False)
        root["stream_version"] = int(snapshot.get("version", 0) or 0)
        state = device.get("state") if isinstance(device, dict) and isinstance(device.get("state"), dict) else {}
        if "brightness" in state:
            root["brightness"] = state["brightness"]
        if "temperature" in state:
            root["temperature"] = state["temperature"]
        if "target_temp" in state:
            root["temperature"] = state["target_temp"]

        label = bpy.data.objects.get("Label_" + device_id)
        if label is not None and label.type == "FONT":
            label.data.body = text
            label["label_text"] = text
            _assign_material(label, label_material)
        board = bpy.data.objects.get("Label_" + device_id + "_Board")
        if board is not None:
            board["label_text"] = text

        for suffix in ("_StatusRing", "_Bulb", "_LED", "_Display"):
            part = bpy.data.objects.get(device_id + suffix)
            if part is not None:
                _assign_material(part, status_material)

        glow = bpy.data.objects.get(device_id + "_Glow")
        if glow is not None and glow.type == "LIGHT":
            glow.data.energy = max(0.0, min(100.0, level)) * 18.0 if light_on else 0.0
            glow.hide_render = not light_on

    scene["smart_home_stream_backend"] = str(snapshot.get("backend", "unknown"))
    scene["smart_home_stream_version"] = int(snapshot.get("version", 0) or 0)
    scene["smart_home_last_update"] = datetime.now().strftime("%H:%M:%S")
    return len(roots)


def _start_worker(force=False):
    global _worker
    settings = _settings()
    if settings is None or not _marked_scene():
        return False
    if _worker is not None and _worker.thread.is_alive():
        if not force:
            return True
        _worker.stop()
    _worker = _SSEWorker(settings.base_url)
    settings.connected = False
    settings.status = "正在连接"
    _worker.start()
    return True


def _stop_worker():
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
    settings = _settings()
    if settings is not None:
        settings.connected = False
        settings.status = "已暂停"


def _drain_messages():
    settings = _settings()
    for _index in range(32):
        try:
            kind, payload = _messages.get_nowait()
        except queue.Empty:
            break
        if kind == "snapshot" and isinstance(payload, dict) and _marked_scene():
            apply_snapshot(payload)
            if settings is not None:
                settings.connected = True
                settings.status = "实时状态已更新"
                settings.last_update = bpy.context.scene.get("smart_home_last_update", "")
        elif kind == "status" and settings is not None:
            state = payload.get("state") if isinstance(payload, dict) else ""
            names = {
                "upstream_connected": "Home Assistant 已连接",
                "upstream_disconnected": "Home Assistant 已断开",
                "upstream_error": "Home Assistant 重连中",
                "snapshot_unavailable": "状态暂不可用",
            }
            settings.status = names.get(state, str(state))
        elif kind == "connection" and settings is not None:
            settings.status = str(payload)
            settings.connected = str(payload).startswith("已连接")
    return 0.2


@persistent
def _on_load(_unused):
    settings = _settings()
    if settings is not None and settings.auto_connect and _marked_scene():
        _start_worker(force=True)


class SmartHomeLiveSettings(bpy.types.PropertyGroup):
    base_url: bpy.props.StringProperty(name="服务地址", default="http://127.0.0.1:8765")
    auto_connect: bpy.props.BoolProperty(name="打开场景时自动连接", default=True)
    connected: bpy.props.BoolProperty(name="已连接", default=False, options={"SKIP_SAVE"})
    status: bpy.props.StringProperty(name="状态", default="未连接", options={"SKIP_SAVE"})
    last_update: bpy.props.StringProperty(name="最近更新", default="—", options={"SKIP_SAVE"})


class SMARTHOME_OT_connect(bpy.types.Operator):
    bl_idname = "smart_home.connect_live"
    bl_label = "连接"

    def execute(self, _context):
        return {"FINISHED"} if _start_worker(force=True) else {"CANCELLED"}


class SMARTHOME_OT_pause(bpy.types.Operator):
    bl_idname = "smart_home.pause_live"
    bl_label = "暂停"

    def execute(self, _context):
        _stop_worker()
        return {"FINISHED"}


class SMARTHOME_PT_live_panel(bpy.types.Panel):
    bl_label = "智能家居实时状态"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Smart Home"

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.smart_home_live
        layout.label(text=settings.status, icon="LINKED" if settings.connected else "UNLINKED")
        layout.label(text="最近更新：" + settings.last_update)
        layout.prop(settings, "base_url")
        layout.prop(settings, "auto_connect")
        row = layout.row(align=True)
        row.operator("smart_home.connect_live", text="连接 / 重连", icon="FILE_REFRESH")
        row.operator("smart_home.pause_live", text="暂停", icon="PAUSE")
        layout.separator()
        layout.label(text="只读：仅 GET /events", icon="LOCKED")


_CLASSES = (
    SmartHomeLiveSettings,
    SMARTHOME_OT_connect,
    SMARTHOME_OT_pause,
    SMARTHOME_PT_live_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.smart_home_live = bpy.props.PointerProperty(type=SmartHomeLiveSettings)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if not bpy.app.timers.is_registered(_drain_messages):
        bpy.app.timers.register(_drain_messages, first_interval=0.2, persistent=True)
    if _marked_scene():
        bpy.app.timers.register(lambda: (_start_worker() and None) or None, first_interval=0.5)


def unregister():
    _stop_worker()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_drain_messages):
        bpy.app.timers.unregister(_drain_messages)
    if hasattr(bpy.types.WindowManager, "smart_home_live"):
        del bpy.types.WindowManager.smart_home_live
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
