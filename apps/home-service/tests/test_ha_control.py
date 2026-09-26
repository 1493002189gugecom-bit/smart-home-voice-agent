from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from urllib.error import HTTPError, URLError

import pytest

from ha_gateway import GatewayError, HAGateway
from ha_service import HAServiceApp, load_catalog, normalize_entity
from service_paths import CONFIG
from state import build_default_state
from vision_fixtures import build_store


CATALOG_PATH = Path(__file__).parents[1] / "config" / "ha_entities.json"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeGateway:
    def __init__(self, *, confirm=True):
        self.calls = []
        self.confirm = confirm
        self.call_error = None
        self.read_error = None
        self.items = {
            "light.shv_living_room_light": {
                "state": "off",
                "attributes": {"brightness": 0},
                "last_updated": "light-v1",
            },
            "climate.shv_living_room_ac": {
                "state": "off",
                "attributes": {"temperature": 26, "current_temperature": 26},
                "last_updated": "living-ac-v1",
            },
            "light.shv_bedroom_light": {
                "state": "off",
                "attributes": {"brightness": 0},
                "last_updated": "bedroom-light-v1",
            },
            "climate.shv_bedroom_ac": {
                "state": "off",
                "attributes": {"temperature": 26, "current_temperature": 26},
                "last_updated": "ac-v1",
            },
            "light.shv_kitchen_light": {
                "state": "off",
                "attributes": {"brightness": 0},
                "last_updated": "kitchen-light-v1",
            },
            "climate.shv_kitchen_ac": {
                "state": "off",
                "attributes": {"temperature": 26, "current_temperature": 26},
                "last_updated": "kitchen-ac-v1",
            },
            "switch.shv_desk_plug": {
                "state": "off",
                "attributes": {"power": 0},
                "last_updated": "plug-v1",
            },
            "sensor.shv_indoor_temperature": {
                "state": "26.0",
                "attributes": {},
                "last_updated": "temp-v1",
            },
        }

    def read(self, entity):
        if self.read_error:
            raise self.read_error
        item = self.items.get(entity)
        if item is None:
            raise GatewayError("not_found")
        return {**item, "attributes": dict(item.get("attributes", {}))}

    def call(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))
        if self.call_error:
            raise self.call_error
        if not self.confirm:
            return []
        entity = data["entity_id"]
        if domain == "light":
            old = self.items[entity]
            brightness = old["attributes"].get("brightness", 0)
            if "brightness_pct" in data:
                brightness = round(data["brightness_pct"] * 255 / 100)
            self.items[entity] = {
                "state": "off" if service == "turn_off" else "on",
                "attributes": {"brightness": brightness},
                "last_updated": "light-v2",
            }
        elif domain == "climate":
            old = self.items[entity]
            state = old["state"]
            attrs = dict(old["attributes"])
            if service == "set_hvac_mode":
                state = data["hvac_mode"]
            elif service == "set_temperature":
                attrs["temperature"] = data["temperature"]
            self.items[entity] = {"state": state, "attributes": attrs, "last_updated": "ac-v2"}
        return []


def make_app(tmp_path, gateway, *, db_name="ops.sqlite3"):
    clock = FakeClock()
    app = HAServiceApp(
        gateway,
        load_catalog(CATALOG_PATH),
        tmp_path / db_name,
        confirmation_timeout=0.05,
        poll_interval=0.01,
        clock=clock,
        sleep=clock.sleep,
    )
    return app


def post(app, path, body):
    return app.handle("POST", path, {}, body)


def get(app, path, **query):
    return app.handle("GET", path, {key: [str(value)] for key, value in query.items()}, {})


class ScriptedOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.request = None

    def open(self, request, timeout):
        self.request = request
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_gateway_disables_redirects_and_proxies_and_never_leaks_secret():
    token = "TOP-SECRET-TOKEN"
    gateway = HAGateway("http://ha.local", token)
    handlers = gateway.opener.handlers
    assert gateway.proxy_handler.proxies == {}
    assert any(handler.__class__.__name__ == "NoRedirect" for handler in handlers)

    gateway.opener = ScriptedOpener(
        HTTPError("http://ha.local/api/states/x", 401, "Authorization: Bearer " + token, {}, None)
    )
    with pytest.raises(GatewayError) as caught:
        gateway.read("light.x")
    assert caught.value.code == "backend_rejected"
    assert token not in repr(caught.value)
    assert "Authorization" not in repr(caught.value)


def test_gateway_distinguishes_get_failure_from_uncertain_post_submission():
    gateway = HAGateway("http://ha.local", "secret")
    gateway.opener = ScriptedOpener(URLError("connection reset"))
    with pytest.raises(GatewayError) as read_error:
        gateway.read("light.x")
    assert read_error.value.code == "backend_unavailable"
    assert read_error.value.may_have_submitted is False

    with pytest.raises(GatewayError) as call_error:
        gateway.call("light", "turn_on", {"entity_id": "light.x"})
    assert call_error.value.code == "submission_unknown"
    assert call_error.value.may_have_submitted is True


def test_gateway_keeps_explicit_http_rejection_certain():
    gateway = HAGateway("http://ha.local", "secret")
    gateway.opener = ScriptedOpener(HTTPError("url", 400, "bad request", {}, None))
    with pytest.raises(GatewayError) as caught:
        gateway.call("light", "turn_on", {})
    assert caught.value.code == "backend_rejected"
    assert caught.value.may_have_submitted is False


def test_gateway_classifies_http_5xx_as_unavailable_or_submission_unknown():
    gateway = HAGateway("http://ha.local", "secret")
    gateway.opener = ScriptedOpener(HTTPError("url", 503, "secret response", {}, None))
    with pytest.raises(GatewayError) as read_error:
        gateway.read("light.x")
    assert read_error.value.code == "backend_unavailable"
    assert read_error.value.may_have_submitted is False

    with pytest.raises(GatewayError) as call_error:
        gateway.call("light", "turn_on", {"entity_id": "light.x"})
    assert call_error.value.code == "submission_unknown"
    assert call_error.value.may_have_submitted is True


def test_project_catalog_maps_all_eight_scene_devices():
    catalog = load_catalog(CATALOG_PATH)
    assert set(catalog) == {
        "living_room_light",
        "living_room_ac",
        "desk_plug",
        "indoor_temperature",
        "bedroom_light",
        "bedroom_ac",
        "kitchen_light",
        "kitchen_ac",
    }
    assert catalog["living_room_ac"]["entity_id"] == "climate.shv_living_room_ac"
    assert catalog["bedroom_light"]["entity_id"] == "light.shv_bedroom_light"
    assert catalog["kitchen_light"]["entity_id"] == "light.shv_kitchen_light"
    assert catalog["kitchen_ac"]["entity_id"] == "climate.shv_kitchen_ac"
    assert catalog["living_room_light"]["domain"] == "light"
    assert catalog["living_room_light"]["services"] == {"on": "turn_on", "off": "turn_off"}
    assert catalog["living_room_light"]["confirmation"]["brightness_scale"] == 255
    assert catalog["bedroom_ac"]["services"] == {"mode": "set_hvac_mode", "temperature": "set_temperature"}
    light = normalize_entity(catalog["living_room_light"], {
        "state": "on", "attributes": {"brightness": 128}, "last_updated": "opaque-v"
    })
    assert light["state"] == {"on": True, "brightness": 50}
    assert light["version"] == "opaque-v"
    ac = normalize_entity(catalog["bedroom_ac"], {
        "state": "fan_only",
        "attributes": {"temperature": 24, "current_temperature": 25.5},
        "last_updated": "ac-v",
    })
    assert ac["state"] == {"on": True, "mode": "fan_only", "target_temp": 24.0, "current_temp": 25.5}
    plug = normalize_entity(catalog["desk_plug"], {
        "state": "on", "attributes": {"power": 60}, "last_updated": "p-v"
    })
    assert plug["state"] == {"on": True, "power": 60}
    sensor = normalize_entity(catalog["indoor_temperature"], {
        "state": "26.25", "attributes": {}, "last_updated": "t-v"
    })
    assert sensor["state"] == {"temperature": 26.25}


def test_direct_constructor_reports_eight_device_catalog_contract(tmp_path):
    incomplete_catalog = load_catalog(CATALOG_PATH)
    incomplete_catalog.pop("kitchen_ac")

    with pytest.raises(ValueError, match="HA entity catalog must contain exactly eight devices"):
        HAServiceApp(FakeGateway(), incomplete_catalog, tmp_path / "ops.sqlite3")


def test_unavailable_entity_is_offline():
    catalog = load_catalog(CATALOG_PATH)
    entity = normalize_entity(catalog["living_room_light"], {
        "state": "unavailable", "attributes": {}, "last_updated": "v"
    })
    assert entity["online"] is False


def test_health_and_queries_are_backed_by_ha(tmp_path):
    gateway = FakeGateway()
    app = make_app(tmp_path, gateway)
    assert get(app, "/health")[1]["ok"] is True
    devices = get(app, "/tool/device_status")[1]["data"]["devices"]
    assert len(devices) == 8
    assert get(app, "/tool/device_status", device="living_room_light")[1]["data"]["devices"][0]["id"] == "living_room_light"
    rooms = get(app, "/tool/room_status", room="客厅")[1]["data"]["rooms"]
    assert {device["id"] for device in rooms[0]["devices"]} == {
        "living_room_light", "living_room_ac", "desk_plug", "indoor_temperature"
    }


def test_query_errors_are_safe_and_not_found_is_explicit(tmp_path):
    gateway = FakeGateway()
    gateway.read_error = GatewayError("backend_unavailable")
    app = make_app(tmp_path, gateway)
    status, result = get(app, "/tool/device_status")
    assert status == 503
    assert result["error_code"] == "backend_unavailable"
    assert get(make_app(tmp_path, FakeGateway(), db_name="other.sqlite3"), "/tool/device_status", device="missing")[1]["error_code"] == "not_found"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"operation_id": "x", "device_id": "living_room_light"},
        {"operation_id": "x", "device_id": "living_room_light", "brightness": True},
        {"operation_id": "x", "device_id": "living_room_light", "brightness": 101},
        {"operation_id": "x", "device_id": "living_room_light", "brightness": float("nan")},
        {"operation_id": "x", "device_id": "living_room_light", "on": "false"},
    ],
)
def test_light_requires_operation_id_and_strict_parameters(tmp_path, body):
    gateway = FakeGateway()
    status, result = post(make_app(tmp_path, gateway), "/tool/set_light", body)
    assert status == 400
    assert result["status"] == "rejected"
    assert result["error_code"] == "invalid_request"
    assert gateway.calls == []


def test_light_brightness_uses_percent_service_and_confirms_only_observed_state(tmp_path):
    gateway = FakeGateway()
    app = make_app(tmp_path, gateway)
    status, result = post(app, "/tool/set_light", {
        "device_id": "living_room_light", "brightness": 50, "operation_id": "light-50"
    })
    assert status == 200
    assert result["ok"] is True
    assert result["status"] == "confirmed"
    assert result["data"]["state"] == {"on": True, "brightness": 50}
    assert gateway.calls == [("light", "turn_on", {
        "entity_id": "light.shv_living_room_light", "brightness_pct": 50
    })]


def test_noop_is_confirmed_without_service_call(tmp_path):
    gateway = FakeGateway()
    gateway.items["light.shv_living_room_light"] = {
        "state": "on", "attributes": {"brightness": 128}, "last_updated": "v"
    }
    result = post(make_app(tmp_path, gateway), "/tool/set_light", {
        "device_id": "living_room_light", "brightness": 50, "operation_id": "noop"
    })[1]
    assert result["ok"] is True
    assert result["status"] == "confirmed"
    assert result["data"]["noop"] is True
    assert gateway.calls == []


def test_offline_wrong_type_and_missing_mapping_are_rejected_before_send(tmp_path):
    gateway = FakeGateway()
    gateway.items["light.shv_living_room_light"]["state"] = "unavailable"
    app = make_app(tmp_path, gateway)
    offline = post(app, "/tool/set_light", {
        "device_id": "living_room_light", "on": True, "operation_id": "offline"
    })[1]
    wrong = post(app, "/tool/set_light", {
        "device_id": "bedroom_ac", "on": True, "operation_id": "wrong"
    })[1]
    missing = post(app, "/tool/set_light", {
        "device_id": "missing", "on": True, "operation_id": "missing"
    })[1]
    assert (offline["error_code"], wrong["error_code"], missing["error_code"]) == (
        "offline", "wrong_type", "not_found"
    )
    assert all(item["status"] == "rejected" for item in (offline, wrong, missing))
    assert gateway.calls == []


def test_pre_submit_read_failure_is_rejected_but_unknown_submit_is_unconfirmed(tmp_path):
    read_gateway = FakeGateway()
    read_gateway.read_error = GatewayError("backend_unavailable")
    read_result = post(make_app(tmp_path, read_gateway), "/tool/set_light", {
        "device_id": "living_room_light", "on": True, "operation_id": "read"
    })[1]
    assert read_result["status"] == "rejected"
    assert read_result["error_code"] == "backend_unavailable"

    submit_gateway = FakeGateway()
    submit_gateway.call_error = GatewayError("submission_unknown", may_have_submitted=True)
    submit_result = post(make_app(tmp_path, submit_gateway, db_name="submit.sqlite3"), "/tool/set_light", {
        "device_id": "living_room_light", "on": True, "operation_id": "submit"
    })[1]
    assert submit_result["status"] == "unconfirmed"
    assert submit_result["error_code"] == "submission_unknown"
    assert len(submit_gateway.calls) == 1


def test_explicit_service_rejection_is_rejected(tmp_path):
    gateway = FakeGateway()
    gateway.call_error = GatewayError("backend_rejected")
    result = post(make_app(tmp_path, gateway), "/tool/set_light", {
        "device_id": "living_room_light", "on": True, "operation_id": "reject"
    })[1]
    assert result["status"] == "rejected"
    assert result["error_code"] == "backend_rejected"


def test_http_200_without_observed_target_times_out_and_same_id_replays(tmp_path):
    gateway = FakeGateway(confirm=False)
    body = {"device_id": "living_room_light", "on": True, "operation_id": "one"}
    first_response = post(make_app(tmp_path, gateway), "/tool/set_light", body)
    second_response = post(make_app(tmp_path, gateway), "/tool/set_light", body)
    assert first_response == second_response
    assert first_response[0] == 202
    first = first_response[1]
    assert first["ok"] is False
    assert first["status"] == "unconfirmed"
    assert first["error_code"] == "confirmation_timeout"
    assert len(gateway.calls) == 1


def test_same_operation_id_with_different_normalized_request_conflicts(tmp_path):
    gateway = FakeGateway(confirm=False)
    app = make_app(tmp_path, gateway)
    post(app, "/tool/set_light", {
        "device_id": "living_room_light", "brightness": 50, "operation_id": "same"
    })
    result = post(app, "/tool/set_light", {
        "device_id": "living_room_light", "brightness": 60, "operation_id": "same"
    })[1]
    assert result["error_code"] == "operation_id_conflict"
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"device_id": "bedroom_ac", "operation_id": "x"},
        {"device_id": "bedroom_ac", "operation_id": "x", "target_temp": True},
        {"device_id": "bedroom_ac", "operation_id": "x", "target_temp": float("inf")},
        {"device_id": "bedroom_ac", "operation_id": "x", "target_temp": 15.5},
        {"device_id": "bedroom_ac", "operation_id": "x", "mode": "fan"},
        {"device_id": "bedroom_ac", "operation_id": "x", "on": "true"},
    ],
)
def test_ac_strict_validation(tmp_path, body):
    gateway = FakeGateway()
    result = post(make_app(tmp_path, gateway), "/tool/set_ac", body)[1]
    assert result["status"] == "rejected"
    assert result["error_code"] == "invalid_request"
    assert gateway.calls == []


def test_ac_mode_and_temperature_are_submitted_and_confirmed(tmp_path):
    gateway = FakeGateway()
    app = make_app(tmp_path, gateway)
    result = post(app, "/tool/set_ac", {
        "device_id": "bedroom_ac",
        "mode": "fan_only",
        "target_temp": 24,
        "operation_id": "ac-one",
    })[1]
    assert result["ok"] is True
    assert result["status"] == "confirmed"
    assert result["data"]["state"]["mode"] == "fan_only"
    assert result["data"]["state"]["target_temp"] == 24.0
    assert gateway.calls == [
        ("climate", "set_hvac_mode", {
            "entity_id": "climate.shv_bedroom_ac", "hvac_mode": "fan_only"
        }),
        ("climate", "set_temperature", {
            "entity_id": "climate.shv_bedroom_ac", "temperature": 24.0
        }),
    ]


def test_explicit_relative_ac_change_uses_current_target_and_spoken_degrees(tmp_path):
    gateway = FakeGateway()
    gateway.items["climate.shv_bedroom_ac"]["state"] = "cool"
    gateway.items["climate.shv_bedroom_ac"]["attributes"]["temperature"] = 26.5
    result = post(make_app(tmp_path, gateway), "/tool/adjust_ac", {
        "device_id": "bedroom_ac", "direction": "cooler", "degrees": 2,
        "operation_id": "ac-relative-two",
    })[1]
    assert result["ok"] is True
    assert result["data"]["state"]["target_temp"] == 24.5
    assert gateway.calls == [("climate", "set_temperature", {
        "entity_id": "climate.shv_bedroom_ac", "temperature": 24.5,
    })]


@pytest.mark.parametrize("degrees", [0, -2, True, float("inf"), 20])
def test_explicit_relative_ac_change_rejects_invalid_degrees(tmp_path, degrees):
    gateway = FakeGateway()
    status, result = post(make_app(tmp_path, gateway), "/tool/adjust_ac", {
        "device_id": "bedroom_ac", "direction": "cooler", "degrees": degrees,
        "operation_id": "ac-relative-invalid",
    })
    assert status == 400
    assert result["error_code"] == "invalid_request"
    assert gateway.calls == []


def test_ac_on_defaults_to_cool_at_the_local_comfort_temperature(tmp_path):
    """No temperature in the request means the *local* default is used, so the
    model never has to invent one."""
    gateway = FakeGateway()
    result = post(make_app(tmp_path, gateway), "/tool/set_ac", {
        "device_id": "bedroom_ac", "on": True, "operation_id": "ac-on"
    })[1]
    assert result["ok"] is True
    assert result["data"]["state"]["mode"] == "cool"
    assert result["data"]["state"]["target_temp"] == 26.0
    assert gateway.calls == [
        ("climate", "set_hvac_mode", {
            "entity_id": "climate.shv_bedroom_ac", "hvac_mode": "cool"
        }),
        ("climate", "set_temperature", {
            "entity_id": "climate.shv_bedroom_ac", "temperature": 26.0
        }),
    ]


def test_ac_off_noops_when_already_off_and_submits_when_running(tmp_path):
    gateway = FakeGateway()
    body = {"device_id": "bedroom_ac", "on": False, "operation_id": "ac-off-noop"}
    result = post(make_app(tmp_path, gateway), "/tool/set_ac", body)[1]
    assert result["ok"] is True
    assert result["data"]["noop"] is True
    assert gateway.calls == []

    gateway.items["climate.shv_bedroom_ac"]["state"] = "cool"
    result = post(make_app(tmp_path, gateway, db_name="off.sqlite3"), "/tool/set_ac", {
        "device_id": "bedroom_ac", "on": False, "operation_id": "ac-off-send"
    })[1]
    assert result["ok"] is True
    assert result["data"]["state"]["mode"] == "off"
    assert gateway.calls == [("climate", "set_hvac_mode", {
        "entity_id": "climate.shv_bedroom_ac", "hvac_mode": "off"
    })]


def test_same_device_controls_are_serialized_through_confirmation(tmp_path):
    class BlockingGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.first_entered = Event()
            self.release_first = Event()
            self.counter_lock = Lock()
            self.call_count = 0

        def call(self, domain, service, data):
            with self.counter_lock:
                index = self.call_count
                self.call_count += 1
            if index == 0:
                self.first_entered.set()
                assert self.release_first.wait(2)
            return super().call(domain, service, data)

    gateway = BlockingGateway()
    app = make_app(tmp_path, gateway)
    second_finished = Event()

    def turn_on():
        return post(app, "/tool/set_light", {
            "device_id": "living_room_light", "on": True, "operation_id": "on"
        })

    def turn_off():
        try:
            return post(app, "/tool/set_light", {
                "device_id": "living_room_light", "on": False, "operation_id": "off"
            })
        finally:
            second_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(turn_on)
        assert gateway.first_entered.wait(2)
        second = executor.submit(turn_off)
        assert not second_finished.wait(0.1), "second operation interleaved before first confirmation"
        gateway.release_first.set()
        assert first.result(timeout=2)[1]["status"] == "confirmed"
        assert second.result(timeout=2)[1]["status"] == "confirmed"

    assert [(domain, service) for domain, service, _ in gateway.calls] == [
        ("light", "turn_on"), ("light", "turn_off")
    ]
    assert gateway.items["light.shv_living_room_light"]["state"] == "off"


def test_submitted_recovery_only_reconciles_and_never_resends(tmp_path):
    gateway = FakeGateway(confirm=False)
    app = make_app(tmp_path, gateway)
    app.store.reserve("one", "set_light", "living_room_light", {"on": True}, {"on": True})
    app.store.transition("one", "submitted")
    make_app(tmp_path, gateway).reconcile_unfinished()
    assert gateway.calls == []
    assert app.store.get("one").status == "unconfirmed"


def test_accepted_recovery_reads_then_may_submit_once(tmp_path):
    gateway = FakeGateway()
    app = make_app(tmp_path, gateway)
    app.store.reserve("one", "set_light", "living_room_light", {"brightness": 50}, {"on": True, "brightness": 50})
    make_app(tmp_path, gateway).reconcile_unfinished()
    assert len(gateway.calls) == 1
    assert app.store.get("one").status == "confirmed"


def test_unconfirmed_recovery_only_reconciles_and_can_later_confirm(tmp_path):
    gateway = FakeGateway(confirm=False)
    app = make_app(tmp_path, gateway)
    app.store.reserve("one", "set_light", "living_room_light", {"on": True}, {"on": True})
    app.store.transition("one", "submitted")
    app.store.transition("one", "unconfirmed", error_code="confirmation_timeout")
    make_app(tmp_path, gateway).reconcile_unfinished()
    assert app.store.get("one").status == "unconfirmed"
    gateway.items["light.shv_living_room_light"]["state"] = "on"
    make_app(tmp_path, gateway).reconcile_unfinished()
    assert app.store.get("one").status == "confirmed"
    assert gateway.calls == []


def test_ha_mode_space_and_test_mutation_routes_are_404(tmp_path):
    app = make_app(tmp_path, FakeGateway())
    for method, path in (
        ("POST", "/tool/notify"),
        ("POST", "/tool/broadcast"),
        ("POST", "/test/device_online"),
        ("POST", "/test/set_room_temp"),
    ):
        status, result = app.handle(method, path, {}, {})
        assert status == 404
        assert result["ok"] is False


# ------------------------------------------------- camera-owned person state
def test_person_location_reads_the_camera_store_in_ha_mode(tmp_path):
    """Regression: the voice agent offers `query_person_location` whichever
    backend is active, but only the memory backend served the route. In HA mode
    the tool therefore always failed and the assistant had to admit it could not
    see anyone, even while the camera was reporting a confirmed location."""
    app = make_app(tmp_path, FakeGateway())
    app.visual_state = build_store(build_default_state(CONFIG), {"bedroom": ["dad"]})

    status, payload = get(app, "/tool/person_location", person="爸爸")

    assert status == 200
    assert payload["error_code"] is None
    person = payload["data"]["persons"][0]
    assert person["id"] == "dad"
    assert person["location_known"] is True
    assert person["room_id"] == "bedroom"
    assert person["room_name"] == "卧室"
    assert person["location_source"] == "camera"


def test_person_location_lists_everyone_when_no_name_is_given(tmp_path):
    app = make_app(tmp_path, FakeGateway())
    app.visual_state = build_store(build_default_state(CONFIG), {"bedroom": ["dad"]})

    status, payload = get(app, "/tool/person_location")

    assert status == 200
    names = {item["display_name"]: item["location_known"] for item in payload["data"]["persons"]}
    assert names["爸爸"] is True
    assert names["妈妈"] is False


def test_unseen_people_never_report_a_room(tmp_path):
    """An expired observation means "not seen", not "still in the old room"."""
    app = make_app(tmp_path, FakeGateway())
    app.visual_state = build_store(build_default_state(CONFIG), {})

    status, payload = get(app, "/tool/person_location")

    assert status == 200
    for person in payload["data"]["persons"]:
        assert person["location_known"] is False
        assert person["room_id"] is None
        assert person["room_name"] is None
        assert person["pose"] == "unknown"


def test_unknown_person_name_is_not_found(tmp_path):
    app = make_app(tmp_path, FakeGateway())
    app.visual_state = build_store(build_default_state(CONFIG), {"bedroom": ["dad"]})

    status, payload = get(app, "/tool/person_location", person="隔壁老王")

    assert status == 400
    assert payload["ok"] is False


def test_person_location_without_a_store_is_reported_not_invented(tmp_path):
    app = make_app(tmp_path, FakeGateway())

    status, payload = get(app, "/tool/person_location")

    assert status == 400
    assert payload["ok"] is False
