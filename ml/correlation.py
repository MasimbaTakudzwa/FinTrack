"""Pairwise correlation analytics for tracked assets.

Surfaces "which assets move together?" — the foundational diversification
question for any market-intelligence dashboard. Uses Pearson correlation
on daily log-returns over a configurable lookback window.

Why log-returns and not raw closes:
- Closes are non-stationary (price level drifts over time); naive
  correlation on raw closes would over-emphasise long-run trend
  similarity rather than co-movement.
- Daily log-returns are roughly stationary and are the standard input
  for portfolio-theory calculations (correlation, covariance, beta).
- Log-returns are time-additive — small numerical advantage over
  arithmetic returns, identical correlation result for short windows.

Pure-compute boundary: this module reads ``price_points`` via SQLAlchemy
but doesn't import statsmodels. Just numpy + standard math, so a sidecar
without ``requirements-ml.txt`` could in principle still serve
correlation analytics — but we keep this in ``ml/`` because it's part of
the same analytical-features story as forecasting and sentiment.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, PricePoint

logger = logging.getLogger(__name__)


# Lower bound for a meaningful correlation. Below this, sample noise
# dominates the estimate; the API surfaces the count alongside the
# coefficient so the UI can grey out under-sampled cells.
MIN_OVERLAP_DAYS = 30


@dataclass(frozen=True)
class CorrelationCell:
    """One cell in the correlation matrix.

    ``coefficient`` is the Pearson r over log-returns (range [-1, +1]).
    ``overlap`` is the count of trading days both series had data on —
    the API consumer uses this to fade out sparsely-sampled cells.
    """

    symbol_a: str
    symbol_b: str
    coefficient: float
    overlap: int


@dataclass(frozen=True)
class CorrelationMatrix:
    """Square correlation matrix for a list of assets over a lookback window.

    ``symbols`` is the row/column ordering (sorted alphabetically); the
    UI renders the heatmap in that order. ``cells`` is the upper-triangle
    plus the diagonal — symmetric pairs (A,B) / (B,A) are not duplicated.
    """

    symbols: list[str]
    lookback_days: int
    cells: list[CorrelationCell]
    asset_count: int


# ---------------------------------------------------------------------------
# Pure-compute helpers
# ---------------------------------------------------------------------------


def _log_returns(closes: list[tuple[date, float]]) -> dict[date, float]:
    """Convert a sorted ``(date, close)`` list into a date → log-return map.

    The first date has no return (skipped). Returns are calendar-day-
    indexed; if there's a weekend gap the next trading day's return is
    against the prior Friday's close, which matches how backtesting
    libraries handle it.
    """
    out: dict[date, float] = {}
    if len(closes) < 2:
        return out
    for i in range(1, len(closes)):
        d, price = closes[i]
        prev_price = closes[i - 1][1]
        if prev_price <= 0 or price <= 0:
            continue
        out[d] = math.log(price / prev_price)
    return out


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation coefficient. Returns 0.0 when either series has
    zero variance (avoids NaN; the caller's UI treats 0 as "no signal")."""
    n = len(xs)
    if n != len(ys) or n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = 0.0
    var_x = 0.0
    var_y = 0.0
    for x, y in zip(xs, ys, strict=True):
        dx = x - mean_x
        dy = y - mean_y
        cov += dx * dy
        var_x += dx * dx
        var_y += dy * dy
    if var_x == 0 or var_y == 0:
        return 0.0
    denom = math.sqrt(var_x * var_y)
    return cov / denom


def _aligned_returns(
    a: dict[date, float], b: dict[date, float]
) -> tuple[list[float], list[float]]:
    """Return the two return-series restricted to dates that appear in both."""
    common = sorted(set(a.keys()) & set(b.keys()))
    return [a[d] for d in common], [b[d] for d in common]


# ---------------------------------------------------------------------------
# DB-aware orchestration
# ---------------------------------------------------------------------------


def _load_daily_closes(
    session: Session, asset_id: int, *, since: date
) -> list[tuple[date, float]]:
    """Pull daily closes for an asset since ``since``, sorted by date."""
    rows = session.execute(
        select(PricePoint.timestamp, PricePoint.close)
        .where(
            PricePoint.asset_id == asset_id,
            PricePoint.interval == "1d",
            PricePoint.timestamp >= datetime.combine(since, datetime.min.time(), UTC),
        )
        .order_by(PricePoint.timestamp.asc())
    ).all()
    # Dedup on date (multiple intraday writes shouldn't happen for daily
    # bars but the unique constraint is defensive — last write wins).
    by_date: dict[date, float] = {}
    for ts, close in rows:
        by_date[ts.date()] = float(close)
    return sorted(by_date.items())


def compute_correlation_matrix(
    symbols: Sequence[str],
    *,
    lookback_days: int = 90,
) -> CorrelationMatrix:
    """Compute pairwise correlations on daily log-returns for ``symbols``.

    ``lookback_days`` is the calendar window — the actual sample size
    per pair depends on how many trading days both assets had data on
    inside the window. Cells with fewer than ``MIN_OVERLAP_DAYS``
    overlapping returns are still emitted (the UI greys them out)
    rather than dropped, so the heatmap stays a square matrix.

    Self-correlations (diagonal) are 1.0 by convention. Symmetric pairs
    are emitted only once (upper triangle including diagonal); the UI
    mirrors when rendering the lower triangle.
    """
    sym_set = sorted({s.strip().upper() for s in symbols if s.strip()})
    if not sym_set:
        return CorrelationMatrix(
            symbols=[], lookback_days=lookback_days, cells=[], asset_count=0
        )

    cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).date()

    # Resolve symbols → asset_ids and pull daily closes for each in one pass.
    returns_by_symbol: dict[str, dict[date, float]] = {}
    with session_scope() as session:
        rows = session.execute(
            select(Asset.symbol, Asset.id).where(Asset.symbol.in_(sym_set))
        ).all()
        if not rows:
            return CorrelationMatrix(
                symbols=[],
                lookback_days=lookback_days,
                cells=[],
                asset_count=0,
            )
        sym_to_id: dict[str, int] = {sym: int(aid) for sym, aid in rows}
        for symbol, asset_id in sym_to_id.items():
            closes = _load_daily_closes(session, asset_id, since=cutoff)
            returns_by_symbol[symbol] = _log_returns(closes)

    # Drop symbols whose returns map is empty — we can't correlate
    # anything with no data. Keep symbols with sparse data so the UI can
    # show "1 day overlap" and the user knows to wait for the backfill.
    present_symbols = sorted(returns_by_symbol)

    cells: list[CorrelationCell] = []
    for i, sym_a in enumerate(present_symbols):
        for j in range(i, len(present_symbols)):
            sym_b = present_symbols[j]
            if sym_a == sym_b:
                # Diagonal is 1.0 by convention; the overlap counter
                # reflects the asset's own return-day count for the UI's
                # "this asset has N days of data" tooltip.
                cells.append(
                    CorrelationCell(
                        symbol_a=sym_a,
                        symbol_b=sym_b,
                        coefficient=1.0,
                        overlap=len(returns_by_symbol[sym_a]),
                    )
                )
                continue
            xs, ys = _aligned_returns(
                returns_by_symbol[sym_a], returns_by_symbol[sym_b]
            )
            r = _pearson(xs, ys)
            cells.append(
                CorrelationCell(
                    symbol_a=sym_a,
                    symbol_b=sym_b,
                    coefficient=r,
                    overlap=len(xs),
                )
            )

    return CorrelationMatrix(
        symbols=present_symbols,
        lookback_days=lookback_days,
        cells=cells,
        asset_count=len(present_symbols),
    )
