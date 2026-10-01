import { CapacitorHttp } from "@capacitor/core";
import { isAndroidApp } from "./tablet-api";
import { readControlResult, unknownControlResult, type DeviceCommand, type DeviceControlResult } from "./device-control";

export async function sendDeviceCommand(request: DeviceCommand, origin: string | null): Promise<DeviceControlResult> {
  const path = "/api/home/device/control";
  try {
    if (isAndroidApp()) {
      if (!origin) return { ...unknownControlResult(request), status: "rejected", message: "请先设置家庭服务地址。" };
      const response = await CapacitorHttp.post({ url: `${origin}${path}`, headers: { "Content-Type": "application/json" },
        data: request, connectTimeout: 4000, readTimeout: 30000 });
      return readControlResult(response.data, request, response.status);
    }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request), cache: "no-store", signal: controller.signal });
      return readControlResult(await response.json(), request, response.status);
    } finally { window.clearTimeout(timeout); }
  } catch { return unknownControlResult(request); }
}
