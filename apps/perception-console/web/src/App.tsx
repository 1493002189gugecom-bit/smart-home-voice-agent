import { useCallback, useEffect, useMemo, useState } from "react";
import { display, json, post, remove, StatusResponse, VoiceEvent } from "./api";

type Tab = "overview" | "vision" | "voice" | "identity" | "services";
type Camera = { kind: string; device_id?: string; label: string; width?: number; height?: number; fps?: number };
type Snapshot = { persons?: Array<Record<string, unknown>>; devices?: Array<Record<string, unknown>>; rooms?: Array<Record<string, unknown>> };
type VisionState = { camera_id?: string; camera_room_id?: string; mode?: string; model_state?: string; camera_state?: string; error_code?: string };
type Registration = { person_id?: string; step?: string; step_index?: number; step_count?: number; accepted_samples?: number; required_samples?: number; state?: string; quality_reason?: string };

const tabs: Array<[Tab, string, string]> = [
  ["overview", "总览", "全屋状态"],
  ["vision", "视觉", "摄像头与监控"],
  ["voice", "语音", "对话与设备执行"],
  ["identity", "身份", "人脸与未来声纹"],
  ["services", "服务", "运行状态"],
];

const roomNames: Record<string, string> = { living_room: "客厅", bedroom: "卧室", kitchen: "厨房" };
const personNames: Record<string, string> = { dad: "爸爸", mom: "妈妈", child: "孩子" };

function useConsoleData() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [home, setHome] = useState<Snapshot>({});
  const [vision, setVision] = useState<VisionState>({});
  const [registration, setRegistration] = useState<Registration | null>(null);
  const [voiceEvents, setVoiceEvents] = useState<VoiceEvent[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([
      json<StatusResponse>("/api/status"),
      json<Snapshot>("/api/home/snapshot"),
      json<VisionState>("/api/vision/config"),
      json<{ registration?: Registration }>("/api/vision/registration"),
      json<{ events?: VoiceEvent[] }>("/api/voice/history"),
    ]);
    if (results[0].status === "fulfilled") setStatus(results[0].value);
    if (results[1].status === "fulfilled") setHome(results[1].value);
    if (results[2].status === "fulfilled") setVision(results[2].value);
    if (results[3].status === "fulfilled") setRegistration(results[3].value.registration ?? null);
    if (results[4].status === "fulfilled") setVoiceEvents(results[4].value.events ?? []);
    const rejected = results.find((item) => item.status === "rejected");
    setError(rejected?.status === "rejected" ? rejected.reason?.message ?? "部分服务暂不可用" : "");
  }, []);

  const refreshCameras = useCallback(async () => {
    try {
      const value = await json<{ data?: { cameras?: Camera[] } }>("/api/vision/cameras");
      setCameras(value.data?.cameras ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取摄像头");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { status, home, vision, registration, voiceEvents, cameras, error, refresh, refreshCameras };
}

function ServiceDot({ state }: { state?: string }) {
  return <span className={`dot ${state ?? "down"}`} aria-label={state ?? "down"} />;
}

function App() {
  const data = useConsoleData();
  const [tab, setTab] = useState<Tab>("overview");
  const active = tabs.find((item) => item[0] === tab)!;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">⌂</span><div><strong>小屋感知中心</strong><small>LOCAL PERCEPTION</small></div></div>
        <nav>{tabs.map(([id, label, hint]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}><span>{label}</span><small>{hint}</small></button>)}</nav>
        <div className="sidebar-status"><ServiceDot state={data.status?.state} /><span>{data.status?.state === "up" ? "全部服务在线" : "部分服务需要处理"}</span></div>
      </aside>
      <main>
        <header><div><p className="eyebrow">SMART HOME · 本机运行</p><h1>{active[1]}</h1><p>{active[2]}</p></div><div className="privacy">原始音视频不落盘</div></header>
        {data.error && <div className="notice">{data.error}</div>}
        {tab === "overview" && <Overview {...data} />}
        {tab === "vision" && <Vision {...data} />}
        {tab === "voice" && <Voice events={data.voiceEvents} />}
        {tab === "identity" && <Identity registration={data.registration} refresh={data.refresh} />}
        {tab === "services" && <Services status={data.status} />}
      </main>
    </div>
  );
}

function Overview({ status, home, voiceEvents }: ReturnType<typeof useConsoleData>) {
  const latestTranscript = [...voiceEvents].reverse().find((item) => item.type === "transcript");
  const latestReply = [...voiceEvents].reverse().find((item) => item.type === "agent_reply");
  return <div className="stack">
    <section className="service-grid">{["home", "vision", "voice"].map((name) => <article className="card service-card" key={name}><ServiceDot state={status?.services?.[name]?.state} /><div><strong>{name}-service</strong><span>{status?.services?.[name]?.state ?? "检查中"}</span></div></article>)}</section>
    <section className="two-col"><article className="card"><CardTitle title="人物位置" subtitle="由摄像头确认后同步到 Unity" /><div className="person-list">{(home.persons ?? []).map((person) => <div className="person" key={String(person.id)}><span className="avatar">{personNames[String(person.id)]?.slice(0, 1) ?? "人"}</span><div><strong>{display(person.display_name, personNames[String(person.id)])}</strong><small>{person.location_known ? roomNames[String(person.room_id)] ?? display(person.room_id) : "位置未知"}</small></div><em>{display(person.pose, "—")}</em></div>)}</div></article>
    <article className="card"><CardTitle title="最近对话" subtitle="只保留当前运行会话" /><Dialogue event={latestTranscript} who="你" /><Dialogue event={latestReply} who="小屋" /></article></section>
    <section className="card"><CardTitle title="设备状态" subtitle="来自 home-service 权威快照" /><div className="device-grid">{(home.devices ?? []).map((device) => <div className="device" key={String(device.id)}><strong>{display(device.name, String(device.id))}</strong><span className={device.online ? "online" : "offline"}>{device.online ? "在线" : "离线"}</span><small>{display(device.state)}</small></div>)}</div></section>
  </div>;
}

function Vision({ vision, cameras, refresh, refreshCameras }: ReturnType<typeof useConsoleData>) {
  const [room, setRoom] = useState(vision.camera_room_id ?? "living_room");
  const [camera, setCamera] = useState("");
  const [busy, setBusy] = useState(false);
  const [frame, setFrame] = useState(Date.now());
  useEffect(() => { const timer = window.setInterval(() => setFrame(Date.now()), 150); return () => clearInterval(timer); }, []);
  const action = async (work: () => Promise<unknown>) => { setBusy(true); try { await work(); await refresh(); } finally { setBusy(false); } };
  return <div className="vision-layout"><section className="card preview-card"><div className="preview"><img src={`/api/vision/preview.jpg?t=${frame}`} alt="视觉服务实时预览" onError={(event) => { event.currentTarget.style.visibility = "hidden"; }} onLoad={(event) => { event.currentTarget.style.visibility = "visible"; }} /><span>等待监控或注册画面</span></div><div className="preview-meta"><span>模式：{display(vision.mode)}</span><span>模型：{display(vision.model_state)}</span><span>摄像头：{display(vision.camera_state)}</span></div></section>
  <section className="card controls"><CardTitle title="视觉控制" subtitle="摄像头由 vision-service 独占" /><button className="secondary" disabled={busy} onClick={() => void refreshCameras()}>扫描摄像头</button><label>摄像头<select value={camera} onChange={(e) => setCamera(e.target.value)}><option value="">请选择</option>{cameras.map((item) => <option key={item.device_id} value={item.device_id}>{item.label} {item.width}×{item.height} @{item.fps}fps</option>)}</select></label><button disabled={busy || !camera} onClick={() => void action(() => post("/api/vision/camera/select", { kind: "device", device_id: camera }))}>使用此摄像头</button><label>逻辑房间<select value={room} onChange={(e) => setRoom(e.target.value)}>{Object.entries(roomNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><button disabled={busy} onClick={() => void action(async () => { await post("/api/vision/room/select", { room_id: room }); await post("/api/vision/monitor/start"); })}>开始监控</button><button className="secondary" disabled={busy} onClick={() => void action(() => post("/api/vision/monitor/pause"))}>暂停监控</button></section></div>;
}

function Voice({ events }: { events: VoiceEvent[] }) {
  const visible = useMemo(() => events.filter((item) => ["transcript", "agent_reply", "tool_result", "playback_state", "service_error"].includes(item.type)).slice(-80).reverse(), [events]);
  return <div className="two-col voice-layout"><section className="card"><CardTitle title="实时对话" subtitle="用户转写与助手回复" /><div className="timeline">{visible.filter((item) => item.type === "transcript" || item.type === "agent_reply").map((item) => <Dialogue key={item.id} event={item} who={item.type === "transcript" ? "你" : "小屋"} />)}</div></section><section className="card"><CardTitle title="执行与播报" subtitle="只根据服务确认结果显示成功" /><div className="event-list">{visible.filter((item) => item.type !== "transcript" && item.type !== "agent_reply").map((item) => <div className={`event ${item.type}`} key={item.id}><strong>{item.type === "tool_result" ? "设备操作" : item.type === "playback_state" ? "语音播报" : "状态"}</strong><span>{display(item.payload.message ?? item.payload.phrase ?? item.payload.source_event)}</span><small>{new Date(item.timestamp).toLocaleTimeString()}</small></div>)}</div></section></div>;
}

function Identity({ registration, refresh }: { registration: Registration | null; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const run = async (id: string, work: () => Promise<unknown>) => { setBusy(id); try { await work(); await refresh(); } finally { setBusy(""); } };
  return <div className="stack"><section className="identity-grid">{Object.entries(personNames).map(([id, name]) => <article className="card identity" key={id}><span className="avatar large">{name[0]}</span><h2>{name}</h2><p>人脸身份</p><button disabled={!!busy} onClick={() => void run(id, () => post("/api/vision/registration/start", { person_id: id }))}>注册 / 重新录入</button><button className="danger" disabled={!!busy} onClick={() => void run(id, () => remove(`/api/vision/registration/${id}`))}>删除人脸</button><div className="voiceprint"><strong>声纹</strong><span>尚未启用</span></div></article>)}</section>{registration && <section className="card registration"><CardTitle title="注册进度" subtitle={personNames[registration.person_id ?? ""] ?? display(registration.person_id)} /><div className="progress"><i style={{ width: `${Math.max(0, Math.min(100, ((registration.step_index ?? 0) / (registration.step_count ?? 1)) * 100))}%` }} /></div><p>步骤 {registration.step_index}/{registration.step_count} · {display(registration.step)} · 样本 {registration.accepted_samples}/{registration.required_samples}</p><p>{display(registration.quality_reason, "请按提示面对摄像头")}</p><button className="secondary" onClick={() => void run("cancel", () => post("/api/vision/registration/cancel"))}>取消注册</button></section>}</div>;
}

function Services({ status }: { status: StatusResponse | null }) {
  return <section className="card"><CardTitle title="本地服务" subtitle="桌面启动器只管理自己创建的进程" /><div className="service-list">{Object.entries(status?.services ?? {}).map(([name, item]) => <div key={name}><ServiceDot state={item.state} /><strong>{name}-service</strong><code>{name === "home" ? ":8765" : name === "vision" ? ":8766" : ":8767"}</code><span>{item.state}</span></div>)}<div><ServiceDot state={status ? "up" : "down"} /><strong>perception-console</strong><code>:8770</code><span>{status ? "up" : "down"}</span></div></div><div className="service-note">服务重启功能由 Windows 启动器提供。网页不会任意终止系统进程。</div></section>;
}

function CardTitle({ title, subtitle }: { title: string; subtitle: string }) { return <div className="card-title"><div><h2>{title}</h2><p>{subtitle}</p></div></div>; }
function Dialogue({ event, who }: { event?: VoiceEvent; who: string }) { const text = event?.payload.transcript ?? event?.payload.text ?? event?.payload.message; return <div className={`dialogue ${who === "你" ? "user" : "assistant"}`}><strong>{who}</strong><p>{display(text, "暂无内容")}</p>{event && <small>{new Date(event.timestamp).toLocaleTimeString()}</small>}</div>; }

export default App;
