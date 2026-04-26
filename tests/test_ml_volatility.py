"""Tests for the volatility analytics layer.

Pure-compute paths (`_log_returns`, `_stdev`, `_ewma_volatility`) get
hand-built series. The DB-aware ``compute_volatility`` gets integration
tests that seed PricePoint rows + assert the report shape and metric
values.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ml.volatility import (
    MIN_RETURNS_FOR_VOL,
    RISKMETRICS_LAMBDA,
    TRADING_DAYS_PER_YEAR,
    _ewma_volatility,
    _log_returns,
    _stdev,
    compute_volatility,
)
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint

# ---------------------------------------------------------------------------
# Pure-compute helpers
# ---------------------------------------------------------------------------


def test_log_returns_drops_first_day() -> None:
    closes = [(date(2026, 4, 20), 100.0), (date(2026, 4, 21), 110.0)]
    returns = _log_returns(closes)
    assert len(returns) == 1
    assert returns[0] == pytest.approx(math.log(1.1), abs=1e-9)


def test_log_returns_skips_non_positive_prices() -> None:
    closes = [
        (date(2026, 4, 20), 100.0),
        (date(2026, 4, 21), 0.0),
        (date(2026, 4, 22), 110.0),
    ]
    # Day 21 has zero price → its return AND day 22's return (which would
    # divide by zero) are both filtered out.
    assert _log_returns(closes) == []


def test_log_returns_empty_input() -> None:
    assert _log_returns([]) == []
    assert _log_returns([(date(2026, 4, 20), 100.0)]) == []


def test_stdev_known_values() -> None:
    # Bessel-corrected stdev of [1, 2, 3] = sqrt(((1-2)^2 + (2-2)^2 + (3-2)^2) / 2)
    # = sqrt(2/2) = 1.0
    assert _stdev([1.0, 2.0, 3.0]) == pytest.approx(1.0, abs=1e-9)


def test_stdev_too_short_returns_zero() -> None:
    assert _stdev([]) == 0.0
    assert _stdev([1.0]) == 0.0


def test_stdev_constant_series_is_zero() -> None:
    """No variation → no volatility."""
    assert _stdev([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_ewma_volatility_too_short_returns_zero() -> None:
    """Below MIN_RETURNS_FOR_VOL the EWMA recursion is just sample noise."""
    short = [0.01, -0.02]
    assert _ewma_volatility(short) == 0.0


def test_ewma_volatility_picks_up_recent_burst() -> None:
    """lambda = 0.94 weights recent observations heavily — a shock at the end of
    the series should drag the forecast above the long-run sample stdev."""
    calm = [0.001] * 50  # tiny daily moves
    shocked = [*calm, 0.05, -0.05, 0.05, -0.05, 0.05]  # large recent r^2

    # The forecast for the shocked series should be meaningfully larger.
    calm_vol = _ewma_volatility(calm)
    shocked_vol = _ewma_volatility(shocked)
    assert shocked_vol > calm_vol * 2  # at least 2x — sanity floor


def test_ewma_volatility_matches_stdev_for_iid_constant_series() -> None:
    """For a constant-magnitude alternating series the EWMA vol should
    converge near the sample stdev (within a multiplicative factor of ~3
    given the seeding)."""
    # ±0.01 alternating — sample stdev ≈ 0.01
    series = [0.01, -0.01] * 30
    sample_stdev = _stdev(series)
    ewma_vol = _ewma_volatility(series)
    # EWMA seeded with sample variance → result should be in the same
    # order of magnitude as the sample stdev.
    assert sample_stdev * 0.3 < ewma_vol < sample_stdev * 3.0


def test_riskmetrics_lambda_is_stable() -> None:
    """Pinned to 0.94 (the JPMorgan RiskMetrics 1996 default). If anyone
    bumps this they should know they're breaking comparability with
    third-party tools."""
    assert RISKMETRICS_LAMBDA == 0.94


# ---------------------------------------------------------------------------
# DB-aware compute_volatility
# ---------------------------------------------------------------------------


def _seed_asset(symbol: str = "AAPL") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=symbol, asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _seed_daily_closes(asset_id: int, closes: list[tuple[date, float]]) -> None:
    with session_scope() as s:
        for d, price in closes:
            ts = datetime(d.year, d.month, d.day, tzinfo=UTC)
            s.add(
                PricePoint(
                    asset_id=asset_id,
                    timestamp=ts,
                    interval="1d",
                    open=Decimal(str(price)),
                    high=Decimal(str(price * 1.01)),
                    low=Decimal(str(price * 0.99)),
                    close=Decimal(str(price)),
                    volume=0,
                )
            )


def _walk(start: date, n_days: int, base: float, daily_pct: float) -> list[tuple[date, float]]:
    """Fixed-percent compounding walk — gives a clean, predictable returns
    series for the volatility math to work on."""
    out: list[tuple[date, float]] = []
    price = base
    for i in range(n_days):
        out.append((start + timedelta(days=i), price))
        price *= 1.0 + daily_pct
    return out


def test_compute_volatility_unknown_symbol_returns_empty(isolated_db: Path) -> None:
    report = compute_volatility("NOPE")
    assert report.symbol == "NOPE"
    assert report.realized_vol_daily is None
    assert report.realized_vol_annualized is None
    assert report.ewma_next_day_vol is None
    assert report.expected_move_low is None
    assert report.expected_move_high is None
    assert report.returns_used == 0


def test_compute_volatility_too_few_returns_returns_partial(isolated_db: Path) -> None:
    """Asset exists but has fewer than MIN_RETURNS_FOR_VOL daily bars in window
    → metrics are None, but last_close is still surfaced for the UI."""
    aid = _seed_asset()
    today = date.today()
    _seed_daily_closes(aid, [(today - timedelta(days=2), 100.0), (today - timedelta(days=1), 101.0)])

    report = compute_volatility("AAPL", lookback_days=30)
    assert report.returns_used == 1  # only one return possible from 2 closes
    assert report.last_close == pytest.approx(101.0)
    assert report.realized_vol_daily is None
    assert report.realized_vol_annualized is None
    assert report.expected_move_low is None


def test_compute_volatility_constant_walk_zero_vol(isolated_db: Path) -> None:
    """Zero daily move every day → realized vol = 0, EWMA vol = 0, expected
    move band collapses to a point at last_close."""
    aid = _seed_asset()
    today = date.today()
    _seed_daily_closes(
        aid, _walk(today - timedelta(days=20), 20, 100.0, 0.0)
    )

    report = compute_volatility("AAPL", lookback_days=30)
    assert report.returns_used >= MIN_RETURNS_FOR_VOL
    assert report.realized_vol_daily == pytest.approx(0.0, abs=1e-9)
    assert report.ewma_next_day_vol == pytest.approx(0.0, abs=1e-9)
    assert report.expected_move_low == pytest.approx(report.last_close, abs=1e-9)
    assert report.expected_move_high == pytest.approx(report.last_close, abs=1e-9)


def test_compute_volatility_steady_walk_known_realized_vol(isolated_db: Path) -> None:
    """A perfectly-constant +1% daily walk has zero stdev (every return is
    identical) — confirms the code path computes stdev rather than just
    abs() of the mean return.

    Tolerance is loose (1e-6 not 1e-9) because the closes round-trip
    through SQLite as Decimal(18,6) → float, which introduces ~1e-8
    noise per multiplication step. Over 20 compounding multiplies that
    accumulates into a stdev of a few x1e-9 — well below "real" vol but
    not exactly zero.
    """
    aid = _seed_asset()
    today = date.today()
    _seed_daily_closes(
        aid, _walk(today - timedelta(days=20), 20, 100.0, 0.01)
    )

    report = compute_volatility("AAPL", lookback_days=30)
    assert report.realized_vol_daily is not None
    assert report.realized_vol_daily < 1e-6


def test_compute_volatility_annualization_is_sqrt_252(isolated_db: Path) -> None:
    """Annualized vol = daily vol x sqrt(TRADING_DAYS_PER_YEAR). Validate
    the relationship without depending on the exact volatility number."""
    aid = _seed_asset()
    today = date.today()
    # Alternating ±1% — gives a non-zero, predictable stdev.
    closes: list[tuple[date, float]] = []
    price = 100.0
    for i in range(20):
        closes.append((today - timedelta(days=20 - i), price))
        price *= 1.01 if i % 2 == 0 else 0.99
    _seed_daily_closes(aid, closes)

    report = compute_volatility("AAPL", lookback_days=30)
    assert report.realized_vol_daily is not None
    assert report.realized_vol_annualized is not None
    assert report.realized_vol_annualized == pytest.approx(
        report.realized_vol_daily * math.sqrt(TRADING_DAYS_PER_YEAR),
        rel=1e-9,
    )


def test_compute_volatility_expected_move_band_centred_on_last_close(
    isolated_db: Path,
) -> None:
    """Band half-width = last_close x ewma_next_day_vol; low/high are
    symmetric around last_close."""
    aid = _seed_asset()
    today = date.today()
    closes: list[tuple[date, float]] = []
    price = 100.0
    for i in range(30):
        closes.append((today - timedelta(days=30 - i), price))
        price *= 1.02 if i % 2 == 0 else 0.98
    _seed_daily_closes(aid, closes)

    report = compute_volatility("AAPL", lookback_days=30)
    assert report.last_close is not None
    assert report.expected_move_low is not None
    assert report.expected_move_high is not None
    midpoint = (report.expected_move_low + report.expected_move_high) / 2
    assert midpoint == pytest.approx(report.last_close, abs=1e-9)
    half_width = (report.expected_move_high - report.expected_move_low) / 2
    assert report.ewma_next_day_vol is not None
    assert half_width == pytest.approx(
        report.last_close * report.ewma_next_day_vol, rel=1e-9
    )


def test_compute_volatility_lookback_clips_window(isolated_db: Path) -> None:
    """Closes outside the lookback window aren't pulled in. With only
    pre-window data we should get the empty-returns branch."""
    aid = _seed_asset()
    today = date.today()
    # All 200+ days back — outside any reasonable lookback.
    _seed_daily_closes(
        aid,
        _walk(today - timedelta(days=300), 30, 100.0, 0.01),
    )

    report = compute_volatility("AAPL", lookback_days=30)
    assert report.returns_used == 0
    assert report.realized_vol_daily is None
