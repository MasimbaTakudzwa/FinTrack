"""Forecast endpoints.

- ``GET /api/forecast/`` — list every symbol that currently has a stored
  forecast *and* every symbol that is eligible to be trained (has at least
  one daily bar). The UI uses this to short-circuit 404 dances when asking
  "which assets have a forecast available?"
- ``GET /api/forecast/{symbol}/`` — latest persisted forecast for a symbol.
  404 when the symbol is unknown OR when the asset exists but has no
  forecast row yet (the UI distinguishes via the ``eligible`` list).
- ``POST /api/forecast/{symbol}/retrain/`` — synchronous retrain. Writes the
  new forecast to the ``forecasts`` table and returns it. SARIMAX fits in
  under a second on the modern-daily-bar volume we carry, so we don't need a
  background-job indirection here.

Error mapping:
- 404 — unknown symbol / no forecast available
- 422 — InsufficientDataError (not enough daily closes to train yet)
- 500 — ForecastFitError (statsmodels optimiser couldn't converge)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ml.forecast import (
    ForecastFitError,
    ForecastResult,
    InsufficientDataError,
)
from ml.jobs import (
    UnknownSymbolError,
    symbols_eligible_for_forecast,
    train_one,
)
from ml.persistence import load_forecast_by_symbol

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
    """

    eligible: list[str]
    persisted: list[str]


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
    """Return the symbols eligible to forecast + those that already have one.

    Cheap: two small SELECTs. The UI calls this once on app boot or when the
    "Forecast" toggle is first surfaced and caches the result for the session.
    """
    from sqlalchemy import select

    from ml.persistence import all_forecast_asset_ids
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

    return ForecastAvailabilityModel(eligible=eligible, persisted=persisted)


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
def retrain_forecast(symbol: str) -> ForecastResponseModel:
    """Fit SARIMAX synchronously and persist the result. Returns the new forecast."""
    sym = symbol.strip().upper()
    try:
        result = train_one(sym)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientDataError as exc:
        # 422 Unprocessable Entity matches our convention: the request was
        # well-formed, but the asset isn't ready to train yet (backfill in
        # flight). The UI surfaces this with a friendly "need more history"
        # message.
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
