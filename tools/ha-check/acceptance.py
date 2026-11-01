"""Real-stack acceptance for the Home Assistant virtual device closed loop.

Runs against the live stack: Home Assistant, Mosquitto, the simulator image and
a locally running home-service. Prints a machine-readable JSON summary and exits
non-zero when any check fails.

Secrets come only from the local git-ignored env files and are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, ProxyHandler, HTTPRedirectHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps"))
from home_auth import home_write_headers

from connection import credentials

ENTITY_IDS = {
    "living_room_light": "light.shv_living_room_light",
    "bedroom_ac": "climate.shv_bedroom_ac",
    "desk_plug": "switch.shv_desk_plug",
    "indoor_temperature": "sensor.shv_indoor_temperature",
}
DEFAULT_SERVICE_URL = "http://127.0.0.1:8765"
DEFAULT_FAULT_FILE = "runtime/home-assistant/fault-control.json"


class CheckFailure(Exception):
    """A single named acceptance check failed."""


def atomic_write_json(path, payload):
    """Write the fault record so the simulator never reads a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def ha_request(method, path, data=None, timeout=10):
    url, token = credentials()
    request = Request(
        url + path,
        method=method,
        data=None if data is None else json.dumps(data).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


class _NoServiceRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_service_opener = build_opener(ProxyHandler({}), _NoServiceRedirect())


def service_request(base_url, method, path, data=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if method.upper() == "POST":
        headers.update(home_write_headers(base_url + path))
    request = Request(
        base_url + path,
        method=method,
        data=None if data is None else json.dumps(data).encode(),
        headers=headers,
    )
    try:
        with _service_opener.open(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"ok": False, "error_code": "unparsable_backend_error"}
    except (URLError, TimeoutError, OSError):
        # Reported as a named failure instead of a stack trace so an operator
        # who forgot to start the service sees exactly which step is missing.
        return 0, {"ok": False, "error_code": "service_unreachable"}


def entity_state(entity_id):
    return ha_request("GET", "/api/states/" + entity_id)


def wait_for_entities(expected, timeout=60.0, interval=2.0):
    """Wait a bounded time for MQTT discovery to register every entity."""
    deadline = time.monotonic() + timeout
    seen = {}
    while True:
        for device_id, entity_id in expected.items():
            if device_id in seen:
                continue
            try:
                seen[device_id] = entity_state(entity_id)
            except (HTTPError, URLError, TimeoutError, OSError):
                continue
        if len(seen) == len(expected) or time.monotonic() >= deadline:
            return seen
        time.sleep(interval)


def wait_until(predicate, timeout=15.0, interval=0.5):
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = predicate()
        except (HTTPError, URLError, TimeoutError, OSError):
            value = None
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def normalize_state(record):
    state = record.get("state")
    attributes = record.get("attributes", {})
    brightness = attributes.get("brightness")
    return {
        "raw_state": state,
        "brightness_pct": None if brightness is None else round(brightness * 100 / 255),
        "hvac_mode": state,
        "target_temp": attributes.get("temperature"),
        "power": attributes.get("power"),
        "temperature": state,
    }


def wait_for_state(entity_id, predicate, timeout=15.0, interval=0.5):
    """Return the normalized state once ``predicate`` accepts it, else ``None``.

    ``wait_until`` returns whatever its predicate returned, so a bare comparison
    would yield ``True`` and callers would then subscript a bool.
    """
    matched = {}

    def check():
        state = normalize_state(entity_state(entity_id))
        if predicate(state):
            matched["state"] = state
            return True
        return False

    wait_until(check, timeout=timeout, interval=interval)
    return matched.get("state")


class Report:
    def __init__(self):
        self.checks = []

    def record(self, name, ok, detail=None):
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {name}" + (f" - {detail}" if detail else ""), flush=True)

    def require(self, name, condition, detail=None):
        self.record(name, condition, detail)
        if not condition:
            raise CheckFailure(name)


def run(service_url, fault_file, skip_faults=False):
    """Run every check and always return the partial report plus elapsed time."""
    report = Report()
    started = time.monotonic()
    try:
        _run_checks(report, service_url, fault_file, skip_faults)
    except CheckFailure:
        # The failing check is already recorded; later checks cannot be trusted.
        pass
    except Exception as exc:  # noqa: BLE001 - report the class, never a secret
        report.record("unexpected_error", False, type(exc).__name__)
    return report, time.monotonic() - started


def _run_checks(report, service_url, fault_file, skip_faults=False):
    status, health = service_request(service_url, "GET", "/health")
    report.require(
        "service_reachable",
        status == 200 and health.get("ok") is True,
        f"{service_url} -> http={status} {health.get('error_code') or ''}".strip(),
    )

    entities = wait_for_entities(ENTITY_IDS)
    report.require(
        "four_entities_discovered",
        set(entities) == set(ENTITY_IDS),
        f"found {sorted(entities)}",
    )
    report.require(
        "entities_available",
        all(
            entities[device_id].get("state") not in {"unavailable", "unknown"}
            for device_id in ENTITY_IDS
        ),
        {device_id: entities[device_id].get("state") for device_id in ENTITY_IDS},
    )

    status, body = service_request(
        service_url, "POST", "/tool/set_light",
        {"device_id": "living_room_light", "brightness": 50,
         "operation_id": str(uuid.uuid4())},
    )
    observed = wait_for_state(
        ENTITY_IDS["living_room_light"], lambda state: state["brightness_pct"] == 50
    )
    report.require(
        "light_50_confirmed",
        status == 200 and body.get("status") == "confirmed" and observed is not None,
        f"http={status} status={body.get('status')} "
        f"brightness={observed and observed['brightness_pct']}",
    )

    status, body = service_request(
        service_url, "POST", "/tool/set_ac",
        {"device_id": "bedroom_ac", "mode": "cool", "target_temp": 24,
         "operation_id": str(uuid.uuid4())},
    )
    ac = wait_for_state(
        ENTITY_IDS["bedroom_ac"],
        lambda state: state["hvac_mode"] == "cool" and state["target_temp"] == 24,
    )
    report.require(
        "ac_cool_24_confirmed",
        status == 200 and body.get("status") == "confirmed" and ac is not None,
        f"http={status} status={body.get('status')} "
        f"mode={ac and ac['hvac_mode']} temp={ac and ac['target_temp']}",
    )

    # A duplicate operation id must replay instead of commanding twice.
    duplicate = str(uuid.uuid4())
    first = service_request(
        service_url, "POST", "/tool/set_light",
        {"device_id": "living_room_light", "brightness": 30, "operation_id": duplicate},
    )
    second = service_request(
        service_url, "POST", "/tool/set_light",
        {"device_id": "living_room_light", "brightness": 30, "operation_id": duplicate},
    )
    report.require(
        "duplicate_operation_id_replays",
        first == second,
        f"first={first[0]} second={second[0]}",
    )

    status, body = service_request(
        service_url, "POST", "/tool/set_light",
        {"device_id": "living_room_light", "brightness": 60, "operation_id": duplicate},
    )
    report.require(
        "operation_id_conflict_detected",
        status == 409 and body.get("error_code") == "operation_id_conflict",
        f"http={status} code={body.get('error_code')}",
    )

    if not skip_faults:
        run_fault_checks(report, service_url, fault_file)

    report.require(
        "service_health_after_faults",
        service_request(service_url, "GET", "/health")[0] == 200,
    )
    return report


def run_fault_checks(report, service_url, fault_file):
    light = ENTITY_IDS["living_room_light"]

    atomic_write_json(fault_file, {"device_id": "living_room_light", "online": False})
    time.sleep(1.5)
    status, body = service_request(
        service_url, "POST", "/tool/set_light",
        {"device_id": "living_room_light", "on": True, "operation_id": str(uuid.uuid4())},
    )
    report.require(
        "offline_device_is_rejected",
        body.get("error_code") == "offline",
        f"http={status} code={body.get('error_code')}",
    )
    atomic_write_json(fault_file, {"device_id": "living_room_light", "online": True})
    report.require(
        "offline_device_recovers",
        wait_until(
            lambda: entity_state(light).get("state") in {"on", "off"}
        )
        is not None,
    )

    # Inject a single command failure: the device must keep its state and the
    # service must not claim success.
    before = normalize_state(entity_state(light))
    target_on = before["raw_state"] != "on"
    atomic_write_json(fault_file, {"fail_next": "living_room_light"})
    time.sleep(1.5)
    status, body = service_request(
        service_url, "POST", "/tool/set_light",
        {"device_id": "living_room_light", "on": target_on, "operation_id": str(uuid.uuid4())},
    )
    report.require(
        "injected_failure_is_not_reported_as_success",
        body.get("ok") is not True,
        f"http={status} status={body.get('status')} code={body.get('error_code')}",
    )
    time.sleep(2.0)
    after = normalize_state(entity_state(light))
    report.require(
        "injected_failure_keeps_device_state",
        after["raw_state"] == before["raw_state"],
        f"{before['raw_state']} -> {after['raw_state']}",
    )

    # A delay longer than the confirmation window must report timeout, and the
    # device must not already show the target when the timeout is reported.
    atomic_write_json(fault_file, {"delay_seconds": 6})
    time.sleep(1.5)
    status, body = service_request(
        service_url, "POST", "/tool/set_light",
        {"device_id": "living_room_light", "brightness": 90, "operation_id": str(uuid.uuid4())},
    )
    report.require(
        "delay_reports_unconfirmed_not_failure",
        body.get("status") == "unconfirmed"
        and body.get("error_code") in {"confirmation_timeout", "backend_unavailable", "submission_unknown"},
        f"http={status} status={body.get('status')} code={body.get('error_code')}",
    )
    # Late completion is expected and proves the timeout meant "unconfirmed".
    late = wait_for_state(
        light, lambda state: state["brightness_pct"] == 90, timeout=20.0
    )
    atomic_write_json(fault_file, {"delay_seconds": 0})
    report.record(
        "delayed_command_completes_late",
        late is not None,
        f"brightness={late and late['brightness_pct']}",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--service-url",
        default=os.environ.get("HOME_SERVICE_URL", DEFAULT_SERVICE_URL),
    )
    parser.add_argument(
        "--fault-file",
        default=os.environ.get("SHV_FAULT_FILE_HOST", DEFAULT_FAULT_FILE),
    )
    parser.add_argument("--skip-faults", action="store_true")
    args = parser.parse_args()

    report = Report()
    try:
        report, elapsed = run(args.service_url, args.fault_file, args.skip_faults)
    except Exception as exc:  # noqa: BLE001 - report the class, never a secret
        report.record("unexpected_error", False, type(exc).__name__)
        elapsed = 0.0
    ok = bool(report.checks) and all(check["ok"] for check in report.checks)

    print(
        json.dumps(
            {"ok": ok, "seconds": round(elapsed, 1), "checks": report.checks},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
