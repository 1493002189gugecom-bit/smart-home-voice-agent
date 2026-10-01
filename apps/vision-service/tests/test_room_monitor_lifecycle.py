"""Room changes and preview freshness use fakes, never a real camera."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from capture import CameraSource
from contracts import ServiceMode, VisionSnapshot
from pipeline import VisionRuntime, _Published


def monitoring_runtime() -> VisionRuntime:
    runtime = VisionRuntime.__new__(VisionRuntime)
    runtime._lock = threading.RLock()
    runtime.clock = lambda: 10.0
    runtime.mode = ServiceMode.MONITORING
    runtime.error_code = None
    runtime.message = None
    runtime._room_generation = 0
    runtime._room_started_at_ms = 0
    runtime._pose_lock = threading.Lock()
    runtime._model_ready = True
    runtime._model_error = None
    runtime.source = CameraSource.device(0)
    runtime.camera_room_id = "bedroom"
    runtime.capture = SimpleNamespace(
        session_id="camera-session", latest_frame=SimpleNamespace(discard=Mock()),
        stop=Mock(), start=Mock(return_value="new-camera-session"),
    )
    runtime.sync = SimpleNamespace(
        sync_state="synced", offline=Mock(return_value=True), submit=Mock(),
        mark_not_monitoring=Mock(),
    )
    runtime._published = _Published(
        VisionSnapshot("camera-session", ServiceMode.MONITORING, "device:0", "bedroom", 9_000, "synced"),
        b"frame",
    )
    runtime._tracks = {}
    runtime._last_failure = None
    runtime.identity = SimpleNamespace(reset=Mock())
    runtime.pose_states = SimpleNamespace(reset_session=Mock())
    runtime.pose = SimpleNamespace(reset_session=Mock())
    return runtime


def test_switching_room_keeps_physical_camera_open_and_withdraws_old_room():
    runtime = monitoring_runtime()

    runtime.select_room("living_room")

    runtime.capture.stop.assert_not_called()
    runtime.capture.start.assert_not_called()
    # Frames queued before either side of the offline barrier are withdrawn.
    assert runtime.capture.latest_frame.discard.call_count == 2
    runtime.sync.offline.assert_called_once()
    assert runtime.sync.offline.call_args.args[0]["reason"] == "room_changed"
    assert runtime.sync.offline.call_args.args[0]["session_id"] == "camera-session"
    assert runtime.mode == ServiceMode.MONITORING
    assert runtime.camera_room_id == "living_room"
    assert runtime.capture.session_id == "camera-session"
    assert runtime.preview_jpeg() is None


def test_old_room_frame_cannot_publish_after_switch():
    runtime = monitoring_runtime()
    runtime._render_preview = Mock(return_value=b"old-room")
    old_generation = runtime._room_generation
    runtime.select_room("living_room")

    frame = SimpleNamespace(captured_at_ms=9_500, frame_bgr=object())
    assert runtime._publish([], frame, expected_room_generation=old_generation) is False
    runtime._push_to_home(frame, expected_room_generation=old_generation)

    assert runtime.preview_jpeg() is None
    runtime.sync.submit.assert_not_called()


def test_frame_started_during_room_transition_cannot_publish_after_it():
    runtime = monitoring_runtime()
    runtime._render_preview = Mock(return_value=b"transition-frame")
    transition_generations = []
    runtime.sync.offline.side_effect = lambda _payload: transition_generations.append(
        runtime._room_generation
    )

    runtime.select_room("living_room")

    frame = SimpleNamespace(captured_at_ms=10_001, frame_bgr=object())
    assert runtime._publish(
        [], frame, expected_room_generation=transition_generations[0],
        expected_session_id="camera-session",
    ) is False
    assert runtime.preview_jpeg() is None


def test_room_switch_keeps_camera_fault_that_arrives_during_offline_barrier():
    runtime = monitoring_runtime()
    runtime.sync.offline.side_effect = lambda _payload: runtime._set_mode(
        ServiceMode.ERROR, "camera_disconnected"
    )

    runtime.select_room("living_room")

    assert runtime.camera_room_id == "living_room"
    assert runtime.mode == ServiceMode.ERROR
    assert runtime.error_code == "camera_disconnected"


def test_pause_during_room_switch_does_not_resume_monitoring():
    runtime = monitoring_runtime()
    runtime.sync.offline.side_effect = lambda _payload: runtime.pause_monitoring()

    runtime.select_room("living_room")

    assert runtime.camera_room_id == "living_room"
    assert runtime.mode == ServiceMode.IDLE


def test_old_camera_session_cannot_publish_after_source_replacement():
    runtime = monitoring_runtime()
    runtime._render_preview = Mock(return_value=b"old-camera")
    runtime.capture.session_id = "replacement-session"

    frame = SimpleNamespace(captured_at_ms=9_500, frame_bgr=object())
    assert runtime._publish(
        [], frame, expected_room_generation=0, expected_session_id="camera-session"
    ) is False
    runtime._push_to_home(
        frame, expected_room_generation=0, expected_session_id="camera-session"
    )

    runtime.sync.submit.assert_not_called()


def test_stale_preview_does_not_keep_showing_an_old_jpeg():
    runtime = monitoring_runtime()
    runtime._published = SimpleNamespace(preview_jpeg=b"old", preview_at_ms=1_000)

    assert runtime.preview_jpeg() is None

    runtime._published = SimpleNamespace(preview_jpeg=b"fresh", preview_at_ms=9_000)
    assert runtime.preview_jpeg() == b"fresh"


def test_monitoring_start_that_cannot_claim_camera_reports_error():
    runtime = monitoring_runtime()
    runtime.capture.start.return_value = None

    runtime.start_monitoring()

    assert runtime.mode == ServiceMode.ERROR
    assert runtime.error_code == "camera_open_failed"


def test_reselecting_live_camera_does_not_reopen_it():
    runtime = monitoring_runtime()
    old_session = runtime.capture.session_id

    runtime.select_source(CameraSource.device(0))

    assert runtime.mode == ServiceMode.MONITORING
    assert runtime.capture.session_id == old_session
    runtime.capture.stop.assert_not_called()
    runtime.capture.start.assert_not_called()
    runtime.sync.offline.assert_not_called()


def test_reselecting_camera_after_error_allows_retry():
    runtime = monitoring_runtime()
    runtime._set_mode(ServiceMode.ERROR, "camera_disconnected")

    runtime.select_source(CameraSource.device(0))

    assert runtime.mode == ServiceMode.IDLE
    runtime.capture.stop.assert_called()


def test_monitoring_start_cannot_publish_previous_session_during_camera_claim():
    runtime = monitoring_runtime()
    runtime._set_mode(ServiceMode.IDLE)
    runtime._render_preview = Mock(return_value=b"old-camera")
    previous_generation = runtime._room_generation
    old_frame = SimpleNamespace(captured_at_ms=9_500, frame_bgr=object())
    published_during_start = []

    def claim_camera(_source):
        published_during_start.append(runtime._publish(
            [], old_frame, expected_room_generation=previous_generation,
            expected_session_id="camera-session",
        ))
        runtime.capture.session_id = "new-camera-session"
        return "new-camera-session"

    runtime.capture.start.side_effect = claim_camera

    runtime.start_monitoring()

    assert published_during_start == [False]
    assert runtime.mode == ServiceMode.MONITORING
    assert runtime.capture.session_id == "new-camera-session"
    assert runtime.preview_jpeg() is None


def test_pause_during_camera_claim_does_not_restart_monitoring():
    runtime = monitoring_runtime()
    runtime._set_mode(ServiceMode.IDLE)

    def claim_camera(_source):
        runtime.pause_monitoring()
        runtime.capture.session_id = "new-camera-session"
        return "new-camera-session"

    runtime.capture.start.side_effect = claim_camera

    runtime.start_monitoring()

    assert runtime.mode == ServiceMode.IDLE


def test_source_switch_rejects_old_generation_after_reopening():
    runtime = monitoring_runtime()
    runtime._render_preview = Mock(return_value=b"old-camera")
    previous_generation = runtime._room_generation
    def claim_new_camera(_source):
        runtime.capture.session_id = "new-camera-session"
        return "new-camera-session"

    runtime.capture.start.side_effect = claim_new_camera

    runtime.select_source(CameraSource.device(1))

    old_frame = SimpleNamespace(captured_at_ms=9_500, frame_bgr=object())
    assert runtime._publish([], old_frame, expected_room_generation=previous_generation) is False
    assert runtime.mode == ServiceMode.MONITORING
    assert runtime.source.redacted_label == "device:1"
    assert runtime.preview_jpeg() is None


def test_room_switch_waits_for_pose_inference_before_resetting_tracker():
    runtime = monitoring_runtime()
    runtime.config = SimpleNamespace(face_review_interval_frames=30)
    runtime._frame_index = 0
    runtime._last_faces = []
    entered_inference = threading.Event()
    release_inference = threading.Event()
    discarded_frame = threading.Event()
    tracker_reset = threading.Event()
    runtime.capture.latest_frame.discard.side_effect = discarded_frame.set

    def process_pose(_frame, _session_id):
        entered_inference.set()
        release_inference.wait()
        return []

    runtime.pose.process = process_pose
    runtime.pose.reset_session = tracker_reset.set
    frame = SimpleNamespace(captured_at_ms=9_500, frame_bgr=object())
    processing = threading.Thread(target=runtime._process, args=(frame,))
    switching = threading.Thread(target=runtime.select_room, args=("living_room",))
    try:
        processing.start()
        assert entered_inference.wait(1)
        switching.start()
        assert discarded_frame.wait(1)
        assert not tracker_reset.wait(0.05)
    finally:
        release_inference.set()
        if processing.ident is not None:
            processing.join(timeout=1)
        if switching.ident is not None:
            switching.join(timeout=1)
    assert not processing.is_alive()
    assert not switching.is_alive()
    assert tracker_reset.is_set()


def test_old_room_processing_error_does_not_poison_current_room():
    runtime = monitoring_runtime()
    runtime._stop = threading.Event()
    frame = SimpleNamespace(captured_at_ms=9_500)

    def take_one_frame(timeout):
        runtime._stop.set()
        return frame

    def fail_after_room_switch(_frame):
        runtime._room_generation += 1
        raise RuntimeError("old room frame failed")

    runtime.capture.latest_frame.take = take_one_frame
    runtime._process = fail_after_room_switch

    runtime._run()

    assert runtime.mode == ServiceMode.MONITORING
    assert runtime._last_failure is None
