from event_stream import (
    EventHub,
    canonical_ha_snapshot,
    canonical_memory_snapshot,
    encode_sse,
)


def test_event_hub_returns_first_event_after_sequence():
    hub = EventHub(history_size=4)
    first = hub.publish("status", {"state": "connected"})
    second = hub.publish("snapshot", {"version": 1})

    assert hub.wait_after(first.id, timeout=0) == second


def test_snapshot_publications_coalesce_for_slow_subscribers():
    hub = EventHub(history_size=4)
    first = hub.publish("snapshot", {"version": 1})
    hub.publish("snapshot", {"version": 2})
    latest = hub.publish("snapshot", {"version": 3})

    assert hub.wait_after(first.id, timeout=0) == latest


def test_canonical_ha_snapshot_flattens_devices_and_replaces_string_versions():
    payload = {
        "ok": True,
        "data": {
            "rooms": [
                {
                    "id": "living_room",
                    "name": "客厅",
                    "simulated_temp": 26.0,
                    "devices": [
                        {
                            "id": "living_room_light",
                            "name": "客厅灯",
                            "type": "light",
                            "online": True,
                            "state": {"on": True, "brightness": 50},
                            "version": "2026-09-16T00:00:00+08:00",
                        }
                    ],
                }
            ]
        },
    }

    snapshot = canonical_ha_snapshot(payload, sequence=7, generated_at_ms=123)

    assert snapshot["version"] == 7
    assert snapshot["backend"] == "ha"
    assert snapshot["generated_at_ms"] == 123
    assert snapshot["rooms"][0]["devices"] is None
    assert snapshot["devices"][0]["version"] == 7
    assert snapshot["devices"][0]["room_id"] == "living_room"
    assert snapshot["devices"][0]["room_name"] == "客厅"
    assert snapshot["persons"] == []


def test_canonical_memory_snapshot_preserves_supported_collections():
    payload = {
        "version": 12,
        "rooms": [{"id": "living_room", "name": "客厅"}],
        "devices": [{"id": "living_room_light", "version": 3}],
        "persons": [{"id": "dad"}],
        "broadcasts": [{"id": "b1"}],
        "broadcast_queue": ["b1"],
    }

    snapshot = canonical_memory_snapshot(payload, sequence=8, generated_at_ms=456)

    assert snapshot["version"] == 8
    assert snapshot["backend"] == "memory"
    assert snapshot["devices"][0]["version"] == 3
    assert snapshot["persons"] == [{"id": "dad"}]
    assert snapshot["broadcast_queue"] == ["b1"]


def test_encode_sse_emits_utf8_event_with_terminating_blank_line():
    hub = EventHub()
    item = hub.publish("snapshot", {"name": "客厅灯"})

    encoded = encode_sse(item)

    assert encoded.decode("utf-8") == (
        'id: 1\nevent: snapshot\ndata: {"name":"客厅灯"}\n\n'
    )
