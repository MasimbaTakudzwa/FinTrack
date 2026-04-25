"""Forecast endpoints.

- ``GET /api/forecast/`` — list every symbol that currently has a stored
  forecast *and* every symbol that is eligible to be trained (has at least
  one daily bar). The UI uses this to short-circuit 404 dances when asking
  "which assets have a forecast available?"
- ``GET /api/forecast/{symbol}/`` — latest persisted forecast for a symbol.
  404 when the symbol is unknown OR when the asset exists but has no
  forecast row yet (the UI distinguishes via the ``eligible`` list).
- ``POST /api/forecast/{symbol}/retrain/?engine=...`` — synchronous retrain.
  Writes the new forecast to the ``forecasts`` table and returns it. Both
  SARIMAX and Holt-Winters fit in under a second on the modern-daily-bar
  volume we carry, so we don't need a background-job indirection here.
- ``POST /api/forecast/retrain-all/`` — kick off a synchronous full-batch
  retrain across every active asset. Per-asset failures are swallowed by
  the underlying ``ml.jobs.train_forecasts``; the response reports counts.
- ``DELETE /api/forecast/`` — wipe every stored forecast (used after the
  user switches engines and wants a clean slate). Doesn't touch
  ``price_points`` / ``articles`` — only the ``forecasts`` table.

Error mapping:
- 404 — unknown symbol / no forecast available
- 422 — validation failure (insufficient data, invalid engine)
- 500 — ForecastFitError (statsmodels optimiser couldn't converge)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ml.accuracy import AccuracyReport, EngineAccuracy, compute_accuracy
from ml.forecast import (
    ENGINES,
    ForecastEngine,
    ForecastError,
    ForecastFitError,
    ForecastResult,
    InsufficientDataError,
)
from ml.jobs import (
    UnknownSymbolError,
    symbols_eligible_for_forecast,
    train_forecasts,
    train_one,
)
from ml.persistence import (
    all_forecast_asset_ids,
    delete_forecast,
    load_forecast_by_symbol,
)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ForecastPointModel(BaseModel):
    forecast_date: date
    yhat: float
    lower_80: float
    upper_80: float
    lower_95: float
    upper_95: float


class ForecastResponseModel(BaseModel):
    symbol: str
    asset_id: int
    model: str
    horizon_days: int
    training_rows: int
    last_close: Decimal
    last_close_date: date
    generated_at: datetime
    points: list[ForecastPointModel]


class ForecastAvailabilityModel(BaseModel):
    """Which assets can be forecasted, and which already have a stored forecast.

    ``eligible`` is a superset of ``persisted`` — if an asset is eligible but
    not persisted, the UI can offer a "Train now" button. If persisted, the UI
    fetches via ``GET /api/forecast/{symbol}/`` without further checks.
    ``engines`` lists every engine the backend can fit so the UI's selector
    doesn't have to hard-code the literal set.
    """

    eligible: list[str]
    persisted: list[str]
    engines: list[str]


class RetrainAllResponse(BaseModel):
    """Result of a full-batch retrain — counts only, not per-asset payload."""

    requested: int
    trained: int
    skipped: int
    engine: str


class ClearForecastsResponse(BaseModel):
    """Tally of forecasts removed by the bulk-clear endpoint."""

    deleted: int


class EngineAccuracyModel(BaseModel):
    """Wire shape for ``ml.accuracy.EngineAccuracy``."""

    engine: str
    snapshots: int
    evaluable_points: int
    mape: float | None
    rmse: float | None
    directional: float | None


class AccuracyReportModel(BaseModel):
    """Wire shape for ``ml.accuracy.AccuracyReport``.

    Drives the "Forecast accuracy" panel on AssetDetail. ``per_engine`` is
    sorted by MAPE ascending (best engine first); ``overall`` rolls every
    engine into a single headline metric for assets that have only ever
    used one.
    """

    symbol: str
    days: int
    per_engine: list[EngineAccuracyModel]
    overall: EngineAccuracyModel | None
    actuals_available: int


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _to_response(symbol: str, asset_id: int, r: ForecastResult) -> ForecastResponseModel:
    return ForecastResponseModel(
        symbol=symbol,
        asset_id=asset_id,
        model=r.model,
        horizon_days=r.horizon_days,
        training_rows=r.training_rows,
        last_close=r.last_close,
        last_close_date=r.last_close_date,
        generated_at=r.generated_at,
        points=[
            ForecastPointModel(
                forecast_date=p.forecast_date,
                yhat=p.yhat,
                lower_80=p.lower_80,
                upper_80=p.upper_80,
                lower_95=p.lower_95,
                upper_95=p.upper_95,
            )
            for p in r.points
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=ForecastAvailabilityModel)
def list_forecast_availability() -> ForecastAvailabilityModel:
    """Return the symbols eligible to forecast + those that already have one,
    plus the canonical list of engines the backend can fit.

    Cheap: two small SELECTs. The UI calls this once on app boot or when the
    "Forecast" toggle is first surfaced and caches the result for the session.
    """
    from sqlalchemy import select

    from sidecar.db.engine import session_scope
    from sidecar.db.models import Asset

    eligible = list(symbols_eligible_for_forecast())

    persisted_ids = all_forecast_asset_ids()
    if persisted_ids:
        with session_scope() as s:
            rows = (
                s.execute(
                    select(Asset.symbol)
                    .where(Asset.id.in_(persisted_ids))
                    .order_by(Asset.symbol.asc())
                )
                .scalars()
                .all()
            )
        persisted = list(rows)
    else:
        persisted = []

    return ForecastAvailabilityModel(
        eligible=eligible,
        persisted=persisted,
        engines=list(ENGINES),
    )


def _validate_engine_param(raw: str | None) -> ForecastEngine | None:
    """Coerce a free-form ``engine=`` query string to the typed literal.

    None passes through unchanged (caller defers to the user's setting).
    Anything outside ``ENGINES`` raises 422 via the FastAPI exception path —
    same convention we use for invalid sentiment buckets.
    """
    if raw is None or raw == "":
        return None
    if raw not in ENGINES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown engine {raw!r}; expected one of {sorted(ENGINES)}",
        )
    return raw


@router.get("/{symbol}/", response_model=ForecastResponseModel)
def get_forecast(symbol: str) -> ForecastResponseModel:
    """Return the latest stored forecast for ``symbol``.

    404 fires for both "unknown symbol" and "known asset, no forecast row
    yet". The UI relies on the ``/api/forecast/`` availability list to
    distinguish the two cases before surfacing a retrain CTA.
    """
    loaded = load_forecast_by_symbol(symbol)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail=f"No forecast available for symbol {symbol!r}",
        )
    asset_id, result = loaded
    return _to_response(symbol.strip().upper(), asset_id, result)


@router.post("/{symbol}/retrain/", response_model=ForecastResponseModel)
def retrain_forecast(
    symbol: str,
    engine: Annotated[str | None, Query()] = None,
) -> ForecastResponseModel:
    """Fit a forecast synchronously and persist the result. Returns the new forecast.

    ``engine`` accepts ``"sarimax"`` or ``"holt_winters"``; omit the query
    parameter to defer to the user's Settings choice.
    """
    sym = symbol.strip().upper()
    chosen = _validate_engine_param(engine)
    try:
        result = train_one(sym, engine=chosen)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientDataError as exc:
        # 422 Unprocessable Entity matches our convention: the request was
        # well-formed, but the asset isn't ready to train yet (backfill in
        # flight). The UI surfaces this with a friendly "need more history"
        # message.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ForecastError as exc:
        # Catches "unknown engine" too if the route is hit with a typo we
        # missed in `_validate_engine_param`. 422 matches.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ForecastFitError as exc:
        # 500 is the right answer here — the model refused to converge on
        # actual data, which is our problem, not the client's. The ML
        # worker logs stack traces.
        logger.exception("retrain_forecast: fit failed for %s", sym)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Look the asset id back up so the response matches ``get_forecast``'s
    # shape — ``train_one`` doesn't return it.
    loaded = load_forecast_by_symbol(sym)
    if loaded is None:
        # Shouldn't happen: ``train_one`` just persisted via save_forecast.
        raise HTTPException(status_code=500, detail="forecast disappeared post-train")
    asset_id, _ = loaded
    return _to_response(sym, asset_id, result)


@router.post("/retrain-all/", response_model=RetrainAllResponse)
def retrain_all_forecasts(
    engine: Annotated[str | None, Query()] = None,
) -> RetrainAllResponse:
    """Synchronously retrain every active asset's forecast.

    Per-asset failures (insufficient data / fit failure) are swallowed by
    the underlying job — same semantics as the weekly scheduled run — so
    the response reports `requested` (active asset count), `trained`
    (successes), and `skipped` (the difference). UI shows the counts with
    a hint to check the asset detail pages for which ones bombed.

    ``engine`` accepts ``"sarimax"`` or ``"holt_winters"`` (omit to use
    the user's Settings default).
    """
    chosen = _validate_engine_param(engine)
    eligible = list(symbols_eligible_for_forecast())
    requested = len(eligible)
    trained = train_forecasts(engine=chosen)
    return RetrainAllResponse(
        requested=requested,
        trained=trained,
        skipped=max(requested - trained, 0),
        # Resolve the effective engine string for the response so the UI
        # can label the toast accurately even when the caller passed None.
        engine=chosen or _resolved_default_engine(),
    )


def _resolved_default_engine() -> str:
    """Return the engine that ``ml.jobs._resolve_engine(None)`` would pick.

    Duplicated here (instead of importing the private helper) so the API
    layer doesn't reach into ``ml`` internals; the lookup is cheap and the
    fallback chain is identical.
    """
    try:
        from sidecar.services.settings import load_effective_config

        configured = load_effective_config().get("forecast.default_engine")
        if isinstance(configured, str) and configured in ENGINES:
            return configured
    except Exception:  # pragma: no cover — defensive
        pass
    return "sarimax"


@router.delete("/", response_model=ClearForecastsResponse)
def clear_all_forecasts() -> ClearForecastsResponse:
    """Wipe every stored forecast.

    Used after the user switches engines (they want the next chart load to
    show only forecasts produced by the new engine). The price_points and
    articles tables are untouched — re-running ``retrain-all`` immediately
    rebuilds the corpus. Note: only the ``forecasts`` (latest-per-asset)
    rows are removed; ``forecast_snapshots`` history is preserved so the
    accuracy report can still draw on past runs.
    """
    deleted = 0
    for asset_id in list(all_forecast_asset_ids()):
        if delete_forecast(asset_id):
            deleted += 1
    return ClearForecastsResponse(deleted=deleted)


def _engine_accuracy_to_model(ea: EngineAccuracy) -> EngineAccuracyModel:
    return EngineAccuracyModel(
        engine=ea.engine,
        snapshots=ea.snapshots,
        evaluable_points=ea.evaluable_points,
        mape=ea.mape,
        rmse=ea.rmse,
        directional=ea.directional,
    )


def _accuracy_to_model(report: AccuracyReport) -> AccuracyReportModel:
    return AccuracyReportModel(
        symbol=report.symbol,
        days=report.days,
        per_engine=[_engine_accuracy_to_model(e) for e in report.per_engine],
        overall=(
            _engine_accuracy_to_model(report.overall)
            if report.overall is not None
            else None
        ),
        actuals_available=report.actuals_available,
    )


@router.get("/{symbol}/accuracy/", response_model=AccuracyReportModel)
def get_forecast_accuracy(
    symbol: str,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AccuracyReportModel:
    """Rolling forecast accuracy for one asset over the last ``days`` days.

    Aggregates every snapshot in the window against the daily closes that
    have actually landed since. Returns ``per_engine`` (best MAPE first),
    plus a global ``overall`` rollup. ``snapshots`` counts every snapshot
    even if its horizon hasn't elapsed yet — UI uses this to label
    "snapshots seen" vs. "evaluable points" so users understand sparsity.

    No 404 path here — an unknown symbol returns an empty report so the UI
    can render a "no accuracy data yet" hint without a separate
    error-handling branch.
    """
    report = compute_accuracy(symbol, days=days)
    return _accuracy_to_model(report)
