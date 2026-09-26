const stepNames: Record<string, string> = {
  front: "正视镜头",
  turn_left: "向你自己的左侧转头",
  turn_right: "向你自己的右侧转头",
  look_up: "抬头",
  look_down: "低头",
  blink: "眨眼",
};

const reasons: Record<string, string> = {
  no_face: "当前摄像头画面里没有检测到人脸。请确认画面拍到了你，再面向摄像头靠近一些。",
  multiple_faces: "画面里有多张人脸。请让其他人暂时离开镜头。",
  too_small: "人脸在画面中太小，请靠近摄像头。",
  too_dark: "画面偏暗，请打开正面的灯光。",
  too_bright: "画面过亮，请避开背光或强光。",
  blurred: "仅这一帧的清晰度不足，系统会继续尝试下一帧。请让镜头和头部暂时稳定、增加正面光；若连续数秒仍提示，再擦拭镜头或换摄像头。",
  occluded: "面部被遮挡，请露出完整的脸。",
  low_detection_confidence: "检测到人脸，但轮廓不够清晰；请正对镜头并改善光线。",
  landmarks_incomplete: "面部关键点不完整，请让整张脸进入画面。",
  embedding_unavailable: "已检测到人脸，但无法提取特征；请调整光线后重试。",
  quality_rejected: "这帧人脸未达到录入质量，请保持稳定并调整光线。",
  camera_interrupted: "摄像头画面已中断，请检查连接或换一台摄像头。",
  camera_open_failed: "摄像头无法打开，请检查是否被其他程序占用。",
  camera_disconnected: "摄像头已断开，请重新连接或选择其他设备。",
  model_error: "人脸识别模型发生错误，请到服务页检查视觉服务。",
  registry_write_failed: "人脸特征保存失败，原有记录未更改。",
};

export function canOpenRegistration(serviceState?: string): boolean {
  return serviceState === "up" || serviceState === "degraded";
}

export function registrationStepLabel(step?: string): string {
  return stepNames[step ?? ""] ?? "准备中";
}

const stepGuidance: Record<string, string> = {
  front: "正视步骤：让摄像头与眼睛大致同高，脸朝向镜头并平视；若已端正仍未通过，微调摄像头俯仰角后停稳一秒。",
  turn_left: "按你自己的方向，轻轻向左转头；镜像画面里会朝左，保持双眼仍在画面内并停稳片刻。",
  turn_right: "按你自己的方向，轻轻向右转头；镜像画面里会朝右，保持双眼仍在画面内并停稳片刻。",
  look_up: "请缓慢抬头一点，保持整张脸在画面里，停稳片刻。",
  look_down: "请先回正，再轻轻收下巴点头；身体不用前倾，保持双眼和嘴巴在画面中。",
  blink: "请正对镜头，先自然睁眼约一秒，再慢慢闭眼约半秒并睁开；未通过可重复一次。",
};

export function registrationGuidance(reason?: string | null, fallback?: string, step?: string): string {
  if (!reason) return stepGuidance[step ?? ""] ?? fallback ?? "请看着画面，按当前步骤完成动作。";
  if (reason === "wrong_action") return stepGuidance[step ?? ""] ?? "请按上方动作轻微调整头部，保持整张脸在画面内并停稳片刻。";
  return reasons[reason] ?? `注册暂未推进（${reason}）。请查看实时画面并重试。`;
}
