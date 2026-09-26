export type DeviceChoice = { index: number; name: string; hostapi: string };
export type DevicePreference = { name: string; hostapi: string } | null;
export type SelectionStatus = { request_id: string; state: "applying" | "applied" | "failed"; error_code?: string | null };

export function choiceForPreference(preference: DevicePreference, devices: DeviceChoice[]): string {
  if (!preference) return "auto";
  const found = devices.find((device) => device.name === preference.name && device.hostapi === preference.hostapi);
  return found ? String(found.index) : "auto";
}

export function selectionPayload(input: string, output: string): { input: number | "auto"; output: number | "auto" } {
  return {
    input: input === "auto" ? "auto" : Number(input),
    output: output === "auto" ? "auto" : Number(output),
  };
}

export function selectionStateForRequest(requestId: string, status?: SelectionStatus | null): SelectionStatus["state"] {
  return status?.request_id === requestId ? status.state : "applying";
}

export function confirmedChoice(
  choice: string,
  preference: DevicePreference,
  selected?: DeviceChoice | null,
): boolean {
  if (!selected) return false;
  if (choice === "auto") return preference === null;
  return preference?.name === selected.name && preference?.hostapi === selected.hostapi;
}
