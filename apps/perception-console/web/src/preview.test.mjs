import test from "node:test";
import assert from "node:assert/strict";
import { previewIsStale, previewSourceKey } from "./preview.ts";

test("preview connection changes when room or camera session changes", () => {
  const current = { camera_id: "device:0", camera_room_id: "bedroom", session_id: "session-1" };
  assert.notEqual(previewSourceKey(current), previewSourceKey({ ...current, camera_room_id: "living_room" }));
  assert.notEqual(previewSourceKey(current), previewSourceKey({ ...current, session_id: "session-2" }));
});

test("preview freshness distinguishes paused image from a current frame", () => {
  assert.equal(previewIsStale(1_000, 7_001), true);
  assert.equal(previewIsStale(6_000, 7_001), false);
  assert.equal(previewIsStale(null, 7_001), false);
});
