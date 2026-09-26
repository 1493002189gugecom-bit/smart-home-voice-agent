from __future__ import annotations

import pytest

from service_paths import CONFIG
from server import HomeServiceApp
from state import build_default_state
from visual_state import VisualStateStore


def test_added_person_survives_reload_and_is_in_snapshot(tmp_path):
    state = build_default_state(CONFIG)
    catalog = {person.id: {"display_name": person.display_name} for person in state.persons.values()}
    directory = tmp_path / "persons.json"
    store = VisualStateStore(catalog, observation_ttl_ms=3500, directory_path=directory)
    app = HomeServiceApp(state, visual_state=store)

    status, response = app.handle("POST", "/persons", {}, {"display_name": "姑姑"})
    assert status == 201
    person_id = response["person"]["id"]
    assert person_id.startswith("person_")
    assert any(item["id"] == person_id for item in app.handle("GET", "/snapshot", {}, {})[1]["persons"])
    assert any(item["id"] == person_id for item in VisualStateStore(
        catalog, observation_ttl_ms=3500, directory_path=directory
    ).persons_directory())

    status, response = app.handle("POST", "/persons", {}, {"display_name": "姑姑"})
    assert status == 422
    assert response["ok"] is False


def test_empty_person_name_is_rejected(tmp_path):
    store = VisualStateStore({}, observation_ttl_ms=3500, directory_path=tmp_path / "persons.json")
    with pytest.raises(ValueError):
        store.create_person("  ")
