import { useEffect, useRef, useState } from "react";
import { post, type VoiceEvent } from "./api";

export default function VoiceComposer({ events, stale }: { events: VoiceEvent[]; stale: boolean }) {
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [speak, setSpeak] = useState(true);
  const sending = useRef(false);
  const attempt = useRef<{ text: string; id: string; speak: boolean } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!pending) return;
    const result = events.find((event) => event.type === "text_turn"
      && event.payload.request_id === pending && event.payload.turn_status !== "processing");
    if (result || stale) {
      setPending(null);
      sending.current = false;
      setFeedback(result ? "" : "连接已断开，执行结果尚未确认，请先查看对话记录。");
    }
  }, [events, pending, stale]);

  async function send() {
    const text = draft.trim();
    if (!text || text.length > 1500 || sending.current || stale) return;
    sending.current = true;
    setFeedback("");
    // Reusing an uncertain request id avoids repeating an action if its HTTP
    // acknowledgement was lost. A deliberately new message gets a fresh id.
    if (attempt.current?.text !== text) attempt.current = { text, id: crypto.randomUUID(), speak };
    const request = attempt.current;
    setPending(request.id);
    try {
      await post("/api/voice/conversation/text", { text, request_id: request.id, speak: request.speak });
      setDraft((current) => current.trim() === text ? "" : current);
      attempt.current = null;
      inputRef.current?.focus();
    } catch (reason) {
      setPending(null);
      sending.current = false;
      setFeedback(reason instanceof Error ? `${reason.message}。请先核对对话记录再重试。` : "发送结果未确认，请先核对对话记录。");
    }
  }

  return <form className="voice-composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
    <label htmlFor="voice-text-input">打字与小屋对话</label>
    <textarea id="voice-text-input" ref={inputRef} rows={3} maxLength={1500} value={draft}
      aria-describedby="voice-text-help" placeholder="例如：好热啊，或者打开客厅灯…"
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
          event.preventDefault(); void send();
        }
      }} />
    <div className="voice-composer-actions"><small id="voice-text-help">Enter 发送，Shift+Enter 换行。</small>
      <label className="voice-speech-toggle"><input type="checkbox" checked={speak} disabled={!!pending}
        onChange={(event) => setSpeak(event.target.checked)} />播报回复</label>
      <button type="submit" disabled={stale || !!pending || !draft.trim()}>{pending ? "等待回复…" : "发送"}</button></div>
    <p className="voice-composer-hint">按语音转写处理，可执行家居操作；房间不明确时会追问。</p>
    {feedback && <p className="voice-composer-feedback" role="status">{feedback}</p>}
  </form>;
}
