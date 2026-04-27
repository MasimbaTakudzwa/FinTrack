"""Tests for the asset correlation engine.

Pure-compute paths (`_log_returns`, `_pearson`, `_aligned_returns`) get
unit tests with hand-built series. The DB-aware
``compute_correlation_matrix`` gets integration tests that seed
PricePoint rows + assert the matrix shape and key cells.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ml.correlation import (
    MIN_OVERLAP_DAYS,
    _aligned_returns,
    _log_returns,
    _pearson,
    compute_correlation_matrix,
)
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint

# ---------------------------------------------------------------------------
# Pure-compute helpers
# ---------------------------------------------------------------------------


def test_log_returns_drops_first_day() -> None:
    closes = [(date(2026, 4, 20), 100.0), (date(2026, 4, 21), 110.0)]
    returns = _log_returns(closes)
    # The first date has no prior close → no return.
    assert date(2026, 4, 20) not in returns
    assert date(2026, 4, 21) in returns
    # ln(110/100) ≈ 0.0953
    assert returns[date(2026, 4, 21)] == pytest.approx(math.log(1.1), abs=1e-9)


def test_log_returns_skips_non_positive_prices() -> None:
    """Negative or zero prices are nonsensical; we guard against div-by-zero
    or log(<=0) raising and instead drop those rows."""
    closes = [
        (date(2026, 4, 20), 100.0),
        (date(2026, 4, 21), 0.0),
        (date(2026, 4, 22), 110.0),
    ]
    returns = _log_returns(closes)
    # Day 21 has zero price → return is undefined → skipped.
    # Day 22's return is computed against day 21's zero price → also skipped.
    assert date(2026, 4, 21) not in returns
    assert date(2026, 4, 22) not in returns


def test_pearson_perfect_positive_correlation() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [2.0, 4.0, 6.0, 8.0]  # exact 2x scaling — perfectly correlated
    assert _pearson(xs, ys) == pytest.approx(1.0, abs=1e-9)


def test_pearson_perfect_negative_correlation() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [4.0, 3.0, 2.0, 1.0]
    assert _pearson(xs, ys) == pytest.approx(-1.0, abs=1e-9)


def test_pearson_zero_variance_returns_zero() -> None:
    """Constant series → variance is zero → r is undefined → we return 0."""
    xs = [5.0, 5.0, 5.0]
    ys = [1.0, 2.0, 3.0]
    assert _pearson(xs, ys) == 0.0


def test_pearson_too_short_returns_zero() -> None:
    assert _pearson([1.0], [2.0]) == 0.0
    assert _pearson([], []) == 0.0


def test_aligned_returns_intersects_dates() -> None:
    a = {date(2026, 4, 21): 0.1, date(2026, 4, 22): 0.2}
    b = {date(2026, 4, 22): 0.5, date(2026, 4, 23): -0.1}
    xs, ys = _aligned_returns(a, b)
    # Only April 22 is in both maps.
    assert xs == [0.2]
    assert ys == [0.5]


# ---------------------------------------------------------------------------
# DB-aware compute_correlation_matrix
# ---------------------------------------------------------------------------


def _seed_asset(symbol: str) -> int:
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


def _series(start: date, values: list[float]) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_compute_correlation_matrix_empty_inputs() -> None:
    matrix = compute_correlation_matrix([], lookback_days=90)
    assert matrix.symbols == []
    assert matrix.cells == []
    assert matrix.asset_count == 0


def test_compute_correlation_matrix_single_asset_diagonal_only(
    isolated_db: Path,
) -> None:
    aid = _seed_asset("AAPL")
    today = date.today()
    _seed_daily_closes(aid, _series(today - timedelta(days=10), [100.0, 101.0, 102.0]))

    matrix = compute_correlation_matrix(["AAPL"], lookback_days=90)
    assert matrix.symbols == ["AAPL"]
    # One cell (the diagonal) with coefficient 1.0.
    assert len(matrix.cells) == 1
    assert matrix.cells[0].symbol_a == "AAPL"
    assert matrix.cells[0].symbol_b == "AAPL"
    assert matrix.cells[0].coefficient == pytest.approx(1.0, abs=1e-9)


def test_compute_correlation_matrix_perfect_correlation(isolated_db: Path) -> None:
    """Two assets that move identically → r = 1.0 on the off-diagonal cell."""
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT")
    today = date.today()
    # Identical price movement, scaled differently.
    moves = [100.0, 102.0, 104.0, 103.0, 105.0, 107.0, 110.0]
    _seed_daily_closes(aapl, _series(today - timedelta(days=15), moves))
    _seed_daily_closes(
        msft,
        _series(today - timedelta(days=15), [m * 2 for m in moves]),
    )

    matrix = compute_correlation_matrix(["AAPL", "MSFT"], lookback_days=90)
    assert matrix.symbols == ["AAPL", "MSFT"]
    # 3 cells: (AAPL, AAPL) (AAPL, MSFT) (MSFT, MSFT)
    assert len(matrix.cells) == 3
    off_diagonal = next(
        c for c in matrix.cells if c.symbol_a != c.symbol_b
    )
    assert off_diagonal.coefficient == pytest.approx(1.0, abs=1e-6)


def test_compute_correlation_matrix_anti_correlated_pair(
    isolated_db: Path,
) -> None:
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT")
    today = date.today()
    aapl_moves = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 100.0]
    msft_moves = [100.0, 90.0, 100.0, 90.0, 100.0, 90.0, 100.0]  # mirror image
    _seed_daily_closes(aapl, _series(today - timedelta(days=15), aapl_moves))
    _seed_daily_closes(msft, _series(today - timedelta(days=15), msft_moves))

    matrix = compute_correlation_matrix(["AAPL", "MSFT"], lookback_days=90)
    off_diagonal = next(
        c for c in matrix.cells if c.symbol_a != c.symbol_b
    )
    assert off_diagonal.coefficient < 0  # anti-correlated


def test_compute_correlation_matrix_dedups_and_normalizes_symbols(
    isolated_db: Path,
) -> None:
    _seed_asset("AAPL")
    matrix = compute_correlation_matrix(
        [" aapl ", "AAPL", "Aapl"], lookback_days=90
    )
    # Whitespace trimmed, case-folded, deduped.
    assert matrix.symbols == ["AAPL"]


def test_compute_correlation_matrix_unknown_symbol_silently_dropped(
    isolated_db: Path,
) -> None:
    aapl = _seed_asset("AAPL")
    today = date.today()
    _seed_daily_closes(aapl, _series(today - timedelta(days=10), [100.0, 101.0, 102.0]))

    matrix = compute_correlation_matrix(["AAPL", "ZZZZ"], lookback_days=90)
    # ZZZZ doesn't exist → silently filtered. Only the diagonal remains.
    assert matrix.symbols == ["AAPL"]
    assert matrix.asset_count == 1


def test_compute_correlation_matrix_respects_lookback_window(
    isolated_db: Path,
) -> None:
    """Closes outside the lookback window aren't pulled into the returns."""
    aapl = _seed_asset("AAPL")
    today = date.today()
    # 200 days ago — outside any reasonable window.
    _seed_daily_closes(
        aapl,
        _series(today - timedelta(days=200), [100.0, 101.0, 102.0, 103.0]),
    )

    # 30-day lookback excludes everything → diagonal is still present
    # but the asset's overlap count is zero.
    matrix = compute_correlation_matrix(["AAPL"], lookback_days=30)
    diag = matrix.cells[0]
    assert diag.symbol_a == "AAPL"
    assert diag.overlap == 0


def test_min_overlap_days_constant_is_stable() -> None:
    """The API exposes this threshold so the UI doesn't hard-code it. Catches
    accidental drift if a future tuning pass changes the floor."""
    # Sanity bounds — anything below 5 is too noisy, anything above 60 would
    # silence half our seed assets on first install.
    assert 5 < MIN_OVERLAP_DAYS < 60
