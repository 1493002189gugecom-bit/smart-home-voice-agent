"""Fixtures for tests that need camera-sourced person locations.

Manual person movement was removed from the approved design: a person has a room
only while a camera whose logical room is selected is reporting a *confirmed*
observation for them. Tests therefore build the same store the vision service
feeds, instead of writing `person.room_id` directly, which no longer has any
effect on a snapshot.

One camera id is used per room because a single physical camera only ever
represents one room; several ids let a test place people in several rooms at once.
"""

from __future__ import annotations

from typing import Iterable

# Imported first, exactly like the test modules: it puts apps/home-service/src on
# sys.path, so the sibling imports below resolve when this module is imported directly.
from service_paths import CONFIG
from models import now_ms
from server import HomeServiceApp
from state import HomeState, build_default_state
from tools import ToolService
from visual_state import VisualStateStore

# Long enough that a slow test machine cannot expire a placement mid-test.
TEST_TTL_MS = 600_000
_TEST_SESSION_ID = "test-session"


def build_store(state: HomeState, placements: dict[str, Iterable[str]]) -> VisualStateStore:
    """Return a visual store holding one confirmed observation per placed person."""
    catalog = {
        person.id: {"display_name": person.display_name, "aliases": list(person.aliases)}
        for person in state.persons.values()
    }
    store = VisualStateStore(catalog, observation_ttl_ms=TEST_TTL_MS)
    timestamp = now_ms()
    for index, (room_id, person_ids) in enumerate(placements.items()):
        observations = [
            {
                "track_id": f"track-{person_id}",
                "bbox": [0.4, 0.3, 0.6, 0.9],
                "keypoints": [[0.5, 0.4, 0.9], [0.5, 0.7, 0.9]],
                "detection_confidence": 0.9,
                "person_id": person_id,
                "identity_state": "confirmed",
                "face_similarity": 0.9,
                "pose": "standing",
                "pose_confidence": 0.8,
                "observed_at_ms": timestamp,
                "track_state": "active",
            }
            for person_id in person_ids
        ]
        if not observations:
            continue
        store.submit_batch(
            {
                "camera_id": f"test-camera-{index}",
                "camera_room_id": room_id,
                "session_id": f"{_TEST_SESSION_ID}-{index}",
                "observed_at_ms": timestamp,
                "observations": observations,
            },
            timestamp,
        )
    return store


def service_with_vision(placements: dict[str, Iterable[str]]) -> ToolService:
    """ToolService whose person locations come from simulated camera observations."""
    state = build_default_state(CONFIG)
    return ToolService(state, visual_state=build_store(state, placements))


def app_with_vision(placements: dict[str, Iterable[str]]) -> HomeServiceApp:
    """HomeServiceApp sharing one visual store, as the real service does."""
    state = build_default_state(CONFIG)
    return HomeServiceApp(state, visual_state=build_store(state, placements))
