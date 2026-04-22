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
        s.add(Asset(symbol="BTC-USD", name="Bitcoin", asset_type=AssetType.CRYPTO))
        s.add(Asset(symbol="ETH-USD", name="Ethereum", asset_type=AssetType.CRYPTO))
        s.add(Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK))


def _make_bars(symbol: str, n: int, base: datetime) -> list[PriceBar]:
    return [
        PriceBar(
            symbol=symbol,
            timestamp=base + timedelta(minutes=30 * i),
            open=Decimal("60000"),
            high=Decimal("60500"),
            low=Decimal("59800"),
            close=Decimal("60200") + Decimal(i),
            volume=0,
        )
        for i in range(n)
    ]


def test_ingest_crypto_inserts_only_crypto(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    base = datetime(2026, 4, 22, 13, 0, tzinfo=UTC)
    bars = _make_bars("BTC-USD", 2, base) + _make_bars("ETH-USD", 2, base)

    from sidecar.scheduler import jobs

    captured: dict[str, list[str]] = {}

    def fake_fetch_crypto(symbols: list[str]) -> list[PriceBar]:
        captured["symbols"] = list(symbols)
        return bars

    monkeypatch.setattr(jobs, "fetch_crypto_prices", fake_fetch_crypto)

    inserted = jobs.ingest_crypto()
    assert inserted == 4
    assert set(captured["symbols"]) == {"BTC-USD", "ETH-USD"}
    assert "AAPL" not in captured["symbols"]

    with session_scope() as s:
        count = len(s.execute(select(PricePoint)).scalars().all())
        assert count == 4


def test_ingest_crypto_is_idempotent(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    base = datetime(2026, 4, 22, 13, 0, tzinfo=UTC)
    bars = _make_bars("BTC-USD", 3, base)

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_crypto_prices", lambda symbols: bars)

    first = jobs.ingest_crypto()
    second = jobs.ingest_crypto()
    assert first == 3
    assert second == 0


def test_ingest_crypto_with_no_crypto_assets(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple", asset_type=AssetType.STOCK))

    from sidecar.scheduler import jobs

    called = {"n": 0}

    def fake_fetch(symbols: list[str]) -> list[PriceBar]:
        called["n"] += 1
        return []

    monkeypatch.setattr(jobs, "fetch_crypto_prices", fake_fetch)

    assert jobs.ingest_crypto() == 0
    assert called["n"] == 0
