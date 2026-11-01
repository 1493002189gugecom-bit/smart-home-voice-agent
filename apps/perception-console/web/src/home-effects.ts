import type { HomeSnapshot, RoomId } from './home-view-data';
export const SNAPSHOT_MAX_AGE = 5000;
export function labelReveal(zoom: number) {
  const t=Math.max(0,Math.min(1,(zoom-1.15)/0.35));
  return t*t*(3-2*t);
}
export function snapshotFresh(home: HomeSnapshot, stale: boolean, now = Date.now()) {
  const stamp=home.generated_at_ms;
  return !stale && typeof stamp==='number' && Number.isFinite(stamp) && now-stamp<=SNAPSHOT_MAX_AGE && stamp<=now+1000;
}
export type DeviceVisual = { id: string; room: RoomId; type: string; available: boolean; on: boolean; level: number; airflow: 'cool' | 'fan' | null; temperature: number | null; targetTemperature: number | null; temperatureSource?: 'measured'|'simulated'|'sensor' };
export function deviceSlots() {
  const positions=new Map<string,{group:string;index:number}>();
  const next=new Map<string,number>();
  return (id:string,room:string,type:string) => {
    const group=room+':'+type; const saved=positions.get(id);
    if(saved?.group===group) return saved.index;
    const index=next.get(group)??0; next.set(group,index+1); positions.set(id,{group,index}); return index;
  };
}
export function temperatureCaption(s:DeviceVisual) { return `${s.temperature}°C ${s.temperatureSource==='measured'?'实测':s.temperatureSource==='simulated'?'模拟':'读数'}`; }
export function deviceVisuals(home: HomeSnapshot, stale: boolean, now = Date.now()): DeviceVisual[] {
  const fresh = snapshotFresh(home,stale,now);
  return (home.devices ?? []).filter(d => typeof d.id === 'string' && ['living_room','bedroom','kitchen'].includes(String(d.room_id)) && ['light','ac','switch','sensor'].includes(String(d.type))).map(d => {
    const s = d.state && typeof d.state === 'object' ? d.state as Record<string, unknown> : {};
    const number = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? v : null;
    const brightness = number(s.brightness);
    const temp = number(s.temperature);
    const valid = d.type === 'sensor' ? temp !== null : typeof s.on === 'boolean' && (d.type !== 'light' || s.on === false || (brightness !== null && brightness >= 0 && brightness <= 100)) && (d.type !== 'ac' || s.on === false || ['cool','fan_only'].includes(String(s.mode)));
    const available = fresh && d.online === true && valid;
    const on = available && s.on === true;
    return {id: String(d.id), room: d.room_id as RoomId, type: String(d.type), available, on, level: on ? d.type === 'light' ? brightness! / 100 : 1 : 0, airflow: on && d.type === 'ac' ? s.mode === 'cool' ? 'cool' : 'fan' : null, temperature: available && d.type === 'sensor' ? temp : null, targetTemperature: available && d.type === 'ac' ? number(s.target_temp) : null, temperatureSource:home.backend==='ha'?'measured':home.backend==='memory'?'simulated':'sensor'};
  });
}
