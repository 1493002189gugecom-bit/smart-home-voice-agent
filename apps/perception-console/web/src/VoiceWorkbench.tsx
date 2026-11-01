import { useEffect, useRef, useState } from "react";
import { json, post, type VoiceEvent } from "./api";

const labels: Record<string, string> = { asr: "语音转写", speaker: "识别说话人", intent: "理解意图", agent: "处理与确认设备", tts_synthesis: "合成语音", playback: "播放回复" };
type Evaluation = { status: string; total?: number; results: Array<{ case_id: string; text: string; expected: string | null; observed: { kind: string; reason?: string }; passed: boolean | null; duration_ms: number }>; summary: { completed?: number; scored?: number; passed?: number; false_triggers?: number; unavailable?: number; p50_ms?: number; p95_ms?: number } };

export default function VoiceWorkbench({ events, stale, capabilities }: { events: VoiceEvent[]; stale: boolean; capabilities?: Record<string, unknown> }) {
  const [feedback, setFeedback] = useState("");
  const [stopping, setStopping] = useState(false);
  const [open, setOpen] = useState(false);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [evaluationBusy, setEvaluationBusy] = useState(false);
  const [sample, setSample] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!open || stale) return;
    let disposed = false;
    let timer: number;
    const refresh = async () => {
      try {
        const value = await json<Evaluation>("/api/voice/intent/evaluation");
        if (!disposed) setEvaluation(value);
      } catch (error) {
        if (!disposed) setFeedback(error instanceof Error ? error.message : "暂时无法获取评测结果。");
      } finally {
        if (!disposed) timer = window.setTimeout(refresh, 1200);
      }
    };
    void refresh();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [open, stale]);
  const stages = events.filter(event => event.type === "voice_stage");
  const latest = stages[stages.length - 1];
  const current = latest ? stages.filter(event => event.payload.turn_id === latest.payload.turn_id) : [];
  const timings = current.filter(event => event.payload.status !== "running" && typeof event.payload.duration_ms === "number");
  const running = latest?.payload.status === "running";
  const partial = [...events].reverse().find(event => event.type === "asr_partial");
  const lastTranscript = [...events].reverse().find(event => event.type === "transcript");
  const showPartial = partial && (!lastTranscript || partial.timestamp > lastTranscript.timestamp)
    && Date.now() - Date.parse(partial.timestamp) < 15000;
  const busy = evaluationBusy || evaluation?.status === "running";
  async function stop() {
    setStopping(true);
    try {
      await post("/api/voice/conversation/stop");
      setFeedback("已请求停止回复；已确认的设备操作仍然有效。");
    } catch (error) { setFeedback(error instanceof Error ? error.message : "停止失败，请重试。"); }
    finally { if (mounted.current) setStopping(false); }
  }
  async function evaluate(body: Record<string, unknown>) {
    setEvaluationBusy(true); setFeedback("");
    try {
      await post("/api/voice/intent/evaluation/start", body);
      if (mounted.current) setEvaluation(await json<Evaluation>("/api/voice/intent/evaluation"));
    } catch (error) { setFeedback(error instanceof Error ? error.message : "评测未启动，请重试。"); }
    finally { if (mounted.current) setEvaluationBusy(false); }
  }
  return <section className="voice-workbench">
    <div className="voice-progress" role="status"><span>{stale ? "语音服务未连接" : latest ? running ? `${labels[String(latest.payload.stage)] ?? "处理中"}…` : "本轮处理已结束" : "等待对话"}</span>
      <button className="secondary" disabled={stale || stopping} onClick={() => void stop()}>{stopping ? "正在停止…" : "停止回复"}</button></div>
    {showPartial && <p className="voice-partial">听到：{String(partial.payload.text)}<small>识别中，尚未执行</small></p>}
    {timings.length > 0 && <div className="voice-timings">{timings.map(event => <span key={event.id}>{labels[String(event.payload.stage)] ?? String(event.payload.stage)} <strong>{(Number(event.payload.duration_ms) / 1000).toFixed(2)}秒</strong></span>)}</div>}
    {capabilities && <p className="voice-workbench-feedback">{capabilities.asr_provider === "funasr" ? "实时云端识别" : "本地语音识别"} · {capabilities.audio_processing === "ready" ? "回声消除与降噪已启用" : "音频前处理未启用"} · {capabilities.tts_provider === "cosyvoice" ? "CosyVoice 流式播报" : capabilities.tts_provider === "edge" ? "Edge 分句播报" : "本地或未启用播报"}</p>}
    {feedback && <p className="voice-workbench-feedback" role="status">{feedback}</p>}
    <details className="voice-evaluation" onToggle={event => setOpen(event.currentTarget.open)}><summary>检查意图识别效果</summary>
      <p>只判断意图，不操作家居。调用当前配置的意图 API，可能产生模型费用。结果仅保留在本次服务内存中。</p>
      <div className="voice-evaluation-actions"><button disabled={stale || busy} onClick={() => void evaluate({ limit: 24 })}>{busy ? "评测中…" : "运行24个关键场景"}</button>
        {evaluation?.status === "running" && <button className="secondary" onClick={() => void post("/api/voice/intent/evaluation/cancel").catch(error => setFeedback(String(error)))}>停止评测</button>}</div>
      <label htmlFor="intent-preview">单句试判</label><div className="voice-evaluation-input"><input id="intent-preview" value={sample} maxLength={1500} placeholder="例如：还嫌不够热吗？" onChange={event => setSample(event.target.value)} /><button disabled={stale || busy || !sample.trim()} onClick={() => void evaluate({ text: sample.trim() })}>只判断</button></div>
      {evaluation && evaluation.status !== "idle" && <>
        <p role="status">{evaluation.status === "running" ? "正在评测" : evaluation.status === "completed" ? "评测完成" : evaluation.status === "cancelled" ? "评测已停止" : "评测失败，请检查意图服务"} · {evaluation.summary.completed ?? 0}/{evaluation.total ?? 0}</p>
        {!!evaluation.summary.scored && <p>符合预期 {evaluation.summary.passed}/{evaluation.summary.scored} · 误触发 {evaluation.summary.false_triggers} · 服务异常 {evaluation.summary.unavailable}<br />耗时中位数 {evaluation.summary.p50_ms}毫秒 · P95 {evaluation.summary.p95_ms}毫秒</p>}
        <div className="voice-evaluation-results">{evaluation.results.map(row => <div key={row.case_id}><span>{row.text}</span><small>{row.observed.kind}{row.observed.reason ? " · 服务不可用" : ""} · {row.duration_ms}毫秒{row.passed === null ? "" : row.passed ? " · 符合预期" : ` · 预期 ${row.expected}`}</small></div>)}</div>
      </>}
    </details>
  </section>;
}
