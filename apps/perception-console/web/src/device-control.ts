export type DeviceDraft = { on: boolean | null; brightness: number | null; mode: "cool" | "fan_only" | null; targetTemp: number | null };
export type DeviceCommand = { device_id: string; operation_id: string; changes: Record<string, boolean | number | string> };
export type DeviceControlResult = {
  ok: boolean; status: "confirmed" | "rejected" | "unconfirmed"; operation_id: string; message: string;
  state?: Record<string, unknown>;
};

export const controllableTypes = new Set(["light", "ac", "switch"]);

export function initialDeviceDraft(device: Record<string, unknown>): DeviceDraft {
  const state = device.state && typeof device.state === "object" ? device.state as Record<string, unknown> : {};
  return {
    on: typeof state.on === "boolean" ? state.on : null,
    brightness: typeof state.brightness === "number" && Number.isInteger(state.brightness) && state.brightness >= 1 && state.brightness <= 100 ? state.brightness : null,
    mode: state.mode === "cool" || state.mode === "fan_only" ? state.mode : null,
    targetTemp: typeof state.target_temp === "number" && Number.isFinite(state.target_temp) && state.target_temp >= 16 && state.target_temp <= 30 ? state.target_temp : null,
  };
}

export function makeDeviceCommand(device: Record<string, unknown>, draft: DeviceDraft, operationId: string, stale: boolean): DeviceCommand {
  if (stale || device.online !== true) throw new Error("设备状态暂不可用，请等待恢复同步。");
  if (!controllableTypes.has(String(device.type)) || typeof device.id !== "string") throw new Error("此设备仅支持查看读数。");
  if (typeof draft.on !== "boolean") throw new Error("请先选择打开或关闭。");
  const changes: DeviceCommand["changes"] = { on: draft.on };
  if (draft.on && device.type === "light" && draft.brightness !== null) {
    if (!Number.isInteger(draft.brightness) || draft.brightness < 1 || draft.brightness > 100) throw new Error("亮度应为 1 至 100%。");
    changes.brightness = draft.brightness;
  }
  if (draft.on && device.type === "ac") {
    if (draft.mode !== null) {
      if (draft.mode !== "cool" && draft.mode !== "fan_only") throw new Error("请选择制冷或送风模式。");
      changes.mode = draft.mode;
    }
    if (draft.mode !== "fan_only" && draft.targetTemp !== null) {
      if (!Number.isFinite(draft.targetTemp) || draft.targetTemp < 16 || draft.targetTemp > 30) throw new Error("设定温度应为 16 至 30 度。");
      changes.target_temp = draft.targetTemp;
    }
  }
  return { device_id: device.id, operation_id: operationId, changes };
}

export function unknownControlResult(request: DeviceCommand): DeviceControlResult {
  return { ok: false, status: "unconfirmed", operation_id: request.operation_id,
    message: "本次操作结果未确认，请查看最新设备状态。可重试本次操作，请勿连续重复提交。" };
}

export function readControlResult(value: unknown, request: DeviceCommand, httpStatus: number): DeviceControlResult {
  const unknown = unknownControlResult(request);
  if (!value || typeof value !== "object" || Array.isArray(value)) return unknown;
  const result = value as Record<string, unknown>;
  const state = result.state;
  const matches = state && typeof state === "object" && !Array.isArray(state) && Object.entries(request.changes).every(([key, desired]) => (state as Record<string, unknown>)[key] === desired);
  const message = typeof result.message === "string" && result.message ? result.message : unknown.message;
  if (httpStatus === 200 && result.ok === true && result.status === "confirmed" &&
      result.operation_id === request.operation_id && result.device_id === request.device_id && matches) {
    return { ok: true, status: "confirmed", operation_id: request.operation_id, message, state: state as Record<string, unknown> };
  }
  if (result.ok === true || result.status === "confirmed") return unknown;
  if (result.status === "rejected" && (result.operation_id === request.operation_id || result.operation_id == null))
    return { ok: false, status: "rejected", operation_id: request.operation_id, message };
  if (result.status === "unconfirmed" && result.operation_id === request.operation_id)
    return { ...unknown, message };
  return unknown;
}
