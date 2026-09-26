import test from "node:test";
import assert from "node:assert/strict";
import { canOpenRegistration, registrationGuidance, registrationStepLabel } from "./registration.ts";

test("no face guidance refers to the visible camera rather than blaming the person", () => {
  assert.match(registrationGuidance("no_face"), /画面/);
  assert.match(registrationGuidance("no_face"), /摄像头/);
});

test("camera faults and low-quality faces have distinct guidance", () => {
  assert.notEqual(registrationGuidance("camera_disconnected"), registrationGuidance("no_face"));
  assert.notEqual(registrationGuidance("multiple_faces"), registrationGuidance("no_face"));
  assert.equal(registrationStepLabel("blink"), "眨眼");
});

test("blur guidance describes a temporary frame and suggests practical checks", () => {
  assert.match(registrationGuidance("blurred"), /这一帧/);
  assert.match(registrationGuidance("blurred"), /下一帧/);
  assert.match(registrationGuidance("blurred"), /正面光/);
});

test("the front-step guidance explains camera height when pose is rejected", () => {
  assert.match(registrationGuidance("wrong_action", undefined, "front"), /眼睛大致同高/);
  assert.match(registrationGuidance("wrong_action", undefined, "front"), /摄像头俯仰角/);
});

test("a reachable but degraded vision service still allows recovery through registration", () => {
  assert.equal(canOpenRegistration("up"), true);
  assert.equal(canOpenRegistration("degraded"), true);
  assert.equal(canOpenRegistration("down"), false);
  assert.equal(canOpenRegistration(undefined), false);
});
