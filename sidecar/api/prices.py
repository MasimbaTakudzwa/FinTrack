from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, PricePoint

router = APIRouter(prefix="/api/prices", tags=["prices"])


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
) -> PriceSeriesOut:
    symbol = symbol.upper()
    with session_scope() as s:
        asset = s.execute(select(Asset).where(Asset.symbol == symbol)).scalar_one_or_none()
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

        stmt = select(PricePoint).where(PricePoint.asset_id == asset.id)
        if start is not None:
            stmt = stmt.where(PricePoint.timestamp >= start)
        if end is not None:
            stmt = stmt.where(PricePoint.timestamp <= end)
        stmt = stmt.order_by(PricePoint.timestamp.desc()).limit(limit)

        rows = list(s.execute(stmt).scalars().all())
        rows.reverse()
        points = [PricePointOut.model_validate(r) for r in rows]
        return PriceSeriesOut(symbol=symbol, count=len(points), points=points)
