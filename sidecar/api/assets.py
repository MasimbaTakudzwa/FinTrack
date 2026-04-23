"""Asset endpoints.

Read-side: ``GET /api/assets/`` lists tracked assets.

Write-side (Sprint 4 follow-up): ``POST /api/assets/lookup/`` previews a
yfinance resolution without persisting, and ``POST /api/assets/`` actually
persists + kicks off a one-shot ingest so the dashboard shows bars
immediately. See :mod:`sidecar.services.assets` for the resolution logic.

``POST /api/assets/`` is idempotent: posting a symbol that's already
tracked returns the existing row (with ``newly_added=false`` and
``bars_ingested=0``) instead of 409-ing. That lets the "Track new…"
button on a non-default watchlist succeed even when the asset is already
in another watchlist — the backend will just link it to the requested
``watchlist_id`` without re-fetching price history.

Error mapping:
- 404 — symbol could not be resolved against yfinance (only when the
  symbol is genuinely new; already-tracked symbols skip yfinance entirely)
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
    AssetServiceError,
    SymbolNotFoundError,
    add_asset,
    resolve_symbol,
)
from sidecar.services.watchlists import (
    ItemAlreadyExistsError,
    WatchlistError,
    WatchlistNotFoundError,
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
    # Optional: also link the asset to this specific watchlist. The "Track
    # new…" button on a non-default watchlist sends this so an already-tracked
    # asset can be linked to the current list without surfacing a 409. Must
    # reference an existing watchlist — a bogus id silently no-ops (logged).
    watchlist_id: int | None = Field(default=None, gt=0)


class CreateAssetOut(BaseModel):
    asset: AssetOut
    bars_ingested: int
    added_to_watchlist: bool
    # True iff the asset was freshly resolved+persisted by this call. False
    # means it already existed in the assets table and we skipped the
    # yfinance round-trip entirely. Lets the UI tell "just added AAPL" from
    # "linked an existing AAPL to this list".
    newly_added: bool


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

    Idempotent on the (symbol) side: posting an already-tracked symbol
    returns the existing row with ``newly_added=false`` and
    ``bars_ingested=0`` — it does NOT 409. This matters for the "Track
    new…" button on a non-default watchlist, where the user's intent
    is "put this on MY list" regardless of whether it's already tracked
    elsewhere.

    Watchlist linking is additive: we link to the default watchlist if
    ``add_to_default_watchlist`` is true AND link to ``watchlist_id`` if
    provided. Either linking path is non-fatal — failure logs and sets
    ``added_to_watchlist=false``, but the asset itself is still persisted
    (or left in place, for the already-tracked case).
    """
    try:
        result = add_asset(body.symbol)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssetServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    added_to_watchlist = False

    def _try_link(watchlist_id: int, *, context: str) -> bool:
        try:
            add_item(watchlist_id, result.asset_id)
            return True
        except ItemAlreadyExistsError:
            # Already on that watchlist — end-state matches intent.
            return True
        except WatchlistNotFoundError as exc:
            logger.info(
                "add_asset(%s): %s-watchlist link skipped (not found): %s",
                result.symbol,
                context,
                exc,
            )
            return False
        except WatchlistError as exc:
            logger.info(
                "add_asset(%s): %s-watchlist link skipped: %s",
                result.symbol,
                context,
                exc,
            )
            return False

    if body.add_to_default_watchlist:
        try:
            default = get_default_watchlist()
        except WatchlistError as exc:
            logger.info("add_asset(%s): default lookup failed: %s", result.symbol, exc)
            default = None
        if default is not None and _try_link(default.id, context="default"):
            added_to_watchlist = True

    if body.watchlist_id is not None and _try_link(
        body.watchlist_id, context="explicit"
    ):
        added_to_watchlist = True

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
        newly_added=result.newly_added,
    )
