export function voiceErrorText(payload: Record<string, unknown>): string {
  if (typeof payload.message === "string" && payload.message.trim()) return payload.message;
  const messages: Record<string, string> = {
    tts_synthesis: "语音合成失败，回复文字仍可查看，请检查语音服务或网络。",
    playback: "音频播放失败，请检查扬声器或耳机设备。",
    asr: "语音转写失败，请重新说一句。",
    intent: "意图识别失败，本句未执行，请稍后重试。",
    agent: "对话处理失败，请查看设备确认结果后重试。",
  };
  if (typeof payload.stage === "string" && messages[payload.stage]) return messages[payload.stage];
  if (payload.error_type === "audio_input_open_failed") return "麦克风打开失败，请检查输入设备。";
  return "语音处理失败，请检查语音服务和音频设备后重试。";
}
