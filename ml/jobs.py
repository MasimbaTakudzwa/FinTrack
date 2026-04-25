"""Orchestration layer for the forecasting and sentiment engines.

Forecasting entry points:
- ``train_forecasts()`` — scheduler job. Iterates every active asset, pulls
  its daily-close history from `price_points` WHERE ``interval="1d"``, fits
  SARIMAX, persists the result. Swallows per-asset errors so one bad series
  doesn't nuke the whole batch (the scheduler retries on its next tick).
- ``train_one(symbol)`` — user-triggered "retrain now" from the UI. Raises
  so the API layer can surface the error (distinguish InsufficientData from
  Fit failures from Unknown symbol).

Sentiment entry points:
- ``score_articles(batch_size=...)`` — scheduler job. Picks up unscored
  articles in batches, runs them through VADER, persists. Idempotent (only
  touches rows where ``sentiment IS NULL``).
- ``score_article_ids(ids)`` — used by ``ingest_news`` to score the small
  batch of articles it just inserted, before the scheduler's next tick.

Design notes:
- We consume `price_points` and `articles` directly rather than going
  through the API layer — we're in the same process, SQLAlchemy is already
  here, and an HTTP round-trip would buy nothing.
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

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ml.forecast import (
    DEFAULT_ENGINE,
    ENGINES,
    ForecastEngine,
    ForecastError,
    ForecastFitError,
    ForecastResult,
    InsufficientDataError,
    forecast_series,
)
from ml.persistence import save_forecast
from ml.sentiment import SentimentBackendError, score_many
from sidecar.db.engine import session_scope
from sidecar.db.models import Article, Asset, PricePoint

logger = logging.getLogger(__name__)


DEFAULT_HORIZON_DAYS = 14
DEFAULT_SENTIMENT_BATCH_SIZE = 200


def _resolve_engine(engine: ForecastEngine | None) -> ForecastEngine:
    """Pick the effective engine — explicit arg wins, otherwise the user's
    Settings choice, otherwise the hard-coded default. Lazy import of the
    settings service avoids a circular import (ml → sidecar.services →
    sidecar.db → models which already imports ml indirectly via SQLEnums in
    some test paths).
    """
    if engine is not None:
        if engine not in ENGINES:
            raise ForecastError(
                f"unknown engine {engine!r}; expected one of {sorted(ENGINES)}"
            )
        return engine
    try:
        from sidecar.services.settings import load_effective_config

        configured = load_effective_config().get("forecast.default_engine")
    except Exception:  # pragma: no cover — defensive (DB not migrated yet, etc.)
        return DEFAULT_ENGINE
    if isinstance(configured, str) and configured in ENGINES:
        return configured
    return DEFAULT_ENGINE


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
    engine: ForecastEngine | None = None,
) -> int:
    """Retrain the forecast for every active asset. Returns count of successes.

    Errors per-asset (insufficient data / fit failure / unexpected exception)
    are logged at warning-or-error level and **swallowed** — the point of
    the weekly job is "make progress", not "all or nothing". User-triggered
    retrains use ``train_one`` which surfaces errors.

    ``engine=None`` defers to the user's Settings choice (or the default
    SARIMAX when no setting is configured).
    """
    effective_engine = _resolve_engine(engine)
    successes = 0
    with session_scope() as session:
        assets = _active_asset_symbol_ids(session)

    if not assets:
        logger.info("train_forecasts: no active assets, skipping")
        return 0

    for asset_id, symbol in assets:
        try:
            _train_one_inner(
                asset_id,
                symbol,
                horizon_days=horizon_days,
                engine=effective_engine,
            )
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
        "train_forecasts: retrained %d / %d active assets via %s",
        successes,
        len(assets),
        effective_engine,
    )
    return successes


def train_one(
    symbol: str,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    engine: ForecastEngine | None = None,
) -> ForecastResult:
    """Retrain the forecast for a single symbol and return the fitted result.

    ``engine`` defers to the configured default when ``None``.

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
    return _train_one_inner(
        int(row), sym, horizon_days=horizon_days, engine=_resolve_engine(engine)
    )


def _train_one_inner(
    asset_id: int,
    symbol: str,
    *,
    horizon_days: int,
    engine: ForecastEngine,
) -> ForecastResult:
    """Shared fit+persist path used by both the batch job and the API retrain.

    Split out so ``train_forecasts`` doesn't re-resolve the symbol it already
    has in hand, while ``train_one`` gets the same code path after its own
    lookup. ``engine`` is always concrete here — callers resolve via
    ``_resolve_engine`` before reaching this layer.
    """
    with session_scope() as session:
        closes = _load_daily_closes(session, asset_id)
    # `forecast_series` validates MIN_TRAINING_ROWS + ordering; let its
    # exceptions propagate.
    result = forecast_series(closes, horizon_days=horizon_days, engine=engine)
    save_forecast(asset_id, result)
    logger.info(
        "trained forecast for %s via %s: training_rows=%d horizon=%d last_close=%s",
        symbol,
        engine,
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


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------


def _score_and_persist(
    session: Session, ids: Sequence[int], headlines: Sequence[str]
) -> int:
    """Run VADER over `headlines`, write each score back to its article row.

    Returns the number of rows updated. Caller controls the surrounding
    session/transaction.
    """
    if not ids:
        return 0
    scores = score_many(headlines)
    for article_id, score in zip(ids, scores, strict=True):
        session.execute(
            update(Article)
            .where(Article.id == article_id)
            .values(sentiment=score)
        )
    return len(ids)


def score_article_ids(ids: Sequence[int]) -> int:
    """Score a specific list of article IDs in a single batch.

    Called by ``ingest_news`` immediately after the upsert so freshly-pulled
    headlines arrive with sentiment already populated — the user never sees
    a "loading" placeholder for new articles.

    Skips silently when VADER isn't installed (returns 0). Errors during
    scoring are logged and non-fatal — leaving ``sentiment IS NULL`` lets
    the periodic ``score_articles`` job pick the row up later.
    """
    if not ids:
        return 0
    try:
        with session_scope() as session:
            rows = session.execute(
                select(Article.id, Article.headline).where(Article.id.in_(ids))
            ).all()
            if not rows:
                return 0
            id_list = [row[0] for row in rows]
            headlines = [row[1] for row in rows]
            return _score_and_persist(session, id_list, headlines)
    except SentimentBackendError as exc:
        logger.warning(
            "score_article_ids: VADER backend unavailable (%s); leaving "
            "%d articles unscored",
            exc,
            len(ids),
        )
        return 0


def score_articles(batch_size: int = DEFAULT_SENTIMENT_BATCH_SIZE) -> int:
    """Score every article that doesn't yet have a sentiment value.

    Idempotent: re-running picks up any new ``sentiment IS NULL`` rows that
    may have been inserted by direct DB writes (tests, manual SQL, etc.).
    Done in batches so a single transaction doesn't lock the whole table for
    seconds at a time on a large backfill.

    Returns the total number of articles scored across all batches.
    """
    total = 0
    while True:
        try:
            with session_scope() as session:
                rows = session.execute(
                    select(Article.id, Article.headline)
                    .where(Article.sentiment.is_(None))
                    .order_by(Article.id.asc())
                    .limit(batch_size)
                ).all()
                if not rows:
                    break
                ids = [row[0] for row in rows]
                headlines = [row[1] for row in rows]
                total += _score_and_persist(session, ids, headlines)
                if len(rows) < batch_size:
                    break
        except SentimentBackendError as exc:
            logger.error(
                "score_articles: VADER backend unavailable (%s); aborting "
                "after scoring %d articles in this run",
                exc,
                total,
            )
            return total
    if total:
        logger.info("score_articles: scored %d previously-unscored articles", total)
    return total
