"""Single owner of the camera session, identity stabilisation, and publication.

Everything that could publish a person position passes through here. The rules
that matter:

* Only the newest frame is processed; an old frame is dropped, never queued.
* A track identity is confirmed by several agreeing face rankings inside a short
  window, so one unlucky frame cannot rename a person.
* A source or room change withdraws the previous session *before* the new one may
  publish, so a person is never shown in two rooms at once.
* When the models or the camera fail, published state is cleared instead of being
  left to look live.
"""

from __future__ import annotations

import threading
import time
import traceback
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from diagnostics import DiagnosticWriter

from capture import CaptureSession, CameraSource
from config import VisionConfig
from contracts import (
    BodyTrack,
    FaceCandidate,
    FaceSample,
    IdentityState,
    PoseEstimate,
    PoseState,
    RegistrationStatus,
    ServiceMode,
    TrackObservation,
    TrackState,
    VisionSnapshot,
)
from face import FaceEngine, FaceModelMissing, associate_faces
from home_client import HomeSyncClient
from pose import PoseStateMachine, PoseTracker
from registration import RegistrationSession
from registry import FaceRegistry
from speaker_registry import SpeakerRegistry

# Longest edge of the preview JPEG. Small enough to stream at ~1 Hz, large enough
# to read a name and a pose label.
PREVIEW_MAX_EDGE = 960
MAX_CONSECUTIVE_FRAME_ERRORS = 3
DIAGNOSTICS = DiagnosticWriter(component="vision")


def _safe_error_code(error: Exception, default: str) -> str:
    """Keep native numeric error codes out of the public service status."""
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and code else default


def _failure_location(error: Exception) -> dict[str, str | int]:
    """Expose a traceback location without exception text, paths, or frame data."""
    source_dir = Path(__file__).resolve().parent
    frames = traceback.extract_tb(error.__traceback__)
    for frame in reversed(frames):
        if Path(frame.filename).resolve().parent == source_dir:
            return {"type": type(error).__name__, "module": Path(frame.filename).name,
                    "function": frame.name, "line": frame.lineno}
    return {"type": type(error).__name__, "module": "external", "function": "unknown", "line": 0}


@dataclass
class _TrackIdentity:
    """Per-track evidence used to confirm one name."""

    votes: deque[FaceCandidate] = field(default_factory=lambda: deque(maxlen=8))
    person_id: str | None = None
    similarity: float | None = None
    confirmed_at_ms: int = 0
    last_face_at_ms: int = 0

    def clear_name(self) -> None:
        self.person_id = None
        self.similarity = None
        self.confirmed_at_ms = 0


class IdentityStabilizer:
    """Turns a stream of per-frame rankings into a stable, hesitating identity."""

    def __init__(self, config: VisionConfig) -> None:
        self._window = int(config.identity_window_frames)
        self._min_votes = int(config.identity_min_votes)
        self._threshold = float(config.identity_similarity_threshold)
        self._margin = float(config.identity_margin_threshold)
        self._hold_ms = int(config.identity_hold_ms)
        self._tracks: dict[str, _TrackIdentity] = {}

    def reset(self) -> None:
        self._tracks.clear()

    def forget(self, track_id: str) -> None:
        self._tracks.pop(track_id, None)

    def observe(self, track_id: str, candidate: FaceCandidate | None, at_ms: int) -> _TrackIdentity:
        state = self._tracks.get(track_id)
        if state is None:
            state = _TrackIdentity(votes=deque(maxlen=self._window))
            self._tracks[track_id] = state
        if candidate is not None and candidate.person_id is not None:
            margin_ok = candidate.margin is None or candidate.margin >= self._margin
            if (candidate.similarity or 0.0) >= self._threshold and margin_ok:
                state.votes.append(candidate)
                state.last_face_at_ms = at_ms

        if state.person_id is None:
            winner = self._winner(state)
            if winner is not None:
                state.person_id = winner.person_id
                state.similarity = winner.similarity
                state.confirmed_at_ms = at_ms
        elif at_ms - state.last_face_at_ms > self._hold_ms:
            # A short turn of the head may hold the name; a long absence may not.
            state.clear_name()
            state.votes.clear()
        elif candidate is not None and candidate.person_id == state.person_id:
            state.similarity = candidate.similarity
        return state

    def _winner(self, state: _TrackIdentity) -> FaceCandidate | None:
        if len(state.votes) < self._min_votes:
            return None
        grouped: dict[str, list[FaceCandidate]] = defaultdict(list)
        for vote in state.votes:
            if vote.person_id:
                grouped[vote.person_id].append(vote)
        best: FaceCandidate | None = None
        for person_id, votes in grouped.items():
            if len(votes) < self._min_votes:
                continue
            candidate = max(votes, key=lambda item: item.similarity or 0.0)
            if best is None or (candidate.similarity or 0.0) > (best.similarity or 0.0):
                best = FaceCandidate(person_id, candidate.similarity, candidate.margin)
        return best

    def rank_key(self, track_id: str) -> tuple[int, float, int]:
        """Evidence strength, used to break a duplicate-identity conflict."""
        state = self._tracks.get(track_id)
        if state is None:
            return (0, 0.0, 0)
        agreeing = sum(1 for vote in state.votes if vote.person_id == state.person_id)
        median = sorted((vote.similarity or 0.0) for vote in state.votes if vote.person_id == state.person_id)
        middle = median[len(median) // 2] if median else 0.0
        return (agreeing, middle, state.last_face_at_ms)


@dataclass
class _Published:
    snapshot: VisionSnapshot
    preview_jpeg: bytes | None


class VisionRuntime:
    """Owns the capture session and publishes one coherent snapshot at a time."""

    def __init__(self, config: VisionConfig, *, clock=time.time) -> None:
        self.config = config
        self.clock = clock
        self.mode = ServiceMode.IDLE
        self.camera_room_id: str | None = None
        self.source: CameraSource | None = None
        self.error_code: str | None = None
        self.message: str | None = None
        self._last_failure: dict[str, str | int] | None = None

        self._lock = threading.RLock()
        self._published = _Published(
            snapshot=VisionSnapshot(
                session_id=None, mode=ServiceMode.IDLE, camera_id=None,
                camera_room_id=None, observed_at_ms=0, sync_state="not_monitoring",
            ),
            preview_jpeg=None,
        )
        self._tracks: dict[str, TrackObservation] = {}
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

        self.capture = CaptureSession(self._on_capture_status)
        self.pose = PoseTracker(config)
        self.pose_states = PoseStateMachine(config)
        self.faces = FaceEngine(config)
        self.registry = FaceRegistry(config.runtime_dir, config.insightface_pack)
        # Voiceprints are stored here rather than in voice-service because this is
        # where the only DPAPI implementation lives; the voice service computes the
        # embeddings and hands them over loopback.
        self.speaker_registry = SpeakerRegistry(config.runtime_dir, config.speaker_pack)
        self.identity = IdentityStabilizer(config)
        self.sync = HomeSyncClient(config.home_service_url, config.runtime_dir)
        self.registration: RegistrationSession | None = None

        self._model_ready = False
        self._model_error: str | None = None
        self._registry_error: str | None = None
        self._frame_index = 0
        self._last_faces: list[FaceSample] = []

    # ------------------------------------------------------------- lifecycle
    def load_models(self) -> None:
        """Load models once; a failure is remembered and published honestly."""
        try:
            self.pose.load()
            self.faces.load()
        except Exception as exc:  # noqa: BLE001 - model errors must not crash the API
            self._model_ready = False
            if isinstance(exc, (FileNotFoundError, FaceModelMissing)):
                self._model_error = "model_missing"
            else:
                self._model_error = _safe_error_code(exc, "model_error")
            self._set_mode(ServiceMode.ERROR, self._model_error)
            return
        self._model_ready = True
        self._model_error = None
        try:
            self.registry.load()
            self._registry_error = None
        except Exception as exc:  # noqa: BLE001 - a bad registry must not disable vision
            # Pose and detection keep working; nobody can be recognised until the
            # user registers again, and the panel can say exactly that.
            self._registry_error = _safe_error_code(exc, type(exc).__name__)

    def start_worker(self) -> None:
        # Start the publisher first so the first processed frame cannot be lost
        # when HomeSyncClient initializes its capacity-one handoff.
        self.sync.start()
        with self._lock:
            if self._worker is not None:
                return
            self._stop = threading.Event()
            self._worker = threading.Thread(target=self._run, name="vision-pipeline", daemon=True)
            self._worker.start()

    def shutdown(self) -> None:
        self.stop_monitoring(reason="service_stopping")
        self._stop.set()
        worker = self._worker
        self._worker = None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3.0)
        self.capture.stop("service_stopping")
        # Stopping the publisher last keeps the final offline message orderable.
        self.sync.stop()

    # ------------------------------------------------------------- controls
    def select_source(self, source: CameraSource) -> None:
        """Switch camera, withdrawing the previous session before the new one may publish."""
        if self.mode == ServiceMode.REGISTERING:
            # The camera enrolment was using is going away, so enrolment ends with
            # an explicit reason instead of stalling on frames that never arrive.
            self._abort_registration("source_changed")
        was_monitoring = self.mode == ServiceMode.MONITORING
        self._begin_transition()
        if self.source is not None:
            self._offline_current("source_changed")
        self.source = source
        self._reset_tracking()
        if was_monitoring:
            # The user was watching, so keep monitoring on the newly selected
            # camera rather than leaving a "monitoring" mode with no frames.
            self.start_monitoring()
        elif self.mode != ServiceMode.REGISTERING:
            # A new source invalidates a previous camera failure: nothing has been
            # tried yet, so idle is the honest state.
            self._set_mode(ServiceMode.IDLE)

    def select_room(self, room_id: str) -> None:
        """Switch the logical room, withdrawing the previous session first.

        Re-selecting the current room must not disturb a live capture: the room is
        an interpretation of the one physical camera, not a reason to reopen it.
        """
        if self.camera_room_id == room_id:
            return
        if self.mode == ServiceMode.REGISTERING:
            # Enrolment is not tied to a room, so remember the choice without
            # stopping the capture the registration session is using.
            self.camera_room_id = room_id
            return
        was_monitoring = self.mode == ServiceMode.MONITORING
        was_error = self.mode == ServiceMode.ERROR
        if was_monitoring:
            self._begin_transition()
            self._offline_current("room_changed")
            self._reset_tracking()
        self.camera_room_id = room_id
        if was_monitoring:
            self.start_monitoring()
        elif not was_error:
            # The room does not affect the camera, so a camera or model fault must
            # survive this call instead of being cleared by it.
            self._set_mode(ServiceMode.IDLE)

    def start_monitoring(self) -> None:
        if not self._model_ready:
            self._set_mode(ServiceMode.ERROR, self._model_error or "model_missing")
            return
        if self.source is None:
            self._set_mode(ServiceMode.ERROR, "camera_not_selected")
            return
        if self.camera_room_id is None:
            self._set_mode(ServiceMode.ERROR, "room_not_selected")
            return
        # Set the intended mode before the capture thread starts. A camera that
        # fails immediately may callback before start() returns; its error must
        # win instead of being overwritten by a late monitoring assignment.
        self._set_mode(ServiceMode.MONITORING)
        self.capture.start(self.source)

    def pause_monitoring(self) -> None:
        self.stop_monitoring(reason="service_stopping")

    def stop_monitoring(self, *, reason: str) -> None:
        if self.mode == ServiceMode.MONITORING:
            self._set_mode(ServiceMode.IDLE)
            self._offline_current(reason)
        self.capture.stop(reason)
        self.sync.mark_not_monitoring()
        self._tracks.clear()
        self.identity.reset()
        if self.mode != ServiceMode.REGISTERING:
            # Refresh the idle snapshot after sync_state becomes not_monitoring.
            self._set_mode(ServiceMode.IDLE)

    # ------------------------------------------------------------- registration
    def start_registration(self, person_id: str) -> RegistrationStatus | None:
        """Collect enrolment samples without ever assigning a room.

        Monitoring stops publishing first, so the person enrolling is not also
        reported as a located observation. The selected camera keeps running,
        because registration needs frames even though it publishes nothing.
        """
        self.stop_monitoring(reason="service_stopping")
        if not self._model_ready:
            self._set_mode(ServiceMode.ERROR, self._model_error or "model_missing")
            return None
        if self.source is None:
            self._set_mode(ServiceMode.ERROR, "camera_not_selected")
            return None
        # Reuse the already loaded engine and the shared registry so enrolment
        # neither loads a second model nor writes a second registry copy.
        self.registration = RegistrationSession(
            person_id, self.config, engine=self.faces, registry=self.registry
        )
        self._set_mode(ServiceMode.REGISTERING)
        self.capture.start(self.source)
        return self.registration.status()

    def cancel_registration(self) -> RegistrationStatus | None:
        if self.registration is None:
            return None
        status = self.registration.cancel()
        self._set_mode(ServiceMode.IDLE)
        self.capture.stop("service_stopping")
        return status

    def delete_registration(self, person_id: str) -> bool:
        return self.registry.delete(person_id)

    # ------------------------------------------------------------- reads
    def snapshot(self) -> VisionSnapshot:
        with self._lock:
            return self._published.snapshot

    def preview_jpeg(self) -> bytes | None:
        with self._lock:
            return self._published.preview_jpeg

    def registration_status(self) -> RegistrationStatus | None:
        with self._lock:
            return self.registration.status() if self.registration is not None else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode.value,
                "model_ready": self._model_ready,
                "model_error": self._model_error,
                "registry_error": self._registry_error,
                "camera_id": self.source.redacted_label if self.source else None,
                "camera_room_id": self.camera_room_id,
                "session_id": self.capture.session_id,
                "last_frame_at_ms": self.capture.last_frame_at_ms,
                "actual_mode": self.capture.actual_mode,
                "sync_state": self.sync.sync_state,
                "error_code": self.error_code,
                "message": self.message,
                "last_failure": dict(self._last_failure) if self._last_failure else None,
            }

    # ------------------------------------------------------------- internals
    def _reset_tracking(self) -> None:
        """Drop tracker, identity and pose evidence belonging to the old session."""
        self._tracks.clear()
        self.identity.reset()
        self.pose_states.reset_session()
        self.pose.reset_session()

    def _begin_transition(self) -> None:
        # In-flight processing checks this mode before publishing, which prevents
        # a frame from the previous source/room leaking past the offline barrier.
        self._set_mode(ServiceMode.IDLE)
        self.capture.stop("source_changed")

    def _offline_current(self, reason: str) -> None:
        session_id = self.capture.session_id
        if session_id is None or self.source is None:
            return
        self.sync.offline({
            "camera_id": self.source.redacted_label,
            "session_id": session_id,
            "observed_at_ms": int(self.clock() * 1000),
            "reason": reason,
        })

    def _set_mode(self, mode: ServiceMode, error_code: str | None = None) -> None:
        with self._lock:
            self.mode = mode
            self.error_code = error_code
            if error_code is None:
                self.message = None
            else:
                self.message = _ERROR_MESSAGES.get(error_code, "视觉服务不可用")
            if mode != ServiceMode.MONITORING:
                # Idle, enrolment and fault states must never retain a monitoring
                # overlay from an earlier frame or session.
                self._tracks.clear()
                self._published = _Published(
                    snapshot=VisionSnapshot(
                        session_id=self.capture.session_id,
                        mode=mode,
                        camera_id=self.source.redacted_label if self.source else None,
                        camera_room_id=self.camera_room_id,
                        observed_at_ms=int(self.clock() * 1000),
                        sync_state=self.sync.sync_state,
                        tracks=(),
                        error_code=error_code,
                        message=self.message,
                    ),
                    preview_jpeg=None,
                )

    def _on_capture_status(self, state: str, camera_id: str | None) -> None:
        if state == "camera_open_failed":
            self._abort_registration("camera_open_failed")
            self.sync.mark_not_monitoring()
            self._set_mode(ServiceMode.ERROR, "camera_open_failed")
        elif state == "camera_disconnected":
            self._offline_current("camera_disconnected")
            self._abort_registration("camera_disconnected")
            self.sync.mark_not_monitoring()
            self._set_mode(ServiceMode.ERROR, "camera_disconnected")

    def _abort_registration(self, reason: str) -> None:
        """End enrolment on an owner-side fault instead of leaving stale progress.

        The session is kept rather than dropped so `GET /registration` can report
        the terminal state and its reason; a new start replaces it anyway.
        """
        session = self.registration
        if session is None:
            return
        session.abort(reason, int(self.clock() * 1000))
        if self.mode == ServiceMode.REGISTERING:
            # Enrolment is over, so the service must stop claiming to be
            # registering. A caller may still move it into an error state next.
            self._set_mode(ServiceMode.IDLE)

    def _run(self) -> None:
        consecutive_errors = 0
        while not self._stop.is_set():
            frame = self.capture.latest_frame.take(timeout=0.5)
            if frame is None:
                continue
            session_id = self.capture.session_id
            try:
                self._process(frame)
                consecutive_errors = 0
                with self._lock:
                    self._last_failure = None
            except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the loop
                if session_id:
                    captured_at_ms = getattr(frame, "captured_at_ms", None)
                    DIAGNOSTICS.emit("frame", outcome="error",
                                     observation_id=f"{session_id}:{captured_at_ms}" if isinstance(captured_at_ms, int) else None,
                                     error_code="frame_processing_error")
                # An in-flight frame can fail while a control request switches the
                # source. Never let that old frame poison the new session.
                if self.capture.session_id != session_id or self.mode not in (
                    ServiceMode.MONITORING, ServiceMode.REGISTERING
                ):
                    consecutive_errors = 0
                    continue
                with self._lock:
                    self._last_failure = _failure_location(exc)
                consecutive_errors += 1
                if consecutive_errors < MAX_CONSECUTIVE_FRAME_ERRORS:
                    continue
                self._offline_current("frame_processing_error")
                # A persistent processing fault must end enrolment too, or `/registration` keeps
                # reporting progress that can never advance.
                self._abort_registration("frame_processing_error")
                self.capture.stop("frame_processing_error")
                self.sync.mark_not_monitoring()
                self._set_mode(ServiceMode.ERROR, _safe_error_code(exc, "frame_processing_error"))
                consecutive_errors = 0

    def _process(self, frame) -> None:
        if self.mode == ServiceMode.REGISTERING:
            self._process_registration(frame)
            return
        if self.mode != ServiceMode.MONITORING:
            return

        session_id = self.capture.session_id or ""
        started = time.perf_counter()
        bodies: list[BodyTrack] = self.pose.process(frame, session_id)
        pose_ms = (time.perf_counter() - started) * 1000
        self._frame_index += 1
        measure = self._frame_index % 30 == 0
        observation_id = f"{session_id}:{frame.captured_at_ms}"
        if measure:
            DIAGNOSTICS.emit("capture_age", observation_id=observation_id,
                             duration_ms=max(0, int(time.time() * 1000) - frame.captured_at_ms))
            DIAGNOSTICS.emit("pose", observation_id=observation_id, duration_ms=pose_ms)

        face_map: dict[str, FaceSample] = {}
        if self._frame_index % max(1, int(self.config.face_review_interval_frames)) == 0:
            face_started = time.perf_counter()
            self._last_faces = self.faces.analyze(frame.frame_bgr)
            if measure:
                DIAGNOSTICS.emit("face", observation_id=observation_id,
                                 duration_ms=(time.perf_counter() - face_started) * 1000)
            if self._last_faces:
                face_map = associate_faces(self._last_faces, bodies)

        ranked: dict[str, FaceCandidate | None] = {}
        for body in bodies:
            sample = face_map.get(body.track_id)
            ranking: FaceCandidate | None = None
            if sample is not None and sample.embedding is not None and sample.quality.ok:
                candidates = self.registry.rank(sample.embedding)
                ranking = candidates[0] if candidates else None
            ranked[body.track_id] = ranking

        observations = self._build_observations(bodies, ranked, frame.captured_at_ms)
        # Controls run on HTTP threads while inference runs here. Re-check the
        # ownership boundary after the expensive work so a stale in-flight frame
        # cannot publish after a pause or session replacement.
        if self.mode != ServiceMode.MONITORING or self.capture.session_id != session_id:
            return
        render_started = time.perf_counter()
        self._publish(observations, frame)
        if measure:
            DIAGNOSTICS.emit("render", observation_id=observation_id,
                             duration_ms=(time.perf_counter() - render_started) * 1000)
        self._push_to_home(frame)
        if measure:
            DIAGNOSTICS.emit("frame", observation_id=observation_id,
                             duration_ms=(time.perf_counter() - started) * 1000)

    def _process_registration(self, frame) -> None:
        session = self.registration
        if session is None:
            return
        session_id = self.capture.session_id
        observation_id = f"{session_id}:{frame.captured_at_ms}" if session_id else None
        frame_started = time.perf_counter()
        # Publish the live frame before face inference.  Registration can spend
        # noticeable time in the model on its first frame; waiting for it made
        # Unity replace the previous image with a black/empty transition.
        preview_jpeg = self._render_preview(frame.frame_bgr, [])
        DIAGNOSTICS.emit("registration_preview", observation_id=observation_id,
                         duration_ms=(time.perf_counter() - frame_started) * 1000)
        with self._lock:
            if (
                self.mode != ServiceMode.REGISTERING
                or self.registration is not session
                or self.capture.session_id != session_id
            ):
                return
            self._published = _Published(
                snapshot=self._published.snapshot,
                preview_jpeg=preview_jpeg,
            )
        face_started = time.perf_counter()
        faces = self.faces.analyze(frame.frame_bgr)
        DIAGNOSTICS.emit("registration_face", observation_id=observation_id,
                         duration_ms=(time.perf_counter() - face_started) * 1000)
        accept_started = time.perf_counter()
        session.accept(frame.frame_bgr, faces, frame.captured_at_ms)
        DIAGNOSTICS.emit("registration_accept", observation_id=observation_id,
                         duration_ms=(time.perf_counter() - accept_started) * 1000)
        DIAGNOSTICS.emit("registration_frame", observation_id=observation_id,
                         duration_ms=(time.perf_counter() - frame_started) * 1000)
        if session.finished():
            # RegistrationSession already performed the one atomic registry
            # replacement before confirmation. Keep the terminal session so the
            # polling UI can display its final result.
            self._set_mode(ServiceMode.IDLE)
            self.capture.stop("service_stopping")
            return

    def _build_observations(
        self,
        bodies: list[BodyTrack],
        ranked: dict[str, FaceCandidate | None],
        at_ms: int,
    ) -> list[TrackObservation]:
        live_ids = {body.track_id for body in bodies}
        for gone in [track_id for track_id in self._tracks if track_id not in live_ids]:
            self._tracks.pop(gone, None)
            self.identity.forget(gone)
            self.pose_states.forget(gone)

        states: dict[str, _TrackIdentity] = {}
        for body in bodies:
            states[body.track_id] = self.identity.observe(body.track_id, ranked.get(body.track_id), at_ms)

        claims: dict[str, list[str]] = defaultdict(list)
        for track_id, state in states.items():
            if state.person_id:
                claims[state.person_id].append(track_id)

        losers: set[str] = set()
        for person_id, track_ids in claims.items():
            if len(track_ids) < 2:
                continue
            # One person cannot be two tracks: keep the strongest evidence and
            # show the rest as an unresolved conflict rather than guessing.
            ordered = sorted(track_ids, key=self.identity.rank_key, reverse=True)
            losers.update(ordered[1:])

        observations: list[TrackObservation] = []
        for body in bodies:
            state = states[body.track_id]
            pose: PoseEstimate = self.pose_states.update(
                body.track_id, body.keypoints, body.bbox, at_ms
            )
            if body.track_id in losers:
                identity_state = IdentityState.CONFLICT
                person_id = None
            elif state.person_id is not None:
                identity_state = IdentityState.CONFIRMED
                person_id = state.person_id
            elif ranked.get(body.track_id) is not None:
                identity_state = IdentityState.CANDIDATE
                person_id = None
            else:
                identity_state = IdentityState.UNKNOWN
                person_id = None

            observations.append(
                TrackObservation(
                    track_id=body.track_id,
                    bbox=body.bbox,
                    keypoints=body.keypoints,
                    detection_confidence=body.confidence,
                    person_id=person_id,
                    identity_state=identity_state,
                    face_similarity=state.similarity if person_id else None,
                    pose=pose.pose,
                    pose_confidence=pose.confidence,
                    observed_at_ms=at_ms,
                    track_state=TrackState.ACTIVE,
                )
            )
        return observations

    def _publish(self, observations: list[TrackObservation], frame) -> None:
        snapshot = VisionSnapshot(
            session_id=self.capture.session_id,
            mode=ServiceMode.MONITORING,
            camera_id=self.source.redacted_label if self.source else None,
            camera_room_id=self.camera_room_id,
            observed_at_ms=frame.captured_at_ms,
            sync_state=self.sync.sync_state,
            tracks=tuple(observations),
        )
        preview = self._render_preview(frame.frame_bgr, observations)
        with self._lock:
            self._published = _Published(snapshot=snapshot, preview_jpeg=preview)

    def _push_to_home(self, frame) -> None:
        session_id = self.capture.session_id
        if session_id is None or self.source is None or self.camera_room_id is None:
            return
        snapshot = self.snapshot()
        self.sync.submit({
            "camera_id": self.source.redacted_label,
            "camera_room_id": self.camera_room_id,
            "session_id": session_id,
            "observed_at_ms": frame.captured_at_ms,
            "observations": [track.to_dict() for track in snapshot.tracks],
        })

    def _render_preview(self, frame_bgr, observations: list[TrackObservation]) -> bytes | None:
        """Mirror the preview for the person facing the screen, then draw readable overlays.

        Recognition and world-location coordinates stay in the original camera frame.
        Raw frames are never written to disk.
        """
        try:
            import cv2
        except ImportError:
            return None
        canvas = cv2.flip(frame_bgr, 1)
        height, width = canvas.shape[:2]
        for track in observations:
            x1, y1, x2, y2 = (
                int(track.bbox[0] * width), int(track.bbox[1] * height),
                int(track.bbox[2] * width), int(track.bbox[3] * height),
            )
            mirrored_x1, mirrored_x2 = width - x2, width - x1
            cv2.rectangle(canvas, (mirrored_x1, y1), (mirrored_x2, y2), (60, 200, 190), 2)
            label = _track_label(track)
            cv2.putText(canvas, label, (mirrored_x1, max(12, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        longest = max(height, width)
        if longest > PREVIEW_MAX_EDGE:
            scale = PREVIEW_MAX_EDGE / float(longest)
            canvas = cv2.resize(canvas, (int(width * scale), int(height * scale)))
        ok, buffer = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), int(self.config.preview_jpeg_quality)])
        return buffer.tobytes() if ok else None


def _track_label(track: TrackObservation) -> str:
    if track.person_id:
        name = track.person_id
    elif track.identity_state == IdentityState.CONFLICT:
        name = "conflict"
    else:
        name = "unknown"
    pose = track.pose.value if isinstance(track.pose, PoseState) else str(track.pose)
    return f"{name} {pose} {track.pose_confidence:.2f}"


_ERROR_MESSAGES = {
    "model_missing": "模型文件缺失，请先按 README 放置本地模型",
    "model_error": "模型推理异常，已停止发布视觉状态",
    "frame_processing_error": "连续处理画面失败，已停止视觉监控；请查看故障位置",
    "camera_open_failed": "摄像头打开失败",
    "camera_disconnected": "摄像头已断开",
    "camera_not_selected": "尚未选择摄像头",
    "room_not_selected": "尚未选择逻辑房间",
    "home_service_token_missing": "未找到 home-service 令牌，人物位置未同步",
    "home_sync_unavailable": "home-service 不可达，人物位置未同步",
}
