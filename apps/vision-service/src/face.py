"""InsightFace adapter with strict local loading, quality gates and body association.

The heavy third-party imports (`insightface`, `cv2`, `numpy`) happen inside the
methods that need them, matching the house style of `capture.py`. Nothing here
persists a frame, an embedding or a model path to a log.

Privacy boundary (design spec section 10): this module never writes images or
features to disk. Embeddings produced here are handed to `registry.py`, which is
the only place that persists them, and only in DPAPI-encrypted form.
"""

from __future__ import annotations

import os
from numbers import Real
from pathlib import Path

from contracts import BodyTrack, FaceQuality, FaceSample, NormalizedPoint


# Face detection input size; InsightFace resizes the frame to this square.
_DETECTION_SIZE = (640, 640)
# Minimum mean grayscale brightness (0-255) accepted for a face crop.
_MIN_BRIGHTNESS = 40.0
# Maximum mean grayscale brightness (0-255) accepted for a face crop.
_MAX_BRIGHTNESS = 215.0
# Minimum variance of the Laplacian over the face crop (scale 0-255).
_MIN_SHARPNESS = 60.0
# Minimum per-landmark score required for all five landmarks to count as present.
_MIN_LANDMARK_CONFIDENCE = 0.50
# Minimum horizontal spread of the five landmarks relative to the face box width.
# A narrower spread means the face is turned far enough that most of it is hidden,
# which is reported as `occluded` rather than as a quality-ok sample.
_MIN_LANDMARK_SPREAD_RATIO = 0.20
# Minimum IoU lead over the runner-up body box before association is trusted.
_BODY_IOU_LEAD = 0.10
# InsightFace buffalo_l recognition output dimension; used only as a sanity check.
_EMBEDDING_DIM = 512
# Model files the pack must provide on disk; the detector and the recognizer are
# both required before any inference object is constructed.
_REQUIRED_PACK_FILES = ("det_10g.onnx", "w600k_r50.onnx")
# Additional pack files reported through `FaceEngine.available_models`, if present.
_OPTIONAL_PACK_FILES = ("1k3d68.onnx", "genderage.onnx", "2d106det.onnx")


class FaceModelMissing(RuntimeError):
    """Raised when the configured InsightFace pack is not present on disk.

    `args[0]` is a redacted message: the model pack name only, never a full
    filesystem path and never a URL.
    """

    def __init__(self, pack: str) -> None:
        super().__init__(f"insightface model pack '{pack}' is not available locally")
        self.pack = pack
        self.code = "model_missing"


def _clamp_unit(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _is_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _float_or_none(value: object) -> float | None:
    return float(value) if _is_number(value) else None


def _iou(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
    """Intersection over union of two normalized boxes."""

    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    left = max(ax1, bx1)
    top = max(ay1, by1)
    right = min(ax2, bx2)
    bottom = min(ay2, by2)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return 0.0 if union <= 0.0 else intersection / union


def _point_in_box(box: tuple[float, float, float, float], x: float, y: float) -> bool:
    """Containment with one-unit-epsilon slack for floating point borders."""

    x1, y1, x2, y2 = box
    return (x1 - 1e-6) <= x <= (x2 + 1e-6) and (y1 - 1e-6) <= y <= (y2 + 1e-6)


class FaceEngine:
    """Strictly local InsightFace owner: one frame in, normalized samples out."""

    def __init__(self, config: object) -> None:
        self.config = config
        self.pack: str = str(getattr(config, "insightface_pack", "buffalo_l"))
        self.root: Path = Path(getattr(config, "insightface_root", Path(".")))
        self.providers: tuple[str, ...] = tuple(getattr(config, "providers", ()))
        self.min_pixels: int = int(getattr(config, "face_min_pixels", 80))
        self.min_detection_confidence: float = float(
            getattr(config, "face_min_detection_confidence", 0.65)
        )
        self._app: object | None = None
        self._model_dir: Path | None = None
        self._landmarks_106: dict[tuple[float, float, float, float], tuple[tuple[float, float, float], ...]] = {}
        self._last_message: str | None = None

    # ------------------------------------------------------------------ loading

    @property
    def loaded(self) -> bool:
        return self._app is not None

    @property
    def last_message(self) -> str | None:
        """Redacted, human-readable reason for the last load/analyse failure."""

        return self._last_message

    @property
    def model_dir(self) -> Path | None:
        return self._model_dir

    def candidate_dirs(self) -> tuple[Path, ...]:
        """Directories that could hold the pack, in the order InsightFace checks."""

        return (
            self.root / "models" / self.pack,
            self.root / self.pack,
        )

    def _resolve_model_dir(self) -> Path | None:
        for directory in self.candidate_dirs():
            if directory.is_dir():
                return directory
        return None

    def _missing_files(self) -> tuple[str, ...]:
        directory = self._model_dir
        if directory is None:
            return _REQUIRED_PACK_FILES
        return tuple(name for name in _REQUIRED_PACK_FILES if not (directory / name).is_file())

    def load(self) -> None:
        """Load InsightFace strictly from disk.

        Raises `FaceModelMissing` when the pack directory or a required `.onnx`
        file is absent. Automatic download is disabled: `INSIGHTFACE_HOME` points
        at the configured local root and every temporary working directory is a
        non-existent absolute path, so any download attempt fails instead of
        writing a model into the user's home directory.
        """

        if self._app is not None:
            return
        directory = self._resolve_model_dir()
        if directory is None:
            self._last_message = f"model directory missing for pack '{self.pack}'"
            raise FaceModelMissing(self.pack)
        self._model_dir = directory
        missing = self._missing_files()
        if missing:
            self._last_message = f"model files missing for pack '{self.pack}'"
            raise FaceModelMissing(self.pack)

        os.environ["INSIGHTFACE_HOME"] = str(self.root)
        os.environ.setdefault("INSIGHTFACE_DISABLE_DOWNLOAD", "1")

        from insightface.app import FaceAnalysis

        providers = list(self.providers) if self.providers else ["CPUExecutionProvider"]
        try:
            # InsightFace forwards constructor kwargs to ONNX Runtime sessions.
            # Passing providers to prepare() is ignored by current releases.
            # Only these tasks feed registration and identity. Running the pack's
            # 3D landmarks and age/gender models adds work for every detected face.
            app = FaceAnalysis(
                name=self.pack,
                root=str(self.root),
                providers=providers,
                allowed_modules=["detection", "recognition", "landmark_2d_106"],
            )
        except Exception as exc:  # pragma: no cover - defensive, never executed here
            self._last_message = f"insightface construction failed: {type(exc).__name__}"
            raise FaceModelMissing(self.pack) from exc

        try:
            ctx_id = 0 if any(
                item in {"CUDAExecutionProvider", "TensorrtExecutionProvider"}
                for item in providers
            ) else -1
            # Detect more conservatively than we accept for registration.  The
            # quality gate below still requires min_detection_confidence, while
            # this lower detector threshold lets the UI explain a weak face
            # instead of incorrectly reporting that no face exists at all.
            detector_threshold = max(0.10, min(0.50, self.min_detection_confidence * 0.60))
            app.prepare(
                ctx_id=ctx_id,
                det_thresh=detector_threshold,
                det_size=_DETECTION_SIZE,
            )
        except Exception as exc:
            self._last_message = f"insightface prepare failed: {type(exc).__name__}"
            raise RuntimeError("face_model_prepare_failed") from exc

        self._app = app
        self._last_message = None

    def ensure_loaded(self) -> None:
        """Load on first use so callers can construct the engine without disk I/O."""

        if self._app is None:
            self.load()

    def available_models(self) -> tuple[str, ...]:
        """Pack file names present locally, for the service status endpoint."""

        if self._model_dir is None:
            return ()
        return tuple(
            name for name in (*_REQUIRED_PACK_FILES, *_OPTIONAL_PACK_FILES)
            if (self._model_dir / name).is_file()
        )

    # ---------------------------------------------------------------- analysis

    def analyze(self, frame_bgr: object) -> list[FaceSample]:
        """Return one `FaceSample` per detected face in normalized coordinates.

        Every detected face is reported so callers can distinguish "no face" from
        "several faces". `FaceQuality.ok` is False when a sample must not be used
        for identity; geometry is still populated for association and progress.
        """

        self.ensure_loaded()
        app = self._app
        if app is None:  # pragma: no cover - ensure_loaded raises instead
            return []
        height, width = self._frame_shape(frame_bgr)
        self._landmarks_106 = {}
        try:
            detected = app.get(frame_bgr)
        except Exception as exc:  # pragma: no cover - inference failure is honest
            self._last_message = f"face inference failed: {type(exc).__name__}"
            raise
        samples: list[FaceSample] = []
        for face in detected:
            bbox = self._normalized_bbox(face, width, height)
            if bbox is None:
                continue
            landmarks = self._normalized_landmarks(face, width, height)
            confidence = _float_or_none(getattr(face, "det_score", None)) or 0.0
            quality, embedding, sharpness, brightness = self._assess(
                frame_bgr, bbox, landmarks, confidence, face
            )
            points_106 = self._index_landmarks_106(face, width, height)
            if points_106:
                self._landmarks_106[bbox] = points_106
            samples.append(FaceSample(
                bbox=bbox,
                landmarks=landmarks,
                detection_confidence=confidence,
                quality=quality,
                embedding=embedding,
                sharpness=sharpness,
                brightness=brightness,
                frame_aspect_ratio=width / height,
            ))
        return samples

    def landmarks_106(
        self, bbox: tuple[float, float, float, float],
    ) -> tuple[tuple[float, float, float], ...] | None:
        """Return the 106 alignment landmarks for the last analysed face `bbox`.

        Coordinates are normalized to the frame. `None` means the pack did not
        produce the dense landmarks, so blink detection cannot be proven.
        """

        return self._landmarks_106.get(tuple(bbox))

    # ----------------------------------------------------------------- helpers

    def _frame_shape(self, frame_bgr: object) -> tuple[int, int]:
        shape = getattr(frame_bgr, "shape", None)
        if shape is None or len(shape) < 2:
            raise ValueError("frame_bgr must be an HxWx3 numpy array")
        return int(shape[0]), int(shape[1])

    def _normalized_bbox(
        self, face: object, width: int, height: int,
    ) -> tuple[float, float, float, float] | None:
        raw = getattr(face, "bbox", None)
        if raw is None or len(raw) < 4 or width <= 0 or height <= 0:
            return None
        values = [_float_or_none(item) for item in raw[:4]]
        if None in values:
            return None
        x1 = float(values[0])  # type: ignore[arg-type]
        y1 = float(values[1])  # type: ignore[arg-type]
        x2 = float(values[2])  # type: ignore[arg-type]
        y2 = float(values[3])  # type: ignore[arg-type]
        if x2 < x1 or y2 < y1:
            return None
        return (
            _clamp_unit(x1 / width),
            _clamp_unit(y1 / height),
            _clamp_unit(x2 / width),
            _clamp_unit(y2 / height),
        )

    def _normalized_landmarks(
        self, face: object, width: int, height: int,
    ) -> tuple[NormalizedPoint, ...]:
        raw = getattr(face, "kps", None)
        if raw is None or width <= 0 or height <= 0:
            return ()
        points: list[NormalizedPoint] = []
        for item in raw:
            if len(item) < 2:
                continue
            x = _clamp_unit(float(item[0]) / width)
            y = _clamp_unit(float(item[1]) / height)
            confidence = float(item[2]) if len(item) > 2 else 1.0
            points.append(NormalizedPoint(x=x, y=y, confidence=confidence))
        return tuple(points)

    def _index_landmarks_106(
        self,
        face: object,
        width: int,
        height: int,
    ) -> tuple[tuple[float, float, float], ...]:
        raw = getattr(face, "landmark_2d_106", None)
        if raw is None or width <= 0 or height <= 0:
            return ()
        points: list[tuple[float, float, float]] = []
        for item in raw:
            if len(item) < 2:
                return ()
            x = _clamp_unit(float(item[0]) / width)
            y = _clamp_unit(float(item[1]) / height)
            score = float(item[2]) if len(item) > 2 else 1.0
            points.append((x, y, score))
        return tuple(points)

    def _assess(
        self,
        frame_bgr: object,
        bbox: tuple[float, float, float, float],
        landmarks: tuple[NormalizedPoint, ...],
        confidence: float,
        face: object,
    ) -> tuple[FaceQuality, tuple[float, ...] | None, float, float]:
        if confidence < self.min_detection_confidence:
            return FaceQuality(False, "low_detection_confidence"), None, 0.0, 0.0

        height, width = self._frame_shape(frame_bgr)
        x1 = int(round(bbox[0] * width))
        y1 = int(round(bbox[1] * height))
        x2 = int(round(bbox[2] * width))
        y2 = int(round(bbox[3] * height))
        x1c, x2c = max(0, x1), min(width, x2)
        y1c, y2c = max(0, y1), min(height, y2)
        pixel_width = x2c - x1c
        pixel_height = y2c - y1c
        if pixel_width <= 0 or pixel_height <= 0:
            return FaceQuality(False, "too_small"), None, 0.0, 0.0
        if min(pixel_width, pixel_height) < self.min_pixels:
            return FaceQuality(False, "too_small"), None, 0.0, 0.0

        import cv2
        import numpy as np

        crop = frame_bgr[y1c:y2c, x1c:x2c]
        if crop.size == 0:
            return FaceQuality(False, "too_small"), None, 0.0, 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        brightness = float(np.mean(gray))
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if brightness < _MIN_BRIGHTNESS:
            return FaceQuality(False, "too_dark"), None, sharpness, brightness
        if brightness > _MAX_BRIGHTNESS:
            return FaceQuality(False, "too_bright"), None, sharpness, brightness
        if sharpness < _MIN_SHARPNESS:
            return FaceQuality(False, "blurred"), None, sharpness, brightness
        if not self._landmarks_complete(landmarks):
            return FaceQuality(False, "landmarks_incomplete"), None, sharpness, brightness
        if not self._landmarks_spread(landmarks, bbox):
            return FaceQuality(False, "occluded"), None, sharpness, brightness

        embedding = self._embedding(face)
        if embedding is None:
            return FaceQuality(False, "embedding_unavailable"), None, sharpness, brightness
        return FaceQuality(True, None), embedding, sharpness, brightness

    def _landmarks_complete(self, landmarks: tuple[NormalizedPoint, ...]) -> bool:
        if len(landmarks) < 5:
            return False
        return all(point.confidence >= _MIN_LANDMARK_CONFIDENCE for point in landmarks[:5])

    def _landmarks_spread(
        self, landmarks: tuple[NormalizedPoint, ...], bbox: tuple[float, float, float, float],
    ) -> bool:
        """False when the five landmarks collapse into a near-profile view."""

        if len(landmarks) < 5:
            return False
        face_width = bbox[2] - bbox[0]
        if face_width <= 0.0:
            return False
        xs = [point.x for point in landmarks[:5]]
        return (max(xs) - min(xs)) >= _MIN_LANDMARK_SPREAD_RATIO * face_width

    def _embedding(self, face: object) -> tuple[float, ...] | None:
        """Return a unit-length embedding, preferring InsightFace's normalized one."""

        import numpy as np

        raw = getattr(face, "normed_embedding", None)
        if raw is None:
            raw = getattr(face, "embedding", None)
            if raw is None:
                return None
            vector = np.asarray(raw, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(norm) or norm <= 0.0:
                return None
            vector = vector / norm
        else:
            vector = np.asarray(raw, dtype=np.float32).reshape(-1)
        if vector.size != _EMBEDDING_DIM:
            return None
        return tuple(float(value) for value in vector)


def associate_faces(
    faces: list[FaceSample], bodies: list[BodyTrack],
) -> dict[str, FaceSample]:
    """Associate normalized faces to body tracks by face-centre containment.

    A face may be claimed by at most one track. When several body boxes contain
    the face centre the highest IoU wins only if it leads the runner-up by
    `_BODY_IOU_LEAD`; otherwise the face stays unassociated so the pipeline
    reports an unknown identity instead of guessing.
    """

    association: dict[str, FaceSample] = {}
    if not faces or not bodies:
        return association
    claimed: set[str] = set()
    for face in faces:
        center_x = (face.bbox[0] + face.bbox[2]) / 2.0
        center_y = (face.bbox[1] + face.bbox[3]) / 2.0
        containing: list[tuple[float, BodyTrack]] = []
        for body in bodies:
            if body.track_id in claimed:
                continue
            if not _point_in_box(body.bbox, center_x, center_y):
                continue
            containing.append((_iou(face.bbox, body.bbox), body))
        if not containing:
            continue
        containing.sort(key=lambda item: (-item[0], item[1].track_id))
        if len(containing) > 1 and (containing[0][0] - containing[1][0]) < _BODY_IOU_LEAD:
            continue
        best_score, best_body = containing[0]
        if best_score <= 0.0:
            continue
        association[best_body.track_id] = face
        claimed.add(best_body.track_id)
    return association
