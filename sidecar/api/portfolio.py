"""Portfolio endpoints — transaction CRUD + derived positions / summary.

Design split: ``transactions`` is the input log, ``positions`` and
``summary`` are derived views computed at read time. See
:mod:`sidecar.services.portfolio` for the rationale and average-cost
math.

Error mapping:
- 404 — asset / transaction not found
- 400 — validation (bad quantity, bad date, etc.)
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sidecar.services.portfolio import (
    AssetNotFoundError,
    PerformancePoint,
    PortfolioError,
    TransactionNotFoundError,
    TransactionOut,
    add_transaction,
    compute_performance,
    compute_summary,
    delete_transaction,
    get_transaction,
    list_positions,
    list_transactions,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


TransactionTypeLiteral = Literal["buy", "sell"]


class TransactionOutModel(BaseModel):
    id: int
    asset_id: int
    symbol: str
    asset_name: str
    transaction_type: TransactionTypeLiteral
    quantity: Decimal
    price_per_unit: Decimal
    transaction_date: date
    fee: Decimal
    notes: str | None
    created_at: datetime


class TransactionListOut(BaseModel):
    count: int
    transactions: list[TransactionOutModel]


class CreateTransactionIn(BaseModel):
    asset_id: int
    transaction_type: TransactionTypeLiteral
    # No ``gt=0`` here — the service enforces metric-specific bounds
    # so the API surfaces 400 with an informative message instead of a
    # half-clear 422 (matches the alerts pattern).
    quantity: Decimal
    price_per_unit: Decimal
    transaction_date: date
    fee: Decimal = Decimal("0")
    notes: str | None = Field(default=None, max_length=256)


class PositionOutModel(BaseModel):
    asset_id: int
    symbol: str
    asset_name: str
    quantity: Decimal
    avg_cost: Decimal
    cost_basis: Decimal
    realized_pl: Decimal
    last_close: Decimal | None
    last_close_at: datetime | None
    current_value: Decimal | None
    unrealized_pl: Decimal | None
    unrealized_pl_pct: Decimal | None
    transaction_count: int


class PositionsOut(BaseModel):
    count: int
    positions: list[PositionOutModel]


class PortfolioSummaryOut(BaseModel):
    total_cost_basis: Decimal
    total_current_value: Decimal
    total_unrealized_pl: Decimal
    total_unrealized_pl_pct: Decimal | None
    total_realized_pl: Decimal
    open_positions: int


class PerformancePointModel(BaseModel):
    date: date
    value: Decimal
    cost_basis: Decimal
    realized_pl: Decimal


class PerformanceOut(BaseModel):
    """Daily portfolio-value timeseries — drives the Performance chart on
    the Portfolio page. Last-observation-carried-forward is applied per
    asset so weekends + missing-bar days don't punch holes in the line."""

    lookback_days: int
    points: list[PerformancePointModel]


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _txn_to_model(t: TransactionOut) -> TransactionOutModel:
    return TransactionOutModel(
        id=t.id,
        asset_id=t.asset_id,
        symbol=t.symbol,
        asset_name=t.asset_name,
        transaction_type=t.transaction_type.value,
        quantity=t.quantity,
        price_per_unit=t.price_per_unit,
        transaction_date=t.transaction_date,
        fee=t.fee,
        notes=t.notes,
        created_at=t.created_at,
    )


# ---------------------------------------------------------------------------
# Routes — transactions
# ---------------------------------------------------------------------------


@router.get("/transactions/", response_model=TransactionListOut)
def list_transactions_route(
    asset_id: Annotated[int | None, Query()] = None,
) -> TransactionListOut:
    txns = list_transactions(asset_id=asset_id)
    return TransactionListOut(
        count=len(txns), transactions=[_txn_to_model(t) for t in txns]
    )


@router.get("/transactions/export.csv")
def export_transactions_csv() -> StreamingResponse:
    """Stream every transaction as CSV for backup / audit.

    Columns chosen for round-trip compatibility with a future ``import``
    endpoint: ``transaction_date, symbol, transaction_type, quantity,
    price_per_unit, fee, notes``. The ``id`` and ``created_at`` columns
    are omitted because they're regenerated on import (``id`` is a
    fresh PK; ``created_at`` is set at insert time).

    Empty portfolio still returns the header row so the file is
    well-formed and consumers don't have to special-case zero rows.
    """
    txns = list_transactions()

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "transaction_date",
            "symbol",
            "transaction_type",
            "quantity",
            "price_per_unit",
            "fee",
            "notes",
        ]
    )
    for t in txns:
        writer.writerow(
            [
                t.transaction_date.isoformat(),
                t.symbol,
                t.transaction_type.value,
                # Decimals serialise via str() which gives a clean
                # canonical form (no scientific notation at our scale).
                str(t.quantity),
                str(t.price_per_unit),
                str(t.fee),
                t.notes or "",
            ]
        )
    body = buffer.getvalue()

    filename = f"fintrack-transactions-{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/transactions/{transaction_id}/", response_model=TransactionOutModel)
def get_transaction_route(transaction_id: int) -> TransactionOutModel:
    try:
        return _txn_to_model(get_transaction(transaction_id))
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/transactions/", response_model=TransactionOutModel, status_code=201
)
def create_transaction_route(body: CreateTransactionIn) -> TransactionOutModel:
    try:
        return _txn_to_model(
            add_transaction(
                asset_id=body.asset_id,
                transaction_type=body.transaction_type,
                quantity=body.quantity,
                price_per_unit=body.price_per_unit,
                transaction_date=body.transaction_date,
                fee=body.fee,
                notes=body.notes,
            )
        )
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/transactions/{transaction_id}/", status_code=204)
def delete_transaction_route(transaction_id: int) -> None:
    try:
        delete_transaction(transaction_id)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Routes — positions / summary
# ---------------------------------------------------------------------------


@router.get("/positions/", response_model=PositionsOut)
def list_positions_route() -> PositionsOut:
    positions = list_positions()
    return PositionsOut(
        count=len(positions),
        positions=[
            PositionOutModel(
                asset_id=p.asset_id,
                symbol=p.symbol,
                asset_name=p.asset_name,
                quantity=p.quantity,
                avg_cost=p.avg_cost,
                cost_basis=p.cost_basis,
                realized_pl=p.realized_pl,
                last_close=p.last_close,
                last_close_at=p.last_close_at,
                current_value=p.current_value,
                unrealized_pl=p.unrealized_pl,
                unrealized_pl_pct=p.unrealized_pl_pct,
                transaction_count=p.transaction_count,
            )
            for p in positions
        ],
    )


@router.get("/performance/", response_model=PerformanceOut)
def performance_route(
    lookback_days: Annotated[int, Query(ge=1, le=3650)] = 90,
) -> PerformanceOut:
    """Daily portfolio-value timeseries over the last ``lookback_days``."""
    points = compute_performance(lookback_days=lookback_days)
    return PerformanceOut(
        lookback_days=lookback_days,
        points=[_perf_to_model(p) for p in points],
    )


def _perf_to_model(p: PerformancePoint) -> PerformancePointModel:
    return PerformancePointModel(
        date=p.date,
        value=p.value,
        cost_basis=p.cost_basis,
        realized_pl=p.realized_pl,
    )


@router.get("/summary/", response_model=PortfolioSummaryOut)
def summary_route() -> PortfolioSummaryOut:
    s = compute_summary()
    return PortfolioSummaryOut(
        total_cost_basis=s.total_cost_basis,
        total_current_value=s.total_current_value,
        total_unrealized_pl=s.total_unrealized_pl,
        total_unrealized_pl_pct=s.total_unrealized_pl_pct,
        total_realized_pl=s.total_realized_pl,
        open_positions=s.open_positions,
    )
