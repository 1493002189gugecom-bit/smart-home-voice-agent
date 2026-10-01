import type { HomeSnapshot } from "./home-view-data";

export function normalizeTabletOrigin(input: string): string | null {
  try {
    const url = new URL(input.trim());
    if (url.protocol !== "https:" || !url.hostname || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function validateTabletSnapshot(value: unknown, nowMs = Date.now()): HomeSnapshot {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid_snapshot");
  const snapshot = value as Record<string, unknown>;
  const age = nowMs - Number(snapshot.generated_at_ms);
  if (!Number.isInteger(snapshot.generated_at_ms) || age < -1000 || age > 5000 ||
      !Array.isArray(snapshot.rooms) || !Array.isArray(snapshot.devices) || !Array.isArray(snapshot.persons) ||
      ![...snapshot.rooms, ...snapshot.devices, ...snapshot.persons].every(
        (item) => item !== null && typeof item === "object" && !Array.isArray(item),
      )) throw new Error("stale_or_incomplete_snapshot");
  return snapshot as HomeSnapshot;
}
