import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from home_auth import ensure_write_token, home_write_headers, read_write_token


def test_token_is_created_once_and_not_replaced(tmp_path, monkeypatch):
    path = tmp_path / "local" / "write.token"
    monkeypatch.setenv("HOME_SERVICE_WRITE_TOKEN_FILE", str(path))
    token = ensure_write_token()
    assert re.fullmatch(r"[a-f0-9]{64}", token)
    assert ensure_write_token() == token
    assert home_write_headers("http://127.0.0.1:8765/tool/set_light") == {"X-Home-Token": token}


@pytest.mark.parametrize("existing", ["", "broken", "ÿ"])
def test_corrupt_credential_is_rejected_without_overwriting_it(tmp_path, monkeypatch, existing):
    path = tmp_path / "write.token"
    path.write_text(existing, encoding="utf-8")
    monkeypatch.setenv("HOME_SERVICE_WRITE_TOKEN_FILE", str(path))
    assert read_write_token() == ""
    with pytest.raises(ValueError, match="missing or invalid"):
        ensure_write_token()
    assert path.read_text(encoding="utf-8") == existing


@pytest.mark.parametrize("url", ["https://example.invalid", "http://127.0.0.1.evil.invalid", "http://user@localhost:8765", "file:///tmp/write.token"])
def test_credential_is_never_attached_to_nonlocal_destinations(tmp_path, monkeypatch, url):
    monkeypatch.setenv("HOME_SERVICE_WRITE_TOKEN_FILE", str(tmp_path / "write.token"))
    ensure_write_token()
    with pytest.raises(ValueError, match="loopback"):
        home_write_headers(url)
