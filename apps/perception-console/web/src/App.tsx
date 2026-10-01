import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { display, json, post, remove, StatusResponse, VoiceEvent } from "./api";
import { canOpenRegistration, registrationGuidance, registrationStepLabel } from "./registration";
import { choiceForPreference, confirmedChoice, selectionPayload, selectionStateForRequest, SelectionStatus } from "./audio-device-ui";
import { deviceStateLabel } from "./device-labels";
import { speakerChip, speakerChipText } from "./speaker-chip";
import { previewIsStale, previewSourceKey } from "./preview";
import { VoiceEnrollment, VoiceEnrollmentResponse, enrollmentGuidance, enrollmentInstruction, enrollmentProgress, enrollmentSummary, startErrorGuidance } from "./voice-enrollment";
import Home3D from "./Home3D";

type Tab = "home" | "overview" | "vision" | "voice" | "identity" | "services";
type Camera = { kind: string; device_id?: string; label: string; width?: number; height?: number; fps?: number };
type Snapshot = { persons?: Array<Record<string, unknown>>; devices?: Array<Record<string, unknown>>; rooms?: Array<Record<string, unknown>> };
type VisionState = { camera_id?: string; camera_room_id?: string; session_id?: string | null; preview_at_ms?: number | null; mode?: string; model_state?: string; camera_state?: string; actual_mode?: { width: number; height: number; fps: number | null } | null; error_code?: string | number; message?: string; last_failure?: { type: string; module: string; function: string; line: number } | null };
type Registration = { person_id?: string; step?: string; step_index?: number; step_count?: number; accepted_samples?: number; required_samples?: number; state?: string; active?: boolean; message?: string; quality_reason?: string; action_progress?: number | null; rejection_counts?: Record<string, number> };
type PersonEntry = { id: string; display_name: string };
type AudioDevice = { index: number; name: string; hostapi: string; channels: number; is_system_default: boolean; is_selected: boolean; usable: boolean };
type AudioDevices = { inputs: AudioDevice[]; outputs: AudioDevice[]; preferences?: { input: { name: string; hostapi: string } | null; output: { name: string; hostapi: string } | null }; warnings?: { input?: string | null; output?: string | null }; selection?: SelectionStatus | null };

const tabs: Array<[Tab, string, string]> = [
  ["home", "我的家", "3D 全屋视图"],
  ["overview", "总览", "全屋状态"],
  ["vision", "视觉", "摄像头与监控"],
  ["voice", "语音", "对话与设备执行"],
  ["identity", "身份", "人脸与声纹"],
  ["services", "服务", "运行状态"],
];

const roomNames: Record<string, string> = { living_room: "客厅", bedroom: "卧室", kitchen: "厨房" };
const personNames: Record<string, string> = { dad: "爸爸", mom: "妈妈", child: "孩子" };

function visionFailureText(vision: VisionState): string {
  const failure = vision.last_failure;
  if (!failure) return display(vision.message, "视觉处理失败");
  return `${display(vision.message, "视觉处理失败")} · ${failure.module}:${failure.line} ${failure.function} (${failure.type})`;
}

function cameraModeText(mode: VisionState["actual_mode"]): string {
  if (!mode) return "尚未收到画面";
  return `${mode.width}×${mode.height} · ${mode.fps == null ? "帧率测量中" : `${mode.fps} 帧/秒（实测）`}`;
}

function useConsoleData() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [home, setHome] = useState<Snapshot>({});
  const [vision, setVision] = useState<VisionState>({});
  const [registration, setRegistration] = useState<Registration | null>(null);
  const [people, setPeople] = useState<PersonEntry[]>(Object.entries(personNames).map(([id, display_name]) => ({ id, display_name })));
  const [registeredIds, setRegisteredIds] = useState<string[]>([]);
  const [speakerIds, setSpeakerIds] = useState<string[]>([]);
  const [voiceEnrollment, setVoiceEnrollment] = useState<VoiceEnrollmentResponse>({});
  const [voiceEvents, setVoiceEvents] = useState<VoiceEvent[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [audioDevices, setAudioDevices] = useState<AudioDevices>({ inputs: [], outputs: [] });
  const [homeStale, setHomeStale] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const updates = [
      json<VisionState>("/api/vision/config").then(setVision),
      json<{ registration?: Registration | null }>("/api/vision/registration")
        .then((value) => setRegistration(value.registration ?? null), () => setRegistration(null)),
    ];
    const results = await Promise.allSettled(updates);
    const rejected = results.find((item) => item.status === "rejected");
    if (rejected?.status === "rejected") setError(rejected.reason?.message ?? "视觉服务暂不可用");
    else setError("");
  }, []);

  const refreshCameras = useCallback(async () => {
    try {
      const value = await json<{ data?: { cameras?: Camera[] } }>("/api/vision/cameras");
      setCameras(value.data?.cameras ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取摄像头");
    }
  }, []);

  const refreshPeople = useCallback(async () => {
    const [directory, enrolled] = await Promise.all([
      json<{ persons: PersonEntry[] }>("/api/home/persons"),
      json<{ person_ids: string[] }>("/api/vision/registrations"),
    ]);
    setPeople(directory.persons);
    setRegisteredIds(enrolled.person_ids);
  }, []);

  const refreshAudioDevices = useCallback(async () => {
    const value = await json<AudioDevices>("/api/voice/devices");
    setAudioDevices(value);
    return value;
  }, []);

  useEffect(() => {
    function poll<T>(path: string, onData: (value: T) => void, onFailure: () => void, interval = 1500) {
      let stopped = false;
      let delay = interval;
      let timer: number | undefined;
      let controller: AbortController | null = null;
      const tick = async () => {
        controller = new AbortController();
        const abortTimer = window.setTimeout(() => controller?.abort(), 4000);
        try {
          const value = await json<T>(path, { signal: controller.signal });
          if (!stopped) onData(value);
          delay = interval;
        } catch {
          if (!stopped) onFailure();
          delay = Math.min(delay * 2, 12000);
        } finally {
          window.clearTimeout(abortTimer);
          controller = null;
          if (!stopped) timer = window.setTimeout(() => void tick(), delay);
        }
      };
      void tick();
      return () => { stopped = true; if (timer !== undefined) window.clearTimeout(timer); controller?.abort(); };
    }
    const stop = [
      poll<StatusResponse>("/api/status", setStatus, () => setStatus(null)),
      poll<Snapshot>("/api/home/snapshot", (value) => { setHome(value); setHomeStale(false); }, () => setHomeStale(true)),
      poll<VisionState>("/api/vision/config", setVision, () => setVision({})),
      poll<{ registration?: Registration | null }>("/api/vision/registration", (value) => setRegistration(value.registration ?? null), () => setRegistration(null), 250),
      poll<{ persons: PersonEntry[] }>("/api/home/persons", (value) => setPeople(value.persons), () => {}, 3000),
      poll<{ person_ids: string[] }>("/api/vision/registrations", (value) => setRegisteredIds(value.person_ids), () => {}, 3000),
      poll<{ person_ids?: string[] }>("/api/vision/identity/speaker/status", (value) => setSpeakerIds(value.person_ids ?? []), () => {}, 3000),
      poll<VoiceEnrollmentResponse>("/api/voice/identity/enroll/voice/status", setVoiceEnrollment, () => setVoiceEnrollment({}), 700),
      poll<{ events?: VoiceEvent[] }>("/api/voice/history", (value) => setVoiceEvents(value.events ?? []), () => {}),
      poll<AudioDevices>("/api/voice/devices", setAudioDevices, () => {}, 10000),
    ];
    return () => stop.forEach((close) => close());
  }, []);

  useEffect(() => {
    const stream = new EventSource("/api/voice/events");
    const types = ["voice_state", "transcript", "agent_reply", "tool_result", "playback_state", "service_error"];
    const receive = (message: Event) => {
      try {
        const event = JSON.parse((message as MessageEvent).data) as VoiceEvent;
        setVoiceEvents((previous) => {
          if (previous.some((item) => item.id === event.id && item.timestamp === event.timestamp)) return previous;
          return [...previous, event].slice(-200);
        });
      } catch { /* A malformed event must not erase the current session history. */ }
    };
    types.forEach((type) => stream.addEventListener(type, receive));
    return () => { types.forEach((type) => stream.removeEventListener(type, receive)); stream.close(); };
  }, []);

  return { status, home, homeStale, vision, registration, people, registeredIds, speakerIds, voiceEnrollment, voiceEvents, cameras, audioDevices, error, refresh, refreshCameras, refreshPeople, refreshAudioDevices };
}

function ServiceDot({ state }: { state?: string }) {
  return <span className={`dot ${state ?? "down"}`} aria-label={state ?? "down"} />;
}

function App() {
  const data = useConsoleData();
  const [tab, setTab] = useState<Tab>("home");
  const [homeControlBusy, setHomeControlBusy] = useState(false);
  const active = tabs.find((item) => item[0] === tab)!;

  return (
    <div className="shell home-mode">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">⌂</span><div><strong>小屋感知中心</strong><small>LOCAL PERCEPTION</small></div></div>
        <nav>{tabs.map(([id, label, hint]) => <button key={id} disabled={homeControlBusy} className={tab === id ? "active" : ""} onClick={() => setTab(id)}><span>{label}</span><small>{hint}</small></button>)}</nav>
        <div className="sidebar-status"><ServiceDot state={data.status?.state} /><span>{data.status?.state === "up" ? "全部服务在线" : "部分服务需要处理"}</span></div>
      </aside>
      <main className={tab === "home" ? "home-main" : "console-main"}>
        {tab !== "home" && <header><div><p className="eyebrow">SMART HOME · 本机运行</p><h1>{active[1]}</h1><p>{active[2]}</p></div><div className="privacy">原始音视频不落盘</div></header>}
        {data.error && <div className="notice">{data.error}</div>}
        {tab === "home" && <Home3D home={data.home} stale={data.homeStale} onControlBusyChange={setHomeControlBusy} />}
        {tab === "overview" && <Overview {...data} />}
        {tab === "vision" && <Vision {...data} />}
        {tab === "voice" && <Voice events={data.voiceEvents} devices={data.audioDevices} stale={data.status?.services?.voice?.state !== "up"} refreshDevices={data.refreshAudioDevices} />}
        {tab === "identity" && <Identity registration={data.registration} vision={data.vision} cameras={data.cameras} people={data.people} registeredIds={data.registeredIds} speakerIds={data.speakerIds} voiceEnrollment={data.voiceEnrollment} refresh={data.refresh} refreshCameras={data.refreshCameras} refreshPeople={data.refreshPeople} visionAvailable={canOpenRegistration(data.status?.services?.vision?.state)} />}
        {tab === "services" && <Services status={data.status} />}
      </main>
    </div>
  );
}

function Overview({ status, home, homeStale, voiceEvents }: ReturnType<typeof useConsoleData>) {
  const latestTranscript = [...voiceEvents].reverse().find((item) => item.type === "transcript");
  const latestReply = [...voiceEvents].reverse().find((item) => item.type === "agent_reply");
  return <div className="stack">
    <section className="service-grid">{["home", "vision", "voice"].map((name) => <article className="card service-card" key={name}><ServiceDot state={status?.services?.[name]?.state} /><div><strong>{({ home: "家庭服务", vision: "视觉服务", voice: "语音服务" } as Record<string, string>)[name]}</strong><span>{status?.services?.[name]?.state === "up" ? name === "vision" && status?.services?.vision?.data?.mode === "idle" ? "服务在线 · 摄像头未开启" : "运行中" : status?.services?.[name]?.state === "degraded" ? "需要处理" : "未连接"}</span></div></article>)}</section>
    <section className="two-col"><article className="card"><CardTitle title="人物位置" subtitle={homeStale ? "历史快照 · home-service 暂不可用" : "由摄像头确认后同步到 Unity"} /><div className="person-list">{(home.persons ?? []).map((person) => <div className="person" key={String(person.id)}><span className="avatar">{personNames[String(person.id)]?.slice(0, 1) ?? "人"}</span><div><strong>{display(person.display_name, personNames[String(person.id)])}</strong><small>{person.location_known ? roomNames[String(person.room_id)] ?? display(person.room_id) : "位置未知"}</small></div><em>{display(person.pose, "—")}</em></div>)}</div></article>
    <article className="card"><CardTitle title="最近对话" subtitle="只保留当前运行会话" /><Dialogue event={latestTranscript} who="你" /><Dialogue event={latestReply} who="小屋" /></article></section>
    <section className="card"><CardTitle title="设备状态" subtitle={homeStale ? "历史快照 · 当前状态未知" : "来自家庭服务的实时状态"} /><div className="device-grid">{(home.devices ?? []).map((device) => <div className="device" key={String(device.id)}><strong>{display(device.name, String(device.id))}</strong><span className={device.online ? "online" : "offline"}>{device.online ? "在线" : "离线"}</span><small>{deviceStateLabel(device.state, device.type)}</small></div>)}</div></section>
  </div>;
}

function LivePreview({ active, sourceKey, previewAtMs, label }: { active: boolean; sourceKey: string; previewAtMs?: number | null; label: string }) {
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const stale = active && previewIsStale(previewAtMs, nowMs);
  useEffect(() => { setVisible(false); setFailed(false); setAttempt(0); }, [active, sourceKey]);
  useEffect(() => { if (stale) setVisible(false); }, [stale]);
  useEffect(() => {
    if (!active || !failed) return;
    const timer = window.setTimeout(() => { setFailed(false); setAttempt((value) => value + 1); }, 2500);
    return () => window.clearTimeout(timer);
  }, [active, failed, attempt]);
  const hasCurrentFrame = visible && !stale;
  return <div className={`preview ${hasCurrentFrame ? "has-frame" : ""}`}>
    {active && !failed && !stale && <img key={`${sourceKey}-${attempt}`} src={`/api/vision/preview.mjpeg?attempt=${attempt}`} alt={label} onLoad={() => { setVisible(true); setFailed(false); }} onError={() => { setVisible(false); setFailed(true); }} />}
    {!hasCurrentFrame && <span role="status">{active ? stale ? "画面已停滞，等待摄像头恢复…" : failed ? "画面连接中断，正在重试…" : "正在等待摄像头画面…" : "摄像头未开启"}</span>}
    {hasCurrentFrame && <span className="live-badge">镜像实时画面</span>}
  </div>;
}

function Vision({ vision, cameras, status, refresh, refreshCameras }: ReturnType<typeof useConsoleData>) {
  const [room, setRoom] = useState(vision.camera_room_id ?? "living_room");
  const [roomDirty, setRoomDirty] = useState(false);
  const [camera, setCamera] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!roomDirty && vision.camera_room_id) setRoom(vision.camera_room_id);
  }, [roomDirty, vision.camera_room_id]);
  const live = status?.services?.vision?.state === "up" && ["monitoring", "registering"].includes(vision.mode ?? "");
  const action = async (work: () => Promise<unknown>) => { setBusy(true); try { await work(); await refresh(); } catch (reason) { window.alert(reason instanceof Error ? reason.message : "操作失败"); } finally { setBusy(false); } };
  const applyRoom = async () => {
    await post("/api/vision/room/select", { room_id: room });
    const actual = await json<VisionState>("/api/vision/config");
    if (actual.mode !== "monitoring") await post("/api/vision/monitor/start");
    setRoomDirty(false);
  };
  return <div className="vision-layout"><section className="card preview-card"><LivePreview active={live} sourceKey={previewSourceKey(vision)} previewAtMs={vision.preview_at_ms} label="视觉服务实时预览" /><div className="preview-meta"><span>模式：{display(vision.mode)}</span><span>模型：{display(vision.model_state)}</span><span>摄像头：{display(vision.camera_state)}</span><span>实际画面：{cameraModeText(vision.actual_mode)}</span><span>当前监控房间：{roomNames[vision.camera_room_id ?? ""] ?? "未选择"}</span></div>{vision.mode === "error" && <div className="notice" role="alert">{visionFailureText(vision)}</div>}</section>
  <section className="card controls"><CardTitle title="视觉控制" subtitle="在画面中确认身份后，人物位置会同步到终端和 Unity" /><button className="secondary" disabled={busy} onClick={() => void refreshCameras()}>扫描摄像头</button><label>摄像头<select value={camera} onChange={(e) => setCamera(e.target.value)}><option value="">请选择</option>{cameras.map((item) => <option key={item.device_id} value={item.device_id}>{item.label}{item.width && item.height ? ` ${item.width}×${item.height} @${item.fps ?? 0}fps` : ""}</option>)}</select></label><button disabled={busy || !camera} onClick={() => void action(() => post("/api/vision/camera/select", { kind: "device", device_id: camera }))}>使用此摄像头</button><label>摄像头所在房间<select value={room} onChange={(e) => { setRoom(e.target.value); setRoomDirty(true); }}>{Object.entries(roomNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><button disabled={busy} onClick={() => void action(applyRoom)}>{vision.mode === "monitoring" ? "切换监控房间" : "开始监控"}</button><button className="secondary" disabled={busy} onClick={() => void action(() => post("/api/vision/monitor/pause"))}>暂停监控</button></section></div>;
}

function voiceEventText(item: VoiceEvent): string {
  if (item.type === "playback_state") {
    if (item.payload.source_event === "tts_start") return display(item.payload.barge_in_hint, "正在播报…");
    if (item.payload.source_event === "tts_interrupted") return "已打断播报，等待新命令";
    if (item.payload.source_event === "agent_interrupted") return "已打断处理，这条回复不再播报";
    if (item.payload.source_event === "tts_end") return item.payload.interrupted ? "播报已打断" : "播报完成";
  }
  return display(item.payload.message ?? item.payload.phrase ?? item.payload.source_event);
}

function Voice({ events, devices, stale, refreshDevices }: { events: VoiceEvent[]; devices: AudioDevices; stale: boolean; refreshDevices: () => Promise<AudioDevices> }) {
  // Oldest first, newest last: a conversation reads downward. The container
  // scrolls itself, so the newest line is the one you are already looking at.
  const visible = useMemo(() => {
    const keep = events.filter((item) => ["transcript", "agent_reply", "agent_delta", "tool_result", "playback_state", "service_error"].includes(item.type));
    // Each delta repeats the sentence so far, so only the newest one is ever
    // displayed; letting the rest through would push the conversation out of the
    // 80-item window during a single long reply.
    const newest = keep.reduce((at, item, index) => (item.type === "agent_delta" ? index : at), -1);
    return keep.filter((item, index) => item.type !== "agent_delta" || index === newest).slice(-80);
  }, [events]);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);
  useEffect(() => {
    const node = timelineRef.current;
    // Only follow when the reader is already at the bottom: scrolling up to read
    // history must not be yanked away by the next token.
    if (node && pinnedRef.current) node.scrollTop = node.scrollHeight;
  }, [visible.length]);
  const latestState = [...events].reverse().find((item) => item.type === "voice_state");
  const [inputChoice, setInputChoice] = useState("auto");
  const [outputChoice, setOutputChoice] = useState("auto");
  const [applying, setApplying] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [feedback, setFeedback] = useState("");
  useEffect(() => {
    if (applying || dirty) return;
    setInputChoice(choiceForPreference(devices.preferences?.input ?? null, devices.inputs));
    setOutputChoice(choiceForPreference(devices.preferences?.output ?? null, devices.outputs));
  }, [devices, applying, dirty]);
  const apply = async () => {
    setApplying(true); setFeedback("正在切换音频设备…");
    try {
      const request = await post<{ request_id?: string }>("/api/voice/devices/select", selectionPayload(inputChoice, outputChoice));
      if (!request.request_id) throw new Error("语音服务尚未支持切换结果确认，请重启语音服务后重试。");
      const deadline = Date.now() + 30000;
      while (Date.now() < deadline) {
        const updated = await refreshDevices();
        const state = selectionStateForRequest(request.request_id, updated.selection);
        if (state === "failed") {
          const code = updated.selection?.error_code ?? "audio_device_switch_failed";
          throw new Error(`设备切换失败（${code}），原设备仍在使用；请选择其他设备重试。`);
        }
        if (state === "applied") {
          const inputConfirmed = confirmedChoice(inputChoice, updated.preferences?.input ?? null, updated.inputs.find((item) => item.is_selected));
          const outputConfirmed = confirmedChoice(outputChoice, updated.preferences?.output ?? null, updated.outputs.find((item) => item.is_selected));
          setFeedback(inputConfirmed && outputConfirmed ? "设备已切换。" : "服务报告已应用，但当前使用设备与选择不一致，请检查服务状态。");
          if (inputConfirmed && outputConfirmed) setDirty(false);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
      setFeedback("设备仍在应用中，请留意正在使用的设备和服务状态。");
    } catch (reason) { setFeedback(reason instanceof Error ? reason.message : "设备切换失败，请重试"); }
    finally { setApplying(false); }
  };
  const selectedInput = devices.inputs.find((item) => item.is_selected);
  const selectedOutput = devices.outputs.find((item) => item.is_selected);
  const transcript = visible.filter((item) => item.type === "transcript" || item.type === "agent_reply");
  // A delta carries the whole sentence so far, so only the newest one matters,
  // and it stops being shown the moment the finished reply lands.
  const lastDeltaAt = visible.reduce((at, item, index) => (item.type === "agent_delta" ? index : at), -1);
  const lastReplyAt = visible.reduce((at, item, index) => (item.type === "agent_reply" ? index : at), -1);
  const streaming = lastDeltaAt > lastReplyAt ? String(visible[lastDeltaAt].payload.text ?? "") : "";
  const tail = visible[visible.length - 1];
  const awaiting = !streaming && Boolean(tail) && (tail.type === "transcript" || tail.type === "tool_result");
  const choices = (list: AudioDevice[]) => list.filter((item) => item.usable && item.hostapi !== "Windows WDM-KS").map((item) => <option key={item.index} value={item.index}>{item.name} · {item.hostapi}{item.is_system_default ? " · 系统默认" : ""}</option>);
  return <div className="stack">{stale && <div className="notice">语音服务尚未就绪；下方对话是本次页面保留的历史记录。设备列表可用于排查和重选。</div>}
    <section className="card audio-panel"><CardTitle title="语音与设备" subtitle={display(latestState?.payload.state, "等待语音服务")} />
      {(devices.warnings?.input || devices.warnings?.output) && <div className="notice" role="status">此前手动选择的{[devices.warnings?.input && "麦克风", devices.warnings?.output && "扬声器"].filter(Boolean).join("和")}当前不可用，语音服务已尝试自动回退。请核对下方实际使用的设备。</div>}
      <div className="audio-current"><div><span>正在使用 · 麦克风</span><strong>{selectedInput?.name ?? "尚未连接"}</strong><small>{selectedInput?.hostapi ?? "请检查语音服务"}</small></div><div><span>正在使用 · 扬声器</span><strong>{selectedOutput?.name ?? "尚未连接"}</strong><small>{selectedOutput?.hostapi ?? "请检查语音服务"}</small></div></div>
      <div className="audio-choice"><label htmlFor="input-device">麦克风<select id="input-device" value={inputChoice} onChange={(event) => { setInputChoice(event.target.value); setDirty(true); }} disabled={applying || !devices.inputs.length}><option value="auto">跟随系统当前设备（自动跳过虚拟设备）</option>{choices(devices.inputs)}</select></label><label htmlFor="output-device">扬声器<select id="output-device" value={outputChoice} onChange={(event) => { setOutputChoice(event.target.value); setDirty(true); }} disabled={applying || !devices.outputs.length}><option value="auto">跟随系统当前设备（自动跳过虚拟设备）</option>{choices(devices.outputs)}</select></label><button disabled={applying || !devices.inputs.length || !devices.outputs.length} onClick={() => void apply()}>{applying ? "应用中…" : "应用设备选择"}</button></div>
      {feedback && <p className="audio-feedback" role="status">{feedback}</p>}
    </section>
    <div className="two-col voice-layout"><section className="card dialogue-card"><CardTitle title="实时对话" subtitle="播报时说唤醒词，等播报停止后说新命令" /><div className="timeline" ref={timelineRef} onScroll={(event) => { const node = event.currentTarget; pinnedRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 32; }}>
      {!transcript.length && !streaming && !awaiting && <p className="timeline-empty">还没有对话。说“小屋小屋”唤醒后开始说话。</p>}
      {transcript.map((item) => <Dialogue key={item.id} event={item} who={item.type === "transcript" ? "你" : "小屋"} />)}
      {awaiting && <div className="dialogue assistant"><strong>小屋</strong><p className="thinking">正在思考…</p></div>}
      {streaming && <div className="dialogue assistant streaming"><strong>小屋</strong><p>{streaming}<span className="caret" /></p><small>正在回答…</small></div>}
    </div></section><section className="card"><CardTitle title="执行与播报" subtitle="只根据服务确认结果显示成功" /><div className="event-list">{visible.filter((item) => !["transcript", "agent_reply", "agent_delta"].includes(item.type)).map((item) => <div className={`event ${item.type}`} key={item.id}><strong>{item.type === "tool_result" ? `设备操作 · ${item.payload.ok === true ? "成功" : "未成功"}` : item.type === "playback_state" ? "语音播报" : "状态"}</strong><span>{voiceEventText(item)}</span><small>{new Date(item.timestamp).toLocaleTimeString()}</small></div>)}</div></section></div></div>;
}

function Identity({ registration, vision, cameras, people, registeredIds, speakerIds, voiceEnrollment, refresh, refreshCameras, refreshPeople, visionAvailable }: {
  registration: Registration | null;
  vision: VisionState;
  cameras: Camera[];
  people: PersonEntry[];
  registeredIds: string[];
  speakerIds: string[];
  voiceEnrollment: VoiceEnrollmentResponse;
  refresh: () => Promise<void>;
  refreshCameras: () => Promise<void>;
  refreshPeople: () => Promise<void>;
  visionAvailable: boolean;
}) {
  const [busy, setBusy] = useState("");
  const [person, setPerson] = useState<string | null>(registration?.person_id ?? null);
  const [newName, setNewName] = useState("");
  const [selectedCamera, setSelectedCamera] = useState("");
  const [feedback, setFeedback] = useState("");
  useEffect(() => { if (!registration?.active) void refreshCameras(); }, [refreshCameras, registration?.active]);
  useEffect(() => {
    if (registration?.person_id && (registration.active || registration.state === "completed")) setPerson(registration.person_id);
  }, [registration?.active, registration?.person_id, registration?.state]);
  useEffect(() => {
    if (vision.camera_id?.startsWith("device:")) setSelectedCamera(vision.camera_id.slice(7));
  }, [vision.camera_id]);
  const run = async (id: string, work: () => Promise<unknown>) => {
    setBusy(id); setFeedback("");
    try { await work(); await refresh(); }
    catch (reason) { setFeedback(reason instanceof Error ? reason.message : "操作失败，请重试"); }
    finally { setBusy(""); }
  };
  const begin = (id: string) => {
    setPerson(id);
    setFeedback("");
    if (vision.camera_id) void run(id, () => post("/api/vision/registration/start", { person_id: id }));
  };
  const addPerson = async () => {
    const name = newName.trim();
    if (!name) { setFeedback("请先填写人物称呼"); return; }
    setBusy("add"); setFeedback("");
    try {
      const result = await post<{ person: PersonEntry }>("/api/home/persons", { display_name: name });
      await refreshPeople();
      setNewName("");
      setPerson(result.person.id);
      setFeedback(`已添加${result.person.display_name}，请选择摄像头开始录入人脸。`);
    } catch (reason) { setFeedback(reason instanceof Error ? reason.message : "添加人物失败"); }
    finally { setBusy(""); }
  };
  const chooseCamera = () => {
    if (!selectedCamera || !person) return;
    if (registration?.active && !window.confirm("更换摄像头会取消当前注册进度，确定继续吗？")) return;
    void run("camera", async () => {
      if (registration?.active) await post("/api/vision/registration/cancel");
      await post("/api/vision/camera/select", { kind: "device", device_id: selectedCamera });
      await post("/api/vision/registration/start", { person_id: person });
    });
  };
  const returnToList = async () => {
    if (registration?.active) {
      if (!window.confirm("离开注册画面会取消当前采集，确定返回吗？")) return;
      setBusy("return");
      try { await post("/api/vision/registration/cancel"); await refresh(); }
      catch (reason) { setFeedback(reason instanceof Error ? reason.message : "无法取消注册，请重试"); setBusy(""); return; }
      setBusy("");
    }
    setPerson(null);
  };
  const current = person && registration?.person_id === person ? registration : null;
  const personName = people.find((item) => item.id === person)?.display_name ?? person ?? "人物";
  const completed = current?.state === "completed";
  const previewActive = visionAvailable && !!current?.active && vision.mode === "registering";
  const rejectionCounts = Object.entries(current?.rejection_counts ?? {}).sort((a, b) => b[1] - a[1]);
  const guidance = current?.active
    ? registrationGuidance(current.quality_reason,
      current.state === "confirming" ? "动作采集完成，请正视镜头并保持睁眼，正在验证识别结果。" : "请在实时画面里确认自己已入镜，再按当前步骤完成动作。",
      current.state === "confirming" ? undefined : current.step)
    : current ? registrationGuidance(current.quality_reason, current.state === "completed" ? "注册已完成。" : "本次注册已结束。", current.step) : "选好摄像头后即可开始。";
  return <div className="stack">
    <section className="card add-person"><CardTitle title="添加人物" subtitle="填写称呼后，该人物会同时出现在身份名单和 Unity 场景中" /><div className="add-person-row"><input aria-label="人物称呼" value={newName} maxLength={40} placeholder="例如：姑姑、舅舅" onChange={(event) => setNewName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void addPerson(); }} /><button disabled={!!busy || !newName.trim()} onClick={() => void addPerson()}>添加人物</button></div>{feedback && !person && <div className="notice" role="alert">{feedback}</div>}</section>
    <section className="identity-grid">{people.map(({ id, display_name: name }) => <article className={`card identity ${person === id ? "selected" : ""}`} key={id}><span className="avatar large">{name[0]}</span><h2>{name}</h2><p>{registeredIds.includes(id) ? "人脸已录入" : "人脸未录入"}</p><button disabled={!!busy || !visionAvailable || !!registration?.active} onClick={() => begin(id)}>{registeredIds.includes(id) ? "重新录入" : "录入人脸"}</button><button className="danger" disabled={!!busy || !!registration?.active || !registeredIds.includes(id)} onClick={() => { if (window.confirm(`确定删除${name}的人脸特征？此操作无法撤销。`)) void run(id, async () => { await remove(`/api/vision/registration/${id}`); await refreshPeople(); }); }}>删除人脸</button><div className="voiceprint"><VoiceprintPanel personId={id} personName={name} enrolled={speakerIds.includes(id)} response={voiceEnrollment} busy={!!busy} onStart={() => void run(id, () => post("/api/voice/identity/enroll/voice/start", { person_id: id }))} onCancel={() => void run("voice-cancel", () => post("/api/voice/identity/enroll/voice/cancel"))} /></div></article>)}</section>
    {person && <section className="card registration-workspace" aria-label="人脸注册工作区">
      <div className="registration-heading"><CardTitle title={`为${personName}录入人脸`} subtitle={completed ? "注册结果已保存" : "请从实时画面确认摄像头拍到了你，再按步骤完成动作"} /><button className="secondary" disabled={!!busy} onClick={() => void returnToList()}>返回身份列表</button></div>
      {feedback && <div className="notice" role="alert">{feedback}</div>}
      {vision.mode === "error" && <div className="notice" role="alert">{visionFailureText(vision)}{vision.error_code != null ? `（代码 ${vision.error_code}）` : ""}。可重新选择摄像头后重试。</div>}
      {completed && <div className="registration-success" role="status"><strong>✓ {personName}的人脸已录入成功</strong><span>采集和识别确认均已完成。现在可以到“视觉”页开启监控；识别到该人物时，Unity 会更新所在房间。</span></div>}
      <div className="registration-layout"><div>{completed ? <div className="preview-finished">人脸录入已结束 · 摄像头画面已关闭</div> : <LivePreview active={previewActive} sourceKey={previewSourceKey(vision)} previewAtMs={vision.preview_at_ms} label="人脸注册实时摄像头画面" />}<div className="preview-meta"><span>当前摄像头：{vision.camera_id ?? "尚未选择"}</span><span>实际画面：{cameraModeText(vision.actual_mode)}</span><span>画面状态：{completed ? "录入完成" : previewActive ? "采集中" : "未采集"}</span></div></div>
      <div className="registration-side"><label htmlFor="registration-camera">选择摄像头</label><select id="registration-camera" value={selectedCamera} disabled={!!busy} onChange={(event) => setSelectedCamera(event.target.value)}><option value="">请选择摄像头</option>{selectedCamera && !cameras.some((item) => item.device_id === selectedCamera) && <option value={selectedCamera}>当前摄像头 {selectedCamera}</option>}{cameras.map((item) => <option key={item.device_id} value={item.device_id}>{item.label} · {item.width}×{item.height}</option>)}</select><button className="secondary" disabled={!!busy || !!current?.active} onClick={() => void refreshCameras()}>重新扫描摄像头</button><button disabled={!!busy || !selectedCamera || !visionAvailable} onClick={chooseCamera}>{current?.active ? "切换摄像头并重新开始" : "使用此摄像头并开始"}</button>
        {current && !completed && <><div className="step-count">步骤 {current.step_index ?? 0} / {current.step_count ?? 6}</div><h3>{registrationStepLabel(current.step)}</h3><div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={current.step_count ?? 6} aria-valuenow={current.step_index ?? 0}><i style={{ width: `${Math.max(0, Math.min(100, ((current.step_index ?? 0) / (current.step_count ?? 6)) * 100))}%` }} /></div><p>本步合格样本：{current.accepted_samples ?? 0} / {current.required_samples ?? 0}</p>{typeof current.action_progress === "number" && <p>点头动作进度：{Math.round(Math.max(0, Math.min(1, current.action_progress)) * 100)}%</p>}<p className="guidance" role="status">{guidance}</p>{rejectionCounts.length > 0 && <details className="quality-breakdown"><summary>本次未通过原因（按帧计数）</summary><ul>{rejectionCounts.map(([reason, count]) => <li key={reason}>{registrationGuidance(reason, undefined, current.step).split("。")[0]}：{count} 帧</li>)}</ul></details>}<button className="secondary" disabled={!!busy || !current.active} onClick={() => void run("cancel", () => post("/api/vision/registration/cancel"))}>取消本次注册</button></>}
      </div></div>
    </section>}
  </div>;
}

function Services({ status }: { status: StatusResponse | null }) {
  const recovery: Record<string, string> = {
    audio_input_unavailable: "麦克风不可用，请到语音页选择其他设备",
    audio_output_unavailable: "扬声器不可用，请到语音页选择其他设备",
    audio_input_open_failed: "麦克风打开失败，请到语音页更换设备",
    voice_model_unavailable: "语音模型加载失败，请检查本机模型文件",
    agent_key_missing: "语音助手密钥缺失，请检查本地配置",
  };
  return <section className="card">
    <CardTitle title="本地服务" subtitle="进程、模型和摄像头状态分别检查" />
    <div className="service-list">{Object.entries(status?.services ?? {}).map(([name, item]) =>
      <div key={name}><ServiceDot state={item.state} /><strong>{({ home: "家庭服务", vision: "视觉服务", voice: "语音服务" } as Record<string, string>)[name] ?? name}</strong>
        <code>{name === "home" ? ":8765" : name === "vision" ? ":8766" : ":8767"}</code>
        <span>{item.state === "up" ? name === "vision" && item.data?.mode === "idle" ? "服务在线 · 摄像头未开启" : "运行中" : item.state === "degraded" ? "需要处理" : "未连接"}{item.message ? ` · ${item.message}` : item.state === "degraded" ? ` · ${recovery[String(item.error_code ?? item.data?.error_code)] ?? display(item.data?.model_error ?? item.data?.mode, "状态异常")}` : ""}{item.state === "degraded" && item.error_code ? `（${item.error_code}）` : ""}
          <small>最近响应：{item.last_success_at_ms ? new Date(item.last_success_at_ms).toLocaleString("zh-CN") : "尚无"}{name === "vision" && typeof item.data?.last_frame_at_ms === "number" ? ` · 最近画面：${new Date(item.data.last_frame_at_ms).toLocaleString("zh-CN")}` : ""}{name === "home" && item.data?.backend === "ha" ? ` · 最近连接 HA：${typeof item.data?.upstream_last_connected_at_ms === "number" ? new Date(item.data.upstream_last_connected_at_ms).toLocaleString("zh-CN") : "尚无"}` : ""}</small>
        </span>
      </div>)}
      <div><ServiceDot state={status ? "up" : "down"} /><strong>感知终端</strong><code>:8770</code><span>{status ? "运行中" : "未连接"}</span></div>
    </div>
    <div className="service-note">启动：tools\start-perception.ps1 · 停止：tools\stop-perception.ps1。需要重启时先停止再启动；停止后会检查四个服务端口。</div>
  </section>;
}

function CardTitle({ title, subtitle }: { title: string; subtitle: string }) { return <div className="card-title"><div><h2>{title}</h2><p>{subtitle}</p></div></div>; }

/**
 * Voiceprint enrolment for one person.
 *
 * Samples are spoken, not uploaded: the microphone belongs to the voice loop, so
 * this can only start, watch and cancel. "Not configured" is deliberately a
 * different message from "not enrolled", because the fix is different too.
 */
function VoiceprintPanel({ personId, personName, enrolled, response, busy, onStart, onCancel }: {
  personId: string; personName: string; enrolled: boolean; response: VoiceEnrollmentResponse; busy: boolean;
  onStart: () => void; onCancel: () => void;
}) {
  const configured = response.configured !== false;
  const enrollment: VoiceEnrollment | undefined = response.enrollment;
  const mine = enrollment && enrollment.person_id === personId ? enrollment : undefined;
  const collecting = !!mine?.active;
  const otherActive = !!enrollment?.active && enrollment.person_id !== personId;
  const failure = enrollment && enrollment.person_id === personId && enrollment.state === "failed" ? enrollment.last_reason : null;
  const guidance = enrollmentGuidance(mine?.last_reason) ?? startErrorGuidance(failure);
  // No session for this person is the normal state, so there is nothing to say.
  const summary = enrollmentSummary(mine);
  return <>
    <div className="voiceprint-head"><strong>声纹</strong><span>{configured ? (enrolled ? "已录入" : "未录入") : "未启用 · 未配置声纹模型"}</span></div>
    {configured && <>
      {summary && <p className="voiceprint-state" role="status">{summary}{collecting && <i> · 每句不少于 {mine?.min_seconds} 秒</i>}</p>}
      {collecting && <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={mine?.required ?? 0} aria-valuenow={mine?.accepted ?? 0}><i style={{ width: `${enrollmentProgress(mine) * 100}%` }} /></div>}
      {collecting && guidance && <p className="guidance" role="status">{guidance}</p>}
      <p className="voiceprint-help">{collecting ? enrollmentInstruction(mine) : enrolled ? "已录入。重新录入会用新样本整体替换，不会叠加。" : enrollmentInstruction(mine)}</p>
      <div className="voiceprint-actions">
        <button disabled={busy || collecting || otherActive} onClick={onStart}>{enrolled ? "重新录入声纹" : `为${personName}录入声纹`}</button>
        <button className="secondary" disabled={busy || !collecting} onClick={onCancel}>取消录入</button>
      </div>
      {otherActive && <p className="voiceprint-help">正在为另一位家人录入声纹，请先完成或取消。</p>}
      {failure && <p className="guidance" role="status">{guidance ?? failure}</p>}
    </>}
  </>;
}

function Dialogue({ event, who }: { event?: VoiceEvent; who: string }) { const text = event?.payload.transcript ?? event?.payload.text ?? event?.payload.message; const chip = who === "你" && event ? speakerChip(event.payload) : null; return <div className={`dialogue ${who === "你" ? "user" : "assistant"}`}><strong>{who}{chip && <span className={`speaker-chip ${chip.tone}`} title={chip.title}>{speakerChipText(chip)}</span>}</strong><p>{display(text, "暂无内容")}</p>{event && <small>{new Date(event.timestamp).toLocaleTimeString()}</small>}</div>; }

export default App;
