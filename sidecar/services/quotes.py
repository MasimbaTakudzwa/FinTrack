"""Quote service — the single source of truth for "day change %".

Before this, three screens each computed "change" differently (last two 5-min
bars, a 24h intraday anchor, window first-vs-last) and none of them was a real
*day* change, because intraday bars over a single session have no previous
session close to compare against.

``get_quotes`` computes one consistent figure per asset from the data Phase 2
already ingests — daily bars stored in ``price_points`` with ``interval="1d"``
(written by the ``ingest_prices_daily`` job) plus the latest intraday
(``interval="5m"``) bar:

* ``previous_close`` — the close of the prior completed session (the second
  most-recent ``"1d"`` bar). With only one daily bar, falls back to that bar's
  open so we still show an intra-session change.
* ``last_price`` — the latest intraday close when available (so the figure is
  live during a session), else the latest daily close.
* ``change`` / ``change_pct`` — ``last_price`` vs. ``previous_close``.

This lives in the service layer (no FastAPI dependency) so it can be reused by
scripts/tests and by the alerts engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint

INTRADAY_INTERVAL = "5m"
DAILY_INTERVAL = "1d"


class QuoteError(ValueError):
    """Base for quote-service errors."""


class SymbolNotFoundError(QuoteError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    asset_type: AssetType
    last_price: Decimal | None
    last_at: datetime | None
    previous_close: Decimal | None
    change: Decimal | None
    change_pct: float | None


def _latest_intraday(session: Session, asset_id: int) -> PricePoint | None:
    return session.execute(
        select(PricePoint)
        .where(
            PricePoint.asset_id == asset_id,
            PricePoint.interval == INTRADAY_INTERVAL,
        )
        .order_by(PricePoint.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()


def _recent_daily(session: Session, asset_id: int) -> list[PricePoint]:
    return list(
        session.execute(
            select(PricePoint)
            .where(
                PricePoint.asset_id == asset_id,
                PricePoint.interval == DAILY_INTERVAL,
            )
            .order_by(PricePoint.timestamp.desc())
            .limit(2)
        ).scalars()
    )


def _build_quote(session: Session, asset: Asset) -> Quote:
    daily = _recent_daily(session, asset.id)
    intraday = _latest_intraday(session, asset.id)

    previous_close: Decimal | None
    if len(daily) >= 2:
        previous_close = daily[1].close
    elif len(daily) == 1:
        previous_close = daily[0].open
    else:
        previous_close = None

    last_price: Decimal | None
    last_at: datetime | None
    if intraday is not None:
        last_price = intraday.close
        last_at = intraday.timestamp
    elif daily:
        last_price = daily[0].close
        last_at = daily[0].timestamp
    else:
        last_price = None
        last_at = None

    change: Decimal | None = None
    change_pct: float | None = None
    if last_price is not None and previous_close is not None:
        change = last_price - previous_close
        if previous_close != 0:
            change_pct = float(change / previous_close) * 100.0

    return Quote(
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        last_price=last_price,
        last_at=last_at,
        previous_close=previous_close,
        change=change,
        change_pct=change_pct,
    )


def get_quotes(
    symbols: list[str] | None = None, *, active_only: bool = True
) -> list[Quote]:
    """Return quotes for the given symbols (or all active assets if None).

    Order follows the requested ``symbols`` list when provided; otherwise
    assets are returned in symbol order.
    """
    requested = (
        [s.strip().upper() for s in symbols if s.strip()] if symbols is not None else None
    )
    with session_scope() as s:
        stmt = select(Asset)
        if requested is not None:
            stmt = stmt.where(Asset.symbol.in_(requested))
        elif active_only:
            stmt = stmt.where(Asset.is_active.is_(True))
        assets = list(s.execute(stmt.order_by(Asset.symbol)).scalars())

        quotes = [_build_quote(s, a) for a in assets]

    if requested is not None:
        order = {sym: i for i, sym in enumerate(requested)}
        quotes.sort(key=lambda q: order.get(q.symbol, len(order)))
    return quotes


def get_quote(symbol: str) -> Quote:
    sym = symbol.strip().upper()
    with session_scope() as s:
        asset = s.execute(
            select(Asset).where(Asset.symbol == sym)
        ).scalar_one_or_none()
        if asset is None:
            raise SymbolNotFoundError(f"Unknown symbol: {sym}")
        return _build_quote(s, asset)
