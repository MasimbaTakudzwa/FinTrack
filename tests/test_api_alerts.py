from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.main import app
from sidecar.services import alerts as svc


def _seed_asset(symbol: str = "AAPL", name: str = "Apple Inc.") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=name, asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _add_price(asset_id: int, close: Decimal) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=datetime.now(UTC),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=0,
            )
        )


# ---------------------------------------------------------------------------
# list + create
# ---------------------------------------------------------------------------


def test_list_empty(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/alerts/")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "alerts": []}


def test_create_alert_201(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/alerts/",
            json={
                "asset_id": aid,
                "threshold": "150.00",
                "direction": "above",
                "note": "earnings",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["asset_id"] == aid
        assert body["symbol"] == "AAPL"
        assert Decimal(body["threshold"]) == Decimal("150.00")
        assert body["direction"] == "above"
        assert body["is_active"] is True
        assert body["triggered_at"] is None
        assert body["notified_at"] is None
        assert body["note"] == "earnings"
        assert body["last_price"] is None


def test_create_unknown_asset_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/alerts/",
            json={"asset_id": 9999, "threshold": "1", "direction": "above"},
        )
        assert resp.status_code == 404


def test_create_bad_direction_422(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/alerts/",
            json={"asset_id": aid, "threshold": "1", "direction": "sideways"},
        )
        assert resp.status_code == 422


def test_create_nonpositive_threshold_422(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/alerts/",
            json={"asset_id": aid, "threshold": "0", "direction": "above"},
        )
        assert resp.status_code == 422


def test_list_hydrates_last_price(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("123.45"))
    svc.create_alert(asset_id=aid, threshold=Decimal("100"), direction="above")
    with TestClient(app) as client:
        resp = client.get("/api/alerts/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert Decimal(body["alerts"][0]["last_price"]) == Decimal("123.45")


def test_list_filter_by_asset_id(isolated_db: Path) -> None:
    aid1 = _seed_asset("AAPL", "Apple")
    aid2 = _seed_asset("MSFT", "Microsoft")
    svc.create_alert(asset_id=aid1, threshold=1, direction="above")
    svc.create_alert(asset_id=aid2, threshold=2, direction="above")
    with TestClient(app) as client:
        resp = client.get("/api/alerts/", params={"asset_id": aid1})
        assert resp.status_code == 200
        alerts = resp.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["asset_id"] == aid1


def test_list_active_only(isolated_db: Path) -> None:
    aid = _seed_asset()
    on = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    off = svc.create_alert(asset_id=aid, threshold=2, direction="above")
    svc.update_alert(off.id, is_active=False)
    with TestClient(app) as client:
        resp = client.get("/api/alerts/", params={"active_only": "true"})
        assert resp.status_code == 200
        alerts = resp.json()["alerts"]
        assert [a["id"] for a in alerts] == [on.id]


# ---------------------------------------------------------------------------
# get / update / delete
# ---------------------------------------------------------------------------


def test_get_alert(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with TestClient(app) as client:
        resp = client.get(f"/api/alerts/{a.id}/")
        assert resp.status_code == 200
        assert resp.json()["id"] == a.id


def test_get_alert_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/alerts/9999/")
        assert resp.status_code == 404


def test_update_alert_fields(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with TestClient(app) as client:
        resp = client.put(
            f"/api/alerts/{a.id}/",
            json={"threshold": "42", "direction": "below", "is_active": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert Decimal(body["threshold"]) == Decimal("42")
        assert body["direction"] == "below"
        assert body["is_active"] is False


def test_update_alert_note_omitted_preserves(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(
        asset_id=aid, threshold=1, direction="above", note="keep me"
    )
    with TestClient(app) as client:
        resp = client.put(
            f"/api/alerts/{a.id}/", json={"threshold": "2"}
        )
        assert resp.status_code == 200
        assert resp.json()["note"] == "keep me"


def test_update_alert_note_explicit_null_clears(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(
        asset_id=aid, threshold=1, direction="above", note="gone soon"
    )
    with TestClient(app) as client:
        resp = client.put(f"/api/alerts/{a.id}/", json={"note": None})
        assert resp.status_code == 200
        assert resp.json()["note"] is None


def test_update_alert_reset_clears_timestamps(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("200"))
    a = svc.create_alert(
        asset_id=aid, threshold=Decimal("150"), direction="above"
    )
    svc.check_alerts()
    svc.mark_notified(a.id)
    with TestClient(app) as client:
        resp = client.put(f"/api/alerts/{a.id}/", json={"reset": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["triggered_at"] is None
        assert body["notified_at"] is None


def test_update_alert_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.put("/api/alerts/9999/", json={"threshold": "1"})
        assert resp.status_code == 404


def test_update_alert_unknown_field_422(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with TestClient(app) as client:
        resp = client.put(
            f"/api/alerts/{a.id}/", json={"asset_id": 9999}  # not editable
        )
        assert resp.status_code == 422


def test_delete_alert(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with TestClient(app) as client:
        resp = client.delete(f"/api/alerts/{a.id}/")
        assert resp.status_code == 204
        assert client.get(f"/api/alerts/{a.id}/").status_code == 404


def test_delete_alert_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.delete("/api/alerts/9999/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# pending notifications handshake
# ---------------------------------------------------------------------------


def test_pending_notifications_empty(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/alerts/pending-notifications/")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "alerts": []}


def test_pending_notifications_end_to_end(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("200"))
    a = svc.create_alert(asset_id=aid, threshold=Decimal("150"), direction="above")
    svc.check_alerts()

    with TestClient(app) as client:
        resp = client.get("/api/alerts/pending-notifications/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["alerts"][0]["id"] == a.id

        resp = client.post(f"/api/alerts/{a.id}/mark-notified/")
        assert resp.status_code == 200
        assert resp.json()["notified_at"] is not None

        resp = client.get("/api/alerts/pending-notifications/")
        assert resp.json()["count"] == 0


def test_mark_notified_not_triggered_400(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with TestClient(app) as client:
        resp = client.post(f"/api/alerts/{a.id}/mark-notified/")
        assert resp.status_code == 400


def test_mark_notified_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post("/api/alerts/9999/mark-notified/")
        assert resp.status_code == 404
