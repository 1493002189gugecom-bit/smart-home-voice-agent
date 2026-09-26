import assert from "node:assert/strict";
import test from "node:test";
import { deviceStateLabel } from "./device-labels.ts";

test("device states use Chinese labels and measured values", () => {
  assert.equal(deviceStateLabel({ on: true, brightness: 71 }, "light"), "开启 · 亮度 71%");
  assert.equal(deviceStateLabel({ on: true, mode: "cool", target_temp: 27 }, "ac"), "开启 · 制冷 · 目标 27°C");
  assert.equal(deviceStateLabel({ on: false }, "switch"), "关闭");
  assert.equal(deviceStateLabel({ temperature: 26 }, "sensor"), "26°C");
});
