from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.services import quotes as svc


def _seed_asset(symbol: str = "AAPL") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=f"{symbol} Inc.", asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _add_daily(asset_id: int, ts: datetime, close: str, *, open_: str | None = None) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=ts,
                interval="1d",
                open=Decimal(open_ or close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=1_000,
            )
        )


def _add_intraday(asset_id: int, close: str, ts: datetime) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=ts,
                interval="5m",
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=0,
            )
        )


def test_day_change_uses_previous_session_close(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_daily(aid, datetime(2026, 6, 11, tzinfo=UTC), "100")  # previous session
    _add_daily(aid, datetime(2026, 6, 12, tzinfo=UTC), "110")  # current session
    q = svc.get_quote("AAPL")
    assert q.previous_close == Decimal("100")
    assert q.last_price == Decimal("110")
    assert q.change == Decimal("10")
    assert q.change_pct == pytest.approx(10.0)


def test_day_change_prefers_live_intraday_price(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_daily(aid, datetime(2026, 6, 11, tzinfo=UTC), "100")
    _add_daily(aid, datetime(2026, 6, 12, tzinfo=UTC), "110")
    # A live intraday tick above the daily close should drive last_price.
    _add_intraday(aid, "115", datetime(2026, 6, 12, 15, 0, tzinfo=UTC))
    q = svc.get_quote("AAPL")
    assert q.previous_close == Decimal("100")
    assert q.last_price == Decimal("115")
    assert q.change == Decimal("15")
    assert q.change_pct == pytest.approx(15.0)


def test_day_change_single_session_falls_back_to_open(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_daily(aid, datetime(2026, 6, 12, tzinfo=UTC), "110", open_="100")
    q = svc.get_quote("AAPL")
    assert q.previous_close == Decimal("100")  # the only bar's open
    assert q.last_price == Decimal("110")
    assert q.change_pct == pytest.approx(10.0)


def test_quote_no_data_is_all_none(isolated_db: Path) -> None:
    _seed_asset()
    q = svc.get_quote("AAPL")
    assert q.last_price is None
    assert q.previous_close is None
    assert q.change is None
    assert q.change_pct is None


def test_intraday_only_does_not_use_daily_for_previous_close(isolated_db: Path) -> None:
    # Only 5m bars exist (no 1d yet) → last_price from intraday, no previous
    # close, so change is null (not a misleading intraday delta).
    aid = _seed_asset()
    _add_intraday(aid, "200", datetime(2026, 6, 12, 14, 0, tzinfo=UTC))
    _add_intraday(aid, "205", datetime(2026, 6, 12, 15, 0, tzinfo=UTC))
    q = svc.get_quote("AAPL")
    assert q.last_price == Decimal("205")
    assert q.previous_close is None
    assert q.change_pct is None


def test_get_quote_unknown_symbol(isolated_db: Path) -> None:
    with pytest.raises(svc.SymbolNotFoundError):
        svc.get_quote("NOPE")


def test_get_quotes_orders_by_requested_symbols(isolated_db: Path) -> None:
    _seed_asset("AAPL")
    _seed_asset("MSFT")
    _seed_asset("NVDA")
    quotes = svc.get_quotes(["NVDA", "AAPL"])
    assert [q.symbol for q in quotes] == ["NVDA", "AAPL"]
