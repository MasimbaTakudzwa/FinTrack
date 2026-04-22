from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, PricePoint
from sidecar.ingestion.yfinance_fetcher import FetcherError, PriceBar, fetch_prices

logger = logging.getLogger(__name__)


def _load_symbol_to_id(session: Session, symbols: Sequence[str]) -> dict[str, int]:
    if not symbols:
        return {}
    rows = session.execute(
        select(Asset.symbol, Asset.id).where(Asset.symbol.in_(symbols))
    ).all()
    return {sym: aid for sym, aid in rows}


def _upsert_bars(session: Session, symbol_to_id: dict[str, int], bars: list[PriceBar]) -> int:
    if not bars:
        return 0
    rows: list[dict[str, object]] = []
    for bar in bars:
        asset_id = symbol_to_id.get(bar.symbol)
        if asset_id is None:
            continue
        rows.append(
            {
                "asset_id": asset_id,
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
        )
    if not rows:
        return 0
    stmt = sqlite_insert(PricePoint).values(rows).on_conflict_do_nothing(
        index_elements=["asset_id", "timestamp"]
    )
    result = cast(CursorResult[object], session.execute(stmt))
    return result.rowcount or 0


def ingest_prices() -> int:
    """Fetch latest OHLCV bars for every active asset and persist new rows.

    Returns the number of newly inserted PricePoint rows (duplicates are skipped
    via ON CONFLICT DO NOTHING on (asset_id, timestamp)).
    """
    with session_scope() as session:
        symbols = list(
            session.execute(
                select(Asset.symbol).where(Asset.is_active.is_(True))
            ).scalars()
        )
        if not symbols:
            logger.info("ingest_prices: no active assets, skipping")
            return 0

        try:
            bars = fetch_prices(symbols)
        except FetcherError as exc:
            logger.error("ingest_prices: fetch failed: %s", exc)
            return 0

        if not bars:
            logger.info("ingest_prices: fetched 0 bars for %d symbols", len(symbols))
            return 0

        symbol_to_id = _load_symbol_to_id(session, symbols)
        inserted = _upsert_bars(session, symbol_to_id, bars)
        logger.info(
            "ingest_prices: inserted %d new bars from %d fetched across %d symbols",
            inserted,
            len(bars),
            len(symbols),
        )
        return inserted
