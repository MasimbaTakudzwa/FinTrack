from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType
from sidecar.main import app


def test_list_assets_empty(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.get("/api/assets/")
    assert r.status_code == 200
    assert r.json() == []


def test_list_assets_returns_active(isolated_db: Path) -> None:
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK))
        s.add(
            Asset(
                symbol="MSFT",
                name="Microsoft Corporation",
                asset_type=AssetType.STOCK,
            )
        )
        s.add(
            Asset(
                symbol="DEAD",
                name="Delisted",
                asset_type=AssetType.STOCK,
                is_active=False,
            )
        )

    client = TestClient(app)
    r = client.get("/api/assets/")
    assert r.status_code == 200
    body = r.json()
    symbols = [a["symbol"] for a in body]
    assert symbols == ["AAPL", "MSFT"]

    r_all = client.get("/api/assets/?active_only=false")
    assert r_all.status_code == 200
    assert {a["symbol"] for a in r_all.json()} == {"AAPL", "MSFT", "DEAD"}
