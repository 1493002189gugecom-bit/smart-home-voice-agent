import test from "node:test";
import assert from "node:assert/strict";
import { choiceForPreference, confirmedChoice, selectionPayload, selectionStateForRequest } from "./audio-device-ui.ts";

const devices = [{ index: 4, name: "Realtek", hostapi: "WASAPI" }];

test("automatic preference stays automatic even if a fallback is currently selected", () => {
  assert.equal(choiceForPreference(null, devices), "auto");
});

test("manual preference is resolved by name and host API, not a saved index", () => {
  assert.equal(choiceForPreference({ name: "Realtek", hostapi: "WASAPI" }, devices), "4");
  assert.deepEqual(selectionPayload("4", "auto"), { input: 4, output: "auto" });
});

test("a previous device change cannot confirm a newer request", () => {
  assert.equal(selectionStateForRequest("new", { request_id: "old", state: "applied" }), "applying");
  assert.equal(selectionStateForRequest("new", { request_id: "new", state: "failed" }), "failed");
});

test("confirmation uses the stable device preference after indexes change", () => {
  const selected = { index: 9, name: "Realtek", hostapi: "WASAPI", is_selected: true };
  assert.equal(confirmedChoice("4", { name: "Realtek", hostapi: "WASAPI" }, selected), true);
  assert.equal(confirmedChoice("auto", null, selected), true);
  assert.equal(confirmedChoice("4", { name: "HyperX", hostapi: "WASAPI" }, selected), false);
});
