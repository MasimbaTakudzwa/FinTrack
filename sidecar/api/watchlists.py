"""Watchlists CRUD + item management endpoints.

The service layer (`sidecar.services.watchlists`) does the heavy lifting —
this module is a thin translation to HTTP.

Error-code mapping:
- 404 — watchlist / asset / item not found
- 409 — duplicate watchlist name, or asset already on the list
- 400 — other validation errors (empty name, deleting default, reorder mismatch)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sidecar.services.watchlists import (
    AssetNotFoundError,
    CannotDeleteDefaultError,
    ItemAlreadyExistsError,
    ItemNotFoundError,
    WatchlistDetail,
    WatchlistError,
    WatchlistItemDetail,
    WatchlistNameConflictError,
    WatchlistNotFoundError,
    WatchlistSummary,
    add_item,
    create_watchlist,
    delete_watchlist,
    get_default_watchlist,
    get_watchlist,
    list_watchlists,
    remove_item,
    rename_watchlist,
    reorder_items,
    set_default,
)

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WatchlistSummaryOut(BaseModel):
    id: int
    name: str
    is_default: bool
    item_count: int


class WatchlistItemOut(BaseModel):
    asset_id: int
    symbol: str
    name: str
    asset_type: str
    position: int


class WatchlistDetailOut(BaseModel):
    id: int
    name: str
    is_default: bool
    items: list[WatchlistItemOut]


class WatchlistListOut(BaseModel):
    watchlists: list[WatchlistSummaryOut]


class CreateWatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    is_default: bool = False


class UpdateWatchlistIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_default: bool | None = None


class AddItemIn(BaseModel):
    asset_id: int


class ReorderIn(BaseModel):
    asset_ids: list[int]


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


def _summary_out(w: WatchlistSummary) -> WatchlistSummaryOut:
    return WatchlistSummaryOut(
        id=w.id,
        name=w.name,
        is_default=w.is_default,
        item_count=w.item_count,
    )


def _item_out(i: WatchlistItemDetail) -> WatchlistItemOut:
    return WatchlistItemOut(
        asset_id=i.asset_id,
        symbol=i.symbol,
        name=i.name,
        asset_type=i.asset_type,
        position=i.position,
    )


def _detail_out(d: WatchlistDetail) -> WatchlistDetailOut:
    return WatchlistDetailOut(
        id=d.id,
        name=d.name,
        is_default=d.is_default,
        items=[_item_out(i) for i in d.items],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=WatchlistListOut)
def list_watchlists_route() -> WatchlistListOut:
    return WatchlistListOut(
        watchlists=[_summary_out(w) for w in list_watchlists()]
    )


@router.get("/default/", response_model=WatchlistDetailOut)
def get_default_watchlist_route() -> WatchlistDetailOut:
    d = get_default_watchlist()
    if d is None:
        raise HTTPException(
            status_code=404, detail="no default watchlist exists"
        )
    return _detail_out(d)


@router.post("/", response_model=WatchlistSummaryOut, status_code=201)
def create_watchlist_route(body: CreateWatchlistIn) -> WatchlistSummaryOut:
    try:
        return _summary_out(
            create_watchlist(body.name, is_default=body.is_default)
        )
    except WatchlistNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WatchlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{watchlist_id}/", response_model=WatchlistDetailOut)
def get_watchlist_route(watchlist_id: int) -> WatchlistDetailOut:
    try:
        return _detail_out(get_watchlist(watchlist_id))
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{watchlist_id}/", response_model=WatchlistSummaryOut)
def update_watchlist_route(
    watchlist_id: int, body: UpdateWatchlistIn
) -> WatchlistSummaryOut:
    if body.name is None and body.is_default is None:
        raise HTTPException(
            status_code=400,
            detail="at least one of name or is_default must be provided",
        )
    try:
        summary: WatchlistSummary | None = None
        if body.name is not None:
            summary = rename_watchlist(watchlist_id, body.name)
        if body.is_default is True:
            summary = set_default(watchlist_id)
        elif body.is_default is False:
            # Explicit un-default — only allowed if it isn't already default.
            # We don't support "clear the default entirely" via this route.
            raise HTTPException(
                status_code=400,
                detail=(
                    "to change default, set is_default=true on another watchlist; "
                    "cannot un-default without promoting a replacement"
                ),
            )
        assert summary is not None  # mypy: one of the branches ran
        return _summary_out(summary)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WatchlistNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WatchlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{watchlist_id}/", status_code=204)
def delete_watchlist_route(watchlist_id: int) -> None:
    try:
        delete_watchlist(watchlist_id)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CannotDeleteDefaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{watchlist_id}/items/", response_model=WatchlistItemOut, status_code=201)
def add_item_route(watchlist_id: int, body: AddItemIn) -> WatchlistItemOut:
    try:
        return _item_out(add_item(watchlist_id, body.asset_id))
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ItemAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{watchlist_id}/items/{asset_id}/", status_code=204)
def remove_item_route(watchlist_id: int, asset_id: int) -> None:
    try:
        remove_item(watchlist_id, asset_id)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{watchlist_id}/items/reorder", status_code=204)
def reorder_items_route(watchlist_id: int, body: ReorderIn) -> None:
    try:
        reorder_items(watchlist_id, body.asset_ids)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WatchlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
