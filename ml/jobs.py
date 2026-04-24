"""Orchestration layer for the forecasting engine.

Two public entry points:
- ``train_forecasts()`` — scheduler job. Iterates every active asset, pulls
  its daily-close history from `price_points` WHERE ``interval="1d"``, fits
  SARIMAX, persists the result. Swallows per-asset errors so one bad series
  doesn't nuke the whole batch (the scheduler retries on its next tick).
- ``train_one(symbol)`` — user-triggered "retrain now" from the UI. Raises
  so the API layer can surface the error (distinguish InsufficientData from
  Fit failures from Unknown symbol).

Design notes:
- We consume `price_points` directly rather than going through the API layer
  — we're in the same process, SQLAlchemy is already here, and the API would
  add a round-trip through FastAPI's TestClient-style plumbing for no gain.
- Horizon is fixed at 14 days (project default, user-visible in Settings as
  a future toggle). Persisted on each row so future retrains can change it
  without a migration.
- `datetime.now(UTC)` reads lazily on each call so tests can freeze time
  around the whole pipeline when asserting `generated_at`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ml.forecast import (
    ForecastError,
    ForecastFitError,
    ForecastResult,
    InsufficientDataError,
    forecast_series,
)
from ml.persistence import save_forecast
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, PricePoint

logger = logging.getLogger(__name__)


DEFAULT_HORIZON_DAYS = 14


class UnknownSymbolError(ForecastError):
    """Raised by ``train_one`` when the requested symbol isn't a tracked asset."""


def _load_daily_closes(
    session: Session, asset_id: int
) -> list[tuple[date, float]]:
    """Pull daily closes for an asset, ordered oldest-first.

    The forecaster validates strictly-ascending dates and enforces a minimum
    row count; we just load & coerce here. `timestamp` is tz-aware in the
    source table but the forecast cares about the calendar day only, so we
    project to `date` directly.
    """
    rows = session.execute(
        select(PricePoint.timestamp, PricePoint.close)
        .where(
            PricePoint.asset_id == asset_id,
            PricePoint.interval == "1d",
        )
        .order_by(PricePoint.timestamp.asc())
    ).all()
    # De-dup on date in case a future ingest accidentally writes two bars on
    # the same day at different timestamps (shouldn't happen — unique constraint
    # covers it — but being defensive is cheap here).
    seen: dict[date, float] = {}
    for ts, close in rows:
        seen[ts.date()] = float(close)
    return sorted(seen.items())


def _active_asset_symbol_ids(session: Session) -> list[tuple[int, str]]:
    rows = session.execute(
        select(Asset.id, Asset.symbol)
        .where(Asset.is_active.is_(True))
        .order_by(Asset.symbol.asc())
    ).all()
    return [(aid, sym) for aid, sym in rows]


def train_forecasts(
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Retrain the forecast for every active asset. Returns count of successes.

    Errors per-asset (insufficient data / fit failure / unexpected exception)
    are logged at warning-or-error level and **swallowed** — the point of
    the weekly job is "make progress", not "all or nothing". User-triggered
    retrains use ``train_one`` which surfaces errors.
    """
    successes = 0
    with session_scope() as session:
        assets = _active_asset_symbol_ids(session)

    if not assets:
        logger.info("train_forecasts: no active assets, skipping")
        return 0

    for asset_id, symbol in assets:
        try:
            _train_one_inner(asset_id, symbol, horizon_days=horizon_days)
            successes += 1
        except InsufficientDataError as exc:
            logger.info(
                "train_forecasts: skipping %s (insufficient data: %s)",
                symbol,
                exc,
            )
        except ForecastFitError as exc:
            logger.warning(
                "train_forecasts: fit failed for %s: %s", symbol, exc
            )
        except Exception:  # pragma: no cover — truly defensive
            logger.exception("train_forecasts: unexpected error for %s", symbol)
    logger.info(
        "train_forecasts: retrained %d / %d active assets",
        successes,
        len(assets),
    )
    return successes


def train_one(
    symbol: str,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ForecastResult:
    """Retrain the forecast for a single symbol and return the fitted result.

    Raises:
        UnknownSymbolError: when ``symbol`` doesn't match a tracked asset.
        InsufficientDataError: not enough daily closes yet.
        ForecastFitError: statsmodels failed to fit.
    """
    sym = symbol.strip().upper()
    with session_scope() as session:
        row = session.execute(
            select(Asset.id).where(Asset.symbol == sym)
        ).scalar_one_or_none()
    if row is None:
        raise UnknownSymbolError(f"no tracked asset with symbol {sym!r}")
    return _train_one_inner(int(row), sym, horizon_days=horizon_days)


def _train_one_inner(
    asset_id: int,
    symbol: str,
    *,
    horizon_days: int,
) -> ForecastResult:
    """Shared fit+persist path used by both the batch job and the API retrain.

    Split out so ``train_forecasts`` doesn't re-resolve the symbol it already
    has in hand, while ``train_one`` gets the same code path after its own
    lookup.
    """
    with session_scope() as session:
        closes = _load_daily_closes(session, asset_id)
    # `forecast_series` validates MIN_TRAINING_ROWS + ordering; let its
    # exceptions propagate.
    result = forecast_series(closes, horizon_days=horizon_days)
    save_forecast(asset_id, result)
    logger.info(
        "trained forecast for %s: training_rows=%d horizon=%d last_close=%s",
        symbol,
        result.training_rows,
        result.horizon_days,
        result.last_close,
    )
    return result


def symbols_eligible_for_forecast() -> Sequence[str]:
    """Return active asset symbols that have at least one daily bar.

    Used by the API layer when the UI asks "which assets have a forecast
    available?" — cheaper than issuing N GET requests and 404'ing most of
    them.
    """
    with session_scope() as session:
        rows = session.execute(
            select(Asset.symbol)
            .join(PricePoint, PricePoint.asset_id == Asset.id)
            .where(
                Asset.is_active.is_(True),
                PricePoint.interval == "1d",
            )
            .distinct()
            .order_by(Asset.symbol.asc())
        ).scalars().all()
    return list(rows)
