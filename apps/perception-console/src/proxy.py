"""Allow-listed loopback proxy used by the perception console."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


MAX_REQUEST_BODY = 1024 * 1024


@dataclass(frozen=True)
class Service:
    name: str
    base_url: str
    allowed: dict[str, tuple[str, ...]]


SERVICES = {
    "home": Service("home", "http://127.0.0.1:8765", {
        "GET": ("/health", "/state", "/snapshot", "/events", "/tool/room_status", "/tool/person_location", "/tool/device_status"),
    }),
    "vision": Service("vision", "http://127.0.0.1:8766", {
        "GET": ("/health", "/config", "/cameras", "/results", "/preview.jpg", "/registration"),
        "POST": ("/camera/select", "/room/select", "/monitor/start", "/monitor/pause", "/registration/start", "/registration/cancel"),
        "DELETE": ("/registration/",),
    }),
    "voice": Service("voice", "http://127.0.0.1:8767", {
        "GET": ("/health", "/history", "/events", "/devices"),
    }),
}


class ProxyRejected(ValueError):
    pass


def resolve_target(service_name: str, method: str, suffix: str) -> str:
    service = SERVICES.get(service_name)
    if service is None:
        raise ProxyRejected("unknown service")
    method = method.upper()
    path = urlsplit(suffix).path
    allowed = service.allowed.get(method, ())
    accepted = any(path == item or (item.endswith("/") and path.startswith(item)) for item in allowed)
    if not accepted:
        raise ProxyRejected("route is not allowed")
    return service.base_url + suffix


def open_upstream(service_name: str, method: str, suffix: str, body: bytes | None, timeout: float = 15.0):
    if body is not None and len(body) > MAX_REQUEST_BODY:
        raise ProxyRejected("request body is too large")
    target = resolve_target(service_name, method, suffix)
    headers = {"Accept": "application/json, text/event-stream, image/jpeg"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(target, data=body, headers=headers, method=method.upper())
    return urllib.request.urlopen(request, timeout=timeout)

