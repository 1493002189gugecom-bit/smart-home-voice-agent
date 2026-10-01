import { useEffect, useRef, useState, type CSSProperties } from "react";
import { controllableTypes, initialDeviceDraft, makeDeviceCommand, type DeviceCommand, type DeviceDraft } from "./device-control";

export default function DeviceControls({ device, stale, busy, onCommand }: {
  device: Record<string, unknown>; stale: boolean; busy: boolean; onCommand: (request: DeviceCommand) => Promise<void>;
}) {
  const [draft, setDraft] = useState<DeviceDraft>(() => initialDeviceDraft(device));
  const [error, setError] = useState("");
  const draftRef = useRef(draft);
  const contextRef = useRef({ device, stale, onCommand });
  contextRef.current = { device, stale, onCommand };
  const queued = useRef<DeviceDraft | null>(null);
  const sending = useRef(false);
  const lastCommand = useRef("");
  const pointerStart = useRef<number | null>(null);
  const suppressClick = useRef(false);
  const brightnessEdited = useRef(false);
  const draggingBrightness = useRef(false);
  useEffect(() => {
    if (!sending.current && !draggingBrightness.current) {
      const actual = initialDeviceDraft(device);
      draftRef.current = actual;
      setDraft(actual);
    }
  }, [device.state]);
  if (!controllableTypes.has(String(device.type))) return <p className="device-control-note">此设备仅支持查看读数。</p>;
  const unavailable = stale || device.online !== true;

  function update(field: keyof DeviceDraft, value: DeviceDraft[keyof DeviceDraft], apply = true) {
    const next = { ...draftRef.current, [field]: value };
    draftRef.current = next;
    setDraft(next);
    setError("");
    if (apply) commit(next);
  }
  function commit(next: DeviceDraft) {
    try {
      const current = contextRef.current;
      const request = makeDeviceCommand(current.device, next, `ui-${crypto.randomUUID()}`, current.stale);
      const signature = JSON.stringify(request.changes);
      if (signature === lastCommand.current) return;
      lastCommand.current = signature;
      queued.current = next;
      void drain();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "设备设置无效"); }
  }
  async function drain() {
    if (sending.current) return;
    sending.current = true;
    try {
      while (queued.current) {
        const next = queued.current;
        queued.current = null;
        const current = contextRef.current;
        const request = makeDeviceCommand(current.device, next, `ui-${crypto.randomUUID()}`, current.stale);
        await current.onCommand(request);
      }
    } catch (reason) {
      queued.current = null;
      lastCommand.current = "";
      setError(reason instanceof Error ? reason.message : "设备暂时无法调整");
    } finally { sending.current = false; }
  }

  return <section className="device-controls device-controls-live" aria-label={`${String(device.name ?? device.id)}实时控制`}>
    <fieldset disabled={unavailable}>
      <legend>设备开关</legend>
      <button type="button" role="switch" aria-checked={draft.on === true} aria-label={`${String(device.name ?? device.id)}开关`} className={`device-ios-power${draft.on === true ? " is-on" : ""}`}
        onPointerDown={(event) => { pointerStart.current = event.clientX; suppressClick.current = false; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerCancel={() => { pointerStart.current = null; suppressClick.current = false; }}
        onPointerUp={(event) => {
          if (pointerStart.current !== null && Math.abs(event.clientX - pointerStart.current) > 14) {
            suppressClick.current = true;
            update("on", event.clientX > pointerStart.current);
          }
          pointerStart.current = null;
        }}
        onClick={() => { if (suppressClick.current) { suppressClick.current = false; return; } update("on", draftRef.current.on !== true); }}>
        <span className="device-ios-power-label">{draft.on === null ? "开启设备" : draft.on ? "开启" : "关闭"}</span>
        <span className="device-ios-switch-track" aria-hidden="true"><span className="device-ios-switch-thumb" /></span>
      </button>
      {draft.on === true && device.type === "light" && <div className="device-brightness">
        <div className="device-setting-heading"><label htmlFor={`brightness-${String(device.id)}`}>亮度</label><output>{draft.brightness === null ? "沿用当前亮度" : <><strong>{draft.brightness}</strong>%</>}</output></div>
        <input id={`brightness-${String(device.id)}`} className="device-ios-range" type="range" min="1" max="100" step="1" value={draft.brightness ?? 50} aria-valuetext={draft.brightness === null ? "未调整，沿用当前亮度" : `${draft.brightness}%`} style={{ "--brightness-fill": `${draft.brightness ?? 50}%` } as CSSProperties}
          onPointerDown={(event) => { draggingBrightness.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerCancel={() => { draggingBrightness.current = false; brightnessEdited.current = false; }}
          onChange={(event) => { brightnessEdited.current = true; update("brightness", Number(event.target.value), false); }}
          onPointerUp={() => { draggingBrightness.current = false; if (brightnessEdited.current) { brightnessEdited.current = false; commit(draftRef.current); } }}
          onKeyUp={(event) => { if (brightnessEdited.current && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(event.key)) { brightnessEdited.current = false; commit(draftRef.current); } }}
          onBlur={() => { draggingBrightness.current = false; if (brightnessEdited.current) { brightnessEdited.current = false; commit(draftRef.current); } }} />
        <div className="device-range-hints" aria-hidden="true"><span>柔和</span><span>明亮</span></div>
        <div className="device-brightness-presets" aria-label="快捷亮度">{[25, 50, 75, 100].map((value) => <button key={value} type="button" aria-pressed={draft.brightness === value} className={draft.brightness === value ? "selected" : ""} onClick={() => update("brightness", value)}>{value}%</button>)}</div>
      </div>}
      {draft.on === true && device.type === "ac" && <>
        <div className="device-mode-options" aria-label="空调模式">
          <button type="button" aria-pressed={draft.mode === "cool"} className={draft.mode === "cool" ? "selected" : ""} onClick={() => update("mode", "cool")}>❄ 制冷</button>
          <button type="button" aria-pressed={draft.mode === "fan_only"} className={draft.mode === "fan_only" ? "selected" : ""} onClick={() => update("mode", "fan_only")}>≋ 送风</button>
        </div>
        {draft.mode !== "fan_only" && <div className="device-temperature-control">
          <span className="device-temperature-label">设定温度</span>
          {draft.targetTemp === null ? <button type="button" className="device-temperature-start" onClick={() => update("targetTemp", 26)}>设为 26°C</button> : <div className="device-temperature-stepper">
            <button type="button" aria-label="降低温度 0.5 度" disabled={draft.targetTemp <= 16} onClick={() => update("targetTemp", Math.max(16, draftRef.current.targetTemp! - 0.5))}>−</button>
            <output aria-live="polite"><strong>{draft.targetTemp}</strong><span>°C</span></output>
            <button type="button" aria-label="升高温度 0.5 度" disabled={draft.targetTemp >= 30} onClick={() => update("targetTemp", Math.min(30, draftRef.current.targetTemp! + 0.5))}>+</button>
          </div>}
          <p className="device-control-note">{draft.targetTemp === null ? "温度尚未读取" : "每次 0.5° · 16–30°C"}</p>
        </div>}
      </>}
    </fieldset>
    <p className="device-control-note" role="status">{busy ? "正在同步到设备…" : device.type === "light" ? "开关即刻调整 · 亮度松手即同步" : "点按或滑动即可调整"}</p>
    {unavailable && <p className="device-control-error" role="status">{stale ? "家庭服务未同步，恢复后才能修改。" : "设备离线，恢复后才能修改。"}</p>}
    {error && <p className="device-control-error" role="alert">{error}</p>}
  </section>;
}
