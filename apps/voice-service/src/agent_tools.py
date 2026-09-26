"""The voice agent's tool surface, derived from the live device catalog.

Nothing here hardcodes a device list: the executor asks home-service for its
catalog and builds one tool per controllable device type. Adding a device to
`ha_entities.json` therefore makes it voice-controllable with no code change —
which is the whole point of a whole-home voice interface.

The model still never sees a path, a URL, or a free-form device name: every
device is an enum member of a closed set taken from that catalog.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

# Device type -> the tool that acts on it.
TOOL_KIND_BY_TYPE = {"light": "set_light", "ac": "set_ac", "switch": "set_switch"}
CONTROL_KINDS = ("set_light", "set_ac", "set_switch")
READ_TOOLS = ("query_room_status", "query_device_status", "query_person_location")
SCENE_TOOL = "run_scene"
ADJUST_TOOL = "adjust_ac"
END_TOOL = "end_conversation"
ADJUST_DIRECTIONS = ("cooler", "warmer")
# Tools that change the house. Their results, not the model's prose, decide what
# the user is told.
WRITE_TOOLS = CONTROL_KINDS + (SCENE_TOOL, ADJUST_TOOL)

# ---------------------------------------------------------- target authority
# How a write may be *targeted* once Phase B resolves `target: "self"`.
#
# A tier describes the action, never the user's right to perform it: a `forbidden`
# tool stays fully available whenever the user names its target. The tier answers
# exactly one question — may a voiceprint-derived default choose this target?
#
# Why this is a table and not a model: the question "is this action safe to
# auto-target?" is a static property of the action, so answering it with an LLM
# adds a network hop, a privacy leak and an argument surface while removing the
# audit trail. A model may classify intent and report a confidence; it must never
# decide authority, because it can be argued with and its failures are silent.
TIER_REVERSIBLE_SELF = "reversible_self"
TIER_EXPLICIT_ONLY = "explicit_only"
TIER_FORBIDDEN = "forbidden"
TIER_NOT_A_HOUSE_ACTION = "not_a_house_action"

TARGET_TIERS = {
    # Reversible, local, and felt only by the speaker: the worst case is the wrong
    # room, which one spoken sentence takes back.
    "set_light": TIER_REVERSIBLE_SELF,
    "set_ac": TIER_REVERSIBLE_SELF,
    # A generic outlet may power a device whose interruption is consequential.
    # Its load is not described by the catalog, so require an explicit device.
    "set_switch": TIER_EXPLICIT_ONLY,
    ADJUST_TOOL: TIER_REVERSIBLE_SELF,
    # A scene rewrites many rooms at once, so a wrong target is no longer confined
    # to the person who spoke.
    SCENE_TOOL: TIER_FORBIDDEN,
    # Not a house action at all; it only closes the session.
    END_TOOL: TIER_NOT_A_HOUSE_ACTION,
}

# Why each forbidden action may never take a voiceprint-derived target. Kept as
# data rather than a comment so a test can require the explanation to exist.
FORBIDDEN_TARGET_REASONS = {
    SCENE_TOOL: "场景一次改动多个房间，误判的代价不局限于说话人自己",
}


def target_tier(name: str) -> str:
    """Return the declared tier, failing loudly for a tool nobody classified."""

    try:
        return TARGET_TIERS[name]
    except KeyError:
        raise ToolArgumentError("unknown_tool", f"未分级工具：{name}") from None


def may_use_self_target(name: str) -> bool:
    """True only where a voiceprint-derived default is allowed to pick the target."""

    return target_tier(name) == TIER_REVERSIBLE_SELF
# Ends the listening session. Intent belongs to the model, not to a keyword list
# in the loop, because no list survives "不聊了" or "我先去忙了".
SESSION_TOOLS = (END_TOOL,)
# Every tool this agent may ever expose. The concrete surface is a subset chosen
# by the catalog, never a different set of names.
HOME_TOOL_NAMES = READ_TOOLS + WRITE_TOOLS + SESSION_TOOLS

# Value domains, which are not device-specific.
AC_MODES = ("off", "cool", "fan_only")
BRIGHTNESS_MIN, BRIGHTNESS_MAX = 0, 100
TEMP_MIN, TEMP_MAX = 16.0, 30.0
SELF_LOCATION_MAX_AGE_MS = 3500


class ToolArgumentError(Exception):
    """The model produced arguments this contract does not accept."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class ToolResult:
    name: str
    ok: bool
    error_code: str | None = None
    message: str | None = None
    phrase: str | None = None
    data: Any = None
    operation_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    resolved_room_name: str | None = None

    def payload(self) -> dict[str, Any]:
        """What is handed back to the model. Never contains credentials."""
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "message": self.message,
            "phrase": self.phrase,
            "data": self.data,
        }


def new_operation_id() -> str:
    return "voice-" + uuid.uuid4().hex[:12]


# --------------------------------------------------------------------- schemas
# Spoken form of the vision service's pose states. The deterministic summary can
# be read aloud verbatim, so an English enum must never reach the speaker.
_POSE_TEXT = {
    "standing": "站着",
    "sitting": "坐着",
    "lying": "躺着",
    "suspected_fall": "疑似摔倒",
    "hand_raised": "在举手",
}


def pose_text(pose: Any) -> str | None:
    return _POSE_TEXT.get(str(pose or "").strip().lower())


def _device_enum(ids: list[str], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(ids), "description": description}


def _self_target_property() -> dict[str, Any]:
    return {"type": "string", "enum": ["self"],
            "description": "用户没有指定房间且想控制自己所在房间时使用；与 device_id 二选一"}


def build_tool_schemas(catalog: list[dict[str, Any]], scenes: list[dict[str, Any]] | None = None):
    """Return ``(schemas, devices_by_kind)`` for one catalog snapshot.

    Scenes come from the same source, so a scene defined in the service is
    immediately speakable and no scene name is ever guessed by the model.
    """
    devices_by_kind: dict[str, list[str]] = {}
    for device in catalog:
        kind = TOOL_KIND_BY_TYPE.get(device.get("type", ""))
        if kind and device.get("controllable"):
            devices_by_kind.setdefault(kind, []).append(device["id"])

    known_ids = [device["id"] for device in catalog]
    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "query_room_status",
                "description": "查询房间内的设备与室温。房间可用中文名（客厅、卧室）或稳定 id，省略则返回全部房间。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "room": {"type": "string", "description": "房间名或稳定 id"}
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_device_status",
                "description": "查询某个设备或某个房间的当前状态。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device_id": _device_enum(known_ids, "稳定设备 id"),
                        "room": {"type": "string", "description": "改用房间过滤时使用"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_person_location",
                "description": (
                    "查询家人当前在哪、在做什么（摄像头实时观察的结果）。"
                    "省略 person 则返回全部已注册家人。"
                    "注意：数据来自摄像头，只有在摄像头当前确认看到该人时才有房间；"
                    "location_known 为 false 表示只是没看到，不等于人在别处或不在家。"
                    "该工具无法判断正在说话的是谁。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "person": {"type": "string", "description": "家人称呼，如 爸爸、妈妈；省略则查全部"}
                    },
                    "additionalProperties": False,
                },
            },
        },
    ]

    for kind in CONTROL_KINDS:
        ids = devices_by_kind.get(kind)
        if not ids:
            continue
        if kind == "set_light":
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": "set_light",
                        "description": "开关灯或设置亮度。亮度 0-100。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "device_id": _device_enum(ids, "要控制的灯"),
                                "target": _self_target_property(),
                                "on": {"type": "boolean"},
                                "brightness": {
                                    "type": "integer",
                                    "minimum": BRIGHTNESS_MIN,
                                    "maximum": BRIGHTNESS_MAX,
                                },
                            },
                            "required": [],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        elif kind == "set_ac":
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": "set_ac",
                        "description": (
                            "开关空调或设置模式。只有在用户明确说出温度时才传 target_temp；"
                            "用户只说“热/冷”而没说温度时不要传，服务端会套用本地舒适温度。"
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "device_id": _device_enum(ids, "要控制的空调"),
                                "target": _self_target_property(),
                                "on": {"type": "boolean"},
                                "mode": {"type": "string", "enum": list(AC_MODES)},
                                "target_temp": {
                                    "type": "number",
                                    "minimum": TEMP_MIN,
                                    "maximum": TEMP_MAX,
                                },
                            },
                            "required": [],
                            "additionalProperties": False,
                        },
                    },
                }
            )
            # Relative requests use the current device target as their base.
            # An omitted amount means one locally configured step.
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": ADJUST_TOOL,
                        "description": (
                            "相对调温使用：有点热、再低一点，或明确说再调低两度。"
                            "说出变化量时把度数放入 degrees；没说变化量时按本地设定调整一档。"
                            "set_ac 仅用于用户说出绝对目标温度，例如调到 24 度。"
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "device_id": _device_enum(ids, "要调整的空调"),
                                "target": _self_target_property(),
                                "direction": {
                                    "type": "string",
                                    "enum": list(ADJUST_DIRECTIONS),
                                    "description": "cooler 表示想更凉，warmer 表示想更暖",
                                },
                                "degrees": {
                                    "type": "number", "exclusiveMinimum": 0, "maximum": 14,
                                    "description": "用户明确说出的相对变化度数，例如再调低两度填 2；没说则省略",
                                },
                            },
                            "required": ["direction"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        else:
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": "set_switch",
                        "description": "开关插座等通断类设备。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "device_id": _device_enum(ids, "要控制的插座"),
                                "on": {"type": "boolean"},
                            },
                            "required": ["device_id", "on"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
    if scenes:
        described = "；".join(
            f"{scene['name']}（{scene['id']}"
            + (f"，也可说：{'、'.join(scene['aliases'])}" if scene.get("aliases") else "")
            + "）"
            for scene in scenes
        )
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": SCENE_TOOL,
                    "description": (
                        "一次执行一组设备动作的场景，适合“我出门了”“我要睡觉了”这类整体指令。"
                        f"可用场景：{described}。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "scene_id": {
                                "type": "string",
                                "enum": sorted(scene["id"] for scene in scenes),
                            }
                        },
                        "required": ["scene_id"],
                        "additionalProperties": False,
                    },
                },
            }
        )

    schemas.append(
        {
            "type": "function",
            "function": {
                "name": END_TOOL,
                "description": (
                    "用户表示要结束这次对话时调用：说再见、拜拜、晚安、不聊了、"
                    "我先去忙、回头再说等任何告别或结束意图。调用后助手会停止聆听。"
                    "只在与用户的对话确实要结束时调用，不要因为一句普通的结束语就调用。"
                ),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    )
    return schemas, devices_by_kind


# ------------------------------------------------------------------ validation
_ALLOWED_ARGUMENTS = {
    "query_room_status": {"room"},
    "query_device_status": {"device_id", "room"},
    "query_person_location": {"person"},
    "set_light": {"device_id", "on", "brightness"},
    "set_ac": {"device_id", "on", "mode", "target_temp"},
    "set_switch": {"device_id", "on"},
    SCENE_TOOL: {"scene_id"},
    ADJUST_TOOL: {"device_id", "direction", "degrees"},
    END_TOOL: set(),
}


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ToolArgumentError("invalid_arguments", f"{name} 必须是布尔值")
    return value


def _require_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError("invalid_arguments", f"{name} 必须是整数")
    if not minimum <= value <= maximum:
        raise ToolArgumentError("invalid_arguments", f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _require_temp(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolArgumentError("invalid_arguments", "target_temp 必须是数字")
    number = float(value)
    if not TEMP_MIN <= number <= TEMP_MAX:
        raise ToolArgumentError(
            "invalid_arguments", f"target_temp 必须在 {TEMP_MIN:g} 到 {TEMP_MAX:g} 度之间"
        )
    return number


def _require_choice(name: str, value: Any, choices) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ToolArgumentError("invalid_arguments", f"{name} 的取值不合法")
    return value


def _optional_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError("invalid_arguments", f"{name} 必须是非空字符串")
    return value.strip()


def normalize_arguments(
    name: str,
    arguments: dict[str, Any],
    devices_by_kind: dict[str, list[str]] | None = None,
    known_ids: list[str] | None = None,
    scene_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Validate against the catalog so no code lists devices by hand."""
    if name not in _ALLOWED_ARGUMENTS:
        raise ToolArgumentError("unknown_tool", f"未知工具：{name}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolArgumentError("invalid_arguments", "参数必须是对象")
    unknown = set(arguments) - _ALLOWED_ARGUMENTS[name]
    if unknown:
        raise ToolArgumentError("invalid_arguments", f"不支持的参数：{', '.join(sorted(unknown))}")

    allowed_devices = list((devices_by_kind or {}).get(name) or [])
    known = list(known_ids or [])

    normalized: dict[str, Any] = {}
    if name == END_TOOL:
        return {}
    if name == ADJUST_TOOL:
        device_id = _optional_text("device_id", arguments.get("device_id"))
        allowed = list((devices_by_kind or {}).get("set_ac") or [])
        if not allowed:
            raise ToolArgumentError("invalid_arguments", "当前没有可调整的空调")
        if device_id not in allowed:
            raise ToolArgumentError("invalid_arguments", f"不能调整该设备：{device_id}")
        normalized = {
            "device_id": device_id,
            "direction": _require_choice(
                "direction", arguments.get("direction"), ADJUST_DIRECTIONS
            ),
        }
        if "degrees" in arguments:
            degrees = arguments["degrees"]
            if isinstance(degrees, bool) or not isinstance(degrees, (int, float)):
                raise ToolArgumentError("invalid_arguments", "degrees 必须是正数")
            degrees = float(degrees)
            if not 0 < degrees <= 14:
                raise ToolArgumentError("invalid_arguments", "degrees 必须在 0 到 14 度之间")
            normalized["degrees"] = degrees
        return normalized
    if name == SCENE_TOOL:
        scene_id = _optional_text("scene_id", arguments.get("scene_id"))
        scenes = list(scene_ids or [])
        if scenes and scene_id not in scenes:
            raise ToolArgumentError("invalid_arguments", f"没有这个场景：{scene_id}")
        if not scenes:
            raise ToolArgumentError("invalid_arguments", "当前没有可用场景")
        return {"scene_id": scene_id}

    if name == "query_room_status":
        if "room" in arguments:
            normalized["room"] = _optional_text("room", arguments["room"])
        return normalized

    if name == "query_device_status":
        if "device_id" in arguments:
            device_id = _optional_text("device_id", arguments["device_id"])
            if known and device_id not in known:
                raise ToolArgumentError("invalid_arguments", f"未知设备：{device_id}")
            normalized["device_id"] = device_id
        if "room" in arguments:
            normalized["room"] = _optional_text("room", arguments["room"])
        if not normalized:
            raise ToolArgumentError("invalid_arguments", "必须指定 device_id 或 room")
        return normalized

    if name == "query_person_location":
        # Asking about "anyone" is legal: the tool then returns every known
        # person, which is exactly what "家里现在有谁" needs.
        if "person" in arguments:
            person = _optional_text("person", arguments["person"])
            if person:
                normalized["person"] = person
        return normalized

    device_id = _optional_text("device_id", arguments.get("device_id"))
    # An empty allow-list means the catalog has no such device at all, which is
    # a contract error rather than something to forward to the device.
    if allowed_devices and device_id not in allowed_devices:
        raise ToolArgumentError("invalid_arguments", f"不能控制该设备：{device_id}")
    if not allowed_devices:
        raise ToolArgumentError("invalid_arguments", "当前没有可控制的该类设备")
    normalized["device_id"] = device_id

    if name == "set_switch":
        if "on" not in arguments:
            raise ToolArgumentError("invalid_arguments", "必须指定 on")
        normalized["on"] = _require_bool("on", arguments["on"])
        return normalized

    if "on" in arguments:
        normalized["on"] = _require_bool("on", arguments["on"])

    if name == "set_light":
        if "brightness" in arguments:
            normalized["brightness"] = _require_int(
                "brightness", arguments["brightness"], BRIGHTNESS_MIN, BRIGHTNESS_MAX
            )
        if "on" not in normalized and "brightness" not in normalized:
            raise ToolArgumentError("invalid_arguments", "必须指定 on 或 brightness")
        return normalized

    if "mode" in arguments:
        normalized["mode"] = _require_choice("mode", arguments["mode"], AC_MODES)
    if "target_temp" in arguments:
        normalized["target_temp"] = _require_temp(arguments["target_temp"])
    if not {"on", "mode", "target_temp"} & set(normalized):
        raise ToolArgumentError("invalid_arguments", "必须指定 on、mode 或 target_temp")
    return normalized


# ------------------------------------------------------------------- executor
class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102 - see base class
        return None


class HomeToolExecutor:
    """Executes validated tool calls against the local home-service."""

    def __init__(self, base_url: str, timeout: float = 10.0, catalog: list[dict[str, Any]] | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # No system proxy and no redirects: the backend is loopback only, and a
        # redirected request must never carry data somewhere else.
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._catalog = catalog
        self._scenes: list[dict[str, Any]] = []
        self._schemas: list[dict[str, Any]] | None = None
        self._devices_by_kind: dict[str, list[str]] = {}
        self._known_ids: list[str] = []
        self._scene_ids: list[str] = []

    # -------------------------------------------------------------- discovery
    def catalog(self) -> list[dict[str, Any]]:
        """Fetch the device catalog once; it defines the whole tool surface."""
        if self._catalog is None:
            self._load_catalog()
        return self._catalog

    def scenes(self) -> list[dict[str, Any]]:
        if self._catalog is None:
            self._load_catalog()
        return self._scenes

    def _load_catalog(self) -> None:
        status, body = self._request("GET", "/catalog", None, None)
        data = (body.get("data") or {}) if status == 200 else {}
        self._catalog = data.get("devices") or []
        self._scenes = data.get("scenes") or []

    def schemas(self) -> list[dict[str, Any]]:
        if self._schemas is None:
            self._schemas, self._devices_by_kind = build_tool_schemas(self.catalog(), self.scenes())
            self._known_ids = [device["id"] for device in self.catalog()]
            self._scene_ids = [scene["id"] for scene in self.scenes()]
        return self._schemas

    def scene_context(self) -> str | None:
        """Live one-line "who the camera sees and where", for the system prompt.

        The model has no eyes, so without this it asks the user which room they
        are in even though the vision service already answered that. This is
        data only: it never claims to know who is speaking, because the camera
        cannot know that and the design forbids guessing.
        """
        status, body = self._request("GET", "/tool/person_location", None, None)
        if status != 200 or not body.get("ok"):
            return None
        persons = (body.get("data") or {}).get("persons") or []
        if not persons:
            return None
        seen = [
            f"{person.get('display_name') or person.get('id')}在"
            f"{person.get('room_name') or person.get('room_id')}"
            + (f"，{pose_text(person.get('pose'))}" if pose_text(person.get("pose")) else "")
            for person in persons
            if person.get("location_known")
        ]
        if not seen:
            return "摄像头实时观察：当前画面里没有识别到已注册的家人。"
        return "摄像头实时观察：" + "，".join(seen) + "。其他已注册家人当前没有被摄像头看到。"

    def device_name(self, device_id: str) -> str:
        for device in self.catalog():
            if device["id"] == device_id:
                return device.get("name") or device_id
        return device_id

    def person_names(self) -> dict[str, str]:
        """Person id to display name, from the directory home-service owns.

        Fetched per call rather than cached: people are added at runtime, the call
        is loopback and sub-millisecond, and a stale directory would print an id
        nobody can pronounce or, worse, the wrong person's name.
        """

        status, body = self._request("GET", "/persons", None, None)
        if status != 200 or not isinstance(body, dict):
            return {}
        names: dict[str, str] = {}
        for item in body.get("persons") or []:
            if isinstance(item, dict) and item.get("id"):
                names[str(item["id"])] = str(item.get("display_name") or "")
        return names

    def self_room(self, person_id: str | None) -> tuple[str, str, float]:
        """The speaker's fresh, camera-confirmed room, or a reason it is unusable.

        Both observations must be available *now*: a confirmed speaker and a
        location the camera confirmed a moment ago. No previous room and no
        model-picked device is ever used as a fallback, because a stale room is
        indistinguishable from a wrong one once it is spoken.
        """
        if not person_id:
            raise ToolArgumentError("speaker_unknown", "我还不能确认是谁在说话，请说出要控制的房间")
        status, body = self._request("GET", "/tool/person_location", {"person": person_id}, None)
        if status != 200 or not body.get("ok"):
            raise ToolArgumentError("location_unavailable", "暂时查不到你的位置，请直接说出房间")
        persons = (body.get("data") or {}).get("persons") or []
        matches = [item for item in persons if isinstance(item, dict) and item.get("id") == person_id]
        if len(matches) != 1:
            raise ToolArgumentError("location_unknown", "我现在没确认你在哪个房间，请直接说出房间")
        person = matches[0]
        observed_at = person.get("observed_at_ms")
        now_ms = int(time.time() * 1000)
        if (not person.get("location_known") or not person.get("room_id")
                or isinstance(observed_at, bool) or not isinstance(observed_at, int)
                or observed_at > now_ms + 1000
                or now_ms - observed_at > SELF_LOCATION_MAX_AGE_MS):
            raise ToolArgumentError("location_unknown", "我现在没确认你在哪个房间，请直接说出房间")
        age_seconds = max(0.0, (now_ms - observed_at) / 1000.0)
        return str(person["room_id"]), str(person.get("room_name") or person["room_id"]), age_seconds

    def resolve_self_device(self, name: str, person_id: str | None) -> tuple[str, str]:
        """Resolve a local reversible action using this turn's speaker and camera."""

        if not may_use_self_target(name):
            raise ToolArgumentError("self_target_forbidden", "这项操作需要明确指定设备")
        kind = "ac" if name == ADJUST_TOOL else {"set_light": "light", "set_ac": "ac", "set_switch": "switch"}.get(name)
        if kind is None:
            raise ToolArgumentError("unknown_tool", "无法确定要控制的设备类型")
        room_id, room_name, _ = self.self_room(person_id)
        candidates = [device for device in self.catalog()
                      if device.get("room_id") == room_id and device.get("type") == kind
                      and device.get("controllable")]
        if len(candidates) != 1:
            raise ToolArgumentError("device_ambiguous", f"{room_name}里没有唯一可选的设备，请明确说出设备")
        device = candidates[0]
        return str(device["id"]), str(device.get("room_name") or room_name)

    # Why each refusal reads differently in the prompt: the model has to choose
    # between "act now" and "ask a question", and that choice depends on which
    # observation is missing. A single vague line made it ask even when the room
    # was known a moment ago, which is the behaviour the user reported.
    _SELF_REASONS = {
        "speaker_unknown": "还没有确认说话人身份",
        "location_unavailable": "暂时连不上位置服务",
        "location_unknown": f"摄像头在最近 {SELF_LOCATION_MAX_AGE_MS / 1000:g} 秒内没有确认到说话人的房间",
        "device_ambiguous": "这个房间没有唯一可选的同类设备",
    }

    def self_target_line(self, person_id: str | None) -> str | None:
        """One data line saying whether ``target: "self"`` can work right now.

        This is the difference between an assistant that knows who and where the
        speaker is and one that still asks which room: the resolved room is handed
        to the model as context, so a plain "空调调高一度" can be executed instead
        of questioned. It carries no authority — every write still goes through
        `resolve_self_device` at execution time, which re-checks freshness.
        """

        if not person_id:
            return None
        try:
            room_id, room_name, age_seconds = self.self_room(person_id)
        except ToolArgumentError as exc:
            reason = self._SELF_REASONS.get(exc.code, exc.message or "位置不可用")
            return (
                f"本轮无可信定位（原因：{reason}）。用户没说房间时，必须先问清房间再控制设备，不要猜。"
            )
        devices = [
            device for device in self.catalog()
            if device.get("room_id") == room_id and device.get("controllable")
            and device.get("type") in ("light", "ac")
        ]
        names = "、".join(str(device.get("name") or device["id"]) for device in devices) or "暂无"
        return (
            f"本轮可信定位：说话人在{room_name}（{age_seconds:.1f} 秒前的摄像头观察），"
            f"该房间可按 self 控制的设备：{names}。"
            "用户没说房间时，直接调用对应工具并传 target=\"self\"，不要再问房间；"
            "只有这一行写成“本轮无可信定位”时才追问。"
        )

    # -------------------------------------------------------------- transport
    def _request(self, method: str, path: str, query: dict[str, str] | None, body: dict | None):
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        request = Request(
            url,
            method=method,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8", errors="replace"))
            except (ValueError, OSError):
                return exc.code, {}
        except (URLError, TimeoutError, OSError, ValueError):
            return 0, {}

    # -------------------------------------------------------------- execution
    def execute(self, name: str, arguments: dict[str, Any], operation_id: str | None = None) -> ToolResult:
        if name in WRITE_TOOLS:
            self.schemas()  # ensure the catalog-derived enums and scenes are loaded
        try:
            normalized = normalize_arguments(
                name, arguments, self._devices_by_kind, self._known_ids, self._scene_ids
            )
        except ToolArgumentError as exc:
            # The model is corrected, not the device: nothing is sent.
            return ToolResult(
                name=name, ok=False, error_code=exc.code, message=exc.message, arguments=arguments or {}
            )

        if name == "query_room_status":
            query = {"room": normalized["room"]} if "room" in normalized else None
            status, body = self._request("GET", "/tool/room_status", query, None)
        elif name == END_TOOL:
            # Ending the conversation is a local decision: the house service has
            # nothing to do with it, so no request is made.
            return ToolResult(name=name, ok=True, phrase="结束对话")
        elif name == "query_device_status":
            # home-service names these query parameters `device`/`room`; sending
            # `device_id` silently returned every device instead of one.
            query = {
                {"device_id": "device", "room": "room"}[key]: str(value)
                for key, value in normalized.items()
            }
            status, body = self._request("GET", "/tool/device_status", query, None)
        elif name == "query_person_location":
            query = {"person": normalized["person"]} if "person" in normalized else None
            status, body = self._request("GET", "/tool/person_location", query, None)
        else:
            operation_id = operation_id or new_operation_id()
            payload = dict(normalized)
            payload["operation_id"] = operation_id
            status, body = self._request("POST", f"/tool/{name}", None, payload)

        if status == 0:
            return ToolResult(
                name=name,
                ok=False,
                error_code="backend_unavailable",
                message="家居服务当前不可用",
                operation_id=operation_id,
                arguments=normalized,
            )

        result = ToolResult(
            name=name,
            ok=bool(body.get("ok")) and status < 400,            error_code=body.get("error_code"),
            message=body.get("error"),
            phrase=body.get("phrase"),
            data=body.get("data"),
            operation_id=body.get("operation_id") or operation_id,
            arguments=normalized,
        )
        if not result.ok and not result.message:
            result.message = f"操作失败（HTTP {status}）"
        if not result.ok and not result.error_code:
            result.error_code = "request_failed"
        return result


def summarize_queries(results: list[ToolResult], device_name=None) -> str:
    """Deterministic fallback text when the model returns nothing usable."""
    lines: list[str] = []
    for result in results:
        if not result.ok:
            lines.append(result.message or "查询失败")
            continue
        data = result.data or {}
        devices = data.get("devices")
        if devices:
            for device in devices:
                state = device.get("state") or {}
                name = device.get("name") or device.get("id")
                if device.get("type") == "light":
                    if state.get("on"):
                        lines.append(f"{name}：已打开，亮度 {state.get('brightness')}")
                    else:
                        lines.append(f"{name}：已关闭")
                elif device.get("type") == "ac":
                    if state.get("on"):
                        lines.append(
                            f"{name}：已打开，模式 {state.get('mode')}，"
                            f"设定 {state.get('target_temp')} 度"
                        )
                    else:
                        lines.append(f"{name}：已关闭")
                elif device.get("type") == "sensor":
                    lines.append(f"{name}：{state.get('temperature')} 度")
                else:
                    lines.append(f"{name}：{'开' if state.get('on') else '关'}")
        for room in data.get("rooms") or []:
            temperature = room.get("simulated_temp")
            if temperature is not None:
                lines.append(f"{room.get('name')}室温 {temperature} 度")
        for person in data.get("persons") or []:
            name = person.get("display_name") or person.get("id")
            if person.get("location_known"):
                room = person.get("room_name") or person.get("room_id")
                pose = pose_text(person.get("pose"))
                lines.append(f"摄像头看到{name}在{room}" + (f"，{pose}" if pose else ""))
            else:
                lines.append(f"{name}：摄像头当前没有看到")
    return "；".join(lines)
