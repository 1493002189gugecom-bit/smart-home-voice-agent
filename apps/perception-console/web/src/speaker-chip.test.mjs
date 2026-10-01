import test from "node:test";
import assert from "node:assert/strict";
import { speakerChip, speakerChipText } from "./speaker-chip.ts";

test("a short understood sentence is shown as unjudged with its measured recording length", () => {
  const chip = speakerChip({
    speaker_state: "unknown",
    speaker_reason: "too_short",
    speaker_audio_seconds: 0.82,
    speaker_min_seconds: 2.4,
    transcript: "啥意思？",
  });

  assert.equal(speakerChipText(chip), "未判定 · 录音不足");
  assert.match(chip.title, /录音 0\.82 秒/);
  assert.match(chip.title, /至少 2\.4 秒/);
  assert.match(chip.title, /不影响文字识别/);
});

test("a gallery failure is unjudged, while a weak score is unconfirmed", () => {
  const offline = speakerChip({ speaker_state: "unknown", speaker_reason: "gallery_unavailable" });
  const weak = speakerChip({ speaker_state: "uncertain", speaker_reason: "below_threshold", speaker_confidence: 0.49 });

  assert.equal(speakerChipText(offline), "未判定 · 声纹库不可用");
  assert.equal(speakerChipText(weak), "未确认 · 0.49");
});

test("confirmed number is described as a match score, not a probability", () => {
  const chip = speakerChip({ speaker_state: "confirmed", speaker_name: "爸爸", speaker_confidence: 0.62 });

  assert.equal(speakerChipText(chip), "爸爸 · 0.62");
  assert.match(chip.title, /匹配分数 0\.62/);
  assert.doesNotMatch(chip.title, /置信度/);
});

test("older events without duration do not invent a fixed configured limit", () => {
  const chip = speakerChip({ speaker_state: "unknown", speaker_reason: "too_short" });

  assert.equal(speakerChipText(chip), "未判定 · 录音不足");
  assert.doesNotMatch(chip.title, /1\.5 秒/);
});
