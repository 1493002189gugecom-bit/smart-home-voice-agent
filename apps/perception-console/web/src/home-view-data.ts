export type HomeSnapshot = {
  generated_at_ms?: number;
  rooms?: Array<Record<string, unknown>>;
  devices?: Array<Record<string, unknown>>;
  persons?: Array<Record<string, unknown>>;
};

export type RoomId = "living_room" | "bedroom" | "kitchen";

export type RoomView = {
  id: RoomId;
  name: string;
  temperature: number | null;
  devices: Array<Record<string, unknown>>;
  people: string[];
  lightOn: boolean | null;
  activeCount: number | null;
};

export const roomOrder: Array<{ id: RoomId; name: string; subtitle: string }> = [
  { id: "living_room", name: "客厅", subtitle: "起居与共享" },
  { id: "bedroom", name: "卧室", subtitle: "休憩空间" },
  { id: "kitchen", name: "厨房", subtitle: "烹饪与餐饮" },
];

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function buildRoomViews(snapshot: HomeSnapshot, stale: boolean): RoomView[] {
  const rooms = snapshot.rooms ?? [];
  const devices = snapshot.devices ?? [];
  const persons = snapshot.persons ?? [];
  return roomOrder.map(({ id, name }) => {
    const room = rooms.find((item) => item.id === id);
    const roomDevices = devices.filter((item) => item.room_id === id);
    const sensor = roomDevices.find((item) => item.type === "sensor" && item.online === true);
    const sensorState = sensor?.state && typeof sensor.state === "object" ? sensor.state as Record<string, unknown> : {};
    const light = roomDevices.find((item) => item.type === "light");
    const lightState = light?.state && typeof light.state === "object" ? light.state as Record<string, unknown> : {};
    const people = persons
      .filter((item) => item.location_known === true && item.room_id === id)
      .map((item) => String(item.display_name ?? item.id ?? "家庭成员"));
    return {
      id,
      name: typeof room?.name === "string" ? room.name : name,
      temperature: stale ? null : finiteNumber(room?.simulated_temp) ?? finiteNumber(sensorState.temperature),
      devices: roomDevices,
      people: stale ? [] : people,
      lightOn: stale || light?.online !== true || typeof lightState.on !== "boolean" ? null : lightState.on,
      activeCount: stale ? null : roomDevices.filter((item) => item.online === true && item.state && typeof item.state === "object" && (item.state as Record<string, unknown>).on === true).length,
    };
  });
}

export function knownPeopleCount(snapshot: HomeSnapshot, stale: boolean): number {
  if (stale) return 0;
  return (snapshot.persons ?? []).filter((item) => item.location_known === true && roomOrder.some((room) => room.id === item.room_id)).length;
}
