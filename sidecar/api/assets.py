"""Asset endpoints.

Read-side: ``GET /api/assets/`` lists tracked assets.

Write-side (Sprint 4 follow-up): ``POST /api/assets/lookup/`` previews a
yfinance resolution without persisting, and ``POST /api/assets/`` actually
persists + kicks off a one-shot ingest so the dashboard shows bars
immediately. See :mod:`sidecar.services.assets` for the resolution logic.

Error mapping:
- 404 — symbol could not be resolved against yfinance
- 409 — symbol already tracked
- 400 — validation error (empty symbol, too long, etc.)
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType
from sidecar.services.assets import (
    AssetAlreadyExistsError,
    AssetServiceError,
    SymbolNotFoundError,
    add_asset,
    resolve_symbol,
)
from sidecar.services.watchlists import (
    ItemAlreadyExistsError,
    WatchlistError,
    add_item,
    get_default_watchlist,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assets", tags=["assets"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: str
    asset_type: AssetType
    is_active: bool
    created_at: datetime


class LookupIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)


class LookupOut(BaseModel):
    symbol: str
    name: str
    asset_type: AssetType
    exchange: str | None
    currency: str | None


class CreateAssetIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    add_to_default_watchlist: bool = True


class CreateAssetOut(BaseModel):
    asset: AssetOut
    bars_ingested: int
    added_to_watchlist: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[AssetOut])
def list_assets(active_only: bool = True) -> list[AssetOut]:
    with session_scope() as s:
        stmt = select(Asset).order_by(Asset.symbol)
        if active_only:
            stmt = stmt.where(Asset.is_active.is_(True))
        rows = s.execute(stmt).scalars().all()
        return [AssetOut.model_validate(a) for a in rows]


@router.post("/lookup/", response_model=LookupOut)
def lookup_asset_route(body: LookupIn) -> LookupOut:
    """Preview a yfinance symbol resolution without persisting.

    Lets the "Add asset" UI show the user what they're about to track
    (name, asset type, exchange, currency) before committing.
    """
    try:
        resolved = resolve_symbol(body.symbol)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssetServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LookupOut(
        symbol=resolved.symbol,
        name=resolved.name,
        asset_type=resolved.asset_type,
        exchange=resolved.exchange,
        currency=resolved.currency,
    )


@router.post("/", response_model=CreateAssetOut, status_code=201)
def create_asset_route(body: CreateAssetIn) -> CreateAssetOut:
    """Resolve a yfinance symbol, persist it, and kick off a one-shot ingest.

    Optionally also adds the new asset to the default watchlist so it
    immediately surfaces on the Dashboard. Failure to add to the watchlist
    (no default exists, or a race condition) is non-fatal: the asset is
    still persisted and ingested.
    """
    try:
        result = add_asset(body.symbol)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssetAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AssetServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    added_to_watchlist = False
    if body.add_to_default_watchlist:
        try:
            default = get_default_watchlist()
            if default is not None:
                add_item(default.id, result.asset_id)
                added_to_watchlist = True
        except ItemAlreadyExistsError:
            # Race — someone else added it between our create + now. Count it.
            added_to_watchlist = True
        except WatchlistError as exc:
            logger.info(
                "add_asset(%s): default-watchlist add skipped: %s",
                result.symbol,
                exc,
            )

    # Re-read the row so we return the hydrated AssetOut shape (created_at etc).
    with session_scope() as s:
        asset = s.execute(
            select(Asset).where(Asset.id == result.asset_id)
        ).scalar_one()
        asset_out = AssetOut.model_validate(asset)

    return CreateAssetOut(
        asset=asset_out,
        bars_ingested=result.bars_ingested,
        added_to_watchlist=added_to_watchlist,
    )
