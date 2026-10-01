"""Home Assistant-backed restricted device queries and confirmed controls."""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

from ha_gateway import GatewayError
from operation_store import Operation, OperationConflict, OperationStore
from visual_state import route_vision_request


DEVICE_IDS = {
    "living_room_light",
    "living_room_ac",
    "bedroom_ac",
    "bedroom_light",
    "kitchen_light",
    "kitchen_ac",
    "desk_plug",
    "indoor_temperature",
}
CONTROL_TOOLS = (
    "query_room_status",
    "query_device_status",
    "set_light",
    "set_ac",
    "set_switch",
    "adjust_ac",
)
SCENE_TOOL = "run_scene"
# A scene step must carry exactly the parameters its device type accepts.
STEP_PARAMETERS = {
    "light": {"on", "brightness"},
    "ac": {"on", "mode", "target_temp"},
    "switch": {"on"},
    "sensor": set(),
}

# What each device type can be asked to do. The agent derives its tool surface
# from this, so adding a device to the catalog makes it controllable without
# touching any code.
CAPABILITIES = {
    "light": ("on", "brightness"),
    "ac": ("on", "mode", "target_temp"),
    "switch": ("on",),
    "sensor": (),
}
# Tool kind -> the device type it may act on, and the reverse for scene steps.
KIND_DEVICE_TYPE = {"set_light": "light", "set_ac": "ac", "set_switch": "switch"}
DEVICE_TYPE_KIND = {device_type: kind for kind, device_type in KIND_DEVICE_TYPE.items()}
# An AC must publish its local comfort policy: the default target, the smallest
# sensible change, and the allowed band. The model is never the source of these
# numbers, so they have to be configuration.
AC_COMFORT_KEYS = ("comfort_temp", "temp_step", "min_temp", "max_temp")
# Spoken mode names, so a phrase never says the raw Home Assistant value.
_MODE_NAMES = {"off": "关闭", "cool": "制冷", "fan_only": "送风"}


def load_catalog(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load the versioned stable-id -> Home Assistant mapping.

    Every record carries its own domain, service names and confirmation
    metadata so no caller has to guess a service name or a numeric scale.
    """
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != DEVICE_IDS:
        raise ValueError("HA entity catalog must contain exactly eight devices")
    required = {
        "entity_id",
        "type",
        "domain",
        "name",
        "room_id",
        "room_name",
        "services",
        "confirmation",
    }
    result: dict[str, dict[str, Any]] = {}
    for device_id, record in value.items():
        if not isinstance(record, dict) or not required <= set(record):
            raise ValueError(f"invalid catalog record: {device_id}")
        if record["type"] not in {"light", "ac", "switch", "sensor"}:
            raise ValueError(f"invalid catalog type: {device_id}")
        if not isinstance(record["services"], dict) or not isinstance(
            record["confirmation"], dict
        ):
            raise ValueError(f"invalid catalog record: {device_id}")
        if record["type"] == "ac":
            missing = [key for key in AC_COMFORT_KEYS if key not in record["confirmation"]]
            if missing:
                # Without a local comfort policy the model would have to invent a
                # target temperature, which the design forbids.
                raise ValueError(
                    f"ac record {device_id} is missing comfort settings: {', '.join(missing)}"
                )
        # The stable id comes from the mapping key, never from Home Assistant,
        # so renaming an entity can never change what the Agent may ask for.
        result[device_id] = {**record, "id": device_id}
    return result


def load_scenes(path: str | Path, catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Load and validate named multi-device scenes.

    A scene is configuration, not code: it may only reference catalogued devices
    and only pass parameters that device type actually accepts, so a typo fails at
    startup rather than at the moment a user says "我出门了".
    """
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise ValueError("scenes must be a non-empty object")

    scenes: dict[str, dict[str, Any]] = {}
    for scene_id, record in value.items():
        if not isinstance(record, dict):
            raise ValueError(f"invalid scene: {scene_id}")
        if not isinstance(record.get("name"), str) or not record["name"].strip():
            raise ValueError(f"scene {scene_id} needs a name")
        steps = record.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"scene {scene_id} needs at least one step")
        aliases = record.get("aliases") or []
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ValueError(f"scene {scene_id} aliases must be strings")

        normalized_steps = []
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError(f"scene {scene_id} has an invalid step")
            device_id = step.get("device_id")
            device = catalog.get(device_id)
            if device is None:
                raise ValueError(f"scene {scene_id} references unknown device {device_id!r}")
            allowed = STEP_PARAMETERS.get(device["type"], set())
            parameters = {key: value for key, value in step.items() if key != "device_id"}
            if not parameters:
                raise ValueError(f"scene {scene_id} step for {device_id} changes nothing")
            extra = set(parameters) - allowed
            if extra:
                raise ValueError(
                    f"scene {scene_id} step for {device_id} has unsupported parameters: "
                    f"{', '.join(sorted(extra))}"
                )
            normalized_steps.append({"device_id": device_id, **parameters})

        scenes[scene_id] = {
            "id": scene_id,
            "name": record["name"].strip(),
            "aliases": [alias.strip() for alias in aliases if alias.strip()],
            "steps": normalized_steps,
        }
    return scenes


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {name}")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"invalid {name}")
    return value


def _ha_number(value: Any, name: str) -> float:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise GatewayError("backend_invalid_response") from None
    try:
        return _number(value, name)
    except ValueError:
        raise GatewayError("backend_invalid_response") from None


def normalize_entity(record: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Convert one HA state response into the stable home-service device view."""
    if not isinstance(raw, dict) or not isinstance(raw.get("attributes", {}), dict):
        raise GatewayError("backend_invalid_response")
    ha_state = raw.get("state")
    if not isinstance(ha_state, str):
        raise GatewayError("backend_invalid_response")
    attributes = raw.get("attributes", {})
    online = ha_state not in {"unavailable", "unknown"}
    type_ = record["type"]

    if type_ == "light":
        brightness = attributes.get("brightness")
        if brightness is not None:
            brightness_value = _ha_number(brightness, "brightness")
            if not 0 <= brightness_value <= 255:
                raise GatewayError("backend_invalid_response")
            brightness = int(round(brightness_value * 100 / 255))
        state = {"on": ha_state == "on", "brightness": brightness}
    elif type_ == "ac":
        target = attributes.get("temperature")
        current = attributes.get("current_temperature")
        state = {
            "on": online and ha_state != "off",
            "mode": ha_state,
            "target_temp": None if target is None else _ha_number(target, "temperature"),
            "current_temp": None if current is None else _ha_number(current, "current_temperature"),
        }
    elif type_ == "switch":
        power = attributes.get("power")
        if power is not None:
            value = _ha_number(power, "power")
            power = int(value) if value.is_integer() else value
        state = {"on": ha_state == "on", "power": power}
    else:
        state = {"temperature": None if not online else _ha_number(ha_state, "temperature")}

    return {
        "id": record["id"],
        "room_id": record["room_id"],
        "room_name": record["room_name"],
        "type": type_,
        "name": record["name"],
        "online": online,
        "state": state,
        "version": raw.get("last_updated"),
    }


def _error(code: str, operation_id: str | None = None, *, status: str = "rejected") -> dict[str, Any]:
    messages = {
        "invalid_request": "这条设备指令无法执行，请确认设备和调整幅度",
        "not_found": "没有找到指定的设备或房间",
        "wrong_type": "选择的设备不支持这项操作",
        "offline": "设备当前离线",
        "backend_unavailable": "暂时无法连接设备，请稍后重试",
        "backend_invalid_response": "暂时无法确认设备当前状态",
        "backend_rejected": "设备没有接受这次操作",
        "submission_unknown": "操作结果暂时无法确认，请查看设备状态",
        "confirmation_timeout": "已尝试操作，但还没有确认设备达到目标状态",
        "operation_id_conflict": "这次请求与先前操作冲突，请重新发起",
    }
    return {
        "ok": False,
        "data": None,
        "error": messages.get(code, "请求失败"),
        "error_code": code,
        "operation_id": operation_id,
        "status": status,
        "phrase": None,
    }


def _confirmed(operation_id: str, entity: dict[str, Any], *, noop: bool) -> dict[str, Any]:
    # Say what the confirmation read actually observed. A generic success
    # message leaves the user unable to tell whether an AC was switched off.
    state = entity["state"]
    name = entity["name"]
    if state.get("on") is False:
        phrase = f"{name}{'已经' if noop else '已'}关闭"
    elif state.get("on") is True:
        phrase = f"{name}{'已经' if noop else '已'}打开"
        if entity["type"] == "ac":
            mode = state.get("mode")
            if mode in _MODE_NAMES and mode != "off":
                phrase += f"，{_MODE_NAMES[mode]}模式"
            target_temp = state.get("target_temp")
            if mode == "cool" and target_temp is not None:
                phrase += f"，设定 {target_temp:g} 度"
        elif entity["type"] == "light" and state.get("brightness") is not None:
            phrase += f"，亮度 {state['brightness']}%"
    else:
        phrase = "设备已经处于目标状态" if noop else "设备状态已更新"
    data = {
        "device": entity["id"],
        "state": entity["state"],
        "version": entity["version"],
        "noop": noop,
    }
    return {
        "ok": True,
        "data": data,
        "error": None,
        "error_code": None,
        "operation_id": operation_id,
        "status": "confirmed",
        "phrase": phrase,
    }


class HAServiceApp:
    def __init__(
        self,
        gateway,
        catalog: dict[str, dict[str, Any]],
        database: str | Path,
        *,
        scenes: dict[str, dict[str, Any]] | None = None,
        visual_state: Any | None = None,
        confirmation_timeout: float = 3.0,
        poll_interval: float = 0.1,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.gateway = gateway
        self.catalog = {}
        for device_id, record in catalog.items():
            copied = dict(record)
            copied["id"] = device_id
            self.catalog[device_id] = copied
        if set(self.catalog) != DEVICE_IDS:
            raise ValueError("HA entity catalog must contain exactly eight devices")
        self.scenes = dict(scenes or {})
        # Camera-owned person state, shared with the memory backend so both
        # publish identical person locations.
        self.visual_state = visual_state
        self.store = OperationStore(database)
        self.confirmation_timeout = float(confirmation_timeout)
        self.poll_interval = float(poll_interval)
        self.clock = clock
        self.sleep = sleep
        # Serialize the whole read -> submit -> confirm cycle per device. Two
        # concurrent writers on one device would otherwise interleave their
        # confirmation reads and could each observe the other's state as their
        # own success. Distinct devices stay fully parallel.
        self._locks_guard = threading.Lock()
        self._device_locks: dict[str, threading.RLock] = {}

    def _device_lock(self, device_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._device_locks.get(device_id)
            if lock is None:
                lock = threading.RLock()
                self._device_locks[device_id] = lock
            return lock

    def handle(self, method: str, path: str, query: dict[str, Any], body: dict[str, Any],
               headers: dict[str, str] | None = None):
        # The camera ingress is resolved before any device route and never reaches
        # the Home Assistant gateway, so an observation cannot actuate a device.
        if self.visual_state is not None and path.startswith("/vision/"):
            vision = route_vision_request(
                self.visual_state,
                method,
                path,
                body,
                (headers or {}).get("X-Vision-Token"),
                now_ms=int(time.time() * 1000),
            )
            if vision is not None:
                return vision

        if method == "GET" and path == "/health":
            return 200, {"ok": True, "backend": "ha", "tools": list(CONTROL_TOOLS)}
        if path == "/persons" and self.visual_state is not None:
            if method == "GET":
                return 200, {"ok": True, "persons": self.visual_state.persons_directory()}
            if method == "POST":
                try:
                    if set(body) != {"display_name"}:
                        raise ValueError("请只填写人物称呼")
                    person = self.visual_state.create_person(body["display_name"])
                except ValueError as exc:
                    return 422, {"ok": False, "message": str(exc)}
                return 201, {"ok": True, "person": person}
        if method == "GET" and path == "/catalog":
            return 200, {"ok": True, "data": {"devices": self.catalog_view(), "scenes": self.scene_view()}}
        if method == "GET" and path == "/tool/device_status":
            return self._query_devices(self._first(query, "device"), self._first(query, "room"))
        if method == "GET" and path == "/tool/room_status":
            return self._query_rooms(self._first(query, "room"))
        if method == "GET" and path == "/tool/person_location":
            return self._query_persons(self._first(query, "person"))
        if method == "POST" and path == "/tool/set_light":
            return self._control("set_light", body)
        if method == "POST" and path == "/tool/set_ac":
            return self._control("set_ac", body)
        if method == "POST" and path == "/tool/set_switch":
            return self._control("set_switch", body)
        if method == "POST" and path == "/tool/run_scene":
            return self._run_scene(body)
        if method == "POST" and path == "/tool/adjust_ac":
            return self._adjust_ac(body)
        return 404, {"ok": False, "error": f"no route for {method} {path}"}

    def scene_view(self) -> list[dict[str, Any]]:
        """Describe the available scenes so a caller can expose them as one tool."""
        return [
            {
                "id": scene["id"],
                "name": scene["name"],
                "aliases": list(scene["aliases"]),
                "devices": [step["device_id"] for step in scene["steps"]],
            }
            for scene in self.scenes.values()
        ]

    def catalog_view(self) -> list[dict[str, Any]]:
        """Describe every device so a caller can build its own tool surface."""
        view = []
        for device_id, record in self.catalog.items():
            capabilities = list(CAPABILITIES.get(record["type"], ()))
            view.append(
                {
                    "id": device_id,
                    "type": record["type"],
                    "name": record["name"],
                    "room_id": record["room_id"],
                    "room_name": record["room_name"],
                    "area_id": record.get("area_id"),
                    "capabilities": capabilities,
                    "controllable": bool(capabilities),
                }
            )
        return view

    @staticmethod
    def _first(query: dict[str, Any], name: str) -> str | None:
        value = query.get(name)
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def _read(self, device_id: str) -> dict[str, Any]:
        record = self.catalog[device_id]
        return normalize_entity(record, self.gateway.read(record["entity_id"]))

    @staticmethod
    def _gateway_status(code: str) -> int:
        return 503 if code in {"backend_unavailable", "backend_invalid_response"} else 400

    def _query_devices(self, device: str | None, room: str | None):
        if device is not None:
            if device not in self.catalog:
                return 400, _error("not_found")
            ids = [device]
        elif room is not None:
            ids = self._room_device_ids(room)
            if ids is None:
                return 400, _error("not_found")
        else:
            ids = list(self.catalog)
        try:
            devices = [self._read(device_id) for device_id in ids]
        except GatewayError as exc:
            return self._gateway_status(exc.code), _error(exc.code)
        return 200, {"ok": True, "data": {"devices": devices}, "error": None, "error_code": None}

    def _room_device_ids(self, room: str) -> list[str] | None:
        ids = [
            device_id for device_id, item in self.catalog.items()
            if room in {item["room_id"], item["room_name"]}
        ]
        return ids or None

    def _query_rooms(self, room: str | None):
        rooms: list[tuple[str, str]] = []
        for item in self.catalog.values():
            pair = (item["room_id"], item["room_name"])
            if pair not in rooms and (room is None or room in pair):
                rooms.append(pair)
        if not rooms:
            return 400, _error("not_found")
        payload = []
        try:
            for room_id, room_name in rooms:
                devices = [
                    self._read(device_id) for device_id, item in self.catalog.items()
                    if item["room_id"] == room_id
                ]
                temperatures = [
                    device["state"].get("temperature") for device in devices
                    if device["type"] == "sensor" and device["online"]
                ]
                payload.append({
                    "id": room_id,
                    "name": room_name,
                    "simulated_temp": temperatures[0] if temperatures else None,
                    "devices": devices,
                })
        except GatewayError as exc:
            return self._gateway_status(exc.code), _error(exc.code)
        return 200, {"ok": True, "data": {"rooms": payload}, "error": None, "error_code": None}

    def _query_persons(self, person: str | None = None):
        """Who the camera currently sees, and where.

        The HA backend keeps no person state of its own, so this reads the shared
        camera-owned store. `location_known` false means "not seen right now",
        never the room the person was in a moment ago: the voice agent speaks
        this answer out loud, so a stale room would be a plain lie.
        """
        if self.visual_state is None:
            return 400, _error("not_found")
        directory = self.visual_state.persons_directory()
        wanted = (person or "").strip()
        if wanted:
            directory = [
                item for item in directory if wanted in {item["id"], item["display_name"]}
            ]
            if not directory:
                return 400, _error("not_found")

        rooms = {item["room_id"]: item["room_name"] for item in self.catalog.values()}
        observed = {
            item["id"]: item for item in self.visual_state.snapshot(int(time.time() * 1000))
        }
        payload = []
        for entry in directory:
            seen = observed.get(entry["id"]) or {}
            room_id = seen.get("room_id")
            payload.append({
                "id": entry["id"],
                "display_name": entry["display_name"],
                "room_id": room_id,
                "room_name": rooms.get(room_id, room_id) if room_id else None,
                "location_known": bool(room_id),
                "location_source": "camera",
                "camera_id": seen.get("camera_id"),
                "track_id": seen.get("track_id"),
                "pose": seen.get("pose") or "unknown",
                "pose_confidence": seen.get("pose_confidence") or 0.0,
                "observed_at_ms": seen.get("observed_at_ms"),
                "x": seen.get("x"),
                "y": seen.get("y"),
                "version": seen.get("version"),
            })
        return 200, {"ok": True, "data": {"persons": payload}, "error": None, "error_code": None}

    def _normalize_request(self, kind: str, body: dict[str, Any]):
        if not isinstance(body, dict):
            raise ValueError("body")
        allowed = {
            "set_light": {"device_id", "operation_id", "on", "brightness"},
            "set_ac": {"device_id", "operation_id", "on", "mode", "target_temp"},
            "set_switch": {"device_id", "operation_id", "on"},
        }[kind]
        if set(body) - allowed:
            raise ValueError("unknown fields")
        operation_id = body.get("operation_id")
        device_id = body.get("device_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id")
        if not isinstance(device_id, str) or not device_id:
            raise ValueError("device_id")

        params: dict[str, Any] = {}
        if "on" in body:
            if not isinstance(body["on"], bool):
                raise ValueError("on")
            params["on"] = body["on"]

        if kind == "set_switch":
            if "on" not in params:
                raise ValueError("switch params")
            target = {"on": params["on"]}
        elif kind == "set_light":
            if "brightness" in body:
                brightness = body["brightness"]
                if isinstance(brightness, bool) or not isinstance(brightness, int) or not 0 <= brightness <= 100:
                    raise ValueError("brightness")
                params["brightness"] = brightness
            if not params or (params.get("on") is False and "brightness" in params):
                raise ValueError("light params")
            target = dict(params)
            if "brightness" in params:
                target["on"] = True
        else:
            if "mode" in body:
                mode = body["mode"]
                if mode not in {"off", "cool", "fan_only"}:
                    raise ValueError("mode")
                params["mode"] = mode
            if "target_temp" in body:
                target_temp = _number(body["target_temp"], "target_temp")
                if not 16 <= target_temp <= 30:
                    raise ValueError("target_temp")
                params["target_temp"] = target_temp
            if not params:
                raise ValueError("ac params")
            if params.get("on") is False and params.get("mode") not in {None, "off"}:
                raise ValueError("conflicting mode")
            if params.get("on") is True and params.get("mode") == "off":
                raise ValueError("conflicting mode")

            # The user expressed an intent to cool without naming a temperature,
            # so the *local* comfort policy supplies it. The model never sees a
            # number it could invent.
            turning_on = params.get("on") is True or params.get("mode") not in (None, "off")
            if "target_temp" not in params and turning_on:
                record = self.catalog.get(device_id)
                if record is not None and record["type"] == "ac":
                    params["target_temp"] = float(record["confirmation"]["comfort_temp"])

            target = dict(params)
            if params.get("on") is False or params.get("mode") == "off":
                target = {key: value for key, value in target.items() if key != "on"}
                target["on"] = False
                target["mode"] = "off"
            elif "mode" in params:
                target["on"] = True
        return operation_id, device_id, params, target

    def _control(self, kind: str, body: dict[str, Any]):
        try:
            operation_id, device_id, params, target = self._normalize_request(kind, body)
        except (ValueError, TypeError):
            candidate = body.get("operation_id") if isinstance(body, dict) else None
            return 400, _error("invalid_request", candidate if isinstance(candidate, str) else None)

        # Holding the device lock across reserve and execution means a retry
        # with the same operation id replays the finished outcome instead of
        # racing the original request.
        with self._device_lock(device_id):
            try:
                reservation = self.store.reserve(operation_id, kind, device_id, params, target)
            except OperationConflict:
                return 409, _error("operation_id_conflict", operation_id)
            if not reservation.created:
                return self._stored_result(reservation.operation)

            expected_type = KIND_DEVICE_TYPE[kind]
            record = self.catalog.get(device_id)
            if record is None:
                return self._reject(reservation.operation, "not_found")
            if record["type"] != expected_type:
                return self._reject(reservation.operation, "wrong_type")
            return self._execute_accepted(reservation.operation)

    # ------------------------------------------------------------------ scenes
    def _run_scene(self, body: dict[str, Any]):
        operation_id = body.get("operation_id") if isinstance(body, dict) else None
        if not isinstance(body, dict) or set(body) - {"scene_id", "operation_id"}:
            return 400, _error("invalid_request", operation_id if isinstance(operation_id, str) else None)
        if not isinstance(operation_id, str) or not operation_id.strip():
            return 400, _error("invalid_request")
        scene_id = body.get("scene_id")
        scene = self.scenes.get(scene_id) if isinstance(scene_id, str) else None
        if scene is None:
            return 400, _error("not_found", operation_id)

        results = []
        for step in scene["steps"]:
            device_id = step["device_id"]
            record = self.catalog[device_id]
            kind = DEVICE_TYPE_KIND[record["type"]]
            # Every device keeps its own operation record, derived from the scene
            # operation. Replaying the scene therefore replays each device's stored
            # outcome instead of commanding the house a second time.
            step_body = {key: value for key, value in step.items() if key != "device_id"}
            step_body["device_id"] = device_id
            step_body["operation_id"] = f"{operation_id}:{device_id}"
            _, payload = self._control(kind, step_body)
            results.append(
                {
                    "device_id": device_id,
                    "device_name": record["name"],
                    "ok": bool(payload.get("ok")),
                    "status": payload.get("status"),
                    "error_code": payload.get("error_code"),
                    "phrase": payload.get("phrase"),
                    "message": payload.get("error"),
                    "state": (payload.get("data") or {}).get("state"),
                }
            )
        return self._scene_result(scene, results, operation_id)

    @staticmethod
    def _describe_step(result: dict[str, Any]) -> str:
        name = result["device_name"]
        if not result["ok"]:
            return f"{name}{result['message'] or '操作失败'}"
        state = result.get("state") or {}
        if state.get("on") is True:
            if "brightness" in state and state["brightness"] is not None:
                return f"{name}已打开，亮度 {state['brightness']}"
            if "target_temp" in state and state["target_temp"] is not None:
                return f"{name}已打开，设定 {state['target_temp']:g} 度"
            return f"{name}已打开"
        if state.get("on") is False:
            return f"{name}已关闭"
        return f"{name}已更新"

    def _scene_result(self, scene: dict[str, Any], results: list[dict[str, Any]], operation_id: str):
        succeeded = [item["device_id"] for item in results if item["ok"]]
        failed = [item["device_id"] for item in results if not item["ok"]]
        data = {
            "scene_id": scene["id"],
            "scene_name": scene["name"],
            "results": results,
            "succeeded": succeeded,
            "failed": failed,
        }
        # Partial success is reported as partial. Claiming the whole scene worked
        # when one device refused would be exactly the lie this service exists to
        # prevent.
        if not failed:
            return 200, {
                "ok": True,
                "data": data,
                "error": None,
                "error_code": None,
                "operation_id": operation_id,
                "status": "confirmed",
                "phrase": f"{scene['name']}已全部完成：" + "、".join(
                    self._describe_step(item) for item in results
                ),
            }

        summary = "；".join(self._describe_step(item) for item in results)
        partial = bool(succeeded)
        return (202 if partial else 400), {
            "ok": False,
            "data": data,
            "error": summary,
            "error_code": "partial_failure" if partial else (results[0]["error_code"] or "request_failed"),
            "operation_id": operation_id,
            "status": "partial_failure" if partial else "failed",
            "phrase": None,
        }

    def _stored_result(self, operation: Operation):
        if isinstance(operation.result, dict):
            return self._replay_status(operation), operation.result
        return 202, {
            "ok": False,
            "data": None,
            "error": "操作仍在处理中",
            "error_code": None,
            "operation_id": operation.id,
            "status": operation.status,
            "phrase": None,
        }

    @staticmethod
    def _replay_status(operation: Operation) -> int:
        """Replay a stored outcome with the same HTTP status as the first reply."""
        if operation.result.get("ok"):
            return 200
        # An unconfirmed outcome is not a client error: the request may still
        # be executing on the device, so it must replay as 202 like the original.
        if operation.status == "unconfirmed":
            return 202
        code = operation.result.get("error_code")
        if code == "operation_id_conflict":
            return 409
        if code in {"backend_unavailable", "backend_invalid_response"}:
            return 503
        return 400

    def _reject(self, operation: Operation, code: str):
        result = _error(code, operation.id)
        updated = self.store.transition(
            operation.id, "rejected", result=result, error_code=code, error=result["error"]
        )
        return self._gateway_status(code), updated.result

    def _finish_unconfirmed(self, operation: Operation, code: str):
        result = _error(code, operation.id, status="unconfirmed")
        updated = self.store.transition(
            operation.id, "unconfirmed", result=result, error_code=code, error=result["error"]
        )
        return 202, updated.result

    def _finish_confirmed(
        self, operation: Operation, entity: dict[str, Any], *, noop: bool, phrase: str | None = None
    ):
        result = _confirmed(operation.id, entity, noop=noop)
        if phrase:
            result["phrase"] = phrase
        updated = self.store.transition(operation.id, "confirmed", result=result)
        return 200, updated.result

    @staticmethod
    def _matches(entity: dict[str, Any], target: dict[str, Any]) -> bool:
        return all(entity["state"].get(key) == value for key, value in target.items())

    def _execute_accepted(self, operation: Operation):
        try:
            entity = self._read(operation.target_id)
        except GatewayError as exc:
            return self._reject(operation, exc.code)
        if not entity["online"]:
            return self._reject(operation, "offline")
        if self._matches(entity, operation.target):
            return self._finish_confirmed(operation, entity, noop=True)

        submitted = self.store.transition(operation.id, "submitted")
        completed_calls = 0
        try:
            for domain, service, data in self._service_calls(submitted):
                self.gateway.call(domain, service, data)
                completed_calls += 1
        except GatewayError as exc:
            if exc.may_have_submitted or completed_calls:
                code = exc.code if exc.may_have_submitted else "submission_unknown"
                return self._finish_unconfirmed(submitted, code)
            return self._reject(submitted, exc.code)
        return self._confirm_until_deadline(submitted)

    def _service_calls(self, operation: Operation):
        """Build service calls from the catalog, never from guessed names."""
        record = self.catalog[operation.target_id]
        entity_id = record["entity_id"]
        domain = record["domain"]
        services = record["services"]
        params = operation.params
        if operation.kind in ("set_light", "set_switch"):
            if params.get("on") is False:
                return [(domain, services["off"], {"entity_id": entity_id})]
            if operation.kind == "set_switch":
                return [(domain, services["on"], {"entity_id": entity_id})]
            data = {"entity_id": entity_id}
            if "brightness" in params:
                data["brightness_pct"] = params["brightness"]
            return [(domain, services["on"], data)]

        calls = []
        default_mode = record["confirmation"].get("default_mode")
        mode = params.get("mode")
        if params.get("on") is False:
            mode = "off"
        elif params.get("on") is True and mode is None:
            mode = default_mode
        if mode is not None:
            calls.append((domain, services["mode"], {"entity_id": entity_id, "hvac_mode": mode}))
        if "target_temp" in params:
            calls.append((domain, services["temperature"], {
                "entity_id": entity_id, "temperature": params["target_temp"]
            }))
        return calls

    def _confirm_until_deadline(
        self,
        operation: Operation,
        desired: dict[str, Any] | None = None,
        phrase: str | None = None,
    ):
        target = operation.target if desired is None else desired
        deadline = self.clock() + self.confirmation_timeout
        while True:
            try:
                entity = self._read(operation.target_id)
            except GatewayError:
                entity = None
            if entity is not None and entity["online"] and self._matches(entity, target):
                return self._finish_confirmed(operation, entity, noop=False, phrase=phrase)
            remaining = deadline - self.clock()
            if remaining <= 0:
                return self._finish_unconfirmed(operation, "confirmation_timeout")
            self.sleep(min(self.poll_interval, remaining))

    # ------------------------------------------------------------- comfort
    def _adjust_ac(self, body: dict[str, Any]):
        """Move the AC by an explicit number of degrees or one configured step.

        The direction comes from the user's words; every number comes from the
        catalog. This is what keeps "有点热" from turning into a temperature the
        model made up.
        """
        operation_id = body.get("operation_id") if isinstance(body, dict) else None
        if not isinstance(body, dict) or set(body) - {"device_id", "direction", "degrees", "operation_id"}:
            return 400, _error("invalid_request", operation_id if isinstance(operation_id, str) else None)
        if not isinstance(operation_id, str) or not operation_id.strip():
            return 400, _error("invalid_request")
        device_id = body.get("device_id")
        direction = body.get("direction")
        if not isinstance(device_id, str) or not device_id:
            return 400, _error("invalid_request", operation_id)
        if direction not in {"cooler", "warmer"}:
            return 400, _error("invalid_request", operation_id)
        degrees = body.get("degrees")
        if degrees is not None:
            if (isinstance(degrees, bool) or not isinstance(degrees, (int, float))
                    or not math.isfinite(degrees) or not 0 < degrees <= 14):
                return 400, _error("invalid_request", operation_id)
            degrees = float(degrees)

        with self._device_lock(device_id):
            try:
                reservation = self.store.reserve(
                    operation_id, "adjust_ac", device_id,
                    {"direction": direction, "degrees": degrees},
                    {"direction": direction, "degrees": degrees},
                )
            except OperationConflict:
                return 409, _error("operation_id_conflict", operation_id)
            if not reservation.created:
                return self._stored_result(reservation.operation)

            operation = reservation.operation
            record = self.catalog.get(device_id)
            if record is None:
                return self._reject(operation, "not_found")
            if record["type"] != "ac":
                return self._reject(operation, "wrong_type")
            return self._execute_adjust(operation, record, direction, degrees)

    def _plan_adjust(self, record: dict[str, Any], entity: dict[str, Any], direction: str,
                     degrees: float | None = None):
        """Return ``(desired_state, service_calls, phrase)`` from local config."""
        confirmation = record["confirmation"]
        comfort = float(confirmation["comfort_temp"])
        step = float(confirmation["temp_step"])
        minimum = float(confirmation["min_temp"])
        maximum = float(confirmation["max_temp"])
        services = record["services"]
        domain = record["domain"]
        entity_id = record["entity_id"]
        state = entity["state"]

        if degrees is not None and abs(degrees / step - round(degrees / step)) > 1e-8:
            return None, None, "温度变化量不符合空调的调节精度"

        if not state.get("on"):
            if degrees is not None:
                return None, None, "空调当前关闭，请先打开再调整温度"
            if direction == "warmer":
                # Raising the target of a unit that is off would not warm anything.
                return None, None, "空调现在是关着的，要打开吗"
            mode = confirmation.get("default_mode") or "cool"
            desired = {"on": True, "mode": mode, "target_temp": comfort}
            calls = [
                (domain, services["mode"], {"entity_id": entity_id, "hvac_mode": mode}),
                (domain, services["temperature"], {"entity_id": entity_id, "temperature": comfort}),
            ]
            return (
                desired,
                calls,
                f"{record['name']}已打开，{_MODE_NAMES.get(mode, mode)}模式，设定 {comfort:g} 度",
            )

        current = state.get("target_temp")
        base = float(current) if isinstance(current, (int, float)) else comfort
        amount = step if degrees is None else degrees
        target = base - amount if direction == "cooler" else base + amount
        if degrees is not None and not minimum <= target <= maximum:
            return None, None, "目标温度超出空调允许范围"
        target = max(minimum, min(maximum, target))
        desired = {"on": True, "target_temp": target}
        if target == base:
            limit = "最低" if direction == "cooler" else "最高"
            return desired, [], f"{record['name']}已经是{limit}温度 {base:g} 度了"
        return desired, [
            (domain, services["temperature"], {"entity_id": entity_id, "temperature": target})
        ], f"已把{record['name']}调到 {target:g} 度"

    def _execute_adjust(self, operation: Operation, record: dict[str, Any], direction: str,
                        degrees: float | None = None):
        try:
            entity = self._read(operation.target_id)
        except GatewayError as exc:
            return self._reject(operation, exc.code)
        if not entity["online"]:
            return self._reject(operation, "offline")

        desired, calls, phrase = self._plan_adjust(record, entity, direction, degrees)
        if desired is None:
            return self._reject(operation, "not_applicable")
        if not calls or self._matches(entity, desired):
            return self._finish_confirmed(operation, entity, noop=True, phrase=phrase)

        submitted = self.store.transition(operation.id, "submitted")
        completed_calls = 0
        try:
            for domain, service, data in calls:
                self.gateway.call(domain, service, data)
                completed_calls += 1
        except GatewayError as exc:
            if exc.may_have_submitted or completed_calls:
                code = exc.code if exc.may_have_submitted else "submission_unknown"
                return self._finish_unconfirmed(submitted, code)
            return self._reject(submitted, exc.code)
        return self._confirm_until_deadline(submitted, desired, phrase)

    def reconcile_unfinished(self):
        """Resolve operations left behind by a crash, per the approved semantics.

        ``accepted`` may still be safely submitted once because it proves nothing
        was sent. ``submitted`` and ``unconfirmed`` may only be reconciled
        against observed state: a blind resend could actuate a device twice.
        """
        for operation in self.store.unfinished():
            with self._device_lock(operation.target_id):
                if operation.status == "accepted":
                    self._execute_accepted(operation)
                    continue
                try:
                    entity = self._read(operation.target_id)
                except GatewayError:
                    entity = None
                if entity is not None and entity["online"] and self._matches(entity, operation.target):
                    self._finish_confirmed(operation, entity, noop=False)
                elif operation.status == "submitted":
                    self._finish_unconfirmed(operation, "confirmation_timeout")
