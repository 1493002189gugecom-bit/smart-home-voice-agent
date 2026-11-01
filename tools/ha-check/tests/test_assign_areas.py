from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from assign_areas import apply_assignments, assign, load_catalog, plan_assignments


def entity(entity_id, area_id=None):
    return {"entity_id": entity_id, "area_id": area_id, "platform": "mqtt"}


def catalog(**rooms):
    return {
        device_id: {"entity_id": entity_id, "area_id": area_id}
        for device_id, (entity_id, area_id) in rooms.items()
    }


def test_plan_reports_only_entities_that_need_moving():
    spec = catalog(
        light=("light.shv_living_room_light", "ke_ting"),
        ac=("climate.shv_bedroom_ac", "wo_shi"),
    )
    entities = [
        entity("light.shv_living_room_light", "ke_ting"),  # already correct
        entity("climate.shv_bedroom_ac", None),
    ]

    changes = plan_assignments(spec, entities)

    assert changes == [
        {"entity_id": "climate.shv_bedroom_ac", "area_id": "wo_shi", "previous": None}
    ]


def test_apply_moves_only_the_planned_entities():
    entities = [entity("light.shv_living_room_light"), entity("climate.shv_bedroom_ac")]
    changes = [{"entity_id": "climate.shv_bedroom_ac", "area_id": "wo_shi", "previous": None}]

    assert apply_assignments(entities, changes) == 1
    assert entities[0]["area_id"] is None
    assert entities[1]["area_id"] == "wo_shi"


def test_a_device_missing_from_home_assistant_is_an_error():
    spec = catalog(ghost=("light.shv_ghost", "ke_ting"))

    with pytest.raises(ValueError, match="not registered"):
        plan_assignments(spec, [entity("light.shv_living_room_light")])


def test_a_device_without_an_area_is_an_error():
    spec = {"light": {"entity_id": "light.shv_living_room_light"}}

    with pytest.raises(ValueError, match="no area_id"):
        plan_assignments(spec, [entity("light.shv_living_room_light")])


def test_an_area_that_does_not_exist_fails_before_home_assistant_is_stopped(tmp_path):
    """A typo must never invent a room, and must not interrupt Home Assistant."""
    config_dir = tmp_path / "config"
    storage = config_dir / ".storage"
    storage.mkdir(parents=True)
    (storage / "core.entity_registry").write_text(
        json.dumps({"data": {"entities": [entity("light.shv_living_room_light")]}}), encoding="utf-8"
    )
    (storage / "core.area_registry").write_text(
        json.dumps({"data": {"areas": [{"id": "ke_ting", "name": "客厅"}]}}), encoding="utf-8"
    )
    spec_path = tmp_path / "catalog.json"
    spec_path.write_text(
        json.dumps({"light": {"entity_id": "light.shv_living_room_light", "area_id": "ku_fang"}}),
        encoding="utf-8",
    )
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="ku_fang"):
        assign("homeassistant", config_dir, spec_path, runner=runner)

    assert commands == []


def test_assign_stops_then_starts_and_writes_a_backup(tmp_path):
    config_dir = tmp_path / "config"
    storage = config_dir / ".storage"
    storage.mkdir(parents=True)
    entity_path = storage / "core.entity_registry"
    entity_path.write_text(
        json.dumps({"data": {"entities": [entity("light.shv_living_room_light")]}}), encoding="utf-8"
    )
    (storage / "core.area_registry").write_text(
        json.dumps({"data": {"areas": [{"id": "ke_ting", "name": "客厅"}]}}), encoding="utf-8"
    )
    spec_path = tmp_path / "catalog.json"
    spec_path.write_text(
        json.dumps({"light": {"entity_id": "light.shv_living_room_light", "area_id": "ke_ting"}}),
        encoding="utf-8",
    )
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    changes = assign("homeassistant", config_dir, spec_path, runner=runner)

    assert [command[1] for command in commands] == ["stop", "start"]
    assert changes[0]["area_id"] == "ke_ting"
    written = json.loads(entity_path.read_text(encoding="utf-8"))
    assert written["data"]["entities"][0]["area_id"] == "ke_ting"
    # A destructive registry edit must always leave an undo copy behind.
    assert list(storage.glob("*.bak"))


def test_the_shipped_catalog_covers_all_three_project_areas():
    """The shipped living-room, bedroom and kitchen mappings stay complete."""
    catalog_data = load_catalog("apps/home-service/config/ha_entities.json")
    area_ids = {record["area_id"] for record in catalog_data.values()}

    assert area_ids == {"ke_ting", "wo_shi", "chu_fang"}
