import json
from dataclasses import dataclass

from model import AIR_CONDITIONERS, LIGHTS


@dataclass(frozen=True)
class Publication:
    topic: str
    payload: str
    retain: bool = True
    qos: int = 1


def command_topics(prefix="shv"):
    topics = {device: f"{prefix}/{device}/set" for device in LIGHTS}
    topics.update(
        {
            f"{device}_mode": f"{prefix}/{device}/mode/set"
            for device in AIR_CONDITIONERS
        }
    )
    topics.update(
        {
            f"{device}_temperature": f"{prefix}/{device}/temperature/set"
            for device in AIR_CONDITIONERS
        }
    )
    topics["desk_plug"] = f"{prefix}/desk_plug/set"
    return topics


def command_publications_for_tests(prefix="shv"):
    """Describe inbound command publications without turning them into retained state."""
    return [
        Publication(topic, "", retain=False, qos=1)
        for topic in command_topics(prefix).values()
    ]


# Deliberately no ``device`` block. Home Assistant 2026.6.3 ignores ``object_id``
# for MQTT discovery and prefixes the entity id with the *device* name slug, so a
# device block produced ``light.ke_ting_deng_shv_living_room_light``. Without it
# the entity id is exactly the entity-name slug, which is the stable id that the
# home-service catalog matches on.
CHINESE_LABELS = {
    "shv_living_room_light": "客厅灯",
    "shv_bedroom_light": "卧室灯",
    "shv_kitchen_light": "厨房灯",
    "shv_living_room_ac": "客厅空调",
    "shv_bedroom_ac": "卧室空调",
    "shv_kitchen_ac": "厨房空调",
    "shv_desk_plug": "智能插座",
    "shv_indoor_temperature": "室内温度",
}


def entity_name(unique_id):
    """Entity name whose Home Assistant slug is exactly ``unique_id``.

    The entity id must be predictable: it is what the home-service catalog maps
    to a stable device id. A Chinese name slugifies to pinyin such as
    ``ke_ting_deng``, which no catalog can anticipate.
    """
    return unique_id.replace("shv_", "SHV ").replace("_", " ").title()


def slugify_entity_name(name):
    """Mirror Home Assistant's slugify so the naming invariant can be tested."""
    lowered = name.strip().lower()
    return "".join(
        character if character.isalnum() else "_" for character in lowered
    ).strip("_")


def _availability(prefix, device_id):
    return {
        "availability": [
            {
                "topic": f"{prefix}/status",
                "payload_available": "online",
                "payload_not_available": "offline",
            },
            {
                "topic": f"{prefix}/{device_id}/availability",
                "payload_available": "online",
                "payload_not_available": "offline",
            },
        ],
        "availability_mode": "all",
    }


def discovery_messages(prefix="shv"):
    messages = []
    for device in LIGHTS:
        unique_id = f"shv_{device}"
        messages.append(
            Publication(
                f"homeassistant/light/{unique_id}/config",
                json.dumps(
                    {
                        "name": entity_name(unique_id),
                        "unique_id": unique_id,
                        "command_topic": f"{prefix}/{device}/set",
                        "state_topic": f"{prefix}/{device}/state",
                        "schema": "json",
                        "brightness": True,
                        "brightness_scale": 100,
                        **_availability(prefix, device),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    for device in AIR_CONDITIONERS:
        unique_id = f"shv_{device}"
        messages.append(
            Publication(
                f"homeassistant/climate/{unique_id}/config",
                json.dumps(
                    {
                        "name": entity_name(unique_id),
                        "unique_id": unique_id,
                        "mode_command_topic": f"{prefix}/{device}/mode/set",
                        "mode_state_topic": f"{prefix}/{device}/mode/state",
                        "temperature_command_topic": f"{prefix}/{device}/temperature/set",
                        "temperature_state_topic": f"{prefix}/{device}/temperature/state",
                        "current_temperature_topic": f"{prefix}/indoor_temperature/state",
                        "modes": ["off", "cool", "fan_only"],
                        "min_temp": 16,
                        "max_temp": 30,
                        "temp_step": 0.5,
                        **_availability(prefix, device),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    messages.extend([
        Publication(
            "homeassistant/switch/shv_desk_plug/config",
            json.dumps(
                {
                    "name": entity_name("shv_desk_plug"),
                    "unique_id": "shv_desk_plug",
                    "command_topic": f"{prefix}/desk_plug/set",
                    "state_topic": f"{prefix}/desk_plug/state",
                    **_availability(prefix, "desk_plug"),
                },
                ensure_ascii=False,
            ),
        ),
        Publication(
            "homeassistant/sensor/shv_indoor_temperature/config",
            json.dumps(
                {
                    "name": entity_name("shv_indoor_temperature"),
                    "unique_id": "shv_indoor_temperature",
                    "state_topic": f"{prefix}/indoor_temperature/state",
                    "device_class": "temperature",
                    "state_class": "measurement",
                    "unit_of_measurement": "°C",
                    **_availability(prefix, "indoor_temperature"),
                },
                ensure_ascii=False,
            ),
        ),
    ])
    return messages


def state_messages(state, prefix="shv"):
    plug = state["desk_plug"]
    sensor = state["indoor_temperature"]
    messages = [
        Publication(f"{prefix}/{device}/state", json.dumps(state[device], ensure_ascii=False, sort_keys=True))
        for device in LIGHTS
    ]
    for device in AIR_CONDITIONERS:
        ac = state[device]
        messages.extend(
            [
                Publication(f"{prefix}/{device}/mode/state", ac["mode"]),
                Publication(
                    f"{prefix}/{device}/temperature/state",
                    str(ac["target_temperature"]),
                ),
            ]
        )
    messages.extend([
        Publication(f"{prefix}/desk_plug/state", plug["state"]),
        Publication(
            f"{prefix}/indoor_temperature/state", str(sensor["temperature"])
        ),
    ])
    return messages


def parse_command_topic(topic, prefix="shv"):
    known = {
        *( (device, "set") for device in LIGHTS ),
        *( (device, "mode/set") for device in AIR_CONDITIONERS ),
        *( (device, "temperature/set") for device in AIR_CONDITIONERS ),
        ("desk_plug", "set"),
    }
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != prefix:
        raise ValueError("unknown command topic")
    device = parts[1]
    action = "/".join(parts[2:])
    if (device, action) not in known:
        raise ValueError("unknown command topic")
    return device, action
