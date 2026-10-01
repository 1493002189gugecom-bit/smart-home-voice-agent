"""Restricted manual device commands and public, observed operation results."""

import math
import re


class ControlRejected(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def validate_command(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"device_id", "operation_id", "changes"}:
        raise ControlRejected("invalid_request", "请只提交所选设备及其设置")
    for key, minimum in (("device_id", 1), ("operation_id", 8)):
        value = payload[key]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{%d,100}" % minimum, value):
            raise ControlRejected("invalid_request", "设备或操作编号无效")
    changes = payload["changes"]
    if not isinstance(changes, dict) or not changes or set(changes) - {"on", "brightness", "mode", "target_temp"}:
        raise ControlRejected("invalid_request", "请选择可修改的设备设置")
    if "on" in changes and not isinstance(changes["on"], bool):
        raise ControlRejected("invalid_request", "开关设置无效")
    return payload


def normalize_control(payload: dict, snapshot: dict) -> tuple[str, dict]:
    validate_command(payload)
    device = next((item for item in snapshot.get("devices", []) if item.get("id") == payload["device_id"]), None)
    if device is None:
        raise ControlRejected("not_found", "没有找到所选设备", 404)
    if device.get("online") is not True:
        raise ControlRejected("offline", "设备当前离线，无法修改", 409)
    device_type = device.get("type")
    fields = {"light": {"on", "brightness"}, "ac": {"on", "mode", "target_temp"}, "switch": {"on"}}
    changes = payload["changes"]
    if device_type not in fields or set(changes) - fields[device_type]:
        raise ControlRejected("unsupported_control", "此设备不支持这项设置")
    if changes.get("on") is False and len(changes) != 1:
        raise ControlRejected("invalid_request", "关闭设备时请只提交关闭设置")
    if "brightness" in changes:
        brightness = changes["brightness"]
        if isinstance(brightness, bool) or not isinstance(brightness, int) or not 1 <= brightness <= 100:
            raise ControlRejected("invalid_request", "灯光亮度应为 1 至 100%")
    if "mode" in changes:
        if not isinstance(changes["mode"], str) or changes["mode"] not in {"cool", "fan_only"}:
            raise ControlRejected("invalid_request", "空调模式应为制冷或送风，关闭请使用开关")
    if "target_temp" in changes:
        temp = changes["target_temp"]
        if isinstance(temp, bool) or not isinstance(temp, (int, float)) or not 16 <= temp <= 30 or not math.isfinite(temp):
            raise ControlRejected("invalid_request", "空调设定温度应为 16 至 30 度")
    kind = {"light": "set_light", "ac": "set_ac", "switch": "set_switch"}[device_type]
    return "/tool/" + kind, {"device_id": payload["device_id"], "operation_id": payload["operation_id"], **changes}


ERROR_MESSAGES = {
    "offline": "设备当前离线",
    "backend_unavailable": "暂时无法连接设备，请检查家庭服务",
    "backend_invalid_response": "暂时无法确认设备当前状态",
    "backend_rejected": "设备没有接受这次操作",
    "invalid_request": "设备设置无效，请检查后重新提交",
    "operation_id_conflict": "这次请求与先前操作冲突，请重新发起",
    "not_found": "没有找到所选设备",
    "wrong_type": "所选设备不支持这项设置",
    "confirmation_timeout": "已尝试操作，但还未确认设备达到目标状态，请查看最新状态",
    "submission_unknown": "操作结果暂时无法确认，请查看最新设备状态",
}


def project_control_result(result: object, request: dict, http_status: int) -> dict:
    operation_id = request["operation_id"]
    unknown = {"ok": False, "status": "unconfirmed", "operation_id": operation_id,
               "error_code": "operation_unconfirmed", "message": "本次操作结果未确认，请查看最新设备状态"}
    if not isinstance(result, dict) or result.get("operation_id") != operation_id:
        return unknown
    data = result.get("data")
    state = data.get("state") if isinstance(data, dict) else None
    confirmed = (http_status == 200 and result.get("ok") is True and result.get("status") == "confirmed"
                 and isinstance(state, dict) and data.get("device") == request["device_id"]
                 and all(state.get(key) == value and
                         (isinstance(state.get(key), bool) if isinstance(value, bool)
                          else not isinstance(state.get(key), bool))
                         for key, value in request["changes"].items()))
    if confirmed:
        public_state = {key: state[key] for key in ("on", "brightness", "mode", "target_temp", "temperature", "power")
                        if key in state and isinstance(state[key], (str, bool, int, float))}
        phrase = result.get("phrase")
        return {"ok": True, "status": "confirmed", "operation_id": operation_id,
                "message": phrase if isinstance(phrase, str) and phrase else "所选设备设置已确认",
                "device_id": request["device_id"], "state": public_state}
    if result.get("status") == "confirmed" or result.get("ok") is True:
        return unknown
    code = result.get("error_code")
    if not isinstance(code, str):
        code = "operation_unconfirmed"
    status = "rejected" if result.get("status") == "rejected" else "unconfirmed"
    return {"ok": False, "status": status, "operation_id": operation_id,
            "error_code": code if code in ERROR_MESSAGES else "operation_unconfirmed",
            "message": ERROR_MESSAGES.get(code, unknown["message"])}
