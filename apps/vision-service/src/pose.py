"""YOLO26 pose tracking and the temporal pose state machine.

Two layers, deliberately separate:

* :class:`PoseTracker` owns the Ultralytics model and ByteTrack, and converts one
  frame into normalized body tracks. Tracker ids are prefixed with the capture
  session id so no temporal evidence can survive a new session.
* :class:`PoseStateMachine` turns a keypoint stream into a stable pose. A single
  frame never decides a pose: a rule must hold for an entry window, and the
  previous state is kept until an exit window has elapsed without support. That
  is what stops a person flickering between sitting and standing, and what stops
  a bend or a deliberate lie-down from being reported as a fall.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from capture import CapturedFrame
from config import VisionConfig
from contracts import BodyTrack, NormalizedPoint, PoseEstimate, PoseState

KEYPOINT_COUNT = 17

# COCO-17 indices used by the rules below.
NOSE = 0
LEFT_SHOULDER, RIGHT_SHOULDER = 5, 6
LEFT_HIP, RIGHT_HIP = 11, 12
LEFT_KNEE, RIGHT_KNEE = 13, 14
LEFT_ANKLE, RIGHT_ANKLE = 15, 16

# Temporal hysteresis, from the approved design.
ENTER_MS = 300
EXIT_MS = 500
HISTORY_MS = 2_000
HISTORY_MAX_SAMPLES = 90

# A fall needs a fast descent followed by sustained lying; a direct lie-down or a
# bend must never satisfy it.
FALL_DROP_PER_SECOND = 0.18
FALL_DROP_WINDOW_MS = 900
FALL_LYING_MS = 700

# Geometry thresholds, expressed in normalized image units and degrees.
STANDING_TORSO_MAX_DEGREES = 35.0
SITTING_TORSO_MAX_DEGREES = 45.0
LYING_TORSO_MIN_DEGREES = 60.0
LYING_ASPECT_MIN = 1.2
STANDING_KNEE_MIN_DEGREES = 150.0
SITTING_KNEE_MAX_DEGREES = 140.0
HAND_RAISE_MIN_MARGIN = 0.0
# A pose rule may not be inferred from the detection box alone: the design
# requires insufficient keypoints to read as unknown. Shape-only lying therefore
# needs a minimum share of usable keypoints, and below a floor no rule may fire.
MIN_SHAPE_LYING_KEYPOINT_RATIO = 0.35
MIN_POSE_KEYPOINT_RATIO = 0.25

_EPSILON = 1e-6


def _angle_degrees(first: NormalizedPoint, vertex: NormalizedPoint, third: NormalizedPoint) -> float | None:
    """Interior angle at ``vertex`` in degrees, or None when degenerate."""
    ax, ay = first.x - vertex.x, first.y - vertex.y
    bx, by = third.x - vertex.x, third.y - vertex.y
    length_a = math.hypot(ax, ay)
    length_b = math.hypot(bx, by)
    if length_a < _EPSILON or length_b < _EPSILON:
        return None
    cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / (length_a * length_b)))
    return math.degrees(math.acos(cosine))


def _midpoint(first: NormalizedPoint, second: NormalizedPoint) -> NormalizedPoint:
    return NormalizedPoint(
        (first.x + second.x) / 2.0,
        (first.y + second.y) / 2.0,
        min(first.confidence, second.confidence),
    )


def _torso_angle_from_vertical(shoulders: NormalizedPoint, hips: NormalizedPoint) -> float | None:
    """Degrees away from vertical: 0 is upright, 90 is horizontal."""
    dx = shoulders.x - hips.x
    dy = shoulders.y - hips.y
    if math.hypot(dx, dy) < _EPSILON:
        return None
    return abs(math.degrees(math.atan2(abs(dx), abs(dy))))


def _linear_support(value: float, limit: float) -> float:
    """1.0 well inside the limit, 0.0 at or beyond it."""
    if limit <= _EPSILON:
        return 0.0
    if value <= 0.0:
        return 1.0
    if value >= limit:
        return 0.0
    return 1.0 - (value / limit)


@dataclass
class _PoseSample:
    at_ms: int
    torso_angle: float | None
    knee_flexion: float | None
    aspect: float | None
    wrist_above_shoulder: bool | None
    hip_center_y: float | None
    available_ratio: float

    def rules(self) -> dict[PoseState, float]:
        """Supported poses and their confidence for this single frame."""
        supported: dict[PoseState, float] = {}
        supports: list[float] = []
        if self.available_ratio < MIN_POSE_KEYPOINT_RATIO:
            # Too few usable keypoints: the honest answer is unknown, never a
            # guess assembled from whatever survived occlusion.
            return supported

        standing_ok = self.torso_angle is not None and self.torso_angle <= STANDING_TORSO_MAX_DEGREES
        if standing_ok and self.knee_flexion is not None and self.knee_flexion < STANDING_KNEE_MIN_DEGREES:
            standing_ok = False
        if standing_ok and self.aspect is not None and self.aspect >= 1.0:
            standing_ok = False
        if standing_ok:
            supports = [_linear_support(self.torso_angle or 0.0, STANDING_TORSO_MAX_DEGREES)]
            if self.knee_flexion is not None:
                supports.append(min(1.0, max(0.0, (self.knee_flexion - STANDING_KNEE_MIN_DEGREES) / 30.0 + 0.5)))
            if self.aspect is not None:
                supports.append(1.0 - min(1.0, max(0.0, self.aspect)))
            supported[PoseState.STANDING] = min(supports)

        sitting_ok = self.torso_angle is not None and self.torso_angle <= SITTING_TORSO_MAX_DEGREES
        if sitting_ok and (self.knee_flexion is None or self.knee_flexion > SITTING_KNEE_MAX_DEGREES):
            sitting_ok = False
        if sitting_ok and self.aspect is not None and self.aspect >= 1.0:
            sitting_ok = False
        if sitting_ok:
            supports = [_linear_support(self.torso_angle or 0.0, SITTING_TORSO_MAX_DEGREES)]
            if self.knee_flexion is not None:
                supports.append(_linear_support(SITTING_KNEE_MAX_DEGREES - self.knee_flexion, SITTING_KNEE_MAX_DEGREES))
            supported[PoseState.SITTING] = min(supports)

        lying_by_torso = self.torso_angle is not None and self.torso_angle >= LYING_TORSO_MIN_DEGREES
        lying_by_shape = (
            self.aspect is not None
            and self.aspect >= LYING_ASPECT_MIN
            and self.available_ratio >= MIN_SHAPE_LYING_KEYPOINT_RATIO
        )
        if lying_by_torso or lying_by_shape:
            supports = []
            if self.torso_angle is not None:
                supports.append(min(1.0, max(0.0, (self.torso_angle - LYING_TORSO_MIN_DEGREES) / 30.0 + 0.5)))
            if self.aspect is not None:
                supports.append(min(1.0, self.aspect / LYING_ASPECT_MIN / 1.5))
            supported[PoseState.LYING] = min(supports) if supports else 0.0

        if self.wrist_above_shoulder:
            supported[PoseState.HAND_RAISED] = 1.0

        return supported


class PoseStateMachine:
    """Stabilizes a pose across time with entry and exit hysteresis."""

    def __init__(self, config: VisionConfig) -> None:
        self._keypoint_min_confidence = float(config.keypoint_min_confidence)
        self._tracks: dict[str, "_TrackHistory"] = {}

    def reset_session(self) -> None:
        self._tracks.clear()

    def forget(self, track_id: str) -> None:
        self._tracks.pop(track_id, None)

    def update(
        self,
        track_id: str,
        keypoints: tuple[NormalizedPoint, ...],
        bbox: tuple[float, float, float, float],
        at_ms: int,
    ) -> PoseEstimate:
        history = self._tracks.setdefault(track_id, _TrackHistory())
        sample = _build_sample(keypoints, bbox, at_ms, self._keypoint_min_confidence)
        history.push(sample)
        return history.resolve(at_ms)


@dataclass
class _TrackHistory:
    samples: deque[_PoseSample] = field(default_factory=lambda: deque(maxlen=HISTORY_MAX_SAMPLES))
    current: PoseState = PoseState.UNKNOWN
    current_confidence: float = 0.0
    candidate: PoseState | None = None
    candidate_since_ms: int = 0
    unsupported_since_ms: int | None = None
    fall_drop_at_ms: int | None = None
    lying_since_ms: int | None = None

    def push(self, sample: _PoseSample) -> None:
        self.samples.append(sample)
        cutoff = sample.at_ms - HISTORY_MS
        while self.samples and self.samples[0].at_ms < cutoff:
            self.samples.popleft()

    def resolve(self, at_ms: int) -> PoseEstimate:
        supported = self._supported_now(at_ms)
        self._update_fall(at_ms, supported)

        target, confidence = self._select(supported)

        if target == self.current and target != PoseState.UNKNOWN:
            self.unsupported_since_ms = None
            self.current_confidence = confidence
            return PoseEstimate(self.current, confidence)

        if target != self.candidate:
            self.candidate = target
            self.candidate_since_ms = at_ms

        # A fall is urgent, so it enters on its own (already sustained) window
        # rather than waiting the generic entry delay.
        entry_ms = 0 if target == PoseState.SUSPECTED_FALL else ENTER_MS
        if target != PoseState.UNKNOWN and at_ms - self.candidate_since_ms >= entry_ms:
            self.current = target
            self.current_confidence = confidence
            self.unsupported_since_ms = None
            return PoseEstimate(self.current, self.current_confidence)

        if target == PoseState.UNKNOWN:
            if self.unsupported_since_ms is None:
                self.unsupported_since_ms = at_ms
            if self.current == PoseState.UNKNOWN or at_ms - self.unsupported_since_ms >= EXIT_MS:
                self.current = PoseState.UNKNOWN
                self.current_confidence = 0.0
                return PoseEstimate(PoseState.UNKNOWN, 0.0)
            return PoseEstimate(self.current, self.current_confidence)

        # Supported but not yet confirmed: report the previous stable state.
        return PoseEstimate(self.current, self.current_confidence)

    def _supported_now(self, at_ms: int) -> dict[PoseState, float]:
        """Poses supported by *every* recent sample inside the entry window."""
        window = [item for item in self.samples if at_ms - item.at_ms <= ENTER_MS]
        if not window:
            return {}
        collected: dict[PoseState, float] = {}
        for sample in window:
            rules = sample.rules()
            for pose, confidence in rules.items():
                collected[pose] = min(collected.get(pose, 1.0), confidence) if pose in collected else confidence
        # Intersection: a pose only survives if the newest window still supports it.
        for pose in list(collected):
            if any(pose not in sample.rules() for sample in window):
                collected.pop(pose, None)
        return collected

    def _update_fall(self, at_ms: int, supported: dict[PoseState, float]) -> None:
        lying = PoseState.LYING in supported or PoseState.SUSPECTED_FALL in supported
        if lying:
            if self.lying_since_ms is None:
                self.lying_since_ms = at_ms
        else:
            self.lying_since_ms = None

        if _rapid_descent(self.samples):
            self.fall_drop_at_ms = at_ms

        if (
            self.fall_drop_at_ms is not None
            and lying
            and self.lying_since_ms is not None
            and at_ms - self.lying_since_ms >= FALL_LYING_MS
            and at_ms - self.fall_drop_at_ms <= FALL_DROP_WINDOW_MS + FALL_LYING_MS
        ):
            supported[PoseState.SUSPECTED_FALL] = supported.get(PoseState.LYING, 0.5)

    def _select(self, supported: dict[PoseState, float]) -> tuple[PoseState, float]:
        if not supported:
            return PoseState.UNKNOWN, 0.0
        if PoseState.SUSPECTED_FALL in supported:
            # A fall outranks plain lying.
            return PoseState.SUSPECTED_FALL, supported[PoseState.SUSPECTED_FALL]

        mutually_exclusive = [
            pose for pose in (PoseState.STANDING, PoseState.SITTING, PoseState.LYING) if pose in supported
        ]
        if len(mutually_exclusive) > 1:
            # Contradictory body evidence must not be resolved by picking the
            # bigger number: the honest answer is that the pose is unknown.
            return PoseState.UNKNOWN, 0.0
        base = mutually_exclusive[0] if mutually_exclusive else None

        if PoseState.HAND_RAISED in supported and base is not PoseState.LYING:
            # A raised hand is the headline state for an upright person, so it
            # outranks standing and sitting. It deliberately does not outrank
            # lying: a raised arm must never mask someone on the floor.
            base_support = supported.get(base, 1.0) if base is not None else 1.0
            return PoseState.HAND_RAISED, min(supported[PoseState.HAND_RAISED], base_support)

        if base is not None:
            return base, supported[base]
        return PoseState.UNKNOWN, 0.0


def _rapid_descent(samples: deque[_PoseSample]) -> bool:
    """True when the hip centre fell fast enough to be a fall, not a lie-down."""
    points = [item for item in samples if item.hip_center_y is not None]
    if len(points) < 2:
        return False
    newest = points[-1]
    for previous in reversed(points[:-1]):
        elapsed_ms = newest.at_ms - previous.at_ms
        if elapsed_ms <= 0:
            continue
        if elapsed_ms > FALL_DROP_WINDOW_MS:
            break
        drop = newest.hip_center_y - previous.hip_center_y
        if drop / (elapsed_ms / 1000.0) >= FALL_DROP_PER_SECOND:
            return True
    return False


def _build_sample(
    keypoints: tuple[NormalizedPoint, ...],
    bbox: tuple[float, float, float, float],
    at_ms: int,
    keypoint_min_confidence: float,
) -> _PoseSample:
    available = [point for point in keypoints if point.confidence >= keypoint_min_confidence]
    available_ratio = len(available) / float(KEYPOINT_COUNT) if keypoints else 0.0

    def usable(index: int) -> NormalizedPoint | None:
        if index >= len(keypoints):
            return None
        point = keypoints[index]
        return point if point.confidence >= keypoint_min_confidence else None

    torso_angle: float | None = None
    shoulders = [point for point in (usable(LEFT_SHOULDER), usable(RIGHT_SHOULDER)) if point]
    hips = [point for point in (usable(LEFT_HIP), usable(RIGHT_HIP)) if point]
    if len(shoulders) == 2 and len(hips) == 2:
        torso_angle = _torso_angle_from_vertical(
            _midpoint(shoulders[0], shoulders[1]), _midpoint(hips[0], hips[1])
        )

    knee_flexion = _knee_flexion(usable)
    aspect = None
    width = abs(bbox[2] - bbox[0])
    height = abs(bbox[3] - bbox[1])
    if height > _EPSILON:
        aspect = width / height

    wrist_above = _wrist_above_shoulder(usable)
    hip_center_y = None
    if len(hips) == 2:
        hip_center_y = _midpoint(hips[0], hips[1]).y

    return _PoseSample(
        at_ms=at_ms,
        torso_angle=torso_angle,
        knee_flexion=knee_flexion,
        aspect=aspect,
        wrist_above_shoulder=wrist_above,
        hip_center_y=hip_center_y,
        available_ratio=available_ratio,
    )


def _knee_flexion(usable) -> float | None:
    """Smallest available hip-knee-ankle angle; lower means more bent."""
    angles: list[float] = []
    for hip_index, knee_index, ankle_index in (
        (LEFT_HIP, LEFT_KNEE, LEFT_ANKLE),
        (RIGHT_HIP, RIGHT_KNEE, RIGHT_ANKLE),
    ):
        hip, knee, ankle = usable(hip_index), usable(knee_index), usable(ankle_index)
        if hip is None or knee is None or ankle is None:
            continue
        angle = _angle_degrees(hip, knee, ankle)
        if angle is not None:
            angles.append(angle)
    return min(angles) if angles else None


def _wrist_above_shoulder(usable) -> bool | None:
    """True when either wrist is clearly above its own shoulder.

    Image y grows downward, so "above" means a smaller y value.
    """
    seen = False
    for shoulder_index, wrist_index in ((LEFT_SHOULDER, 9), (RIGHT_SHOULDER, 10)):
        shoulder, wrist = usable(shoulder_index), usable(wrist_index)
        if shoulder is None or wrist is None:
            continue
        seen = True
        if wrist.y < shoulder.y - HAND_RAISE_MIN_MARGIN:
            return True
    return False if seen else None


class PoseTracker:
    """Owns the YOLO pose model and ByteTrack for one capture session."""

    def __init__(self, config: VisionConfig) -> None:
        self.config = config
        self._model: Any = None
        self._loaded = False

    # ------------------------------------------------------------- lifecycle
    def load(self) -> None:
        """Import and construct the model. Never called during module import."""
        from ultralytics import YOLO

        model_path = self.config.pose_model
        if not model_path.exists():
            # A missing local model must be reported, never downloaded silently.
            raise FileNotFoundError("pose_model_missing")
        self._model = YOLO(str(model_path))
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    def reset_session(self) -> None:
        """Drop ByteTrack ids so no track can survive a session change."""
        predictor = getattr(self._model, "predictor", None)
        trackers = getattr(predictor, "trackers", None) if predictor is not None else None
        if not trackers:
            return
        for tracker in trackers:
            reset = getattr(tracker, "reset", None)
            if callable(reset):
                reset()

    # ------------------------------------------------------------- inference
    def process(self, frame: CapturedFrame, session_id: str) -> list[BodyTrack]:
        if not self._loaded or self._model is None:
            raise RuntimeError("pose_model_not_loaded")

        results = self._model.track(
            frame.frame_bgr,
            persist=True,
            tracker="bytetrack.yaml",
            classes=[0],
            conf=self.config.body_min_confidence,
            verbose=False,
        )
        if not results:
            return []
        result = results[0]

        width = float(frame.width or 0) or float(getattr(frame.frame_bgr, "shape", (0, 0, 0))[1])
        height = float(frame.height or 0) or float(getattr(frame.frame_bgr, "shape", (0, 0, 0))[0])
        if width <= 0 or height <= 0:
            return []

        boxes = getattr(result, "boxes", None)
        keypoints = getattr(result, "keypoints", None)
        if boxes is None or keypoints is None or boxes.id is None:
            return []

        tracks: list[BodyTrack] = []
        identifiers = boxes.id.tolist()
        coordinates = boxes.xyxy.tolist()
        confidences = boxes.conf.tolist()
        points = keypoints.data.tolist()

        for index, raw_id in enumerate(identifiers):
            bbox = _normalized_box(coordinates[index], width, height)
            if bbox is None:
                continue
            track_points = _normalized_keypoints(points[index], width, height)
            if track_points is None:
                continue
            tracks.append(
                BodyTrack(
                    # Prefixing the session id is what makes a stale track unable
                    # to inherit another session's temporal evidence.
                    track_id=f"{session_id}:{int(raw_id)}",
                    bbox=bbox,
                    keypoints=track_points,
                    confidence=float(confidences[index]),
                )
            )
        return tracks


def _normalized_box(values: list[float], width: float, height: float):
    if len(values) < 4:
        return None
    x1 = max(0.0, min(1.0, float(values[0]) / width))
    y1 = max(0.0, min(1.0, float(values[1]) / height))
    x2 = max(0.0, min(1.0, float(values[2]) / width))
    y2 = max(0.0, min(1.0, float(values[3]) / height))
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _normalized_keypoints(values: list[list[float]], width: float, height: float):
    if not values:
        return None
    points: list[NormalizedPoint] = []
    for item in values:
        if len(item) < 3:
            return None
        points.append(
            NormalizedPoint(
                x=max(0.0, min(1.0, float(item[0]) / width)),
                y=max(0.0, min(1.0, float(item[1]) / height)),
                confidence=max(0.0, min(1.0, float(item[2]))),
            )
        )
    return tuple(points)
