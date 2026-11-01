"""Exercise the launcher's configuration gate without starting any services."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is not installed")
@pytest.mark.parametrize("service,health,backend,mode,error", [
    ("home", {"backend": "ha"}, "ha", "hybrid", None),
    ("home", {"backend": "memory"}, "ha", "hybrid", "backend"),
    ("home", {}, "ha", "hybrid", "backend"),
    ("voice", {"listening_mode": "hybrid", "intent_status": "ready"}, "ha", "hybrid", None),
    ("voice", {"listening_mode": "wake", "intent_status": "disabled"}, "ha", "wake", None),
    ("voice", {"listening_mode": "wake", "requested_listening_mode": "hybrid"}, "ha", "hybrid", "listening_mode"),
    ("voice", {"listening_mode": "hybrid", "intent_status": "configuration_error"}, "ha", "hybrid", "intent_status"),
    ("voice", {}, "ha", "hybrid", "listening_mode"),
])
def test_reused_service_must_match_requested_configuration(service, health, backend, mode, error):
    helper = str(ROOT / "tools/perception-health.ps1").replace("'", "''")
    payload = json.dumps(health).replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f". '{helper}'\n"
        f"$health = '{payload}' | ConvertFrom-Json\n"
        f"Assert-ServiceConfiguration -Name '{service}' -Health $health -HomeBackend '{backend}' -ListeningMode '{mode}'\n"
    )
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=15)
    output = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
    if error:
        assert result.returncode != 0 and error in output, output
    else:
        assert result.returncode == 0, output
