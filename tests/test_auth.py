from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidecar.config import settings as cfg
from sidecar.main import app

# Host the Tauri webview/shell actually uses; the Host allowlist must permit it.
_BASE = "http://127.0.0.1"


def test_no_token_means_no_enforcement(isolated_db: Path) -> None:
    """Default (empty token) leaves the API open — TestClient default host
    'testserver' must still work for the whole existing suite."""
    with TestClient(app) as client:
        assert client.get("/api/assets/").status_code == 200


def test_token_required_when_set(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "auth_token", "secret-token")
    with TestClient(app, base_url=_BASE) as client:
        # Missing token → 401.
        assert client.get("/api/assets/").status_code == 401
        # Wrong token → 401.
        assert (
            client.get(
                "/api/assets/", headers={"X-FinTrack-Token": "nope"}
            ).status_code
            == 401
        )
        # Correct token → 200.
        assert (
            client.get(
                "/api/assets/", headers={"X-FinTrack-Token": "secret-token"}
            ).status_code
            == 200
        )


def test_health_is_exempt_from_token(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "auth_token", "secret-token")
    with TestClient(app, base_url=_BASE) as client:
        assert client.get("/api/health/").status_code == 200


def test_foreign_host_blocked(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "auth_token", "secret-token")
    # Simulate a DNS-rebinding request: valid token but attacker Host header.
    with TestClient(app, base_url="http://evil.example.com") as client:
        resp = client.get(
            "/api/assets/", headers={"X-FinTrack-Token": "secret-token"}
        )
        assert resp.status_code == 403
