from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import (
    Article,
    ArticleAsset,
    Asset,
    AssetType,
    MacroDataPoint,
    MacroIndicator,
    PricePoint,
)
from sidecar.ingestion.coingecko_fetcher import fetch_crypto_prices
from sidecar.ingestion.fred_fetcher import fetch_macro_series_many
from sidecar.ingestion.rss_fetcher import NewsItem, fetch_news_for_many
from sidecar.ingestion.yfinance_fetcher import FetcherError, PriceBar, fetch_prices
from sidecar.services.alerts import check_alerts as _check_alerts
from sidecar.services.settings import load_effective_config

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


def ingest_prices_for_symbols(symbols: Sequence[str]) -> int:
    """Fetch and persist OHLCV bars for an explicit list of symbols.

    Used both by the scheduled ``ingest_prices`` job (which passes every
    active asset symbol) and by the "add asset" flow (which passes a single
    newly-resolved symbol so the user sees bars immediately instead of
    waiting up to 5 minutes for the next scheduler tick).

    Returns the number of newly inserted PricePoint rows.
    """
    unique = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    if not unique:
        return 0

    try:
        bars = fetch_prices(unique)
    except FetcherError as exc:
        logger.error("ingest_prices_for_symbols: fetch failed for %s: %s", unique, exc)
        return 0

    if not bars:
        logger.info(
            "ingest_prices_for_symbols: 0 bars fetched for %d symbols", len(unique)
        )
        return 0

    with session_scope() as session:
        symbol_to_id = _load_symbol_to_id(session, unique)
        if not symbol_to_id:
            return 0
        inserted = _upsert_bars(session, symbol_to_id, bars)
        logger.info(
            "ingest_prices_for_symbols: inserted %d new bars from %d fetched across %d symbols",
            inserted,
            len(bars),
            len(symbol_to_id),
        )
        return inserted


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
    return ingest_prices_for_symbols(symbols)


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


def _upsert_articles(
    session: Session, items: Sequence[NewsItem]
) -> dict[str, int]:
    """Insert any missing articles and return a `{url: id}` map for all input URLs."""
    if not items:
        return {}
    payload = [
        {
            "url": item.url,
            "headline": item.headline,
            "source": item.source,
            "published_at": item.published_at,
            "summary": item.summary,
            "image_url": item.image_url,
        }
        for item in items
    ]
    stmt = sqlite_insert(Article).values(payload).on_conflict_do_nothing(
        index_elements=["url"]
    )
    session.execute(stmt)
    # Look up ids for ALL input URLs — both newly inserted and pre-existing.
    urls = {item.url for item in items}
    rows = session.execute(
        select(Article.url, Article.id).where(Article.url.in_(urls))
    ).all()
    return {url: aid for url, aid in rows}


def _upsert_article_assets(
    session: Session,
    items: Sequence[NewsItem],
    url_to_article_id: dict[str, int],
    symbol_to_asset_id: dict[str, int],
) -> int:
    """Link articles to assets based on the symbol each item was fetched for."""
    assoc_rows: list[dict[str, int]] = []
    for item in items:
        article_id = url_to_article_id.get(item.url)
        asset_id = symbol_to_asset_id.get(item.symbol)
        if article_id is None or asset_id is None:
            continue
        assoc_rows.append({"article_id": article_id, "asset_id": asset_id})
    if not assoc_rows:
        return 0
    stmt = sqlite_insert(ArticleAsset).values(assoc_rows).on_conflict_do_nothing(
        index_elements=["article_id", "asset_id"]
    )
    result = cast(CursorResult[object], session.execute(stmt))
    return result.rowcount or 0


def ingest_news() -> int:
    """Fetch Yahoo Finance RSS news for every active asset.

    Articles are dedup'd by URL across assets — the same headline mentioning
    multiple symbols is stored once and linked to each matched asset.

    Returns the number of newly inserted article_asset association rows (which
    is close to, but not exactly, "new articles" — an existing article linking
    to a new asset also counts).
    """
    with session_scope() as session:
        rows = list(
            session.execute(
                select(Asset.symbol, Asset.id).where(Asset.is_active.is_(True))
            ).all()
        )
        if not rows:
            logger.info("ingest_news: no active assets, skipping")
            return 0

        symbol_to_id = {sym: aid for sym, aid in rows}
        items = fetch_news_for_many(list(symbol_to_id.keys()))
        if not items:
            logger.info(
                "ingest_news: fetched 0 items across %d symbols", len(symbol_to_id)
            )
            return 0

        url_to_article_id = _upsert_articles(session, items)
        linked = _upsert_article_assets(
            session, items, url_to_article_id, symbol_to_id
        )
        logger.info(
            "ingest_news: linked %d new (article,asset) pairs from %d items across %d symbols",
            linked,
            len(items),
            len(symbol_to_id),
        )
        return linked


def ingest_macro() -> int:
    """Fetch observations for every active macro indicator from FRED.

    Requires the `fred_api_key` setting (or `FINTRACK_FRED_API_KEY` env var)
    to be set — otherwise the job is a no-op. Read lazily on each invocation
    so runtime updates via the settings API take effect without a restart.
    """
    config = load_effective_config()
    api_key = config.get("fred_api_key") or ""
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
        # Chunk the bulk insert: FRED backfills return decades of observations
        # (monthly CPI since 1947 → ~950 rows; daily DGS10 since 1962 → ~16K
        # rows) and at 3 bound params per row the full payload easily exceeds
        # SQLite's 32766-variable statement limit. 500 rows ≈ 1500 params keeps
        # us well under the cap on every SQLite build we might ship against.
        inserted = 0
        chunk_size = 500
        for offset in range(0, len(payload), chunk_size):
            chunk = payload[offset : offset + chunk_size]
            stmt = sqlite_insert(MacroDataPoint).values(chunk).on_conflict_do_nothing(
                index_elements=["indicator_id", "date"]
            )
            result = cast(CursorResult[object], session.execute(stmt))
            inserted += result.rowcount or 0
        logger.info(
            "ingest_macro: inserted %d new points from %d fetched across %d indicators",
            inserted,
            len(points),
            len(series_to_id),
        )
        return inserted


def check_price_alerts() -> int:
    """Scan active price alerts against the latest price bar.

    Thin wrapper around ``sidecar.services.alerts.check_alerts`` so the
    scheduler import path mirrors other jobs (``ingest_*`` live here).
    Returns the number of alerts newly fired.
    """
    try:
        return _check_alerts()
    except Exception:  # pragma: no cover — defensive; don't let one bad alert nuke the job
        logger.exception("check_price_alerts failed")
        return 0
