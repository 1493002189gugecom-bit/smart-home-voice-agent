"""Local home-service write credential, never exposed to the browser or model."""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit


def token_path() -> Path:
    configured = os.environ.get("HOME_SERVICE_WRITE_TOKEN_FILE")
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / "runtime/home-service/write.token"


def read_write_token() -> str:
    try:
        token = token_path().read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else ""


def ensure_write_token() -> str:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="ascii") as output:
            output.write(secrets.token_hex(32))
    token = read_write_token()
    if not token:
        raise ValueError("home-service write token is missing or invalid")
    return token


def home_write_headers(url: str) -> dict[str, str]:
    destination = urlsplit(url)
    if (destination.scheme != "http" or destination.hostname not in {"127.0.0.1", "localhost", "::1"}
            or destination.username is not None or destination.password is not None):
        raise ValueError("home-service writes require a loopback HTTP destination")
    token = read_write_token()
    return {"X-Home-Token": token} if token else {}
