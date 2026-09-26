"""Six-step face registration with strict quality gates and a short confirmation.

Steps advance through `front`, `turn_left`, `turn_right`, `look_up`, `look_down`
and `blink`. Five steps each collect `SAMPLES_PER_STEP` quality-approved samples
and store exactly one normalized prototype centroid; `blink` proves the action
and stores no prototype, because a closed-eye frame must never become biometric
data. After the last step the previous record for the person is replaced in one
registry write and the session enters `confirming` for
`CONFIRMATION_WINDOW_MS`, during which `CONFIRMATION_MIN_VOTES` accepted frames
must name the same person.

Cancelling before the atomic replacement leaves previously stored features
untouched; a failed confirmation keeps the newly written record and reports
`confirmation_failed` so the user can retry or delete it.
"""

from __future__ import annotations

import math
from collections import Counter

from contracts import (
    valid_person_id,
    FaceSample,
    RegistrationStatus,
)
from face import FaceEngine
from registry import FaceRegistry


# Ordered registration actions; the index drives `step_index`.
STEPS: tuple[str, ...] = ("front", "turn_left", "turn_right", "look_up", "look_down", "blink")
# Quality-approved samples needed for every step except `blink`.
SAMPLES_PER_STEP = 2
# Samples needed for the `blink` step; one eye cycle is enough proof.
BLINK_REQUIRED_SAMPLES = 1
# How long the short post-registration confirmation stays open.
CONFIRMATION_WINDOW_MS = 5000
# Accepted frames naming the same person required to confirm.
CONFIRMATION_MIN_VOTES = 3
# Grace period before the first accepted sample of a step is reported as stalled.
STEP_STALL_MS = 15000
# Frames further apart than this do not continue one blink cycle.
BLINK_MAX_GAP_MS = 700
# Eye-closed threshold as a fraction of the calibrated neutral-open eyelid ratio.
EYELID_CLOSED_RATIO = 0.6
# Absolute floor for the closed ratio, so a dark or occluded eye cannot pass.
EYELID_CLOSED_MIN = 0.10
# Absolute ceiling for the closed ratio, so a wide-open eye is never "closed".
EYELID_CLOSED_MAX = 0.30
# Neutral-open eyelid observations required before a blink cycle can be accepted.
EYELID_CALIBRATION_SAMPLES = 5
# Minimum horizontal eye width in normalized units for a usable eyelid ratio.
EYELID_MIN_EYE_WIDTH = 0.01
# Additional eye-width requirement relative to the detected face box width.
EYELID_MIN_EYE_WIDTH_RATIO = 0.08
# Minimum landmark score for the 106 landmarks used by the eyelid ratio.
EYELID_MIN_LANDMARK_SCORE = 0.30
# Face-relative yaw (eye-midpoint offset / eye distance) outside which the head
# counts as turned.
YAW_TURN_RATIO = 0.18
# Face-relative yaw inside which the head counts as facing the camera.
YAW_FRONT_RATIO = 0.12
# Minimum change in the nose's eye-to-mouth position from the user's neutral
# samples. Eye-to-mouth length alone shortens in BOTH directions when nodding.
NOD_POSITION_CHANGE = 0.08
# Pitch range still counted as neutral for the `front` step. The upper edge
# includes the measured 16:9 live-camera face after aspect-ratio correction.
PITCH_FRONT_MIN_RATIO = 0.66
PITCH_FRONT_MAX_RATIO = 1.10
# Minimum score for each of the five landmarks used by the direction rules.
DIRECTION_MIN_LANDMARK_SCORE = 0.40
# InsightFace 2d106det contour order: corner, upper1, upper2, other corner,
# lower2, lower1. Points 33/87 are lower eyelid centres, NOT eye corners.
# Official markup linked from alignment/coordinate_reg/README.md:
# https://github.com/nttstar/insightface-resources/blob/master/alignment/images/2d106markup.jpg
_EYELID_CONTOURS = ((35, 41, 42, 39, 37, 36), (89, 95, 96, 93, 91, 90))
# Session states reported through `RegistrationStatus.state`.
STATE_COLLECTING = "collecting"
STATE_CONFIRMING = "confirming"
STATE_CONFIRMED = "confirmed"
STATE_CONFIRMATION_FAILED = "confirmation_failed"
STATE_CANCELLED = "cancelled"
# Raised by the owner rather than the user, e.g. the camera disappeared mid-step.
STATE_FAILED = "failed"
# The final "record stored" state name. `completed` is used because the pipeline
# and the Unity panel key off it; `confirmed` is accepted as an equal synonym.
STATE_COMPLETED = "completed"
# States in which the caller may stop feeding frames.
_TERMINAL_STATES = frozenset({
    STATE_CONFIRMED, STATE_COMPLETED, STATE_CONFIRMATION_FAILED, STATE_CANCELLED, STATE_FAILED,
})
# Quality reason attached to most failures of the final confirmation.
CONFIRMATION_REASON = "confirmation_failed"
# Owner-raised interruption reasons surfaced through `abort`, with the wording the
# panel shows so the user is told what actually stopped enrolment.
_ABORT_MESSAGES = {
    "camera_open_failed": "摄像头打开失败，注册已中断",
    "camera_disconnected": "摄像头已断开，注册已中断",
    "source_changed": "摄像头已切换，注册已中断",
    "model_error": "模型异常，注册已中断",
}


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, value))


class BlinkCalibration:
    """Adaptive open-eye reference derived from the same session's eyelid ratios.

    The absolute eyelid ratio produced by the 106 landmarks depends on face size,
    glasses and camera geometry, so the closed threshold is a fraction of the
    widest ratio seen while the eyes were neutral and open, clamped into a fixed
    band. A subject who never opens their eyes cannot calibrate, and the step
    simply never completes.
    """

    def __init__(self) -> None:
        self.max_open_ratio = 0.0
        self.open_samples = 0

    def observe_open(self, ratio: float) -> None:
        self.open_samples += 1
        if ratio > self.max_open_ratio:
            self.max_open_ratio = ratio

    def closed_ratio(self) -> float:
        if self.open_samples < EYELID_CALIBRATION_SAMPLES:
            return EYELID_CLOSED_MIN
        calibrated = self.max_open_ratio * EYELID_CLOSED_RATIO
        calibrated = min(calibrated, EYELID_CLOSED_MAX)
        return max(EYELID_CLOSED_MIN, calibrated)


class BlinkDetector:
    """Requires an open -> closed -> open cycle with a gap limit between frames."""

    def __init__(self) -> None:
        self.state = "open"
        self.cycles = 0
        self.last_at_ms: int | None = None

    def reset(self) -> None:
        self.state = "open"
        self.last_at_ms = None

    def update(self, ratio: float, threshold: float, at_ms: int) -> bool:
        if self.last_at_ms is not None and at_ms - self.last_at_ms > BLINK_MAX_GAP_MS:
            self.reset()
        self.last_at_ms = at_ms
        closed = ratio < threshold
        if self.state == "open" and closed:
            self.state = "closed"
        elif self.state == "closed" and not closed:
            self.state = "open"
            self.cycles += 1
            return True
        return False


class RegistrationSession:
    """Collect the six registration actions, then run a short identity confirmation."""

    def __init__(
        self,
        person_id: str,
        config: object,
        engine: FaceEngine | None = None,
        registry: FaceRegistry | None = None,
    ) -> None:
        if not valid_person_id(person_id):
            raise ValueError("person_id is invalid")
        self.person_id = person_id
        self.config = config
        self._engine = engine if engine is not None else FaceEngine(config)
        self._registry = registry
        self.prototypes_collected: list[tuple[float, ...]] = []
        self._step_embeddings: list[tuple[float, ...]] = []
        self._neutral_nose_positions: list[float] = []
        self._action_progress: float | None = None
        self._samples_in_step = 0
        self._blinks = 0
        self._blink = BlinkDetector()
        self._calibration = BlinkCalibration()
        self._record_stored = False
        self._step_started_ms: int | None = None
        self._updated_at_ms = 0
        self._state = STATE_COLLECTING
        self._quality_reason: str | None = None
        self._rejection_counts: Counter[str] = Counter()
        self._message: str | None = None
        self._confirmation_started_ms: int = 0
        self._confirmation_votes = 0
        self._confirmation_person: str | None = None
        self._steps: list[str] = list(STEPS)

    # ------------------------------------------------------------- properties

    @property
    def step(self) -> str:
        return self._steps[0] if self._steps else STEPS[-1]

    @property
    def step_index(self) -> int:
        # Public progress is one-based because the Unity panel renders it as
        # "step N of 6". Internally the tuple naturally remains zero-based.
        return STEPS.index(self.step) + 1 if self._steps else len(STEPS)

    @property
    def state(self) -> str:
        return self._state

    @property
    def quality_reason(self) -> str | None:
        return self._quality_reason

    def required_samples(self) -> int:
        return BLINK_REQUIRED_SAMPLES if self.step == "blink" else SAMPLES_PER_STEP

    # ------------------------------------------------------------- public API

    def bind_registry(self, registry: FaceRegistry) -> None:
        """Attach the registry whose single write replaces the person record."""

        self._registry = registry

    def accept(
        self, frame_bgr: object, faces: list[FaceSample], at_ms: int,
    ) -> RegistrationStatus:
        """Consume one frame and return the current registration status.

        `faces` must come from the engine's analysis of this same frame. That
        analysis also caches the dense 106 landmarks used by blink detection, so
        running the model a second time would only waste latency.
        """

        self._updated_at_ms = int(at_ms)
        if self._state in _TERMINAL_STATES:
            return self.status()
        if self._state == STATE_CONFIRMING:
            return self._accept_confirmation(frame_bgr, faces, at_ms)
        sample, failure = self._quality_gate(frame_bgr, faces)
        if sample is None:
            return self._reject(failure or "no_face", at_ms)

        if self.step == "blink":
            return self._accept_blink(sample, at_ms)

        direction, ratios = self._head_direction(sample)
        nose_position = self._nose_position(sample)
        if direction is None or nose_position is None:
            return self._reject("landmarks_incomplete", at_ms)
        if not self._matches_step(direction, ratios, nose_position):
            return self._reject("wrong_action", at_ms)

        embedding = sample.embedding
        if embedding is None:
            return self._reject(sample.quality.reason or "embedding_unavailable", at_ms)
        self._step_embeddings.append(tuple(float(value) for value in embedding))
        if self.step == "front":
            self._neutral_nose_positions.append(nose_position)
        self._samples_in_step += 1
        self._quality_reason = None
        self._message = None
        if self._samples_in_step < self.required_samples():
            return self.status()
        return self._complete_step(at_ms)

    def status(self) -> RegistrationStatus:
        """Immutable snapshot of the progress; also drives the Unity panel."""

        remaining = 0
        state = self._state
        reason = self._quality_reason
        if state == STATE_CONFIRMING:
            elapsed = self._updated_at_ms - self._confirmation_started_ms
            if elapsed > CONFIRMATION_WINDOW_MS:
                self._fail_confirmation()
                state = self._state
                reason = self._quality_reason
            else:
                remaining = max(0, CONFIRMATION_WINDOW_MS - elapsed)
        return RegistrationStatus(
            person_id=self.person_id,
            step=self.step,
            step_index=self.step_index,
            step_count=len(STEPS),
            accepted_samples=self._samples_in_step,
            required_samples=self.required_samples(),
            state=state,
            updated_at_ms=self._updated_at_ms,
            quality_reason=reason,
            confirmation_remaining_ms=max(0, remaining),
            message=self._message,
            action_progress=self._action_progress,
            rejection_counts=dict(self._rejection_counts),
        )

    def cancel(self) -> RegistrationStatus:
        """Stop collecting without touching any previously stored features.

        After the atomic write the record is already replaced, so cancelling only
        closes the confirmation window and reports the stored outcome honestly
        instead of claiming the stored features were left unchanged.
        """

        if self._state in _TERMINAL_STATES or self._record_stored:
            return self.status()
        self._state = STATE_CANCELLED
        self._quality_reason = None
        self._message = "cancelled by user; stored features unchanged"
        self._step_embeddings = []
        self._samples_in_step = 0
        self._blink.reset()
        return self.status()

    def prototypes(self) -> list[tuple[float, ...]]:
        """Collected prototype centroids, one per completed non-blink step.

        Read-only: an unfinished step is reported through `draft_prototypes`
        instead, so repeated calls can never duplicate a centroid.
        """

        return list(self.prototypes_collected)

    def abort(self, reason: str, at_ms: int) -> RegistrationStatus:
        """Terminal failure raised by the owner, for example a camera that vanished.

        Unlike `cancel`, this records *why* enrolment stopped. Without it the API
        would keep reporting the last progress of a session that can never
        advance, which reads to the user as "still registering".
        """

        if self._state in _TERMINAL_STATES or self._record_stored:
            return self.status()
        self._state = STATE_FAILED
        self._quality_reason = reason
        self._message = _ABORT_MESSAGES.get(reason, "注册已中断，已保存的特征保持不变")
        self._step_embeddings = []
        self._samples_in_step = 0
        self._blink.reset()
        self._updated_at_ms = at_ms
        return self.status()

    def finished(self) -> bool:
        """True once no further frame can change the outcome."""

        self.status()
        return self._state in _TERMINAL_STATES

    def draft_prototypes(self) -> list[tuple[float, ...]]:
        """Centroids collected so far, including an unfinished step, for preview."""

        collected = list(self.prototypes_collected)
        if self._step_embeddings:
            collected.append(self._centroid(self._step_embeddings))
        return collected

    def register_confirmation_candidate(self, person_id: str | None) -> None:
        """Record one ranked identity as a confirmation vote when it matches."""

        if self._state != STATE_CONFIRMING:
            return
        if person_id != self.person_id:
            return
        if self._confirmation_person not in (None, person_id):
            return
        self._confirmation_person = person_id
        self._confirmation_votes += 1
        if self._confirmation_votes >= CONFIRMATION_MIN_VOTES:
            self._state = STATE_COMPLETED
            self._quality_reason = None
            self._message = "confirmation complete"

    # ------------------------------------------------------------- internals

    def _accept_confirmation(
        self, frame_bgr: object, faces: list[FaceSample], at_ms: int,
    ) -> RegistrationStatus:
        if at_ms - self._confirmation_started_ms > CONFIRMATION_WINDOW_MS:
            self._fail_confirmation()
            return self.status()
        sample, failure = self._quality_gate(frame_bgr, faces)
        if sample is None:
            self._quality_reason = failure or "no_face"
            return self.status()
        self._quality_reason = None
        ranking = self._rank_confirmation(sample)
        self.register_confirmation_candidate(ranking)
        return self.status()

    def _rank_confirmation(self, sample: FaceSample) -> str | None:
        registry = self._registry
        embedding = sample.embedding
        if registry is None or embedding is None:
            return None
        try:
            candidates = registry.rank(embedding)
        except Exception:
            # A registry failure must not confirm an identity; it also must not
            # leak through as a traceback into the API response.
            self._message = "registry unavailable during confirmation"
            return None
        if not candidates:
            return None
        candidate = candidates[0]
        threshold = float(getattr(self.config, "identity_similarity_threshold", 0.55))
        margin_threshold = float(getattr(self.config, "identity_margin_threshold", 0.08))
        if candidate.person_id is None or (candidate.similarity or 0.0) < threshold:
            return None
        if candidate.margin is not None and candidate.margin < margin_threshold:
            return None
        return candidate.person_id

    def _fail_confirmation(self) -> None:
        if self._state != STATE_CONFIRMING:
            return
        self._state = STATE_CONFIRMATION_FAILED
        self._quality_reason = CONFIRMATION_REASON
        self._message = "confirmation failed; the new record is stored, retry or delete"

    def _quality_gate(
        self, frame_bgr: object, samples: list[FaceSample],
    ) -> tuple[FaceSample | None, str | None]:
        """Return the single acceptable face, or a stable snake_case reason."""

        shape = getattr(frame_bgr, "shape", None)
        if shape is None or len(shape) < 3:
            return None, "camera_interrupted"
        if not samples:
            return None, "no_face"
        if len(samples) > 1:
            return None, "multiple_faces"
        sample = samples[0]
        if not sample.quality.ok:
            return None, sample.quality.reason or "quality_rejected"
        if len(sample.landmarks) < 5:
            return None, "landmarks_incomplete"
        return sample, None

    def _reject(self, reason: str, at_ms: int) -> RegistrationStatus:
        self._rejection_counts[reason] += 1
        self._quality_reason = reason
        if reason != "wrong_action":
            self._action_progress = None
        self._message = None
        started = self._step_started_ms
        if started is not None and at_ms - started > STEP_STALL_MS:
            self._message = "no accepted sample for this step yet"
        return self.status()

    def _matches_step(
        self, direction: str, ratios: tuple[float | None, float | None],
        nose_position: float | None = None,
    ) -> bool:
        yaw, pitch = ratios
        step = self.step
        if step == "front":
            if yaw is None or pitch is None:
                return False
            return abs(yaw) <= YAW_FRONT_RATIO and PITCH_FRONT_MIN_RATIO <= pitch <= PITCH_FRONT_MAX_RATIO
        if step == "turn_left":
            # The subject's left is image-right in an unmirrored camera frame.
            return yaw is not None and yaw >= YAW_TURN_RATIO
        if step == "turn_right":
            return yaw is not None and yaw <= -YAW_TURN_RATIO
        if step in {"look_up", "look_down"}:
            if nose_position is None or not self._neutral_nose_positions or yaw is None:
                self._action_progress = None
                return False
            baseline = sum(self._neutral_nose_positions) / len(self._neutral_nose_positions)
            change = nose_position - baseline
            if step == "look_up":
                change = -change
            self._action_progress = _clamp_ratio(change / NOD_POSITION_CHANGE)
            return abs(yaw) <= YAW_TURN_RATIO and change >= NOD_POSITION_CHANGE
        return False

    def _nose_position(self, sample: FaceSample) -> float | None:
        """Nose projection along the eye-to-mouth axis in pixel-equivalent space.

        Compare against this session's front samples to account for face shape
        and camera height. Translation, scale and in-plane head roll cancel out.
        """
        if len(sample.landmarks) < 5:
            return None
        aspect = max(float(sample.frame_aspect_ratio), 1e-6)
        left, right, nose, mouth_left, mouth_right = sample.landmarks[:5]
        eye_x, eye_y = (left.x + right.x) / 2, (left.y + right.y) / (2 * aspect)
        dx = (mouth_left.x + mouth_right.x) / 2 - eye_x
        dy = (mouth_left.y + mouth_right.y) / (2 * aspect) - eye_y
        squared_length = dx * dx + dy * dy
        if squared_length <= 1e-12:
            return None
        return ((nose.x - eye_x) * dx + (nose.y / aspect - eye_y) * dy) / squared_length

    def _head_direction(
        self, sample: FaceSample,
    ) -> tuple[str | None, tuple[float | None, float | None]]:
        """Derive yaw/pitch ratios from landmarks in pixel-equivalent space."""

        if len(sample.landmarks) < 5:
            return None, (None, None)
        left_eye, right_eye, nose, mouth_left, mouth_right = sample.landmarks[:5]
        points = (left_eye, right_eye, nose, mouth_left, mouth_right)
        if any(point.confidence < DIRECTION_MIN_LANDMARK_SCORE for point in points):
            return None, (None, None)
        eye_distance = abs(right_eye.x - left_eye.x)
        eye_mid_x = (left_eye.x + right_eye.x) / 2.0
        eye_mid_y = (left_eye.y + right_eye.y) / 2.0
        mouth_mid_y = (mouth_left.y + mouth_right.y) / 2.0
        if eye_distance <= 1e-6:
            return None, (None, None)
        yaw = (nose.x - eye_mid_x) / eye_distance
        mouth_gap = mouth_mid_y - eye_mid_y
        # x and y are normalized against different frame dimensions. Convert
        # the vertical ratio back to pixel-equivalent units before comparing it
        # with the pose thresholds, or a 16:9 frame exaggerates pitch by 1.78x.
        aspect = max(float(sample.frame_aspect_ratio), 1e-6)
        pitch: float | None = mouth_gap / (eye_distance * aspect) if mouth_gap > 1e-6 else None
        if abs(yaw) <= YAW_FRONT_RATIO:
            direction = "front"
        elif yaw > 0.0:
            direction = "turn_left"
        else:
            direction = "turn_right"
        return direction, (yaw, pitch)

    def _accept_blink(self, sample: FaceSample, at_ms: int) -> RegistrationStatus:
        """Advance `blink` only on a proven open -> closed -> open cycle."""

        ratio = self._eyelid_ratio(sample)
        if ratio is None:
            return self._reject("landmarks_incomplete", at_ms)
        # A quality-approved frame supersedes the previous frame's rejection,
        # even while we are still waiting for the eye cycle to complete.
        self._quality_reason = None
        threshold = self._calibration.closed_ratio()
        if ratio >= threshold:
            self._calibration.observe_open(ratio)
        if self._calibration.open_samples < EYELID_CALIBRATION_SAMPLES:
            self._message = "calibrating open eyes; look at the camera"
            return self.status()
        if self._blink.update(ratio, threshold, at_ms):
            self._blinks += 1
            self._samples_in_step = self._blinks
            self._quality_reason = None
            self._message = None
            if self._blinks >= BLINK_REQUIRED_SAMPLES:
                return self._advance("blink proven", at_ms)
        else:
            self._message = "blink not proven yet; keep the face steady and blink"
        return self.status()

    def _eyelid_ratio(self, sample: FaceSample) -> float | None:
        """Mean eye aspect ratio from the two InsightFace 106-point contours."""

        points = self._engine.landmarks_106(sample.bbox)
        if not points:
            return None
        index_count = len(points)
        # Distances must share one scale: x is normalized by width, y by height.
        # This also preserves the ratio when the head rolls slightly.
        aspect = max(float(sample.frame_aspect_ratio), 1e-6)
        ratios: list[float] = []
        for contour in _EYELID_CONTOURS:
            if max(contour) >= index_count:
                return None
            eye = [points[index] for index in contour]
            if any(score < EYELID_MIN_LANDMARK_SCORE for _, _, score in eye):
                return None
            positions = [(x, y / aspect) for x, y, _ in eye]
            width = math.dist(positions[0], positions[3])
            if width < EYELID_MIN_EYE_WIDTH:
                return None
            if width < EYELID_MIN_EYE_WIDTH_RATIO * (sample.bbox[2] - sample.bbox[0]):
                return None
            vertical = (
                math.dist(positions[1], positions[5])
                + math.dist(positions[2], positions[4])
            )
            ratios.append(vertical / (2.0 * width))
        if not ratios:
            return None
        return _clamp_ratio(sum(ratios) / len(ratios))

    def _complete_step(self, at_ms: int) -> RegistrationStatus:
        centroid = self._centroid(self._step_embeddings)
        self.prototypes_collected.append(centroid)
        return self._advance(f"stored prototype {len(self.prototypes_collected)}", at_ms)

    def _advance(self, message: str, at_ms: int) -> RegistrationStatus:
        completed = self._steps.pop(0) if self._steps else None
        self._step_embeddings = []
        self._samples_in_step = 0
        self._blinks = 0
        self._blink.reset()
        self._action_progress = None
        self._step_started_ms = int(at_ms)
        self._quality_reason = None
        self._message = message
        if not self._steps:
            return self._finalize(at_ms)
        self._message = f"{message}; next action {self.step}"
        return self.status()

    def _finalize(self, at_ms: int) -> RegistrationStatus:
        """Replace the person record in one write, then open the confirmation.

        The reported state is `completed` because the record is already stored; the
        internal state stays `confirming` so further `accept` calls can prove that
        the person is recognizable. A confirmation that never reaches
        `CONFIRMATION_MIN_VOTES` keeps the new record and reports
        `confirmation_failed` once the window closes.
        """

        registry = self._registry
        if registry is not None:
            try:
                registry.replace(self.person_id, list(self.prototypes_collected))
            except Exception:
                self._message = "storing the new prototypes failed; previous record kept"
                self._quality_reason = "registry_write_failed"
                return self.status()
        self._state = STATE_CONFIRMING
        self._confirmation_started_ms = int(at_ms)
        self._confirmation_votes = 0
        self._confirmation_person = None
        self._record_stored = registry is not None
        self._message = (
            "record stored; look at the camera to confirm"
            if registry is not None
            else "prototypes ready; bind a registry to store them"
        )
        return self.status()

    @staticmethod
    def _centroid(vectors: list[tuple[float, ...]]) -> tuple[float, ...]:
        if not vectors:
            return ()
        dim = len(vectors[0])
        if any(len(vector) != dim for vector in vectors):
            raise ValueError("embedding dimension changed during registration")
        summed = [0.0] * dim
        for vector in vectors:
            for index, value in enumerate(vector):
                summed[index] += value
        norm = math.sqrt(sum(value * value for value in summed))
        if norm <= 0.0:
            raise ValueError("centroid must have a non-zero norm")
        return tuple(value / norm for value in summed)


def registration_progress(
    sessions: dict[str, RegistrationSession],
) -> list[dict[str, object]]:
    """Serializable progress of every open registration session."""

    return [session.status().to_dict() for session in sessions.values()]
