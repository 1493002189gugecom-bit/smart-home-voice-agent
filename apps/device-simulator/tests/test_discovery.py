import json

import pytest

from discovery import (
    CHINESE_LABELS,
    command_publications_for_tests,
    command_topics,
    discovery_messages,
    parse_command_topic,
    slugify_entity_name,
    state_messages,
)
from model import initial_state


def test_all_eight_discovery_payloads_have_stable_ids_and_availability():
    messages = discovery_messages("house")
    assert len(messages) == 8
    payloads = [json.loads(item.payload) for item in messages]
    assert {payload["unique_id"] for payload in payloads} == {
        "shv_living_room_light",
        "shv_bedroom_light",
        "shv_kitchen_light",
        "shv_living_room_ac",
        "shv_bedroom_ac",
        "shv_kitchen_ac",
        "shv_desk_plug",
        "shv_indoor_temperature",
    }
    by_id = {payload["unique_id"]: payload for payload in payloads}
    for unique_id, payload in by_id.items():
        # The entity name must slugify to the stable id; that slug *is* the
        # entity id Home Assistant creates.
        assert slugify_entity_name(payload["name"]) == unique_id
        device_id = unique_id.removeprefix("shv_")
        assert payload["availability"] == [
            {"topic": "house/status", "payload_available": "online", "payload_not_available": "offline"},
            {"topic": f"house/{device_id}/availability", "payload_available": "online", "payload_not_available": "offline"},
        ]
        assert payload["availability_mode"] == "all"
    assert all(item.retain and item.qos == 1 for item in messages)


def test_payloads_have_no_device_block_so_entity_ids_stay_predictable():
    """A device block makes Home Assistant prefix the entity id with the device
    name slug, which shifted ids to ``light.ke_ting_deng_shv_living_room_light``.
    """
    for payload in (json.loads(item.payload) for item in discovery_messages("shv")):
        assert "device" not in payload


def test_chinese_labels_remain_documented_without_affecting_entity_ids():
    assert CHINESE_LABELS == {
        "shv_living_room_light": "客厅灯",
        "shv_bedroom_light": "卧室灯",
        "shv_kitchen_light": "厨房灯",
        "shv_living_room_ac": "客厅空调",
        "shv_bedroom_ac": "卧室空调",
        "shv_kitchen_ac": "厨房空调",
        "shv_desk_plug": "智能插座",
        "shv_indoor_temperature": "室内温度",
    }
    for unique_id, label in CHINESE_LABELS.items():
        # A Chinese label would slugify to pinyin, so it must never be the name.
        assert slugify_entity_name(label) != unique_id


def test_discovery_registers_all_eight_stable_entity_ids():
    messages = discovery_messages("shv")
    assert len(messages) == 8
    assert {message.topic for message in messages} == {
        "homeassistant/light/shv_living_room_light/config",
        "homeassistant/light/shv_bedroom_light/config",
        "homeassistant/light/shv_kitchen_light/config",
        "homeassistant/climate/shv_living_room_ac/config",
        "homeassistant/climate/shv_bedroom_ac/config",
        "homeassistant/climate/shv_kitchen_ac/config",
        "homeassistant/switch/shv_desk_plug/config",
        "homeassistant/sensor/shv_indoor_temperature/config",
    }


def test_every_light_discovery_payload_uses_json_brightness_scale_100():
    payloads = {json.loads(item.payload)["unique_id"]: json.loads(item.payload)
                for item in discovery_messages("shv")}
    for device in ("living_room_light", "bedroom_light", "kitchen_light"):
        light = payloads[f"shv_{device}"]
        assert light["schema"] == "json"
        assert light["brightness"] is True
        assert light["brightness_scale"] == 100


def test_entity_name_slugifies_to_the_stable_entity_id():
    """Home Assistant 2026.6.3 ignores object_id for MQTT discovery and derives
    the entity id from the entity name, so the name must slugify to the id.

    A Chinese name slugified to pinyin such as
    ``light.ke_ting_deng_ke_ting_deng``, which no catalog can predict.
    """
    for payload in (json.loads(item.payload) for item in discovery_messages("shv")):
        assert slugify_entity_name(payload["name"]) == payload["unique_id"]

    assert slugify_entity_name("SHV Living Room Light") == "shv_living_room_light"


def test_all_known_command_topics_are_explicit_and_never_retained():
    assert command_topics("shv") == {
        "living_room_light": "shv/living_room_light/set",
        "bedroom_light": "shv/bedroom_light/set",
        "kitchen_light": "shv/kitchen_light/set",
        "living_room_ac_mode": "shv/living_room_ac/mode/set",
        "living_room_ac_temperature": "shv/living_room_ac/temperature/set",
        "bedroom_ac_mode": "shv/bedroom_ac/mode/set",
        "bedroom_ac_temperature": "shv/bedroom_ac/temperature/set",
        "kitchen_ac_mode": "shv/kitchen_ac/mode/set",
        "kitchen_ac_temperature": "shv/kitchen_ac/temperature/set",
        "desk_plug": "shv/desk_plug/set",
    }
    publications = command_publications_for_tests("shv")
    assert {item.topic for item in publications} == set(command_topics("shv").values())
    assert all(not item.retain for item in publications)


def test_complete_state_publication_has_eleven_retained_messages():
    messages = state_messages(initial_state(), "shv")
    assert len(messages) == 11
    assert all(message.retain and message.qos == 1 for message in messages)


def test_state_messages_are_complete_retained_and_use_prefix():
    messages = state_messages(initial_state(), "house")
    assert [item.topic for item in messages] == [
        "house/living_room_light/state",
        "house/bedroom_light/state",
        "house/kitchen_light/state",
        "house/living_room_ac/mode/state",
        "house/living_room_ac/temperature/state",
        "house/bedroom_ac/mode/state",
        "house/bedroom_ac/temperature/state",
        "house/kitchen_ac/mode/state",
        "house/kitchen_ac/temperature/state",
        "house/desk_plug/state",
        "house/indoor_temperature/state",
    ]
    assert all(item.retain and item.qos == 1 for item in messages)
    assert json.loads(messages[0].payload) == {"state": "OFF", "brightness": 50}


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("shv/living_room_light/set", ("living_room_light", "set")),
        ("shv/bedroom_light/set", ("bedroom_light", "set")),
        ("shv/kitchen_light/set", ("kitchen_light", "set")),
        ("shv/living_room_ac/mode/set", ("living_room_ac", "mode/set")),
        ("shv/living_room_ac/temperature/set", ("living_room_ac", "temperature/set")),
        ("shv/bedroom_ac/mode/set", ("bedroom_ac", "mode/set")),
        ("shv/bedroom_ac/temperature/set", ("bedroom_ac", "temperature/set")),
        ("shv/kitchen_ac/mode/set", ("kitchen_ac", "mode/set")),
        ("shv/kitchen_ac/temperature/set", ("kitchen_ac", "temperature/set")),
        ("shv/desk_plug/set", ("desk_plug", "set")),
    ],
)
def test_parse_command_topic_accepts_only_known_commands(topic, expected):
    assert parse_command_topic(topic, "shv") == expected


@pytest.mark.parametrize(
    "topic",
    [
        "other/living_room_light/set",
        "shv/indoor_temperature/set",
        "shv/desk_plug/state",
        "shv/desk_plug/set/extra",
        "shv//set",
    ],
)
def test_parse_command_topic_rejects_unknown_commands(topic):
    with pytest.raises(ValueError, match="unknown command topic"):
        parse_command_topic(topic, "shv")
