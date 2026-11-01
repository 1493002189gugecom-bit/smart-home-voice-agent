import test from 'node:test';
import assert from 'node:assert/strict';
import { moveWalker, limitPitch } from './walk-navigation.ts';

const area = {minX:-2,maxX:2,minZ:-2,maxZ:2};
const input = {forward:1,strafe:0,yaw:0,seconds:1/60,fast:false};
const close = (actual,expected) => assert.ok(Math.abs(actual-expected)<1e-9, `${actual} != ${expected}`);

test('walking follows the view rather than world axes', () => {
  const forward=moveWalker({x:0,z:0},input,area,[]);
  close(forward.x,0);close(forward.z,-0.02);
  const turned=moveWalker({x:0,z:0},{...input,yaw:Math.PI/2},area,[]);
  close(turned.x,-0.02);close(turned.z,0);
});
test('diagonal keys do not increase walking speed', () => {
  const p=moveWalker({x:0,z:0},{...input,strafe:1},area,[]);
  close(Math.hypot(p.x,p.z),0.02);
});
test('resuming a paused tab cannot create a large jump', () => {
  const p=moveWalker({x:0,z:0},{...input,seconds:30},area,[]);
  close(p.z,-0.06);
});
test('the camera cannot leave the room floor', () => {
  const p=moveWalker({x:1.8,z:0},{...input,forward:0,strafe:1,seconds:0.05,fast:true},area,[]);
  close(p.x,1.86);
});
test('swept movement cannot pass through a narrow obstacle', () => {
  const step={...input,forward:0,strafe:1,seconds:0.05,fast:true};
  close(moveWalker({x:0,z:0},step,area,[]).x,0.12);
  const p=moveWalker({x:0,z:0},step,area,[{minX:0.15,maxX:0.17,minZ:-1,maxZ:1}]);
  assert.ok(p.x <= 0.01);
});
test('a blocked sideways movement still slides along furniture', () => {
  const p=moveWalker({x:0,z:0},{...input,strafe:1,seconds:0.05,fast:true},area,[{minX:0.15,maxX:1,minZ:-1,maxZ:1}]);
  assert.ok(p.x<=0.01 && p.z< -0.05);
});
test('the view can look up and down without flipping upside down', () => {
  assert.ok(limitPitch(100)<Math.PI/2);
  assert.ok(limitPitch(-100)>-Math.PI/2);
  close(limitPitch(0.25),0.25);
});
