/**
 * What the console is allowed to say about who just spoke.
 *
 * Two rules are encoded here.
 *
 * 1. Never show a name the system is not willing to act on: a verdict below the
 *    threshold carries no identity, so it renders as nameless rather than being
 *    quietly dropped.
 * 2. Never show a bare "未识别" when the reason is known. "No chip" used to mean
 *    four different things at once — audio too short, nobody enrolled, the gallery
 *    is down, or voiceprints switched off — which is impossible to act on.
 */

export type SpeakerChip = {
  label: string;
  detail: string | null;
  tone: "known" | "unsure";
  /** Longer explanation for the tooltip, when the short label is not enough. */
  title: string;
};

/** Why there is no name, in the few words a pill can hold. */
const REASON_LABEL: Record<string, string> = {
  too_short: "说话太短",
  silent: "没听到声音",
  clipping: "声音过载",
  low_snr: "噪声太大",
  not_recognisable: "无法判断",
  no_enrolment: "还没录入任何人",
  gallery_unavailable: "声纹库不可用",
  invalid_response: "声纹库返回异常",
  ambiguous: "两个人分数接近",
  below_threshold: "分数不够",
  disabled: "未启用",
  unavailable: "声纹不可用",
  not_evaluated: "未评估",
};

const REASON_TITLE: Record<string, string> = {
  too_short: "这句话短于 1.5 秒，声纹不足以判断，请说完整的句子",
  silent: "没有采集到有效声音，请检查麦克风",
  clipping: "声音过载失真，请离麦克风远一点",
  low_snr: "背景噪声偏大，请靠近麦克风",
  no_enrolment: "声纹库里还没有人，请先在身份页录入",
  gallery_unavailable: "连不上 vision-service 的声纹库",
  ambiguous: "有两位家人的分数非常接近，不做猜测",
  below_threshold: "最高分低于阈值，不做猜测",
  disabled: "未配置声纹模型，整个功能关闭",
  unavailable: "声纹服务或模型出错",
  not_evaluated: "这一句没有经过声纹判断",
};

function confidenceOf(payload: Record<string, unknown>): string | null {
  const value = payload.speaker_confidence;
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : null;
}

function reasonOf(payload: Record<string, unknown>): string | null {
  const value = payload.speaker_reason;
  return typeof value === "string" && value ? value : null;
}

export function speakerChip(payload: Record<string, unknown>): SpeakerChip | null {
  const state = payload.speaker_state;
  // Events recorded before the reason existed carry no state at all: draw nothing
  // rather than inventing an explanation for them.
  if (typeof state !== "string" || !state) return null;
  const reason = reasonOf(payload);
  const confidence = confidenceOf(payload);

  if (state === "confirmed") {
    const name = payload.speaker_name;
    const detail = confidence;
    if (typeof name === "string" && name) {
      return { label: name, detail, tone: "known", title: `声纹判定为${name}，置信度 ${detail ?? "未知"}` };
    }
    // A confirmed match we cannot name is still not a name to invent.
    return { label: "未知身份", detail, tone: "unsure", title: "匹配成功但人物没有名字，请检查人物名单" };
  }

  if (state === "disabled") {
    return { label: "未启用", detail: null, tone: "unsure", title: REASON_TITLE.disabled };
  }

  // Prefer the score when there is one: it is the number used to calibrate the
  // threshold. Otherwise say what actually went wrong.
  const detail = confidence ?? (reason ? REASON_LABEL[reason] ?? null : null);
  const title = reason ? REASON_TITLE[reason] ?? "没有给出可用的声纹判定" : "没有给出可用的声纹判定";
  return { label: "未识别", detail, tone: "unsure", title };
}

export function speakerChipText(chip: SpeakerChip): string {
  return chip.detail ? `${chip.label} · ${chip.detail}` : chip.label;
}
