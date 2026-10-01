import assert from "node:assert/strict";
import test from "node:test";
import { buildRoomViews, knownPeopleCount } from "./home-view-data.ts";

test("room view shows only confirmed room locations and fresh device state", () => {
  const snapshot = {
    rooms: [{ id: "living_room", name: "客厅", simulated_temp: 25.5 }],
    devices: [
      { id: "living_room_light", room_id: "living_room", type: "light", online: true, state: { on: true } },
      { id: "living_room_ac", room_id: "living_room", type: "ac", online: true, state: { on: false } },
    ],
    persons: [
      { id: "dad", display_name: "爸爸", room_id: "living_room", location_known: true },
      { id: "mom", display_name: "妈妈", room_id: "living_room", location_known: false },
    ],
  };
  const [living] = buildRoomViews(snapshot, false);
  assert.equal(living.temperature, 25.5);
  assert.equal(living.lightOn, true);
  assert.equal(living.activeCount, 1);
  assert.deepEqual(living.people, ["爸爸"]);
  assert.equal(knownPeopleCount(snapshot, false), 1);
  const [stale] = buildRoomViews(snapshot, true);
  assert.equal(stale.temperature, null);
  assert.equal(stale.lightOn, null);
  assert.deepEqual(stale.people, []);
  assert.equal(knownPeopleCount(snapshot, true), 0);
});
