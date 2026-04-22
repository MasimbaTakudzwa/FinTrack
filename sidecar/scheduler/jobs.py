from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sidecar.config import settings
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, MacroDataPoint, MacroIndicator, PricePoint
from sidecar.ingestion.coingecko_fetcher import fetch_crypto_prices
from sidecar.ingestion.fred_fetcher import fetch_macro_series_many
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


def ingest_crypto() -> int:
    """Fetch OHLC bars for active crypto assets via CoinGecko.

    Writes into the same `price_points` table as ingest_prices; duplicate
    (asset_id, timestamp) rows are deduped via ON CONFLICT DO NOTHING.
    Meant to run as a complement or fallback to yfinance crypto coverage.
    """
    with session_scope() as session:
        symbols = list(
            session.execute(
                select(Asset.symbol).where(
                    Asset.is_active.is_(True),
                    Asset.asset_type == AssetType.CRYPTO,
                )
            ).scalars()
        )
        if not symbols:
            logger.info("ingest_crypto: no active crypto assets, skipping")
            return 0

        try:
            bars = fetch_crypto_prices(symbols)
        except FetcherError as exc:
            logger.error("ingest_crypto: fetch failed: %s", exc)
            return 0

        if not bars:
            logger.info("ingest_crypto: fetched 0 bars for %d symbols", len(symbols))
            return 0

        symbol_to_id = _load_symbol_to_id(session, symbols)
        inserted = _upsert_bars(session, symbol_to_id, bars)
        logger.info(
            "ingest_crypto: inserted %d new bars from %d fetched across %d symbols",
            inserted,
            len(bars),
            len(symbols),
        )
        return inserted


def ingest_macro() -> int:
    """Fetch observations for every active macro indicator from FRED.

    Requires `FINTRACK_FRED_API_KEY` to be set — otherwise the job is a no-op.
    """
    api_key = settings.fred_api_key
    if not api_key:
        logger.info("ingest_macro: FRED_API_KEY not set, skipping")
        return 0

    with session_scope() as session:
        rows = session.execute(
            select(MacroIndicator.series_id, MacroIndicator.id).where(
                MacroIndicator.is_active.is_(True)
            )
        ).all()
        if not rows:
            logger.info("ingest_macro: no active indicators, skipping")
            return 0

        series_to_id = {sid: iid for sid, iid in rows}
        points = fetch_macro_series_many(list(series_to_id.keys()), api_key)

        if not points:
            logger.info(
                "ingest_macro: fetched 0 points for %d indicators", len(series_to_id)
            )
            return 0

        payload: list[dict[str, object]] = [
            {
                "indicator_id": series_to_id[p.series_id],
                "date": p.date,
                "value": p.value,
            }
            for p in points
            if p.series_id in series_to_id
        ]
        if not payload:
            return 0
        stmt = sqlite_insert(MacroDataPoint).values(payload).on_conflict_do_nothing(
            index_elements=["indicator_id", "date"]
        )
        result = cast(CursorResult[object], session.execute(stmt))
        inserted = result.rowcount or 0
        logger.info(
            "ingest_macro: inserted %d new points from %d fetched across %d indicators",
            inserted,
            len(points),
            len(series_to_id),
        )
        return inserted
