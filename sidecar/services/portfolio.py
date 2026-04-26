"""Portfolio tracking — transactions, positions, P&L.

Domain model
------------
A ``PortfolioTransaction`` is the unit of input — every buy/sell the user
records is a single immutable row. Positions (current quantity, average
cost, realized P&L) are *derived* from the transaction history at read
time. No "positions" table exists, by design:

- **Audit trail** — every position state can be traced back to its
  contributing transactions.
- **No sync issues** — the only mutable state is the append-only log;
  there's nothing to keep in sync between two tables.
- **Cheap on read** — at single-user scale (hundreds of transactions
  max) the per-asset compute is microseconds.

Average-cost basis (not FIFO)
----------------------------
We use weighted-average cost basis: each BUY rolls into a running
average; each SELL subtracts the sold quantity at the average. This is
the convention most amateur traders use for personal P&L, and the math
is dramatically simpler than FIFO/LIFO. A future Phase could add a
``cost_method`` toggle if users want lot-level reporting.

When the cumulative quantity hits zero (or goes negative — a short
position), the average-cost resets to zero and any subsequent BUYs
start a fresh basis.

Realized vs unrealized P&L
--------------------------
- **Realized P&L** comes from SELL transactions: ``qty x (sale_price -
  avg_cost_at_sale)``.
- **Unrealized P&L** is computed against the latest close from
  ``price_points``: ``qty x (latest_close - avg_cost)``.

Both flow through ``PositionSummary`` so the UI can render them
side-by-side.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import (
    Asset,
    PortfolioTransaction,
    PricePoint,
    TransactionType,
)

logger = logging.getLogger(__name__)


class PortfolioError(ValueError):
    """Business-rule violation."""


class TransactionNotFoundError(PortfolioError):
    pass


class AssetNotFoundError(PortfolioError):
    pass


@dataclass(frozen=True)
class TransactionOut:
    id: int
    asset_id: int
    symbol: str
    asset_name: str
    transaction_type: TransactionType
    quantity: Decimal
    price_per_unit: Decimal
    transaction_date: date
    fee: Decimal
    notes: str | None
    created_at: datetime


@dataclass(frozen=True)
class PositionSummary:
    """Derived state for one open position.

    All Decimal-typed; the API layer converts to strings for the wire.
    ``current_value``/``unrealized_pl`` are None when no daily close is
    available (e.g. brand-new asset whose backfill hasn't landed yet)
    so the UI can distinguish "no signal" from a literal $0 unrealized.
    """

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


@dataclass(frozen=True)
class PortfolioSummary:
    """Top-level rollup for the portfolio page header card."""

    total_cost_basis: Decimal
    total_current_value: Decimal
    total_unrealized_pl: Decimal
    total_unrealized_pl_pct: Decimal | None
    total_realized_pl: Decimal
    open_positions: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_decimal(value: Any, *, field: str, allow_zero: bool = False) -> Decimal:
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise PortfolioError(f"{field}: not a valid decimal") from exc
    if allow_zero:
        if d < 0:
            raise PortfolioError(f"{field}: must be >= 0")
    elif d <= 0:
        raise PortfolioError(f"{field}: must be > 0")
    return d


def _parse_transaction_type(value: TransactionType | str) -> TransactionType:
    if isinstance(value, TransactionType):
        return value
    if not isinstance(value, str):
        raise PortfolioError(
            f"transaction_type: expected string, got {type(value).__name__}"
        )
    try:
        return TransactionType(value.lower())
    except ValueError as exc:
        raise PortfolioError(
            f"transaction_type: must be 'buy' or 'sell' (got {value!r})"
        ) from exc


def _txn_to_out(t: PortfolioTransaction, asset: Asset) -> TransactionOut:
    return TransactionOut(
        id=t.id,
        asset_id=t.asset_id,
        symbol=asset.symbol,
        asset_name=asset.name,
        transaction_type=TransactionType(t.transaction_type),
        quantity=t.quantity,
        price_per_unit=t.price_per_unit,
        transaction_date=t.transaction_date,
        fee=t.fee,
        notes=t.notes,
        created_at=t.created_at,
    )


# ---------------------------------------------------------------------------
# Position computation (pure-compute over a transaction list)
# ---------------------------------------------------------------------------


def _compute_position_state(
    transactions: Sequence[PortfolioTransaction],
) -> tuple[Decimal, Decimal, Decimal]:
    """Walk transactions in chronological order and return
    ``(quantity, avg_cost, realized_pl)``.

    Average-cost method:
    - BUY: ``new_avg = (qty * avg_cost + buy_qty * buy_price) / (qty + buy_qty)``
      (fees roll into cost basis: ``buy_qty * buy_price + fee``).
    - SELL: ``realized_pl += sell_qty * (sell_price - avg_cost) - fee``;
      qty decreases, avg_cost stays the same until qty hits 0.
    - Closing the position resets avg_cost to 0 so a re-open starts fresh.

    Sorts a fresh copy of the input list — caller doesn't have to
    pre-sort; the SQL layer happens to give us asc order via the
    composite index but we don't depend on that here.
    """
    sorted_txns = sorted(
        transactions, key=lambda t: (t.transaction_date, t.id)
    )

    qty = Decimal("0")
    avg_cost = Decimal("0")
    realized_pl = Decimal("0")

    for t in sorted_txns:
        ttype = TransactionType(t.transaction_type)
        if ttype == TransactionType.BUY:
            # Fees increase the effective cost basis of the bought shares.
            buy_value = t.quantity * t.price_per_unit + t.fee
            new_qty = qty + t.quantity
            if new_qty > 0:
                avg_cost = (qty * avg_cost + buy_value) / new_qty
            qty = new_qty
        else:  # SELL
            # Realized P&L from the sold portion: revenue (after fees)
            # minus cost basis at avg_cost.
            sell_revenue = t.quantity * t.price_per_unit - t.fee
            cost_of_sold = t.quantity * avg_cost
            realized_pl += sell_revenue - cost_of_sold
            qty -= t.quantity
            if qty <= 0:
                # Closed (or went short, which we model as "starting fresh").
                # Reset avg_cost so a future BUY starts a new lot.
                qty = Decimal("0")
                avg_cost = Decimal("0")

    return qty, avg_cost, realized_pl


# ---------------------------------------------------------------------------
# Public API — transactions
# ---------------------------------------------------------------------------


def list_transactions(
    *, asset_id: int | None = None
) -> list[TransactionOut]:
    """All transactions, newest first. Optional asset filter."""
    with session_scope() as s:
        stmt = (
            select(PortfolioTransaction, Asset)
            .join(Asset, Asset.id == PortfolioTransaction.asset_id)
        )
        if asset_id is not None:
            stmt = stmt.where(PortfolioTransaction.asset_id == asset_id)
        stmt = stmt.order_by(
            PortfolioTransaction.transaction_date.desc(),
            PortfolioTransaction.id.desc(),
        )
        rows = list(s.execute(stmt).all())
        return [_txn_to_out(t, asset) for t, asset in rows]


def get_transaction(transaction_id: int) -> TransactionOut:
    with session_scope() as s:
        row = s.execute(
            select(PortfolioTransaction, Asset)
            .join(Asset, Asset.id == PortfolioTransaction.asset_id)
            .where(PortfolioTransaction.id == transaction_id)
        ).one_or_none()
        if row is None:
            raise TransactionNotFoundError(
                f"transaction {transaction_id} not found"
            )
        t, asset = row
        return _txn_to_out(t, asset)


def add_transaction(
    *,
    asset_id: int,
    transaction_type: TransactionType | str,
    quantity: Any,
    price_per_unit: Any,
    transaction_date: date,
    fee: Any = Decimal("0"),
    notes: str | None = None,
) -> TransactionOut:
    """Append a buy or sell to the portfolio log."""
    ttype = _parse_transaction_type(transaction_type)
    qty = _to_decimal(quantity, field="quantity")
    price = _to_decimal(price_per_unit, field="price_per_unit")
    fee_dec = _to_decimal(fee, field="fee", allow_zero=True)
    notes_clean: str | None = None
    if notes is not None:
        notes_clean = notes.strip()
        if len(notes_clean) > 256:
            raise PortfolioError("notes: must be <= 256 chars")
        if not notes_clean:
            notes_clean = None

    with session_scope() as s:
        asset = s.get(Asset, asset_id)
        if asset is None:
            raise AssetNotFoundError(f"asset {asset_id} not found")
        txn = PortfolioTransaction(
            asset_id=asset_id,
            transaction_type=ttype.value,
            quantity=qty,
            price_per_unit=price,
            transaction_date=transaction_date,
            fee=fee_dec,
            notes=notes_clean,
        )
        s.add(txn)
        s.flush()
        return _txn_to_out(txn, asset)


def delete_transaction(transaction_id: int) -> None:
    with session_scope() as s:
        txn = s.get(PortfolioTransaction, transaction_id)
        if txn is None:
            raise TransactionNotFoundError(
                f"transaction {transaction_id} not found"
            )
        s.delete(txn)


# ---------------------------------------------------------------------------
# Public API — positions
# ---------------------------------------------------------------------------


def _latest_close_per_asset(
    session: Any, asset_ids: list[int]
) -> dict[int, PricePoint]:
    """Pull the most-recent ``PricePoint`` for each asset in one batch."""
    out: dict[int, PricePoint] = {}
    if not asset_ids:
        return out
    for aid in asset_ids:
        p = session.execute(
            select(PricePoint)
            .where(PricePoint.asset_id == aid)
            .order_by(PricePoint.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()
        if p is not None:
            out[aid] = p
    return out


def list_positions() -> list[PositionSummary]:
    """Return a derived position summary per asset that has at least one
    transaction, including closed positions (qty=0 with non-zero
    realized_pl) so users can see their realized history.
    """
    with session_scope() as s:
        # Pull every transaction joined with its asset in one query so
        # we can group by asset without re-querying.
        rows = list(
            s.execute(
                select(PortfolioTransaction, Asset).join(
                    Asset, Asset.id == PortfolioTransaction.asset_id
                )
            ).all()
        )
        if not rows:
            return []

        by_asset: dict[int, list[PortfolioTransaction]] = {}
        asset_lookup: dict[int, Asset] = {}
        for txn, asset in rows:
            by_asset.setdefault(asset.id, []).append(txn)
            asset_lookup[asset.id] = asset

        latest = _latest_close_per_asset(s, list(by_asset.keys()))
        positions: list[PositionSummary] = []
        for aid, txns in by_asset.items():
            asset = asset_lookup[aid]
            qty, avg_cost, realized_pl = _compute_position_state(txns)
            cost_basis = qty * avg_cost
            last_point = latest.get(aid)
            current_value: Decimal | None = None
            unrealized_pl: Decimal | None = None
            unrealized_pl_pct: Decimal | None = None
            if last_point is not None and qty > 0:
                current_value = qty * last_point.close
                unrealized_pl = current_value - cost_basis
                if cost_basis > 0:
                    unrealized_pl_pct = (
                        unrealized_pl / cost_basis * Decimal("100")
                    )
            positions.append(
                PositionSummary(
                    asset_id=aid,
                    symbol=asset.symbol,
                    asset_name=asset.name,
                    quantity=qty,
                    avg_cost=avg_cost,
                    cost_basis=cost_basis,
                    realized_pl=realized_pl,
                    last_close=last_point.close if last_point else None,
                    last_close_at=last_point.timestamp if last_point else None,
                    current_value=current_value,
                    unrealized_pl=unrealized_pl,
                    unrealized_pl_pct=unrealized_pl_pct,
                    transaction_count=len(txns),
                )
            )

        # Open positions first (sorted by symbol), closed positions
        # afterward — UI shows the live book up top with the realized
        # history below it.
        positions.sort(
            key=lambda p: (p.quantity == 0, p.symbol)
        )
        return positions


@dataclass(frozen=True)
class PerformancePoint:
    """One date in the portfolio-value timeseries.

    ``value`` is the sum across open positions of ``qty x daily_close``
    for that date; ``cost_basis`` is the corresponding sum of
    ``qty x avg_cost`` so the UI can plot both as side-by-side lines
    and the gap between them is unrealized P&L. ``realized_pl`` is the
    cumulative realized P&L through that date — flat-line until a sell
    happens, then steps up.
    """

    date: date
    value: Decimal
    cost_basis: Decimal
    realized_pl: Decimal


def _position_state_as_of(
    transactions: Sequence[PortfolioTransaction], cutoff_date: date
) -> dict[int, tuple[Decimal, Decimal]]:
    """Compute (quantity, avg_cost) per asset as of ``cutoff_date``.

    Walks transactions on or before the cutoff through the same
    average-cost recursion the rest of the service uses. Returns
    asset_id → (qty, avg_cost), with avg_cost == 0 for closed
    positions (which the caller can filter out).
    """
    by_asset: dict[int, list[PortfolioTransaction]] = {}
    for t in transactions:
        if t.transaction_date > cutoff_date:
            continue
        by_asset.setdefault(t.asset_id, []).append(t)

    out: dict[int, tuple[Decimal, Decimal]] = {}
    for aid, txns in by_asset.items():
        qty, avg_cost, _ = _compute_position_state(txns)
        out[aid] = (qty, avg_cost)
    return out


def _realized_pl_as_of(
    transactions: Sequence[PortfolioTransaction], cutoff_date: date
) -> Decimal:
    """Cumulative realized P&L across all assets through ``cutoff_date``."""
    by_asset: dict[int, list[PortfolioTransaction]] = {}
    for t in transactions:
        if t.transaction_date > cutoff_date:
            continue
        by_asset.setdefault(t.asset_id, []).append(t)

    total = Decimal("0")
    for txns in by_asset.values():
        _, _, realized = _compute_position_state(txns)
        total += realized
    return total


def compute_performance(lookback_days: int = 90) -> list[PerformancePoint]:
    """Daily portfolio value + cost basis + realized P&L over the window.

    Sample rate: one point per calendar date that has at least one
    daily close for any held asset within the lookback. Closed
    positions don't contribute to ``value`` (qty=0) but their realized
    P&L is included in the ``realized_pl`` rollup so users can see the
    full cumulative outcome.

    Edge cases:
    - No transactions at all → empty list (caller renders empty state).
    - First transaction is more recent than ``lookback_days`` → window
      shrinks to start at the first transaction date so the chart
      doesn't show a misleading flat-zero prefix.
    - Asset has no daily close on a given date → its contribution is
      carried forward from the most recent close before that date
      (last-observation-carried-forward), so the line stays smooth on
      weekends + missing-bar days.
    """
    if lookback_days <= 0:
        raise PortfolioError("lookback_days must be > 0")

    with session_scope() as s:
        # Pull every transaction up front — small data set, simpler than
        # repeating the filter inside the per-date loop.
        txn_rows = list(
            s.execute(
                select(PortfolioTransaction).order_by(
                    PortfolioTransaction.transaction_date.asc(),
                    PortfolioTransaction.id.asc(),
                )
            ).scalars()
        )
        if not txn_rows:
            return []

        earliest_txn_date = min(t.transaction_date for t in txn_rows)
        today_d = datetime.now(UTC).date()
        from datetime import timedelta as _td  # local import — small surface

        cutoff = max(today_d - _td(days=lookback_days), earliest_txn_date)

        asset_ids = sorted({t.asset_id for t in txn_rows})

        # Pull daily closes for each held asset since the earliest
        # transaction date — we may need a bar before the cutoff to
        # carry forward into the window's first day.
        closes_by_asset: dict[int, list[tuple[date, Decimal]]] = {}
        for aid in asset_ids:
            rows = s.execute(
                select(PricePoint.timestamp, PricePoint.close).where(
                    PricePoint.asset_id == aid,
                    PricePoint.interval == "1d",
                    PricePoint.timestamp
                    >= datetime.combine(
                        earliest_txn_date, datetime.min.time(), UTC
                    ),
                )
            ).all()
            # Dedup on date, then sort.
            by_date: dict[date, Decimal] = {}
            for ts, close in rows:
                by_date[ts.date()] = close
            closes_by_asset[aid] = sorted(by_date.items())

    # Build the chart's date axis: every distinct date that has at
    # least one daily close inside the window. We iterate calendar days
    # and pick those that have any close — keeps the axis tight without
    # gaps for never-trading-days.
    candidate_dates: set[date] = set()
    for closes in closes_by_asset.values():
        for d, _ in closes:
            if d >= cutoff and d <= today_d:
                candidate_dates.add(d)
    if not candidate_dates:
        return []
    sorted_dates = sorted(candidate_dates)

    points: list[PerformancePoint] = []
    for d in sorted_dates:
        positions = _position_state_as_of(txn_rows, d)
        total_value = Decimal("0")
        total_cost = Decimal("0")
        for aid, (qty, avg_cost) in positions.items():
            if qty <= 0:
                continue
            close = _close_on_or_before(closes_by_asset[aid], d)
            if close is None:
                continue
            total_value += qty * close
            total_cost += qty * avg_cost
        realized = _realized_pl_as_of(txn_rows, d)
        points.append(
            PerformancePoint(
                date=d,
                value=total_value,
                cost_basis=total_cost,
                realized_pl=realized,
            )
        )
    return points


def _close_on_or_before(
    closes: list[tuple[date, Decimal]], target: date
) -> Decimal | None:
    """Last-observation-carried-forward lookup for daily closes.

    Returns the close on ``target`` if available, else the most recent
    close strictly before. None when no close is on file at all (e.g.
    asset added after the first portfolio date).
    """
    if not closes:
        return None
    # closes is sorted asc — walk backwards to find the latest entry
    # not after `target`.
    for d, c in reversed(closes):
        if d <= target:
            return c
    return None


def compute_summary() -> PortfolioSummary:
    """Roll up portfolio-wide totals from the per-position list.

    Open positions only contribute to ``total_current_value`` and
    ``total_cost_basis``; realized P&L includes every closed lot.
    """
    positions = list_positions()
    cost = Decimal("0")
    value = Decimal("0")
    unrealized = Decimal("0")
    realized = Decimal("0")
    open_count = 0
    for p in positions:
        realized += p.realized_pl
        if p.quantity > 0:
            open_count += 1
            cost += p.cost_basis
            if p.current_value is not None and p.unrealized_pl is not None:
                value += p.current_value
                unrealized += p.unrealized_pl
    pct: Decimal | None = (
        unrealized / cost * Decimal("100") if cost > 0 else None
    )
    return PortfolioSummary(
        total_cost_basis=cost,
        total_current_value=value,
        total_unrealized_pl=unrealized,
        total_unrealized_pl_pct=pct,
        total_realized_pl=realized,
        open_positions=open_count,
    )


# ---------------------------------------------------------------------------
# Convenience for tests / scripting
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:  # tiny indirection so tests can freeze if needed
    return datetime.now(UTC)
