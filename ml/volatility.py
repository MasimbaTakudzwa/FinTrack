"""Realized volatility + EWMA next-day forecast.

Companion to the price forecaster — answers "how big a daily move is
typical for this asset, and what should I expect tomorrow?". Pure
arithmetic, no statsmodels: just stdev over log-returns plus a
RiskMetrics-style EWMA recurrence for the forward estimate.

Two metrics:

- **Realized volatility** — annualized standard deviation of log-returns
  over the trailing window (default 30 days). The standard "vol" number
  traders quote (e.g. "AAPL has been running at ~25% vol"). Annualized
  by ``sqrt(TRADING_DAYS_PER_YEAR)``.
- **EWMA next-day volatility** — exponentially-weighted recurrence
  ``sigma^2_t = lambda_ * sigma^2_{t-1} + (1 - lambda_) * r^2_{t-1}`` with lambda_ = 0.94 (the
  RiskMetrics 1996 default). Heavier weight on recent observations
  captures volatility clustering — the "calm or jumpy?" feel a simple
  rolling stdev misses. Used as the next-day forecast.

Why not GARCH(1,1) — it would model variance clustering more
faithfully and produce a richer multi-day volatility forecast curve,
but it requires the ``arch`` package (~5 MB transitive deps), the fit
takes meaningfully longer than EWMA, and for our "what's the expected
±sigma band tomorrow?" use case EWMA is within a few % of GARCH on most
financial series. We can swap engines later — the public ``compute_*``
surface is stable.

Pure-compute boundary: imports SQLAlchemy for `_load_daily_closes` but
no statsmodels / numpy / scipy — the math is hand-rolled in the
standard library so a sidecar without ``requirements-ml.txt`` could in
principle still serve volatility metrics.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, PricePoint

logger = logging.getLogger(__name__)


# Trading days per calendar year. The conventional choice for equities
# is 252; FX/crypto traders sometimes use 365. We pick 252 because most
# of our seeded assets are stocks and ETFs — crypto vol comes out a hair
# low under this convention, which we view as conservative not wrong.
TRADING_DAYS_PER_YEAR = 252

# Minimum number of return observations to compute a meaningful stdev.
# Below this the stat is dominated by sample noise; the API surfaces the
# count alongside the metric so the UI can fade out under-sampled values.
MIN_RETURNS_FOR_VOL = 5

# RiskMetrics 1996 standard lambda_ for daily-frequency EWMA. JPMorgan picked
# 0.94 empirically against a basket of equity/FX/bond series; staying
# with the convention keeps our numbers comparable to off-the-shelf tools.
RISKMETRICS_LAMBDA = 0.94


@dataclass(frozen=True)
class VolatilityReport:
    """All the volatility metrics the UI surfaces for a single asset.

    All ``*_vol`` fields are decimal proportions (0.025 == 2.5%); the UI
    multiplies by 100 for display. ``expected_move_*`` are price-space
    ±1sigma bounds for the next trading day, computed from
    ``ewma_next_day_vol`` + ``last_close`` so the band scales with the
    asset's actual price level.
    """

    symbol: str
    lookback_days: int
    returns_used: int
    last_close: float | None
    last_close_date: date | None
    realized_vol_daily: float | None
    realized_vol_annualized: float | None
    ewma_next_day_vol: float | None
    expected_move_low: float | None
    expected_move_high: float | None


# ---------------------------------------------------------------------------
# Pure-compute helpers
# ---------------------------------------------------------------------------


def _log_returns(closes: list[tuple[date, float]]) -> list[float]:
    """Convert a date-sorted close series into a list of log-returns.

    First date has no prior close so it's skipped. Non-positive prices
    are guarded against (avoids ``log(<=0)`` raising) — those rows
    drop out of the returns list.
    """
    out: list[float] = []
    if len(closes) < 2:
        return out
    for i in range(1, len(closes)):
        prev = closes[i - 1][1]
        curr = closes[i][1]
        if prev <= 0 or curr <= 0:
            continue
        out.append(math.log(curr / prev))
    return out


def _stdev(values: list[float]) -> float:
    """Sample standard deviation (Bessel-corrected, n-1 in the denominator).

    Returns 0.0 for series shorter than two elements; the caller's
    `MIN_RETURNS_FOR_VOL` gate is responsible for treating that as
    "no signal" rather than a real "zero volatility" reading.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def _ewma_volatility(returns: list[float], lam: float = RISKMETRICS_LAMBDA) -> float:
    """RiskMetrics-style next-period EWMA volatility from a return series.

    Recursion: ``sigma^2_t = lambda_ * sigma^2_{t-1} + (1 - lambda_) * r^2_{t-1}``. Seeds the
    variance at the unconditional sample variance so the recursion has
    something to anchor on; without that the early observations would
    compress toward zero. Returns the *next-step* volatility, i.e. the
    forecast for tomorrow given everything we've seen up to today.
    """
    n = len(returns)
    if n < MIN_RETURNS_FOR_VOL:
        return 0.0
    # Seed with sample variance — better than zero (avoids early-period
    # underestimation) and still gets dominated by recent r^2 as the
    # recursion runs.
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    for r in returns:
        var = lam * var + (1.0 - lam) * (r * r)
    return math.sqrt(var)


# ---------------------------------------------------------------------------
# DB-aware orchestration
# ---------------------------------------------------------------------------


def _load_daily_closes(
    session: Session, asset_id: int, *, since: date
) -> list[tuple[date, float]]:
    """Pull daily closes for an asset since ``since``, deduped by date,
    sorted ascending."""
    rows = session.execute(
        select(PricePoint.timestamp, PricePoint.close).where(
            PricePoint.asset_id == asset_id,
            PricePoint.interval == "1d",
            PricePoint.timestamp >= datetime.combine(
                since, datetime.min.time(), UTC
            ),
        )
    ).all()
    by_date: dict[date, float] = {}
    for ts, close in rows:
        by_date[ts.date()] = float(close)
    return sorted(by_date.items())


def compute_volatility(
    symbol: str,
    *,
    lookback_days: int = 30,
) -> VolatilityReport:
    """Return realized + EWMA-forecast volatility for a single asset.

    ``lookback_days`` is the calendar window for the realized-vol
    computation. The EWMA recursion runs over the same window; the
    seed-with-sample-variance pattern means a longer history would
    produce a slightly different forecast, but for the 30-day default
    the divergence from a 90-day-seeded EWMA is well under the noise
    band.
    """
    sym = symbol.strip().upper()

    with session_scope() as session:
        asset_row = session.execute(
            select(Asset.id).where(Asset.symbol == sym)
        ).scalar_one_or_none()
        if asset_row is None:
            return _empty_report(sym, lookback_days)
        cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).date()
        closes = _load_daily_closes(session, int(asset_row), since=cutoff)

    returns = _log_returns(closes)
    if len(returns) < MIN_RETURNS_FOR_VOL:
        last_close = closes[-1][1] if closes else None
        last_close_date = closes[-1][0] if closes else None
        return VolatilityReport(
            symbol=sym,
            lookback_days=lookback_days,
            returns_used=len(returns),
            last_close=last_close,
            last_close_date=last_close_date,
            realized_vol_daily=None,
            realized_vol_annualized=None,
            ewma_next_day_vol=None,
            expected_move_low=None,
            expected_move_high=None,
        )

    realized_daily = _stdev(returns)
    realized_annual = realized_daily * math.sqrt(TRADING_DAYS_PER_YEAR)
    ewma_next = _ewma_volatility(returns)

    last_close = closes[-1][1]
    last_close_date = closes[-1][0]
    # ±1sigma band in price space — the headline number the UI shows. We use
    # EWMA (the forecast), not realized, because the user asked
    # implicitly "what should I expect tomorrow?" by looking at this.
    band_half = last_close * ewma_next
    return VolatilityReport(
        symbol=sym,
        lookback_days=lookback_days,
        returns_used=len(returns),
        last_close=last_close,
        last_close_date=last_close_date,
        realized_vol_daily=realized_daily,
        realized_vol_annualized=realized_annual,
        ewma_next_day_vol=ewma_next,
        expected_move_low=last_close - band_half,
        expected_move_high=last_close + band_half,
    )


def _empty_report(symbol: str, lookback_days: int) -> VolatilityReport:
    """Returned when the asset doesn't exist (no 404 — UI hides the panel)."""
    return VolatilityReport(
        symbol=symbol,
        lookback_days=lookback_days,
        returns_used=0,
        last_close=None,
        last_close_date=None,
        realized_vol_daily=None,
        realized_vol_annualized=None,
        ewma_next_day_vol=None,
        expected_move_low=None,
        expected_move_high=None,
    )
