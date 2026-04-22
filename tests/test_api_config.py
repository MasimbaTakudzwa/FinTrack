from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidecar.config import settings as cfg
from sidecar.main import app


def test_get_config_returns_defaults(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.get("/api/config/")
    assert r.status_code == 200
    body = r.json()
    assert "settings" in body
    assert "readonly" in body

    keys = {s["key"] for s in body["settings"]}
    assert keys == {
        "ingest_prices.interval_minutes",
        "ingest_crypto.enabled",
        "ingest_crypto.interval_minutes",
        "ingest_macro.cron_hour_utc",
        "fred_api_key",
    }

    by_key = {s["key"]: s for s in body["settings"]}
    assert by_key["ingest_prices.interval_minutes"]["value"] == 5
    assert by_key["ingest_crypto.enabled"]["value"] is False
    assert by_key["ingest_crypto.enabled"]["source"] in ("default", "env")

    # readonly block
    assert isinstance(body["readonly"]["db_path"], str)
    assert isinstance(body["readonly"]["port"], int)


def test_get_config_masks_secret_value(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "fred_api_key", "supersecret")

    client = TestClient(app)
    body = client.get("/api/config/").json()
    by_key = {s["key"]: s for s in body["settings"]}
    fred = by_key["fred_api_key"]
    assert fred["value"] is None  # never returned verbatim
    assert fred["has_value"] is True
    assert fred["type"] == "secret"
    assert fred["source"] == "env"


def test_put_config_persists_int(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.put(
        "/api/config/",
        json={"updates": {"ingest_prices.interval_minutes": 10}},
    )
    assert r.status_code == 200
    body = r.json()
    by_key = {s["key"]: s for s in body["settings"]}
    assert by_key["ingest_prices.interval_minutes"]["value"] == 10
    assert by_key["ingest_prices.interval_minutes"]["source"] == "db"


def test_put_config_persists_bool_and_string(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.put(
        "/api/config/",
        json={
            "updates": {
                "ingest_crypto.enabled": True,
                "fred_api_key": "my-key-123",
            }
        },
    )
    assert r.status_code == 200
    by_key = {s["key"]: s for s in r.json()["settings"]}
    assert by_key["ingest_crypto.enabled"]["value"] is True
    assert by_key["ingest_crypto.enabled"]["source"] == "db"
    assert by_key["fred_api_key"]["has_value"] is True
    assert by_key["fred_api_key"]["value"] is None  # masked
    assert by_key["fred_api_key"]["source"] == "db"


def test_put_config_validation_rejects_out_of_bounds(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.put(
        "/api/config/",
        json={"updates": {"ingest_prices.interval_minutes": 99999}},
    )
    assert r.status_code == 422
    assert "must be <=" in r.json()["detail"]


def test_put_config_rejects_unknown_key(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.put(
        "/api/config/",
        json={"updates": {"bogus.key": 1}},
    )
    assert r.status_code == 422
    assert "unknown setting" in r.json()["detail"]


def test_put_empty_secret_clears_db_override(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "fred_api_key", "env-fallback")

    client = TestClient(app)
    # Set DB override.
    client.put(
        "/api/config/",
        json={"updates": {"fred_api_key": "db-override"}},
    )
    body = client.get("/api/config/").json()
    by_key = {s["key"]: s for s in body["settings"]}
    assert by_key["fred_api_key"]["source"] == "db"

    # Clear via empty string.
    client.put("/api/config/", json={"updates": {"fred_api_key": ""}})
    body = client.get("/api/config/").json()
    by_key = {s["key"]: s for s in body["settings"]}
    assert by_key["fred_api_key"]["source"] == "env"
    assert by_key["fred_api_key"]["has_value"] is True


def test_put_empty_updates_noop(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.put("/api/config/", json={"updates": {}})
    assert r.status_code == 200
    # Should return current state without error.
    assert len(r.json()["settings"]) == 5


def test_put_atomic_on_validation_failure(isolated_db: Path) -> None:
    client = TestClient(app)
    # First valid, second invalid — neither should land.
    r = client.put(
        "/api/config/",
        json={
            "updates": {
                "ingest_prices.interval_minutes": 10,
                "ingest_macro.cron_hour_utc": 99,
            }
        },
    )
    assert r.status_code == 422

    body = client.get("/api/config/").json()
    by_key = {s["key"]: s for s in body["settings"]}
    # interval stayed at default 5 (not the 10 from the rejected batch)
    assert by_key["ingest_prices.interval_minutes"]["value"] == 5
