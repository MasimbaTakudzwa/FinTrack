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
                "interval": bar.interval,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
        )
    if not rows:
        return 0
    # 5y of daily closes for ~10 assets is ~12K rows * 8 cols ≈ 96K bound
    # params — past SQLite's 32766-variable statement cap. Chunk to stay
    # under the ceiling. 500 rows ≈ 4000 params per stmt — comfortable
    # headroom even if the schema grows.
    inserted = 0
    chunk_size = 500
    for offset in range(0, len(rows), chunk_size):
        chunk = rows[offset : offset + chunk_size]
        stmt = sqlite_insert(PricePoint).values(chunk).on_conflict_do_nothing(
            index_elements=["asset_id", "timestamp", "interval"]
        )
        result = cast(CursorResult[object], session.execute(stmt))
        inserted += result.rowcount or 0
    return inserted


def ingest_prices_for_symbols(
    symbols: Sequence[str],
    *,
    period: str = "1d",
    interval: str = "5m",
) -> int:
    """Fetch and persist OHLCV bars for an explicit list of symbols.

    Used both by the scheduled ``ingest_prices`` job (every active asset with
    the 5m defaults — a minimal incremental tick), the "add asset" flow (which
    passes ``period="60d", interval="5m"`` so the user gets ~60 days of
    history on add, not just the last few bars), and by ``ingest_prices_daily``
    (``period="5y", interval="1d"`` — the training base for forecasting).

    Returns the number of newly inserted PricePoint rows.
    """
    unique = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    if not unique:
        return 0

    try:
        bars = fetch_prices(unique, period=period, interval=interval)
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
            "ingest_prices_for_symbols: inserted %d new bars (interval=%s) from %d fetched across %d symbols",
            inserted,
            interval,
            len(bars),
            len(symbol_to_id),
        )
        return inserted


def ingest_prices() -> int:
    """Fetch latest OHLCV bars for every active asset and persist new rows.

    Returns the number of newly inserted PricePoint rows (duplicates are skipped
    via ON CONFLICT DO NOTHING on (asset_id, timestamp, interval)). Intraday
    5-min cadence — see ``ingest_prices_daily`` for the once-per-day close-bar
    backfill that feeds the forecasting engine.
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


def ingest_prices_daily() -> int:
    """Pull daily-bar closes for every active asset — training base for forecasts.

    Uses yfinance ``period="5y", interval="1d"`` which returns ~1,250 rows
    per asset on first call and a single new row on each subsequent call
    after market close. The ON CONFLICT clause dedups, so running this
    more often than once a day is safe but wasteful.

    Scheduled via CronTrigger at ``ingest_prices_daily.cron_hour_utc`` (default
    22 UTC ≈ 6pm ET, after US market close) + fire-on-first-add so a fresh
    install gets the full 5y backfill within seconds of the scheduler starting.
    """
    with session_scope() as session:
        symbols = list(
            session.execute(
                select(Asset.symbol).where(Asset.is_active.is_(True))
            ).scalars()
        )
    if not symbols:
        logger.info("ingest_prices_daily: no active assets, skipping")
        return 0
    return ingest_prices_for_symbols(symbols, period="5y", interval="1d")


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
    multiple symbols is stored once and linked to each matched asset. After
    upserting, every article that came back without a sentiment score yet is
    fed through VADER inline so the user sees scored headlines immediately
    rather than waiting for the next periodic backfill tick.

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
        # Identify which of the upserted articles are still unscored so we
        # don't re-score the existing-and-already-rated ones. This also
        # naturally narrows scoring to the brand-new inserts on a steady-
        # state run when most URLs were already in the DB.
        article_ids = list({aid for aid in url_to_article_id.values()})
        unscored_ids = [
            aid for (aid,) in session.execute(
                select(Article.id).where(
                    Article.id.in_(article_ids),
                    Article.sentiment.is_(None),
                )
            ).all()
        ]

    # Score outside the upsert transaction so a slow VADER load doesn't
    # extend the write lock — and we tolerate a missing ML backend cleanly.
    if unscored_ids:
        try:
            from ml.jobs import score_article_ids

            scored = score_article_ids(unscored_ids)
            if scored:
                logger.info(
                    "ingest_news: scored %d new headlines via VADER", scored
                )
        except ImportError as exc:
            logger.info(
                "ingest_news: ml package unavailable (%s) — skipping inline "
                "sentiment scoring",
                exc,
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


def train_forecasts_job() -> int:
    """Scheduler entry for the weekly SARIMAX retrain.

    Thin wrapper around ``ml.jobs.train_forecasts`` so the scheduler import
    path stays symmetric with other ``ingest_*`` / ``check_*`` jobs. Importing
    ``ml.jobs`` is lazy (inside the function body) so a sidecar launched
    without ``requirements-ml.txt`` still boots — the forecast job then
    simply logs an error if it ever fires without statsmodels installed.
    Returns count of successfully retrained assets.
    """
    try:
        from ml.jobs import train_forecasts

        return train_forecasts()
    except ImportError as exc:
        logger.error(
            "train_forecasts_job: ml package unavailable (%s) — "
            "install requirements-ml.txt to enable forecasting",
            exc,
        )
        return 0
    except Exception:  # pragma: no cover — defensive
        logger.exception("train_forecasts_job failed")
        return 0


def score_news_sentiment_job() -> int:
    """Scheduler entry for the periodic VADER sentiment backfill.

    Picks up any ``Article`` rows that don't yet have a sentiment score
    (typically only historical rows imported before sentiment was wired,
    or rows the inline-scoring step in ``ingest_news`` skipped due to a
    transient backend issue). The new-article hot path stays inside
    ``ingest_news`` so users don't wait on this scheduler tick.

    Lazy import of ``ml.jobs`` so a sidecar without ``requirements-ml.txt``
    still boots cleanly. Returns count of articles newly scored.
    """
    try:
        from ml.jobs import score_articles

        return score_articles()
    except ImportError as exc:
        logger.info(
            "score_news_sentiment_job: ml package unavailable (%s) — "
            "install requirements-ml.txt to enable sentiment scoring",
            exc,
        )
        return 0
    except Exception:  # pragma: no cover — defensive
        logger.exception("score_news_sentiment_job failed")
        return 0
