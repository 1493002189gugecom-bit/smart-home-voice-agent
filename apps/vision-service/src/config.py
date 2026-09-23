"""Strict configuration loading without importing model runtimes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_FIELDS = {
    "host", "port", "home_service_url", "runtime_dir", "pose_model",
    "insightface_root", "insightface_pack", "providers",
    "face_review_interval_frames", "identity_window_frames",
    "identity_min_votes", "identity_similarity_threshold",
    "identity_margin_threshold", "identity_hold_ms", "observation_ttl_ms",
    "face_min_pixels", "face_min_detection_confidence",
    "body_min_confidence", "keypoint_min_confidence", "preview_jpeg_quality",
}


@dataclass(frozen=True)
class VisionConfig:
    host: str
    port: int
    home_service_url: str
    runtime_dir: Path
    pose_model: Path
    insightface_root: Path
    insightface_pack: str
    providers: tuple[str, ...]
    face_review_interval_frames: int
    identity_window_frames: int
    identity_min_votes: int
    identity_similarity_threshold: float
    identity_margin_threshold: float
    identity_hold_ms: int
    observation_ttl_ms: int
    face_min_pixels: int
    face_min_detection_confidence: float
    body_min_confidence: float
    keypoint_min_confidence: float
    preview_jpeg_quality: int

    @classmethod
    def load(cls, path: Path) -> "VisionConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != _FIELDS:
            unknown = set(raw) - _FIELDS if isinstance(raw, dict) else set()
            missing = _FIELDS - set(raw) if isinstance(raw, dict) else _FIELDS
            raise ValueError(f"invalid configuration keys; missing={sorted(missing)}, unknown={sorted(unknown)}")
        if raw["host"] != "127.0.0.1":
            raise ValueError("vision-service host must be 127.0.0.1")
        parsed_home = urlparse(str(raw["home_service_url"]))
        if parsed_home.scheme != "http" or parsed_home.hostname != "127.0.0.1":
            raise ValueError("home_service_url must be loopback HTTP")
        runtime_dir = Path(raw["runtime_dir"])
        pose_model = Path(raw["pose_model"])
        insightface_root = Path(raw["insightface_root"])
        if not runtime_dir.is_absolute():
            runtime_dir = path.resolve().parents[3] / runtime_dir
        if not pose_model.is_absolute() or not insightface_root.is_absolute():
            raise ValueError("model paths must be absolute")
        positive = (
            "port", "face_review_interval_frames", "identity_window_frames",
            "identity_min_votes", "identity_hold_ms", "observation_ttl_ms",
            "face_min_pixels", "preview_jpeg_quality",
        )
        for name in positive:
            if isinstance(raw[name], bool) or int(raw[name]) <= 0:
                raise ValueError(f"{name} must be positive")
        if int(raw["port"]) > 65535:
            raise ValueError("port must be at most 65535")
        if int(raw["preview_jpeg_quality"]) > 100:
            raise ValueError("preview_jpeg_quality must be at most 100")
        for name in (
            "identity_similarity_threshold", "identity_margin_threshold",
            "face_min_detection_confidence", "body_min_confidence",
            "keypoint_min_confidence",
        ):
            value = float(raw[name])
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if int(raw["identity_min_votes"]) > int(raw["identity_window_frames"]):
            raise ValueError("identity_min_votes cannot exceed identity_window_frames")
        if not isinstance(raw["providers"], list) or not raw["providers"]:
            raise ValueError("providers must be a non-empty list")
        return cls(
            host=raw["host"], port=int(raw["port"]),
            home_service_url=raw["home_service_url"], runtime_dir=runtime_dir,
            pose_model=pose_model, insightface_root=insightface_root,
            insightface_pack=str(raw["insightface_pack"]),
            providers=tuple(str(item) for item in raw["providers"]),
            face_review_interval_frames=int(raw["face_review_interval_frames"]),
            identity_window_frames=int(raw["identity_window_frames"]),
            identity_min_votes=int(raw["identity_min_votes"]),
            identity_similarity_threshold=float(raw["identity_similarity_threshold"]),
            identity_margin_threshold=float(raw["identity_margin_threshold"]),
            identity_hold_ms=int(raw["identity_hold_ms"]),
            observation_ttl_ms=int(raw["observation_ttl_ms"]),
            face_min_pixels=int(raw["face_min_pixels"]),
            face_min_detection_confidence=float(raw["face_min_detection_confidence"]),
            body_min_confidence=float(raw["body_min_confidence"]),
            keypoint_min_confidence=float(raw["keypoint_min_confidence"]),
            preview_jpeg_quality=int(raw["preview_jpeg_quality"]),
        )

    def public_view(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "home_service_url": self.home_service_url,
            "face_review_interval_frames": self.face_review_interval_frames,
            "identity_window_frames": self.identity_window_frames,
            "identity_min_votes": self.identity_min_votes,
            "identity_hold_ms": self.identity_hold_ms,
            "observation_ttl_ms": self.observation_ttl_ms,
        }
