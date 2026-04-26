"""Tests for the portfolio service — average-cost math + transaction CRUD.

The position-computation tests are the meatiest because the average-cost
formula is the easiest place to introduce a subtle bug. Hand-built
transaction sequences with known answers keep us honest.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sidecar.db.engine import session_scope
from sidecar.db.models import (
    Asset,
    AssetType,
    PortfolioTransaction,
    PricePoint,
    TransactionType,
)
from sidecar.services import portfolio as svc


def _seed_asset(symbol: str = "AAPL", name: str = "Apple Inc.") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=name, asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _add_close(asset_id: int, close: Decimal) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=datetime.now(UTC),
                interval="1d",
                open=close,
                high=close,
                low=close,
                close=close,
                volume=0,
            )
        )


# ---------------------------------------------------------------------------
# add / get / list / delete
# ---------------------------------------------------------------------------


def test_add_transaction_happy_path(isolated_db: Path) -> None:
    aid = _seed_asset()
    t = svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("10"),
        price_per_unit=Decimal("150"),
        transaction_date=date(2026, 4, 1),
    )
    assert t.transaction_type == TransactionType.BUY
    assert t.quantity == Decimal("10")
    assert t.price_per_unit == Decimal("150")
    assert t.fee == Decimal("0")


def test_add_transaction_validates_quantity(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.PortfolioError):
        svc.add_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=Decimal("0"),
            price_per_unit=Decimal("150"),
            transaction_date=date(2026, 4, 1),
        )


def test_add_transaction_validates_price(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.PortfolioError):
        svc.add_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=Decimal("1"),
            price_per_unit=Decimal("-1"),
            transaction_date=date(2026, 4, 1),
        )


def test_add_transaction_unknown_asset_raises(isolated_db: Path) -> None:
    with pytest.raises(svc.AssetNotFoundError):
        svc.add_transaction(
            asset_id=9999,
            transaction_type="buy",
            quantity=Decimal("1"),
            price_per_unit=Decimal("100"),
            transaction_date=date(2026, 4, 1),
        )


def test_add_transaction_unknown_type_raises(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.PortfolioError):
        svc.add_transaction(
            asset_id=aid,
            transaction_type="dividend",  # not yet supported
            quantity=Decimal("1"),
            price_per_unit=Decimal("100"),
            transaction_date=date(2026, 4, 1),
        )


def test_list_transactions_newest_first(isolated_db: Path) -> None:
    aid = _seed_asset()
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("5"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 3, 1),
    )
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("5"),
        price_per_unit=Decimal("110"),
        transaction_date=date(2026, 4, 1),
    )
    txns = svc.list_transactions()
    assert [t.transaction_date for t in txns] == [
        date(2026, 4, 1),
        date(2026, 3, 1),
    ]


def test_list_transactions_filter_by_asset(isolated_db: Path) -> None:
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT", "Microsoft")
    svc.add_transaction(
        asset_id=aapl,
        transaction_type="buy",
        quantity=Decimal("1"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 4, 1),
    )
    svc.add_transaction(
        asset_id=msft,
        transaction_type="buy",
        quantity=Decimal("2"),
        price_per_unit=Decimal("200"),
        transaction_date=date(2026, 4, 2),
    )
    aapl_txns = svc.list_transactions(asset_id=aapl)
    assert len(aapl_txns) == 1
    assert aapl_txns[0].symbol == "AAPL"


def test_get_transaction_404_when_missing(isolated_db: Path) -> None:
    with pytest.raises(svc.TransactionNotFoundError):
        svc.get_transaction(9999)


def test_delete_transaction_removes_row(isolated_db: Path) -> None:
    aid = _seed_asset()
    t = svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("1"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 4, 1),
    )
    svc.delete_transaction(t.id)
    with pytest.raises(svc.TransactionNotFoundError):
        svc.get_transaction(t.id)


def test_delete_transaction_404_when_missing(isolated_db: Path) -> None:
    with pytest.raises(svc.TransactionNotFoundError):
        svc.delete_transaction(9999)


# ---------------------------------------------------------------------------
# Position computation — pure-compute via _compute_position_state
# ---------------------------------------------------------------------------


def _txn(
    asset_id: int,
    type_: str,
    qty: str,
    price: str,
    d: date,
    fee: str = "0",
) -> PortfolioTransaction:
    return PortfolioTransaction(
        asset_id=asset_id,
        transaction_type=type_,
        quantity=Decimal(qty),
        price_per_unit=Decimal(price),
        transaction_date=d,
        fee=Decimal(fee),
    )


def test_compute_position_single_buy() -> None:
    txns = [_txn(1, "buy", "10", "100", date(2026, 4, 1))]
    qty, avg, realized = svc._compute_position_state(txns)
    assert qty == Decimal("10")
    assert avg == Decimal("100")
    assert realized == Decimal("0")


def test_compute_position_two_buys_weighted_average() -> None:
    """Buy 10 @ 100, then 10 @ 120 → avg cost = (1000 + 1200) / 20 = 110."""
    txns = [
        _txn(1, "buy", "10", "100", date(2026, 4, 1)),
        _txn(1, "buy", "10", "120", date(2026, 4, 2)),
    ]
    qty, avg, realized = svc._compute_position_state(txns)
    assert qty == Decimal("20")
    assert avg == Decimal("110")
    assert realized == Decimal("0")


def test_compute_position_buy_then_partial_sell() -> None:
    """Buy 10 @ 100, sell 4 @ 130 → realized = 4 * (130 - 100) = 120; qty=6."""
    txns = [
        _txn(1, "buy", "10", "100", date(2026, 4, 1)),
        _txn(1, "sell", "4", "130", date(2026, 4, 2)),
    ]
    qty, avg, realized = svc._compute_position_state(txns)
    assert qty == Decimal("6")
    assert avg == Decimal("100")  # avg cost unchanged on sell
    assert realized == Decimal("120")


def test_compute_position_full_close_resets_avg() -> None:
    """Buy 10 @ 100, sell 10 @ 130, buy 5 @ 50 → second buy starts fresh."""
    txns = [
        _txn(1, "buy", "10", "100", date(2026, 4, 1)),
        _txn(1, "sell", "10", "130", date(2026, 4, 2)),
        _txn(1, "buy", "5", "50", date(2026, 4, 3)),
    ]
    qty, avg, realized = svc._compute_position_state(txns)
    assert qty == Decimal("5")
    assert avg == Decimal("50")  # avg cost reset by the close
    assert realized == Decimal("300")  # 10 * (130 - 100)


def test_compute_position_fees_increase_cost_basis() -> None:
    """A buy with a $10 fee should bump the per-share cost above the
    quoted price."""
    txns = [_txn(1, "buy", "10", "100", date(2026, 4, 1), fee="10")]
    qty, avg, _ = svc._compute_position_state(txns)
    assert qty == Decimal("10")
    # avg = (10*100 + 10) / 10 = 101.0
    assert avg == Decimal("101")


def test_compute_position_fees_reduce_realized_on_sell() -> None:
    """A sell with a $5 fee reduces realized P&L by that fee."""
    txns = [
        _txn(1, "buy", "10", "100", date(2026, 4, 1)),
        _txn(1, "sell", "10", "120", date(2026, 4, 2), fee="5"),
    ]
    qty, _, realized = svc._compute_position_state(txns)
    assert qty == Decimal("0")
    # realized = 10*120 - 5 - 10*100 = 1195 - 1000 = 195
    assert realized == Decimal("195")


def test_compute_position_sorts_by_date() -> None:
    """Out-of-order input still produces correct state — the sort is on
    transaction_date, with id as a tiebreaker for same-day transactions."""
    txns = [
        _txn(1, "sell", "4", "130", date(2026, 4, 2)),
        _txn(1, "buy", "10", "100", date(2026, 4, 1)),
    ]
    qty, avg, realized = svc._compute_position_state(txns)
    assert qty == Decimal("6")
    assert avg == Decimal("100")
    assert realized == Decimal("120")


# ---------------------------------------------------------------------------
# list_positions / compute_summary integration
# ---------------------------------------------------------------------------


def test_list_positions_unrealized_pl_against_latest_close(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("10"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 4, 1),
    )
    _add_close(aid, Decimal("120"))

    positions = svc.list_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.quantity == Decimal("10")
    assert p.cost_basis == Decimal("1000")
    assert p.current_value == Decimal("1200")
    assert p.unrealized_pl == Decimal("200")
    assert p.unrealized_pl_pct == Decimal("20")


def test_list_positions_no_close_yields_none_unrealized(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("5"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 4, 1),
    )
    positions = svc.list_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.current_value is None
    assert p.unrealized_pl is None


def test_list_positions_orders_open_before_closed(isolated_db: Path) -> None:
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT", "Microsoft")
    # AAPL: open
    svc.add_transaction(
        asset_id=aapl,
        transaction_type="buy",
        quantity=Decimal("5"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 4, 1),
    )
    # MSFT: closed
    svc.add_transaction(
        asset_id=msft,
        transaction_type="buy",
        quantity=Decimal("5"),
        price_per_unit=Decimal("200"),
        transaction_date=date(2026, 4, 1),
    )
    svc.add_transaction(
        asset_id=msft,
        transaction_type="sell",
        quantity=Decimal("5"),
        price_per_unit=Decimal("220"),
        transaction_date=date(2026, 4, 2),
    )

    positions = svc.list_positions()
    assert len(positions) == 2
    # Open first (AAPL), closed second (MSFT)
    assert positions[0].symbol == "AAPL"
    assert positions[0].quantity > 0
    assert positions[1].symbol == "MSFT"
    assert positions[1].quantity == Decimal("0")
    assert positions[1].realized_pl == Decimal("100")  # 5 * 20


def test_compute_summary_aggregates(isolated_db: Path) -> None:
    aapl = _seed_asset("AAPL")
    svc.add_transaction(
        asset_id=aapl,
        transaction_type="buy",
        quantity=Decimal("10"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 4, 1),
    )
    _add_close(aapl, Decimal("110"))

    s = svc.compute_summary()
    assert s.open_positions == 1
    assert s.total_cost_basis == Decimal("1000")
    assert s.total_current_value == Decimal("1100")
    assert s.total_unrealized_pl == Decimal("100")
    assert s.total_realized_pl == Decimal("0")


def test_compute_summary_empty_portfolio(isolated_db: Path) -> None:
    s = svc.compute_summary()
    assert s.open_positions == 0
    assert s.total_cost_basis == Decimal("0")
    assert s.total_current_value == Decimal("0")
    assert s.total_realized_pl == Decimal("0")


def test_compute_performance_no_transactions_returns_empty(
    isolated_db: Path,
) -> None:
    assert svc.compute_performance(lookback_days=90) == []


def test_compute_performance_clamps_window_to_first_transaction(
    isolated_db: Path,
) -> None:
    """A 365-day lookback when the first transaction is 5 days ago should
    only emit points within the held period — not 360 days of zero."""
    aid = _seed_asset()
    today = date.today()
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("10"),
        price_per_unit=Decimal("100"),
        transaction_date=today - timedelta(days=2),
    )
    # Seed a couple of recent closes so there's something to plot.
    with session_scope() as s:
        for offset in range(3):
            d = today - timedelta(days=offset)
            s.add(
                PricePoint(
                    asset_id=aid,
                    timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
                    interval="1d",
                    open=Decimal("110"),
                    high=Decimal("110"),
                    low=Decimal("110"),
                    close=Decimal("110"),
                    volume=0,
                )
            )

    points = svc.compute_performance(lookback_days=365)
    # Three close-bearing dates are inside the held window; the
    # clamped start date drops the empty pre-buy ones.
    assert all(p.date >= today - timedelta(days=3) for p in points)
    assert len(points) >= 1


def test_compute_performance_carries_close_forward(isolated_db: Path) -> None:
    """A weekend gap shouldn't punch a hole in the line — the most
    recent close is carried forward."""
    aid = _seed_asset()
    today = date.today()
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("10"),
        price_per_unit=Decimal("100"),
        transaction_date=today - timedelta(days=10),
    )
    # Closes on day -5 and day -3 only. Day -4 has no close.
    with session_scope() as s:
        for offset in (5, 3):
            d = today - timedelta(days=offset)
            s.add(
                PricePoint(
                    asset_id=aid,
                    timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
                    interval="1d",
                    open=Decimal("100"),
                    high=Decimal("100"),
                    low=Decimal("100"),
                    close=Decimal("100"),
                    volume=0,
                )
            )

    points = svc.compute_performance(lookback_days=20)
    # Two emitted points — one per close date.
    assert len(points) == 2
    # Both have value = 10 * 100 = 1000.
    for p in points:
        assert p.value == Decimal("1000")


def test_compute_performance_open_and_closed_split(isolated_db: Path) -> None:
    """After a sell that closes the position, ``value`` drops to 0 but
    ``realized_pl`` reflects the gain."""
    aid = _seed_asset()
    today = date.today()
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("10"),
        price_per_unit=Decimal("100"),
        transaction_date=today - timedelta(days=4),
    )
    svc.add_transaction(
        asset_id=aid,
        transaction_type="sell",
        quantity=Decimal("10"),
        price_per_unit=Decimal("120"),
        transaction_date=today - timedelta(days=1),
    )
    with session_scope() as s:
        for offset in (3, 2, 1, 0):
            d = today - timedelta(days=offset)
            s.add(
                PricePoint(
                    asset_id=aid,
                    timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
                    interval="1d",
                    open=Decimal("110"),
                    high=Decimal("110"),
                    low=Decimal("110"),
                    close=Decimal("110"),
                    volume=0,
                )
            )

    points = svc.compute_performance(lookback_days=14)
    by_date = {p.date: p for p in points}
    # On day -3 the position is open at qty=10, value = 10*110 = 1100.
    pre_sell = by_date.get(today - timedelta(days=3))
    assert pre_sell is not None
    assert pre_sell.value == Decimal("1100")
    assert pre_sell.realized_pl == Decimal("0")
    # On day 0 (after the sell) the position is closed → value=0 and
    # realized_pl reflects the 10 * (120 - 100) = 200 gain.
    post_sell = by_date.get(today)
    assert post_sell is not None
    assert post_sell.value == Decimal("0")
    assert post_sell.realized_pl == Decimal("200")


def test_compute_performance_invalid_lookback_raises(isolated_db: Path) -> None:
    with pytest.raises(svc.PortfolioError):
        svc.compute_performance(lookback_days=0)


def test_transactions_cascade_when_asset_deleted(isolated_db: Path) -> None:
    aid = _seed_asset()
    svc.add_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=Decimal("5"),
        price_per_unit=Decimal("100"),
        transaction_date=date(2026, 4, 1),
    )
    with session_scope() as s:
        s.execute(Asset.__table__.delete().where(Asset.id == aid))
    assert svc.list_transactions() == []
