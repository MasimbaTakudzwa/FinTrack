from __future__ import annotations

from fastapi.testclient import TestClient

from sidecar.main import app


def test_health_ok() -> None:
    client = TestClient(app)
    response = client.get("/api/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
