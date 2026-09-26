export function deviceStateLabel(value: unknown, type: unknown): string {
  if (!value || typeof value !== "object") return "状态未知";
  const state = value as Record<string, unknown>;
  const number = (item: unknown) => typeof item === "number" && Number.isFinite(item) ? String(item) : null;
  if (type === "sensor") return number(state.temperature) != null ? `${number(state.temperature)}°C` : "温度未知";
  const on = state.on === true;
  if (state.on !== true && state.on !== false) return "状态未知";
  if (!on) return "关闭";
  if (type === "light") return number(state.brightness) != null ? `开启 · 亮度 ${number(state.brightness)}%` : "开启";
  if (type === "ac") {
    const modes: Record<string, string> = { cool: "制冷", heat: "制热", fan: "送风", fan_only: "送风", dry: "除湿", auto: "自动" };
    const mode = modes[String(state.mode)] ?? "运行中";
    return `开启 · ${mode}${number(state.target_temp) != null ? ` · 目标 ${number(state.target_temp)}°C` : ""}`;
  }
  if (type === "switch") return number(state.power) != null ? `开启 · 功率 ${number(state.power)}W` : "开启";
  return "开启";
}
