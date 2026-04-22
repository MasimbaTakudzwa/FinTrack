from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType
from sidecar.main import app
from sidecar.services import assets as assets_service
from sidecar.services.assets import ResolvedSymbol


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


# ---------------------------------------------------------------------------
# POST /api/assets/lookup/
# ---------------------------------------------------------------------------


def _patch_resolve(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolved: ResolvedSymbol | None = None,
    exc: BaseException | None = None,
) -> None:
    """Replace resolve_symbol + add_asset with canned responses."""

    def _resolve(raw: str) -> ResolvedSymbol:
        if exc is not None:
            raise exc
        assert resolved is not None
        return resolved

    # Patch in BOTH the service module AND the api module — the api module
    # imports the name at import time, so a service-only patch is bypassed.
    monkeypatch.setattr(assets_service, "resolve_symbol", _resolve)
    import sidecar.api.assets as api_assets

    monkeypatch.setattr(api_assets, "resolve_symbol", _resolve)


def test_lookup_asset_returns_preview(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolve(
        monkeypatch,
        resolved=ResolvedSymbol(
            symbol="PLTR",
            name="Palantir Technologies",
            asset_type=AssetType.STOCK,
            exchange="NYQ",
            currency="USD",
        ),
    )
    client = TestClient(app)
    r = client.post("/api/assets/lookup/", json={"symbol": "pltr"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "PLTR"
    assert body["name"] == "Palantir Technologies"
    assert body["asset_type"] == "stock"
    assert body["exchange"] == "NYQ"
    assert body["currency"] == "USD"


def test_lookup_asset_unknown_symbol_404(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sidecar.services.assets import SymbolNotFoundError

    _patch_resolve(monkeypatch, exc=SymbolNotFoundError("nope"))
    client = TestClient(app)
    r = client.post("/api/assets/lookup/", json={"symbol": "FAKEX"})
    assert r.status_code == 404


def test_lookup_asset_validation_422(isolated_db: Path) -> None:
    client = TestClient(app)
    # empty symbol — Field(min_length=1) → 422
    r = client.post("/api/assets/lookup/", json={"symbol": ""})
    assert r.status_code == 422


def test_lookup_asset_does_not_persist(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolve(
        monkeypatch,
        resolved=ResolvedSymbol(
            symbol="NEW",
            name="Newco",
            asset_type=AssetType.STOCK,
            exchange=None,
            currency=None,
        ),
    )
    client = TestClient(app)
    r = client.post("/api/assets/lookup/", json={"symbol": "NEW"})
    assert r.status_code == 200

    from sqlalchemy import select

    with session_scope() as s:
        count = s.execute(
            select(Asset).where(Asset.symbol == "NEW")
        ).scalar_one_or_none()
        assert count is None


# ---------------------------------------------------------------------------
# POST /api/assets/
# ---------------------------------------------------------------------------


def _patch_add_asset(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fast: dict[str, Any] | None = None,
    info: dict[str, Any] | None = None,
    bars: int = 0,
) -> None:
    """Install a fake yfinance Ticker + stub ingest so add_asset runs end-to-end."""

    class _FI:
        def __init__(self, d: dict[str, Any]) -> None:
            self._d = d

        def __getattr__(self, name: str) -> Any:
            if name in self._d:
                return self._d[name]
            raise AttributeError(name)

        def get(self, name: str, default: Any = None) -> Any:
            return self._d.get(name, default)

    class _T:
        def __init__(self, sym: str) -> None:
            self.ticker = sym

        @property
        def fast_info(self) -> _FI:
            return _FI(fast or {})

        @property
        def info(self) -> dict[str, Any]:
            return info or {}

    monkeypatch.setattr(assets_service.yf, "Ticker", _T)

    import sidecar.scheduler.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "ingest_prices_for_symbols", lambda symbols: bars
    )


def test_create_asset_persists_and_adds_to_default_watchlist(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed a default watchlist
    from sidecar.services.watchlists import create_watchlist

    create_watchlist("Default", is_default=True)

    _patch_add_asset(
        monkeypatch,
        fast={"quote_type": "EQUITY"},
        info={"longName": "Palantir Technologies"},
        bars=42,
    )

    client = TestClient(app)
    r = client.post("/api/assets/", json={"symbol": "pltr"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["asset"]["symbol"] == "PLTR"
    assert body["asset"]["name"] == "Palantir Technologies"
    assert body["asset"]["asset_type"] == "stock"
    assert body["asset"]["is_active"] is True
    assert body["bars_ingested"] == 42
    assert body["added_to_watchlist"] is True

    from sqlalchemy import select

    from sidecar.db.models import WatchlistItem

    with session_scope() as s:
        items = s.execute(select(WatchlistItem)).scalars().all()
        assert len(items) == 1


def test_create_asset_skip_watchlist(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sidecar.services.watchlists import create_watchlist

    create_watchlist("Default", is_default=True)

    _patch_add_asset(
        monkeypatch,
        fast={"quote_type": "ETF"},
        info={"longName": "iShares Gold"},
    )
    client = TestClient(app)
    r = client.post(
        "/api/assets/",
        json={"symbol": "IAU", "add_to_default_watchlist": False},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["added_to_watchlist"] is False

    from sqlalchemy import select

    from sidecar.db.models import WatchlistItem

    with session_scope() as s:
        items = s.execute(select(WatchlistItem)).scalars().all()
        assert items == []


def test_create_asset_no_default_watchlist_is_non_fatal(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No default watchlist exists — asset should still be created."""
    _patch_add_asset(
        monkeypatch,
        fast={"quote_type": "EQUITY"},
        info={"longName": "Tesla"},
    )
    client = TestClient(app)
    r = client.post("/api/assets/", json={"symbol": "TSLA"})
    assert r.status_code == 201
    body = r.json()
    assert body["asset"]["symbol"] == "TSLA"
    assert body["added_to_watchlist"] is False


def test_create_asset_unknown_symbol_404(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stub fast_info + info as empty; also stub fetch_prices fallback → empty.
    _patch_add_asset(monkeypatch, fast={}, info={})
    monkeypatch.setattr(assets_service, "fetch_prices", lambda *a, **kw: [])

    client = TestClient(app)
    r = client.post("/api/assets/", json={"symbol": "ZZZFAKE"})
    assert r.status_code == 404


def test_create_asset_duplicate_409(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK))

    _patch_add_asset(
        monkeypatch,
        fast={"quote_type": "EQUITY"},
        info={"longName": "Apple Inc."},
    )
    client = TestClient(app)
    r = client.post("/api/assets/", json={"symbol": "aapl"})
    assert r.status_code == 409


def test_create_asset_validation_422(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.post("/api/assets/", json={"symbol": ""})
    assert r.status_code == 422


def test_create_asset_ingest_failure_returns_zero_bars(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ingest errors inside add_asset must not fail the request."""
    class _FI:
        def __init__(self, d: dict[str, Any]) -> None:
            self._d = d

        def __getattr__(self, name: str) -> Any:
            if name in self._d:
                return self._d[name]
            raise AttributeError(name)

        def get(self, name: str, default: Any = None) -> Any:
            return self._d.get(name, default)

    class _T:
        def __init__(self, sym: str) -> None:
            self.ticker = sym

        @property
        def fast_info(self) -> _FI:
            return _FI({"quote_type": "EQUITY"})

        @property
        def info(self) -> dict[str, Any]:
            return {"longName": "Thing"}

    monkeypatch.setattr(assets_service.yf, "Ticker", _T)

    import sidecar.scheduler.jobs as jobs_module

    def _boom(symbols: list[str]) -> int:
        raise RuntimeError("net fail")

    monkeypatch.setattr(jobs_module, "ingest_prices_for_symbols", _boom)

    client = TestClient(app)
    r = client.post("/api/assets/", json={"symbol": "THING"})
    assert r.status_code == 201
    assert r.json()["bars_ingested"] == 0
