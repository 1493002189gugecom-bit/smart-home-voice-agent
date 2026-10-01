import { Capacitor, CapacitorHttp } from "@capacitor/core";
import { json } from "./api";
import type { HomeSnapshot } from "./home-view-data";
import { validateTabletSnapshot } from "./tablet-connection";

export function isAndroidApp(): boolean {
  return Capacitor.isNativePlatform();
}

export async function loadTabletSnapshot(origin: string | null): Promise<HomeSnapshot> {
  if (isAndroidApp()) {
    if (!origin) throw new Error("server_not_configured");
    const response = await CapacitorHttp.get({
      url: `${origin}/api/home/snapshot`,
      connectTimeout: 4000,
      readTimeout: 4000,
    });
    if (response.status !== 200) throw new Error("snapshot_unavailable");
    return validateTabletSnapshot(response.data);
  }
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 4000);
  try { return validateTabletSnapshot(await json<unknown>("/api/home/snapshot", { signal: controller.signal })); }
  finally { window.clearTimeout(timeout); }
}
