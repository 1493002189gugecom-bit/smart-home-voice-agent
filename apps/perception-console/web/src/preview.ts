type PreviewIdentity = {
  camera_id?: string;
  camera_room_id?: string;
  session_id?: string | null;
};

export function previewSourceKey(vision: PreviewIdentity): string {
  return [vision.camera_id ?? "none", vision.session_id ?? "none", vision.camera_room_id ?? "none"].join("|");
}

export function previewIsStale(previewAtMs: number | null | undefined, nowMs: number): boolean {
  return typeof previewAtMs === "number" && nowMs - previewAtMs > 6_000;
}
