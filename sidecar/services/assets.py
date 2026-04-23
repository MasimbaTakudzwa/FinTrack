"""
Asset lookup + add-to-tracking service.

Resolves arbitrary yfinance symbols (so users can track anything Yahoo has —
small caps, foreign ADRs, currency pairs, crypto tickers) and persists them
into the ``assets`` table with a best-effort asset-type classification.

The add flow also kicks off a one-shot ingest for just the new symbol so the
dashboard shows bars immediately, without waiting up to 5 min for the next
scheduler tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import yfinance as yf
from sqlalchemy import func, select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType
from sidecar.ingestion.yfinance_fetcher import fetch_prices

logger = logging.getLogger(__name__)


class AssetServiceError(ValueError):
    """Base class for asset-service domain errors."""


class SymbolNotFoundError(AssetServiceError):
    """yfinance could not resolve the symbol to anything tradable."""


class AssetAlreadyExistsError(AssetServiceError):
    """Symbol is already in the ``assets`` table.

    Preserved for backwards compatibility — :func:`add_asset` no longer
    raises this because the "add" operation is idempotent: calling it with
    a symbol that's already tracked returns the existing row with
    ``newly_added=False``. Callers that want to enforce "must be new"
    semantics should check ``AddAssetResult.newly_added`` instead.
    """


@dataclass(frozen=True)
class ResolvedSymbol:
    """The minimal set of facts we need from Yahoo before persisting."""

    symbol: str
    name: str
    asset_type: AssetType
    exchange: str | None
    currency: str | None


@dataclass(frozen=True)
class AddAssetResult:
    asset_id: int
    symbol: str
    name: str
    asset_type: AssetType
    bars_ingested: int
    # True iff this call freshly resolved+persisted the asset. False means
    # the symbol was already in the ``assets`` table, we returned its
    # existing row as-is, and did NOT run a 60-day re-ingest. Callers
    # that want "create or fail" semantics can branch on this.
    newly_added: bool


# yfinance returns its own vocabulary for ``quoteType`` (also surfaced on
# ``fast_info.quote_type``). Map to our narrower ``AssetType`` set; unknown
# values fall back to ``stock`` (a safe default — users can re-classify
# later via a Settings UI once that exists).
_QUOTE_TYPE_MAP: dict[str, AssetType] = {
    "EQUITY": AssetType.STOCK,
    "ETF": AssetType.ETF,
    "CRYPTOCURRENCY": AssetType.CRYPTO,
    "INDEX": AssetType.INDEX,
    "FUTURE": AssetType.COMMODITY,
    "COMMODITY": AssetType.COMMODITY,
    "MUTUALFUND": AssetType.ETF,
    "CURRENCY": AssetType.COMMODITY,
}

_MAX_SYMBOL_LEN = 32


def _map_quote_type(q: str | None) -> AssetType:
    if not q:
        return AssetType.STOCK
    return _QUOTE_TYPE_MAP.get(q.upper(), AssetType.STOCK)


def _safe_fast_info(ticker: yf.Ticker) -> dict[str, Any]:
    """Extract a small dict from ``ticker.fast_info``.

    ``fast_info`` attribute access can raise on many edge cases (missing
    values, rate limits). Swallow everything and return what we managed to
    read — empty dict means nothing resolved.
    """
    out: dict[str, Any] = {}
    try:
        fi = ticker.fast_info
    except Exception as exc:
        logger.debug("fast_info access raised for %s: %s", ticker.ticker, exc)
        return out
    for key in ("quote_type", "currency", "exchange", "last_price"):
        try:
            val = getattr(fi, key, None)
            if val is None and hasattr(fi, "get"):
                val = fi.get(key)
        except Exception:
            val = None
        if val is not None:
            out[key] = val
    return out


def _safe_info(ticker: yf.Ticker) -> dict[str, Any]:
    """Extract long-form metadata. ``.info`` hits a slower endpoint."""
    try:
        info = ticker.info
    except Exception as exc:
        logger.debug("ticker.info raised for %s: %s", ticker.ticker, exc)
        return {}
    if not isinstance(info, dict):
        return {}
    return info


def resolve_symbol(raw: str) -> ResolvedSymbol:
    """Validate a user-supplied symbol against yfinance and return canonical info.

    Raises :class:`SymbolNotFoundError` if yfinance can't resolve the symbol.
    """
    symbol = raw.strip().upper()
    if not symbol:
        raise AssetServiceError("symbol must not be empty")
    if len(symbol) > _MAX_SYMBOL_LEN:
        raise AssetServiceError(
            f"symbol too long (max {_MAX_SYMBOL_LEN} chars)"
        )

    ticker = yf.Ticker(symbol)
    fast = _safe_fast_info(ticker)
    info = _safe_info(ticker)

    quote_type = fast.get("quote_type") or info.get("quoteType")
    exchange = fast.get("exchange") or info.get("exchange") or info.get("fullExchangeName")
    currency = fast.get("currency") or info.get("currency")
    name = (info.get("longName") or info.get("shortName") or "").strip()

    # A valid symbol should have at least a quote_type or a resolvable price.
    if not quote_type and not name and fast.get("last_price") is None:
        # Final fallback — try a 5-day download. If we get bars, symbol is real.
        try:
            bars = fetch_prices([symbol], period="5d", interval="1d")
        except Exception as exc:
            logger.info("resolve_symbol fallback download errored for %s: %s", symbol, exc)
            bars = []
        if not bars:
            raise SymbolNotFoundError(f"Unable to resolve symbol {symbol!r}")
        # Found bars but no metadata — best effort.

    asset_type = _map_quote_type(quote_type)
    return ResolvedSymbol(
        symbol=symbol,
        name=name or symbol,
        asset_type=asset_type,
        exchange=str(exchange) if exchange else None,
        currency=str(currency) if currency else None,
    )


def add_asset(raw: str) -> AddAssetResult:
    """Resolve, persist, and immediately ingest bars for a new asset.

    Idempotent: if the symbol is already in the ``assets`` table, we
    short-circuit with the existing row (``newly_added=False``,
    ``bars_ingested=0``) — no yfinance lookup, no re-ingest. This is the
    semantic the "Track new…" button on a watchlist expects: the user
    wants the asset on this list, but if we already have it everywhere
    else, there's no reason to re-fetch its price history.

    Callers that want strict "create or fail" behaviour can branch on
    ``result.newly_added``.

    Raises:
        SymbolNotFoundError: if yfinance can't resolve a *new* symbol.
        AssetServiceError: for validation failures (empty/too-long symbol).
    """
    # Normalise first so the fast-path lookup (below) uses the same key
    # the slow-path Asset row would.
    normalised = raw.strip().upper()
    if not normalised:
        raise AssetServiceError("symbol must not be empty")
    if len(normalised) > _MAX_SYMBOL_LEN:
        raise AssetServiceError(
            f"symbol too long (max {_MAX_SYMBOL_LEN} chars)"
        )

    # Fast path: already tracked → no yfinance call at all.
    with session_scope() as session:
        existing = session.execute(
            select(Asset).where(func.upper(Asset.symbol) == normalised)
        ).scalar_one_or_none()
        if existing is not None:
            return AddAssetResult(
                asset_id=existing.id,
                symbol=existing.symbol,
                name=existing.name,
                asset_type=existing.asset_type,
                bars_ingested=0,
                newly_added=False,
            )

    # Slow path: fresh add — resolve, persist, ingest.
    resolved = resolve_symbol(normalised)

    with session_scope() as session:
        # Re-check inside the session to close the race where two
        # concurrent add-asset calls both passed the fast-path check.
        existing = session.execute(
            select(Asset).where(func.upper(Asset.symbol) == resolved.symbol)
        ).scalar_one_or_none()
        if existing is not None:
            return AddAssetResult(
                asset_id=existing.id,
                symbol=existing.symbol,
                name=existing.name,
                asset_type=existing.asset_type,
                bars_ingested=0,
                newly_added=False,
            )
        asset = Asset(
            symbol=resolved.symbol,
            name=resolved.name,
            asset_type=resolved.asset_type,
            is_active=True,
        )
        session.add(asset)
        session.flush()
        new_id = asset.id

    # Kick off a one-shot ingest outside the transaction so the user sees
    # bars without waiting for the 5-min scheduler tick. We request a 60-day
    # backfill at 5-min resolution (yfinance's max window at that granularity)
    # so the chart is immediately usable on all timeframes, not just "1H".
    # Failures here aren't fatal — the next scheduler run will pick this
    # symbol up anyway.
    bars_ingested = 0
    try:
        from sidecar.scheduler.jobs import ingest_prices_for_symbols

        bars_ingested = ingest_prices_for_symbols(
            [resolved.symbol],
            period="60d",
            interval="5m",
        )
    except Exception:
        logger.exception("one-shot ingest failed for %s", resolved.symbol)

    return AddAssetResult(
        asset_id=new_id,
        symbol=resolved.symbol,
        name=resolved.name,
        asset_type=resolved.asset_type,
        bars_ingested=bars_ingested,
        newly_added=True,
    )
