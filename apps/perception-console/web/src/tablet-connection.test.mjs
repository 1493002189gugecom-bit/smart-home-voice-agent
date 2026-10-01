import assert from "node:assert/strict";
import test from "node:test";
import { normalizeTabletOrigin, validateTabletSnapshot } from "./tablet-connection.ts";

test("tablet server setting accepts only an HTTPS root origin", () => {
  assert.equal(normalizeTabletOrigin(" https://home.example.ts.net/ "), "https://home.example.ts.net");
  for (const input of [
    "http://home.example.ts.net",
    "https://user:secret@home.example.ts.net",
    "https://home.example.ts.net/tablet",
    "https://home.example.ts.net/?token=secret",
    "https://home.example.ts.net/#room",
    "javascript:alert(1)",
  ]) {
    assert.equal(normalizeTabletOrigin(input), null, input);
  }
});

test("tablet rejects incomplete or old snapshots before showing a room", () => {
  const now = 1_800_000_000_000;
  const current = { generated_at_ms: now - 1000, rooms: [], devices: [], persons: [] };
  assert.deepEqual(validateTabletSnapshot(current, now), current);
  assert.throws(() => validateTabletSnapshot({ ...current, generated_at_ms: now - 6000 }, now));
  assert.throws(() => validateTabletSnapshot({ ...current, persons: undefined }, now));
  assert.throws(() => validateTabletSnapshot({ ...current, generated_at_ms: now + 2000 }, now));
});
