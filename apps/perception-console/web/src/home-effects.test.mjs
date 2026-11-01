import assert from 'node:assert/strict';
import test from 'node:test';
import { deviceVisuals, deviceSlots, temperatureCaption, labelReveal } from './home-effects.ts';
const now = 100000;
const snapshot = (state, extra = {}) => ({ generated_at_ms: now, devices: [{ id: 'living_room_light', type: 'light', room_id: 'living_room', online: true, state, ...extra }] });
test('confirmed brightness is independent per stable device ID', () => {
  for (const brightness of [25, 50, 100]) assert.equal(deviceVisuals(snapshot({on:true,brightness}), false, now)[0].level, brightness / 100);
  assert.equal(deviceVisuals(snapshot({on:true}), false, now)[0].available, false);
});
test('device inventory additions keep unique stable position slots',()=>{
  const slot=deviceSlots();
  assert.equal(slot('a','living_room','light'),0);
  assert.equal(slot('b','living_room','light'),1);
  assert.equal(slot('b','living_room','light'),1);
  assert.equal(slot('c','living_room','light'),2);
  assert.equal(slot('a','living_room','light'),0);
});
test('temperature source is not invented when backend provenance is absent',()=>{
  const s=snapshot({temperature:26},{type:'sensor'});
  assert.equal(temperatureCaption(deviceVisuals(s,false,now)[0]),'26°C 读数');
  assert.equal(temperatureCaption(deviceVisuals({...s,backend:'memory'},false,now)[0]),'26°C 模拟');
  assert.equal(temperatureCaption(deviceVisuals({...s,backend:'ha'},false,now)[0]),'26°C 实测');
});
test('device captions stay hidden in the wide view and reveal smoothly as zoom approaches',()=>{
  assert.equal(labelReveal(0.78),0);
  assert.equal(labelReveal(1),0);
  assert.equal(labelReveal(1.15),0);
  assert.ok(labelReveal(1.3)>0 && labelReveal(1.3)<1);
  assert.ok(labelReveal(1.4)>labelReveal(1.3));
  assert.equal(labelReveal(1.5),1);
  assert.equal(labelReveal(2.1),1);
});
test('offline, stale, missing timestamp and unknown state never show operation', () => {
  for (const s of [snapshot({on:true,brightness:100}, {online:false}), {...snapshot({on:true,brightness:100}), generated_at_ms:1}, {devices:snapshot({on:true}).devices}]) assert.equal(deviceVisuals(s, false, now)[0].available, false);
  assert.equal(deviceVisuals(snapshot({on:true,brightness:100}), true, now)[0].available, false);
});
test('AC only accepts confirmed cooling/fan modes; temperature is measured', () => {
  for (const mode of ['cool','fan_only','unknown']) {
    const v = deviceVisuals(snapshot({on:true,mode,target_temp:20}, {type:'ac'}), false, now)[0];
    assert.equal(v.airflow, mode === 'cool' ? 'cool' : mode === 'fan_only' ? 'fan' : null);
  }
  assert.equal(deviceVisuals(snapshot({target_temp:20}, {type:'sensor'}), false, now)[0].temperature, null);
  assert.equal(deviceVisuals(snapshot({temperature:24.5}, {type:'sensor'}), false, now)[0].temperature, 24.5);
});
