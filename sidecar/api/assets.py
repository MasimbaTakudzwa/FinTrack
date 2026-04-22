from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType

router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: str
    asset_type: AssetType
    is_active: bool
    created_at: datetime


@router.get("/", response_model=list[AssetOut])
def list_assets(active_only: bool = True) -> list[AssetOut]:
    with session_scope() as s:
        stmt = select(Asset).order_by(Asset.symbol)
        if active_only:
            stmt = stmt.where(Asset.is_active.is_(True))
        rows = s.execute(stmt).scalars().all()
        return [AssetOut.model_validate(a) for a in rows]
