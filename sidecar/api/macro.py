from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import MacroDataPoint, MacroIndicator

router = APIRouter(prefix="/api/macro", tags=["macro"])


class MacroIndicatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    series_id: str
    name: str
    description: str | None
    units: str | None
    frequency: str | None
    is_active: bool


class MacroDataPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    value: Decimal


class MacroSeriesOut(BaseModel):
    series_id: str
    count: int
    points: list[MacroDataPointOut]


@router.get("/", response_model=list[MacroIndicatorOut])
def list_indicators(active_only: bool = True) -> list[MacroIndicatorOut]:
    with session_scope() as s:
        stmt = select(MacroIndicator).order_by(MacroIndicator.series_id)
        if active_only:
            stmt = stmt.where(MacroIndicator.is_active.is_(True))
        rows = s.execute(stmt).scalars().all()
        return [MacroIndicatorOut.model_validate(r) for r in rows]


@router.get("/{series_id}/", response_model=MacroSeriesOut)
def get_series(
    series_id: str,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 500,
) -> MacroSeriesOut:
    series_id_up = series_id.upper()
    with session_scope() as s:
        indicator = s.execute(
            select(MacroIndicator).where(MacroIndicator.series_id == series_id_up)
        ).scalar_one_or_none()
        if indicator is None:
            raise HTTPException(status_code=404, detail=f"Unknown series: {series_id_up}")

        stmt = select(MacroDataPoint).where(MacroDataPoint.indicator_id == indicator.id)
        if start is not None:
            stmt = stmt.where(MacroDataPoint.date >= start)
        if end is not None:
            stmt = stmt.where(MacroDataPoint.date <= end)
        stmt = stmt.order_by(MacroDataPoint.date.desc()).limit(limit)

        rows = list(s.execute(stmt).scalars().all())
        rows.reverse()
        points = [MacroDataPointOut.model_validate(r) for r in rows]
        return MacroSeriesOut(series_id=series_id_up, count=len(points), points=points)
