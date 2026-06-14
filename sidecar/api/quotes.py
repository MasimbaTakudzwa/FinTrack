from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from sidecar.db.models import AssetType
from sidecar.services.quotes import (
    Quote,
    SymbolNotFoundError,
    get_quote,
    get_quotes,
)

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


class QuoteOut(BaseModel):
    symbol: str
    name: str
    asset_type: AssetType
    last_price: Decimal | None
    last_at: datetime | None
    previous_close: Decimal | None
    change: Decimal | None
    change_pct: float | None

    @classmethod
    def from_quote(cls, q: Quote) -> QuoteOut:
        return cls(
            symbol=q.symbol,
            name=q.name,
            asset_type=q.asset_type,
            last_price=q.last_price,
            last_at=q.last_at,
            previous_close=q.previous_close,
            change=q.change,
            change_pct=q.change_pct,
        )


class QuoteListOut(BaseModel):
    count: int
    quotes: list[QuoteOut]


@router.get("/", response_model=QuoteListOut)
def list_quotes(
    symbols: Annotated[str | None, Query()] = None,
    active_only: Annotated[bool, Query()] = True,
) -> QuoteListOut:
    """Batch quotes. Pass ``?symbols=AAPL,MSFT`` to scope, else all active."""
    requested = (
        [s for s in symbols.split(",") if s.strip()] if symbols is not None else None
    )
    quotes = [QuoteOut.from_quote(q) for q in get_quotes(requested, active_only=active_only)]
    return QuoteListOut(count=len(quotes), quotes=quotes)


@router.get("/{symbol}/", response_model=QuoteOut)
def single_quote(symbol: str) -> QuoteOut:
    try:
        return QuoteOut.from_quote(get_quote(symbol))
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
