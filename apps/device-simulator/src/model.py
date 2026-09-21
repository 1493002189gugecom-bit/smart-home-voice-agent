import copy
import json
import math
import os
from pathlib import Path


LIGHTS = ("living_room_light", "bedroom_light", "kitchen_light")
AIR_CONDITIONERS = ("living_room_ac", "bedroom_ac", "kitchen_ac")
DEVICES = (*LIGHTS, *AIR_CONDITIONERS, "desk_plug", "indoor_temperature")


def _default_ac():
    return {"mode": "off", "target_temperature": 26.0, "current_temperature": 26.0}


def initial_state():
    return {
        "living_room_light": {"state": "OFF", "brightness": 50},
        "bedroom_light": {"state": "OFF", "brightness": 40},
        "kitchen_light": {"state": "OFF", "brightness": 60},
        "living_room_ac": _default_ac(),
        "bedroom_ac": _default_ac(),
        "kitchen_ac": _default_ac(),
        "desk_plug": {"state": "OFF", "power": 0},
        "indoor_temperature": {"temperature": 26.0},
    }


def _finite_number(name, value, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} out of range")


def _validate_light_command(command):
    if (
        not isinstance(command, dict)
        or not command
        or set(command) - {"state", "brightness"}
    ):
        raise ValueError("invalid light command")
    if "state" in command and command["state"] not in {"ON", "OFF"}:
        raise ValueError("invalid light state")
    if "brightness" in command:
        _finite_number("brightness", command["brightness"], 0, 100)
        if not isinstance(command["brightness"], int):
            raise ValueError("brightness must be an integer")


def _exact(record, keys, name):
    if not isinstance(record, dict) or set(record) != set(keys):
        raise ValueError(f"invalid {name} shape")


def _validate_persisted_light(record):
    _exact(record, {"state", "brightness"}, "light")
    if record["state"] not in {"ON", "OFF"}:
        raise ValueError("invalid light state")
    _finite_number("brightness", record["brightness"], 0, 100)
    if not isinstance(record["brightness"], int):
        raise ValueError("brightness must be an integer")


def _validate_persisted_ac(record):
    _exact(record, {"mode", "target_temperature", "current_temperature"}, "ac")
    if record["mode"] not in {"off", "cool", "fan_only"}:
        raise ValueError("invalid ac mode")
    _finite_number("target_temperature", record["target_temperature"], 16, 30)
    _finite_number("current_temperature", record["current_temperature"], -40, 85)


def _validate_persisted_plug(record):
    _exact(record, {"state", "power"}, "plug")
    if record["state"] not in {"ON", "OFF"}:
        raise ValueError("invalid plug state")
    _finite_number("power", record["power"], 0, 60)
    expected_power = 60 if record["state"] == "ON" else 0
    if record["power"] != expected_power:
        raise ValueError("invalid plug power for state")


def _validate_persisted_sensor(record):
    _exact(record, {"temperature"}, "sensor")
    _finite_number("temperature", record["temperature"], -40, 85)


def validate_state(state):
    if not isinstance(state, dict) or set(state) != set(DEVICES):
        raise ValueError("invalid device set")
    for device in LIGHTS:
        _validate_persisted_light(state[device])
    for device in AIR_CONDITIONERS:
        _validate_persisted_ac(state[device])
    _validate_persisted_plug(state["desk_plug"])
    _validate_persisted_sensor(state["indoor_temperature"])
    return state


def transition(state, device, action, payload):
    current = validate_state(copy.deepcopy(state))
    if device in LIGHTS and action == "set":
        command = json.loads(payload)
        _validate_light_command(command)
        current[device].update(command)
    elif device in AIR_CONDITIONERS and action == "mode/set":
        if payload not in {"off", "cool", "fan_only"}:
            raise ValueError("invalid ac mode")
        current[device]["mode"] = payload
    elif device in AIR_CONDITIONERS and action == "temperature/set":
        value = json.loads(payload)
        _finite_number("target_temperature", value, 16, 30)
        current[device]["target_temperature"] = float(value)
    elif device == "desk_plug" and action == "set":
        if payload not in {"ON", "OFF"}:
            raise ValueError("invalid plug state")
        current[device] = {"state": payload, "power": 60 if payload == "ON" else 0}
    else:
        raise ValueError("unsupported command")
    return validate_state(current)


def with_temperature(state, value):
    _finite_number("temperature", value, -40, 85)
    changed = validate_state(copy.deepcopy(state))
    changed["indoor_temperature"]["temperature"] = float(value)
    for device in AIR_CONDITIONERS:
        changed[device]["current_temperature"] = float(value)
    return changed


def load_state(path):
    path = Path(path)
    if not path.exists():
        return initial_state()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(persisted, dict) or not set(persisted).issubset(DEVICES):
        raise ValueError("invalid device set")
    migrated = initial_state()
    migrated.update(persisted)
    return validate_state(migrated)


def save_state(path, state):
    validated = validate_state(copy.deepcopy(state))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(validated, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temp, path)
