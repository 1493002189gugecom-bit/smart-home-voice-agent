import { useEffect, useMemo, useRef, useState } from "react";
import { deviceStateLabel } from "./device-labels";
import { buildRoomViews, HomeSnapshot, knownPeopleCount, RoomId, roomOrder } from "./home-view-data";
import { createHomeScene, HomeSceneController } from "./home-scene";
import DeviceControls from "./DeviceControls";
import { sendDeviceCommand } from "./device-control-api";
import type { DeviceCommand, DeviceControlResult } from "./device-control";

const typeName: Record<string, string> = { light: "灯光", ac: "空调", sensor: "传感器", switch: "开关", speaker: "音箱" };

function value(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function HouseStage({ selected, lights, occupants, onSelect }: { selected: RoomId; lights: Record<RoomId, boolean | null>; occupants: Record<RoomId, number>; onSelect: (id: RoomId) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<HomeSceneController | null>(null);
  const onSelectRef = useRef(onSelect);
  const [failure, setFailure] = useState(false);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!host.current) return;
    try {
      const scene = createHomeScene(host.current, (id) => onSelectRef.current(id));
      controller.current = scene;
      setFailure(false);
      return () => { controller.current = null; scene.dispose(); };
    } catch {
      setFailure(true);
    }
  }, []);
  useEffect(() => { controller.current?.update(selected, lights, occupants); }, [selected, lights, occupants]);

  return <div className="house-stage">
    <div ref={host} className="house-render" role="img" aria-label="三房间立体示意图；房间状态请查看下方按钮和右侧详情" />
    {failure && <div className="house-render-fallback" role="status">此设备暂不能显示 3D 画面。房间和设备状态仍可在右侧查看。</div>}
    <div className="house-stage-top"><span className="house-live-mark"><i /> HOME VIEW</span><span>房间级示意 · 非真实户型</span></div>
    <div className="house-stage-bottom"><span>拖动旋转 · 滚轮或双指缩放 · 点击房间查看</span><button type="button" onClick={() => controller.current?.resetCamera()}>重置视角</button></div>
  </div>;
}

export default function Home3D({ home, stale, tabletMode = false, controlOrigin = null, onControlBusyChange }: {
  home: HomeSnapshot; stale: boolean; tabletMode?: boolean; controlOrigin?: string | null; onControlBusyChange?: (busy: boolean) => void;
}) {
  const [selected, setSelected] = useState<RoomId>("living_room");
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [operation, setOperation] = useState<{ request: DeviceCommand; name: string; result: DeviceControlResult | null } | null>(null);
  const rooms = useMemo(() => buildRoomViews(home, stale), [home, stale]);
  const current = rooms.find((room) => room.id === selected) ?? rooms[0];
  const lights = useMemo(() => Object.fromEntries(rooms.map((room) => [room.id, room.lightOn])) as Record<RoomId, boolean | null>, [rooms]);
  const occupants = useMemo(() => Object.fromEntries(rooms.map((room) => [room.id, room.people.length])) as Record<RoomId, number>, [rooms]);
  const online = stale ? null : (home.devices ?? []).filter((device) => device.online === true).length;
  const people = knownPeopleCount(home, stale);
  const focusedDevice = current.devices.find((device) => device.id === selectedDevice);
  const lastUpdate = !stale && typeof home.generated_at_ms === "number" && Number.isFinite(home.generated_at_ms)
    ? new Date(home.generated_at_ms).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  const selectRoom = (id: RoomId) => { if (!busyRef.current) { setSelected(id); setSelectedDevice(null); setOperation(null); } };
  const selectDevice = (id: string) => { if (!busyRef.current) { setSelectedDevice((old) => old === id ? null : id); setOperation(null); } };
  useEffect(() => {
    if (operation?.result?.status !== "confirmed") return;
    const timer = window.setTimeout(() => setOperation((currentOperation) => currentOperation === operation ? null : currentOperation), 3000);
    return () => window.clearTimeout(timer);
  }, [operation]);
  useEffect(() => () => { onControlBusyChange?.(false); }, [onControlBusyChange]);
  async function execute(request: DeviceCommand, name: string) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    onControlBusyChange?.(true);
    setOperation({ request, name, result: null });
    try {
      const result = await sendDeviceCommand(request, controlOrigin);
      setOperation({ request, name, result });
    } finally { busyRef.current = false; setBusy(false); onControlBusyChange?.(false); }
  }

  return <div className="home3d">
    <div className="home3d-heading">
      <div><span className="home3d-kicker">{tabletMode ? "SMART HOME · TABLET VIEW" : "SMART HOME · DIGITAL TWIN"}</span><h1>我的家</h1><p>查看家庭实时状态 · 选择设备可手动调整设置。</p></div>
      <div className={`home3d-sync ${stale ? "is-stale" : ""}`} role="status"><span className="home3d-pulse" />{stale ? "家庭服务未同步 · 状态暂不可用" : `家庭服务已同步${lastUpdate ? ` · ${lastUpdate}` : ""}`}</div>
    </div>

    <div className="home3d-stats" aria-label="全屋概览">
      <div><span>房间</span><strong>03</strong><small>当前示意布局</small></div>
      <div><span>在线设备</span><strong>{online ?? "—"}</strong><small>{stale ? "等待最新快照" : `共 ${(home.devices ?? []).length} 台设备`}</small></div>
      <div><span>可见人物</span><strong>{stale ? "—" : people}</strong><small>仅统计摄像头确认的房间</small></div>
    </div>

    <div className="home3d-main">
      <div className="home3d-visual">
        <HouseStage selected={selected} lights={lights} occupants={occupants} onSelect={selectRoom} />
        <div className="home3d-room-tabs" role="group" aria-label="选择房间">
          {roomOrder.map((entry) => {
            const room = rooms.find((item) => item.id === entry.id)!;
            return <button type="button" key={entry.id} disabled={busy} className={selected === entry.id ? "selected" : ""} aria-pressed={selected === entry.id} onClick={() => selectRoom(entry.id)}>
              <span>{entry.name}</span><small>{room.temperature == null ? "温度未知" : `${room.temperature}°C`} · {stale ? "等待同步" : `${room.devices.length} 台设备`}</small>
            </button>;
          })}
        </div>
      </div>

      <aside className={`home3d-details${focusedDevice ? " has-selection" : ""}`} aria-label={`${current.name}详情`}>
        <div className="home3d-room-heading"><span>01 / 空间详情</span><h2>{current.name}</h2><p>{roomOrder.find((entry) => entry.id === current.id)?.subtitle}</p></div>
        <div className="home3d-temperature"><strong>{current.temperature == null ? "—" : current.temperature}<small>{current.temperature == null ? "" : "°C"}</small></strong><span>室内温度<br />{stale ? "等待实时数据" : current.temperature == null ? "尚无温度读数" : "来自家庭服务"}</span></div>
        <section className="home3d-device-picker" aria-label="选择空间设备">
        <div className="home3d-section-title"><h3>空间设备</h3><span>{current.devices.length} 台</span></div>
        <div className="home3d-device-list">
          {current.devices.length ? current.devices.map((device) => {
            const id = String(device.id);
            const active = selectedDevice === id;
            const state = stale ? "等待同步" : device.online !== true ? "离线" : deviceStateLabel(device.state, device.type);
            return <button type="button" disabled={busy} className={`home3d-device ${active ? "selected" : ""}`} key={id} aria-pressed={active} onClick={() => selectDevice(id)}>
              <span className="home3d-device-type">{typeName[String(device.type)] ?? "设备"}</span>
              <span className="home3d-device-copy"><strong>{value(device.name, id)}</strong><small>{state}</small></span>
              <span className={`home3d-device-dot ${!stale && device.online === true ? "on" : ""}`} />
            </button>;
          }) : <p className="home3d-empty">此房间暂无设备数据。</p>}
        </div>
        </section>
        {focusedDevice && <div className="home3d-device-detail"><span>当前设备</span><strong>{value(focusedDevice.name, String(focusedDevice.id))}</strong><p>{stale ? "等待家庭服务恢复后更新状态。" : deviceStateLabel(focusedDevice.state, focusedDevice.type)}</p>
          <DeviceControls key={String(focusedDevice.id)} device={focusedDevice} stale={stale} busy={busy} onCommand={(request) => execute(request, value(focusedDevice.name, String(focusedDevice.id)))} />
        {operation && operation.request.device_id === focusedDevice.id && <div className={`device-operation ${operation.result?.status ?? "pending"}`} role="status" aria-live="polite">
          <p>{operation.result?.message ?? "正在等待设备确认，请稍候…"}</p>
          {operation.result?.status === "unconfirmed" && <button type="button" disabled={busy || stale || !(home.devices ?? []).some((device) => device.id === operation.request.device_id && device.online === true)} onClick={() => void execute(operation.request, operation.name)}>重试本次操作</button>}
          {operation.result && <button type="button" className="device-operation-dismiss" onClick={() => setOperation(null)}>收起提示</button>}
        </div>}
        </div>}
        <div className="home3d-people"><div className="home3d-section-title"><h3>此处的人</h3><span>{stale ? "—" : current.people.length}</span></div><p>{stale ? "位置暂不可用" : current.people.length ? current.people.join("、") : "摄像头尚未确认有人在此房间"}</p></div>
        <p className="home3d-trust">人物仅显示可信的房间级位置。模型中的家具与站位是示意，不代表现实中的精确坐标。</p>
      </aside>
    </div>
  </div>;
}
