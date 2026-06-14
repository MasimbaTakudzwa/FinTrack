from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, PricePoint

router = APIRouter(prefix="/api/prices", tags=["prices"])


def _to_naive_utc(value: datetime | None) -> datetime | None:
    """Coerce an incoming query datetime to naive-UTC.

    SQLite stores ``DateTime`` columns as naive strings (no offset). An aware
    bound like ``2026-01-01T00:00:00+00:00`` would compare *lexically* against
    the stored ``2026-01-01 00:00:00`` and silently include/exclude boundary
    rows. Normalising both sides to naive-UTC makes range filters correct.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


class PricePointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class PriceSeriesOut(BaseModel):
    symbol: str
    count: int
    points: list[PricePointOut]


@router.get("/{symbol}/", response_model=PriceSeriesOut)
def get_prices(
    symbol: str,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 500,
    interval: Annotated[str, Query(min_length=1, max_length=16)] = "5m",
) -> PriceSeriesOut:
    """Return price bars for `symbol`, newest-first-filtered then ascending.

    The `interval` filter defaults to "5m" (the intraday cadence served by
    `ingest_prices`) to preserve backward compatibility with callers that
    predate the Phase 2 daily-bar layer. Pass `interval=1d` to consume the
    daily-close series used by the forecasting engine.
    """
    symbol = symbol.upper()
    with session_scope() as s:
        asset = s.execute(select(Asset).where(Asset.symbol == symbol)).scalar_one_or_none()
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

        start_n = _to_naive_utc(start)
        end_n = _to_naive_utc(end)
        stmt = select(PricePoint).where(
            PricePoint.asset_id == asset.id,
            PricePoint.interval == interval,
        )
        if start_n is not None:
            stmt = stmt.where(PricePoint.timestamp >= start_n)
        if end_n is not None:
            stmt = stmt.where(PricePoint.timestamp <= end_n)
        stmt = stmt.order_by(PricePoint.timestamp.desc()).limit(limit)

        rows = list(s.execute(stmt).scalars().all())
        rows.reverse()
        points = [PricePointOut.model_validate(r) for r in rows]
        return PriceSeriesOut(symbol=symbol, count=len(points), points=points)
