import assert from "node:assert/strict";
import test from "node:test";
import { initialDeviceDraft, makeDeviceCommand, readControlResult } from "./device-control.ts";

const device = { id: "bedroom_ac", type: "ac", online: true, state: { on: true, mode: "cool", target_temp: 26.5 } };
const id = "ui-test-operation-9012";

test("manual controls keep the selected device and submit absolute settings", () => {
  const draft = initialDeviceDraft(device);
  assert.equal(draft.targetTemp, 26.5);
  assert.deepEqual(makeDeviceCommand(device, { ...draft, targetTemp: 24.5 }, id, false), {
    device_id: "bedroom_ac", operation_id: id, changes: { on: true, mode: "cool", target_temp: 24.5 },
  });
  assert.deepEqual(makeDeviceCommand(device, { ...draft, on: false }, id, false).changes, { on: false });
});

test("offline stale sensor and invalid values never produce a write command", () => {
  const draft = initialDeviceDraft(device);
  assert.throws(() => makeDeviceCommand(device, draft, id, true));
  assert.throws(() => makeDeviceCommand({ ...device, online: false }, draft, id, false));
  assert.throws(() => makeDeviceCommand({ ...device, type: "sensor" }, draft, id, false));
  assert.throws(() => makeDeviceCommand(device, { ...draft, targetTemp: 31 }, id, false));
  assert.throws(() => makeDeviceCommand(device, { ...draft, targetTemp: NaN }, id, false));
  assert.throws(() => makeDeviceCommand(device, { ...draft, on: null }, id, false));
});

test("light and plug drafts expose only supported fields", () => {
  const light = { ...device, type: "light", state: { on: true, brightness: 65 } };
  assert.deepEqual(makeDeviceCommand(light, initialDeviceDraft(light), id, false).changes, { on: true, brightness: 65 });
  assert.deepEqual(makeDeviceCommand({ ...light, type: "switch" }, initialDeviceDraft(light), id, false).changes, { on: true });
  assert.throws(() => makeDeviceCommand(light, { ...initialDeviceDraft(light), brightness: 101 }, id, false));
});

test("only a matching observed confirmation becomes success", () => {
  const request = makeDeviceCommand(device, { ...initialDeviceDraft(device), on: false }, id, false);
  const confirmed = { ok: true, status: "confirmed", operation_id: id, device_id: device.id,
    state: { on: false }, message: "卧室空调已关闭" };
  assert.equal(readControlResult(confirmed, request, 200).status, "confirmed");
  for (const result of [
    { ...confirmed, operation_id: "ui-other-operation" }, { ...confirmed, state: { on: true } },
    { ...confirmed, device_id: "living_room_ac" }, { ok: true, message: "已执行" },
  ]) assert.equal(readControlResult(result, request, 200).status, "unconfirmed");
  assert.equal(readControlResult({ ok: false, status: "unconfirmed", operation_id: id, message: "还未确认" }, request, 202).status, "unconfirmed");
  assert.equal(readControlResult({ ok: false, status: "rejected", operation_id: id, message: "设备离线" }, request, 409).status, "rejected");
});
