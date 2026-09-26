/**
 * Voiceprint enrolment as the console shows it.
 *
 * The same rule as the speaker chip: never imply more certainty than the system
 * has. A refused sample is explained by what was actually wrong with the audio, and
 * "not configured" is a different message from "listened and failed".
 */

export type VoiceEnrollment = {
  state: string;
  active: boolean;
  person_id?: string | null;
  accepted: number;
  required: number;
  min_seconds: number;
  last_reason?: string | null;
  message?: string | null;
};

export type VoiceEnrollmentResponse = {
  ok?: boolean;
  /** False when no speaker model is configured, so the feature is switched off. */
  configured?: boolean;
  enrollment?: VoiceEnrollment;
};

/** Stable codes from the voice service, turned into something actionable. */
const REASON_GUIDANCE: Record<string, string> = {
  too_short: "这句话太短了，请说满两秒以上的完整句子。",
  silent: "没有听到声音，请确认麦克风可用。",
  clipping: "声音过载了，请离麦克风远一点再说。",
  low_snr: "背景声音偏大，请靠近麦克风、减少噪声。",
  not_recognisable: "这段声音无法用作声纹，请重新说一句。",
  save_failed: "声纹没有保存成功，请检查 vision-service 后重试。",
  speaker_model_missing: "声纹模型文件缺失，请检查配置。",
  speaker_runtime_missing: "缺少 onnxruntime，请安装 voice-service 的 speaker 附加依赖。",
  speaker_model_failed: "声纹模型运行失败，请检查模型文件与依赖。",
};

const START_ERRORS: Record<string, string> = {
  speaker_disabled: "未配置声纹模型，无法录入声纹。",
  enrollment_active: "已有声纹录入正在进行，请先完成或取消。",
  unknown_person: "请先在身份页添加该人物。",
  person_directory_unavailable: "人物名单暂不可用，请检查 home-service。",
};

export function enrollmentGuidance(reason?: string | null): string | null {
  if (!reason) return null;
  return REASON_GUIDANCE[reason] ?? "这段声音没有被接受，请重新说一句。";
}

export function startErrorGuidance(code?: string | null): string | null {
  if (!code) return null;
  return START_ERRORS[code] ?? "声纹录入未能开始，请稍后重试。";
}

export function enrollmentProgress(enrollment?: VoiceEnrollment | null): number {
  if (!enrollment || !enrollment.required) return 0;
  return Math.max(0, Math.min(1, enrollment.accepted / enrollment.required));
}

/**
 * Progress of a session that is relevant to one person, or `null` when there is
 * none.
 *
 * This deliberately has no "尚未启用" case. Having no session is the normal state
 * for every person who is not currently enrolling, and saying "not enabled" there
 * described the wrong thing entirely — it made an enrolled, working person look
 * switched off.
 */
export function enrollmentSummary(enrollment?: VoiceEnrollment | null): string | null {
  if (!enrollment) return null;
  if (enrollment.state === "collecting") return `正在采集 ${enrollment.accepted} / ${enrollment.required} 句`;
  if (enrollment.state === "saved") return "声纹已录入";
  if (enrollment.state === "failed") return "本次录入失败";
  return null;
}

/** What the person should do next, in one sentence. */
export function enrollmentInstruction(enrollment?: VoiceEnrollment | null): string {
  if (!enrollment || enrollment.state === "idle") return "点“录入声纹”开始；录完需唤醒一次，才能逐句采集。";
  if (enrollment.state === "saved") return "声纹已保存，可以开始记录下一人。";
  if (enrollment.state === "failed") return enrollment.message ?? "本次录入失败，请重新开始。";
  const min = enrollment.min_seconds ? `${enrollment.min_seconds}` : "2";
  return `先唤醒（说“小屋小屋”），然后每次说一句不少于 ${min} 秒的话，共需 ${enrollment.required} 句。`;
}
