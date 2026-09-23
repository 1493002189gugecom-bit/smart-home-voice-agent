export type ServiceState = "up" | "down" | "degraded";

export type StatusResponse = {
  ok: boolean;
  state: ServiceState;
  services: Record<string, { state: ServiceState; data?: Record<string, unknown>; message?: string }>;
};

export type VoiceEvent = {
  id: number;
  timestamp: string;
  type: "voice_state" | "transcript" | "agent_reply" | "tool_result" | "playback_state" | "service_error";
  payload: Record<string, unknown>;
};

export async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const value = await response.json();
  if (!response.ok) {
    throw new Error(value.message ?? value.error_code ?? `HTTP ${response.status}`);
  }
  return value as T;
}

export function post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  return json<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function remove<T>(path: string): Promise<T> {
  return json<T>(path, { method: "DELETE" });
}

export function display(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  return JSON.stringify(value);
}
