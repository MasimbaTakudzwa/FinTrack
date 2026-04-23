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

    monkeypatch.setattr(jobs, "fetch_prices", lambda symbols, **_kw: bars)

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

    monkeypatch.setattr(jobs, "fetch_prices", lambda symbols, **_kw: bars)

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

    monkeypatch.setattr(jobs, "fetch_prices", lambda symbols, **_kw: bars)

    inserted = jobs.ingest_prices()
    assert inserted == 2


def test_ingest_prices_with_no_assets(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sidecar.scheduler import jobs

    called = {"count": 0}

    def _fake_fetch(
        symbols: object, *, period: str = "1d", interval: str = "5m"
    ) -> list[PriceBar]:
        called["count"] += 1
        return []

    monkeypatch.setattr(jobs, "fetch_prices", _fake_fetch)

    inserted = jobs.ingest_prices()
    assert inserted == 0
    assert called["count"] == 0


def test_ingest_prices_for_symbols_forwards_period_and_interval(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The on-add backfill path passes ``period=60d, interval=5m``; the
    scheduled tick passes the defaults. Lock both in at this boundary so
    a future caller can't silently drop the kwargs.
    """
    _seed_assets()
    calls: list[tuple[str, str]] = []

    def _fake_fetch(
        symbols: object, *, period: str = "1d", interval: str = "5m"
    ) -> list[PriceBar]:
        calls.append((period, interval))
        return []

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_prices", _fake_fetch)

    jobs.ingest_prices_for_symbols(["AAPL"])  # default
    jobs.ingest_prices_for_symbols(["AAPL"], period="60d", interval="5m")
    jobs.ingest_prices_for_symbols(["AAPL"], period="1y", interval="1d")

    assert calls == [("1d", "5m"), ("60d", "5m"), ("1y", "1d")]
