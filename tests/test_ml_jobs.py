"""End-to-end tests for `ml.jobs` — training orchestration.

These exercise the full pipeline against synthetic daily closes seeded into
SQLite. The SARIMAX fit runs for real (~0.5 s per asset), which is slow
enough that we only cover the essential paths here; finer-grained forecast
mechanics live in `test_ml_forecast.py` and persistence mechanics live in
`test_ml_persistence.py`.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ml.forecast import InsufficientDataError
from ml.jobs import (
    DEFAULT_HORIZON_DAYS,
    UnknownSymbolError,
    refresh_stale_forecasts,
    symbols_eligible_for_forecast,
    train_forecasts,
    train_one,
)
from ml.persistence import load_forecast
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint


def _seed_asset_with_daily_closes(
    symbol: str,
    *,
    n_rows: int,
    is_active: bool = True,
    start: date = date(2024, 1, 1),
) -> int:
    """Create an asset + `n_rows` daily closes (interval='1d'), return asset id."""
    with session_scope() as s:
        asset = Asset(
            symbol=symbol, name=symbol, asset_type=AssetType.STOCK, is_active=is_active
        )
        s.add(asset)
        s.flush()
        bars = []
        for i in range(n_rows):
            price = 100.0 + 0.2 * i + 1.5 * math.sin(i / 5.0)
            ts = datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(
                days=i
            )
            bars.append(
                PricePoint(
                    asset_id=asset.id,
                    timestamp=ts,
                    interval="1d",
                    open=Decimal(str(price)),
                    high=Decimal(str(price * 1.01)),
                    low=Decimal(str(price * 0.99)),
                    close=Decimal(str(price)),
                    volume=1_000_000,
                )
            )
        s.add_all(bars)
        return asset.id


def test_train_one_happy_path(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=120)

    result = train_one("AAPL", horizon_days=14)

    assert result.horizon_days == 14
    assert result.training_rows == 120
    assert len(result.points) == 14
    # Persisted — the API layer reads this table, not the returned object.
    persisted = load_forecast(
        # Look up asset_id via symbol, since train_one doesn't expose it.
        _resolve_asset_id("AAPL")
    )
    assert persisted is not None
    assert persisted.horizon_days == 14
    assert len(persisted.points) == 14


def test_train_one_case_insensitive_symbol(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    # Lowercase input — should resolve to AAPL.
    result = train_one("aapl")
    assert result.training_rows == 80


def test_train_one_unknown_symbol_raises(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    with pytest.raises(UnknownSymbolError):
        train_one("NOPE")


def test_train_one_insufficient_data_raises(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=30)  # < MIN_TRAINING_ROWS=60
    with pytest.raises(InsufficientDataError):
        train_one("AAPL")


def test_train_one_uses_default_horizon_when_omitted(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=100)
    result = train_one("AAPL")
    assert result.horizon_days == DEFAULT_HORIZON_DAYS


def test_train_forecasts_fits_every_active_asset(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    _seed_asset_with_daily_closes("MSFT", n_rows=80)

    successes = train_forecasts()
    assert successes == 2

    assert load_forecast(_resolve_asset_id("AAPL")) is not None
    assert load_forecast(_resolve_asset_id("MSFT")) is not None


def test_train_forecasts_skips_inactive_assets(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    _seed_asset_with_daily_closes("OLD", n_rows=80, is_active=False)

    successes = train_forecasts()
    assert successes == 1

    assert load_forecast(_resolve_asset_id("AAPL")) is not None
    assert load_forecast(_resolve_asset_id("OLD")) is None


def test_train_forecasts_swallows_insufficient_data(isolated_db: Path) -> None:
    """A per-asset InsufficientDataError must NOT abort the whole batch.

    Newly-added assets are the most common trigger — their 5y backfill is
    still mid-flight when the weekly cron fires. Skipping them and moving on
    is the right call.
    """
    _seed_asset_with_daily_closes("AAPL", n_rows=80)  # OK
    _seed_asset_with_daily_closes("NEW", n_rows=10)  # too few rows

    successes = train_forecasts()
    assert successes == 1

    assert load_forecast(_resolve_asset_id("AAPL")) is not None
    assert load_forecast(_resolve_asset_id("NEW")) is None


def test_train_forecasts_no_assets_returns_zero(isolated_db: Path) -> None:
    # No assets seeded at all.
    assert train_forecasts() == 0


def test_train_forecasts_ignores_5m_bars(isolated_db: Path) -> None:
    """Forecasting runs off daily closes only — 5-minute bars live in the same
    table but must be filtered out by the loader."""
    # Seed enough 1d bars to fit, plus noise 5m bars that would confuse things.
    asset_id = _seed_asset_with_daily_closes("AAPL", n_rows=80)

    with session_scope() as s:
        # A handful of 5m bars at the same asset but with interval='5m'.
        for i in range(20):
            s.add(
                PricePoint(
                    asset_id=asset_id,
                    timestamp=datetime(2024, 6, 1, 9, 30, tzinfo=UTC)
                    + timedelta(minutes=5 * i),
                    interval="5m",
                    open=Decimal("500.0"),
                    high=Decimal("500.0"),
                    low=Decimal("500.0"),
                    close=Decimal("500.0"),
                    volume=0,
                )
            )

    result = train_one("AAPL")
    # 80 1d bars in; 5m bars must not leak into training.
    assert result.training_rows == 80


def test_symbols_eligible_for_forecast(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=10)
    _seed_asset_with_daily_closes("MSFT", n_rows=10)

    # Asset with only 5m bars — must not be eligible.
    with session_scope() as s:
        asset = Asset(symbol="INTRA", name="Intra", asset_type=AssetType.STOCK)
        s.add(asset)
        s.flush()
        s.add(
            PricePoint(
                asset_id=asset.id,
                timestamp=datetime(2024, 6, 1, 9, 30, tzinfo=UTC),
                interval="5m",
                open=Decimal("1.0"),
                high=Decimal("1.0"),
                low=Decimal("1.0"),
                close=Decimal("1.0"),
                volume=0,
            )
        )

    eligible = list(symbols_eligible_for_forecast())
    assert "AAPL" in eligible
    assert "MSFT" in eligible
    assert "INTRA" not in eligible


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_asset_id(symbol: str) -> int:
    from sqlalchemy import select

    with session_scope() as s:
        aid = s.execute(
            select(Asset.id).where(Asset.symbol == symbol)
        ).scalar_one()
        return int(aid)


def _add_daily_bar(asset_id: int, d: date, close: float = 200.0) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
                interval="1d",
                open=Decimal(str(close)),
                high=Decimal(str(close)),
                low=Decimal(str(close)),
                close=Decimal(str(close)),
                volume=1_000_000,
            )
        )


def test_refresh_trains_when_no_forecast(isolated_db: Path) -> None:
    aid = _seed_asset_with_daily_closes("AAPL", n_rows=120)
    assert load_forecast(aid) is None

    retrained = refresh_stale_forecasts()
    assert retrained == 1
    fc = load_forecast(aid)
    assert fc is not None
    # Anchored to the newest daily bar (120 days from 2024-01-01).
    assert fc.last_close_date == date(2024, 1, 1) + timedelta(days=119)


def test_refresh_skips_up_to_date(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=120)
    assert refresh_stale_forecasts() == 1  # first pass trains
    # Second pass: nothing changed → no retrain.
    assert refresh_stale_forecasts() == 0


def test_refresh_retrains_when_a_new_bar_lands(isolated_db: Path) -> None:
    aid = _seed_asset_with_daily_closes("AAPL", n_rows=120)
    refresh_stale_forecasts()
    before = load_forecast(aid)
    assert before is not None

    # A new daily bar arrives the next day → forecast is now stale.
    _add_daily_bar(aid, before.last_close_date + timedelta(days=1))
    assert refresh_stale_forecasts() == 1
    after = load_forecast(aid)
    assert after is not None
    assert after.last_close_date > before.last_close_date
