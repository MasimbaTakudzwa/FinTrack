from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.ingestion.yfinance_fetcher import PriceBar


def _seed_assets() -> None:
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK))
        s.add(
            Asset(symbol="MSFT", name="Microsoft Corporation", asset_type=AssetType.STOCK)
        )


def _make_bars(symbol: str, n: int, base: datetime) -> list[PriceBar]:
    return [
        PriceBar(
            symbol=symbol,
            timestamp=base + timedelta(minutes=5 * i),
            open=Decimal("100.00"),
            high=Decimal("101.50"),
            low=Decimal("99.50"),
            close=Decimal("100.75") + Decimal(i),
            volume=2_000 * (i + 1),
        )
        for i in range(n)
    ]


def test_ingest_prices_inserts_bars(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    base = datetime(2026, 4, 22, 13, 0, tzinfo=UTC)
    bars = _make_bars("AAPL", 3, base) + _make_bars("MSFT", 2, base)

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_prices", lambda symbols: bars)

    inserted = jobs.ingest_prices()
    assert inserted == 5

    with session_scope() as s:
        rows = s.execute(select(PricePoint)).scalars().all()
        assert len(rows) == 5


def test_ingest_prices_is_idempotent(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    base = datetime(2026, 4, 22, 13, 0, tzinfo=UTC)
    bars = _make_bars("AAPL", 3, base)

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_prices", lambda symbols: bars)

    first = jobs.ingest_prices()
    second = jobs.ingest_prices()
    assert first == 3
    assert second == 0

    with session_scope() as s:
        rows = s.execute(select(PricePoint)).scalars().all()
        assert len(rows) == 3


def test_ingest_prices_skips_unknown_symbols(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    base = datetime(2026, 4, 22, 13, 0, tzinfo=UTC)
    bars = _make_bars("AAPL", 2, base) + _make_bars("GHOST", 2, base)

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_prices", lambda symbols: bars)

    inserted = jobs.ingest_prices()
    assert inserted == 2


def test_ingest_prices_with_no_assets(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sidecar.scheduler import jobs

    called = {"count": 0}

    def _fake_fetch(symbols: object) -> list[PriceBar]:
        called["count"] += 1
        return []

    monkeypatch.setattr(jobs, "fetch_prices", _fake_fetch)

    inserted = jobs.ingest_prices()
    assert inserted == 0
    assert called["count"] == 0
