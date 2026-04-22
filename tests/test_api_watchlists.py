from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType
from sidecar.main import app


def _seed_assets() -> dict[str, int]:
    with session_scope() as s:
        a1 = Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK)
        a2 = Asset(
            symbol="MSFT", name="Microsoft Corporation", asset_type=AssetType.STOCK
        )
        a3 = Asset(symbol="SPY", name="SPDR S&P 500", asset_type=AssetType.ETF)
        s.add_all([a1, a2, a3])
        s.flush()
        return {a.symbol: a.id for a in (a1, a2, a3)}


def test_list_empty(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/watchlists/")
        assert resp.status_code == 200
        assert resp.json() == {"watchlists": []}


def test_create_and_list_watchlist(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/watchlists/", json={"name": "Tech", "is_default": True}
        )
        assert resp.status_code == 201
        wl = resp.json()
        assert wl["name"] == "Tech"
        assert wl["is_default"] is True
        assert wl["item_count"] == 0

        resp = client.get("/api/watchlists/")
        assert resp.status_code == 200
        assert len(resp.json()["watchlists"]) == 1


def test_create_duplicate_name_409(isolated_db: Path) -> None:
    with TestClient(app) as client:
        client.post("/api/watchlists/", json={"name": "Tech"})
        resp = client.post("/api/watchlists/", json={"name": "Tech"})
        assert resp.status_code == 409


def test_create_blank_name_422(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post("/api/watchlists/", json={"name": ""})
        assert resp.status_code == 422  # pydantic min_length


def test_default_watchlist_missing_returns_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/watchlists/default/")
        assert resp.status_code == 404


def test_default_watchlist_shortcut(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post(
            "/api/watchlists/", json={"name": "Primary", "is_default": True}
        ).json()
        client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids["AAPL"]}
        )

        resp = client.get("/api/watchlists/default/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Primary"
        assert body["is_default"] is True
        assert len(body["items"]) == 1
        assert body["items"][0]["symbol"] == "AAPL"
        assert body["items"][0]["position"] == 0


def test_get_watchlist_detail(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids["AAPL"]}
        )
        client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids["MSFT"]}
        )

        resp = client.get(f"/api/watchlists/{wl['id']}/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Tech"
        symbols = [i["symbol"] for i in body["items"]]
        assert symbols == ["AAPL", "MSFT"]


def test_get_unknown_watchlist_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/watchlists/9999/")
        assert resp.status_code == 404


def test_rename_watchlist(isolated_db: Path) -> None:
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Old"}).json()
        resp = client.put(
            f"/api/watchlists/{wl['id']}/", json={"name": "New"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"


def test_rename_to_existing_409(isolated_db: Path) -> None:
    with TestClient(app) as client:
        a = client.post("/api/watchlists/", json={"name": "A"}).json()
        client.post("/api/watchlists/", json={"name": "B"})
        resp = client.put(
            f"/api/watchlists/{a['id']}/", json={"name": "B"}
        )
        assert resp.status_code == 409


def test_set_default_via_put(isolated_db: Path) -> None:
    with TestClient(app) as client:
        a = client.post(
            "/api/watchlists/", json={"name": "A", "is_default": True}
        ).json()
        b = client.post("/api/watchlists/", json={"name": "B"}).json()

        resp = client.put(
            f"/api/watchlists/{b['id']}/", json={"is_default": True}
        )
        assert resp.status_code == 200
        assert resp.json()["is_default"] is True

        # a is no longer default.
        a_after = client.get(f"/api/watchlists/{a['id']}/").json()
        assert a_after["is_default"] is False


def test_put_un_default_rejected(isolated_db: Path) -> None:
    with TestClient(app) as client:
        a = client.post(
            "/api/watchlists/", json={"name": "A", "is_default": True}
        ).json()
        resp = client.put(
            f"/api/watchlists/{a['id']}/", json={"is_default": False}
        )
        assert resp.status_code == 400


def test_put_empty_body_400(isolated_db: Path) -> None:
    with TestClient(app) as client:
        a = client.post("/api/watchlists/", json={"name": "A"}).json()
        resp = client.put(f"/api/watchlists/{a['id']}/", json={})
        assert resp.status_code == 400


def test_delete_watchlist(isolated_db: Path) -> None:
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Disposable"}).json()
        resp = client.delete(f"/api/watchlists/{wl['id']}/")
        assert resp.status_code == 204
        assert (
            client.get(f"/api/watchlists/{wl['id']}/").status_code == 404
        )


def test_delete_default_400(isolated_db: Path) -> None:
    with TestClient(app) as client:
        wl = client.post(
            "/api/watchlists/", json={"name": "Keep", "is_default": True}
        ).json()
        resp = client.delete(f"/api/watchlists/{wl['id']}/")
        assert resp.status_code == 400


def test_add_item(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        resp = client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids["AAPL"]}
        )
        assert resp.status_code == 201
        item = resp.json()
        assert item["symbol"] == "AAPL"
        assert item["position"] == 0


def test_add_item_unknown_asset_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        resp = client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": 9999}
        )
        assert resp.status_code == 404


def test_add_item_duplicate_409(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids["AAPL"]}
        )
        resp = client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids["AAPL"]}
        )
        assert resp.status_code == 409


def test_remove_item_redensifies(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        for sym in ("AAPL", "MSFT", "SPY"):
            client.post(
                f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids[sym]}
            )
        resp = client.delete(
            f"/api/watchlists/{wl['id']}/items/{ids['MSFT']}/"
        )
        assert resp.status_code == 204

        detail = client.get(f"/api/watchlists/{wl['id']}/").json()
        positions = [(i["symbol"], i["position"]) for i in detail["items"]]
        assert positions == [("AAPL", 0), ("SPY", 1)]


def test_remove_item_not_on_list_404(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        resp = client.delete(
            f"/api/watchlists/{wl['id']}/items/{ids['AAPL']}/"
        )
        assert resp.status_code == 404


def test_reorder_items(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        for sym in ("AAPL", "MSFT", "SPY"):
            client.post(
                f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids[sym]}
            )
        resp = client.put(
            f"/api/watchlists/{wl['id']}/items/reorder",
            json={"asset_ids": [ids["SPY"], ids["AAPL"], ids["MSFT"]]},
        )
        assert resp.status_code == 204

        detail = client.get(f"/api/watchlists/{wl['id']}/").json()
        assert [i["symbol"] for i in detail["items"]] == ["SPY", "AAPL", "MSFT"]


def test_reorder_mismatched_400(isolated_db: Path) -> None:
    ids = _seed_assets()
    with TestClient(app) as client:
        wl = client.post("/api/watchlists/", json={"name": "Tech"}).json()
        client.post(
            f"/api/watchlists/{wl['id']}/items/", json={"asset_id": ids["AAPL"]}
        )
        # Missing MSFT on the list, extra id provided.
        resp = client.put(
            f"/api/watchlists/{wl['id']}/items/reorder",
            json={"asset_ids": [ids["AAPL"], ids["MSFT"]]},
        )
        assert resp.status_code == 400
