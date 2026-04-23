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
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import yfinance as yf
from sqlalchemy import func, select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.ingestion.yfinance_fetcher import fetch_prices

logger = logging.getLogger(__name__)


class AssetServiceError(ValueError):
    """Base class for asset-service domain errors."""


class SymbolNotFoundError(AssetServiceError):
    """yfinance could not resolve the symbol to anything tradable."""


class AssetAlreadyExistsError(AssetServiceError):
    """Symbol is already in the ``assets`` table."""


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


@dataclass(frozen=True)
class AssetQuote:
    """Read-only metadata + technicals surface for the AssetDetail page.

    ``last_price`` comes from our own ``price_points`` table (so it matches
    what the chart shows) — yfinance's fast_info ``last_price`` field is a
    near-live quote that can disagree with our 5-min bar close and would be
    confusing. Moving averages and 52-week high/low come from fast_info since
    they're cheap there and we don't compute them ourselves.
    """

    symbol: str
    exchange: str | None
    currency: str | None
    last_price: Decimal | None
    market_cap: int | None
    year_high: Decimal | None
    year_low: Decimal | None
    fifty_day_average: Decimal | None
    two_hundred_day_average: Decimal | None


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

    Raises:
        SymbolNotFoundError: if yfinance can't resolve the symbol.
        AssetAlreadyExistsError: if the symbol is already tracked.
        AssetServiceError: for validation failures (empty/too-long symbol).
    """
    resolved = resolve_symbol(raw)

    with session_scope() as session:
        existing = session.execute(
            select(Asset).where(func.upper(Asset.symbol) == resolved.symbol)
        ).scalar_one_or_none()
        if existing is not None:
            raise AssetAlreadyExistsError(
                f"Symbol {resolved.symbol} is already tracked"
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
    # bars without waiting for the 5-min scheduler tick. Failures here aren't
    # fatal — the next scheduler run will pick this symbol up anyway.
    bars_ingested = 0
    try:
        from sidecar.scheduler.jobs import ingest_prices_for_symbols

        bars_ingested = ingest_prices_for_symbols([resolved.symbol])
    except Exception:
        logger.exception("one-shot ingest failed for %s", resolved.symbol)

    return AddAssetResult(
        asset_id=new_id,
        symbol=resolved.symbol,
        name=resolved.name,
        asset_type=resolved.asset_type,
        bars_ingested=bars_ingested,
    )


# ---------------------------------------------------------------------------
# Quote (fast_info technicals) cache + accessor
# ---------------------------------------------------------------------------

_QUOTE_CACHE_TTL_SECONDS = 60.0
# {symbol -> (monotonic_time, AssetQuote)}. Intentionally module-level and
# process-scoped — the sidecar is a single process, and on restart we want
# a fresh read anyway.
_quote_cache: dict[str, tuple[float, AssetQuote]] = {}


def _to_decimal(val: Any) -> Decimal | None:
    """Best-effort coercion of a yfinance-supplied number to Decimal.

    yfinance returns numpy floats / ints / python floats / sometimes None.
    We go through ``str(float(...))`` so Decimal doesn't pull in the float's
    binary noise and emit ``300.29999999999998`` type values.
    """
    if val is None:
        return None
    try:
        fval = float(val)
    except (TypeError, ValueError):
        return None
    if fval != fval:  # NaN
        return None
    try:
        return Decimal(str(fval))
    except (InvalidOperation, ValueError):
        return None


def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        ival = int(val)
    except (TypeError, ValueError):
        return None
    return ival if ival > 0 else None


def _fast_info_keys(ticker: yf.Ticker, keys: tuple[str, ...]) -> dict[str, Any]:
    """Read a set of keys off ``ticker.fast_info`` with per-key fault tolerance."""
    out: dict[str, Any] = {}
    try:
        fi = ticker.fast_info
    except Exception as exc:
        logger.debug("fast_info access raised for %s: %s", ticker.ticker, exc)
        return out
    for key in keys:
        try:
            val = getattr(fi, key, None)
            if val is None and hasattr(fi, "get"):
                val = fi.get(key)
        except Exception:
            val = None
        if val is not None:
            out[key] = val
    return out


def get_quote(symbol: str) -> AssetQuote:
    """Return live metadata + technicals for a tracked asset.

    The symbol MUST already be tracked (in the ``assets`` table) — this
    endpoint is strictly for the AssetDetail page, not symbol discovery.
    That's how we avoid firing yfinance for random probes from the URL bar.

    Results are cached in-process for ``_QUOTE_CACHE_TTL_SECONDS`` so rapid
    AssetDetail navigation doesn't hammer yfinance. Cache key is the
    uppercased symbol; the cache is dropped on sidecar restart.
    """
    key = symbol.strip().upper()
    if not key:
        raise AssetServiceError("symbol must not be empty")

    now = time.monotonic()
    cached = _quote_cache.get(key)
    if cached is not None and (now - cached[0]) < _QUOTE_CACHE_TTL_SECONDS:
        return cached[1]

    # Verify the symbol is actually tracked and collect our own last_price.
    with session_scope() as session:
        asset = session.execute(
            select(Asset).where(func.upper(Asset.symbol) == key)
        ).scalar_one_or_none()
        if asset is None:
            raise SymbolNotFoundError(f"Symbol {key} is not tracked")
        latest_bar = session.execute(
            select(PricePoint.close)
            .where(PricePoint.asset_id == asset.id)
            .order_by(PricePoint.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()

    ticker = yf.Ticker(key)
    fast = _fast_info_keys(
        ticker,
        (
            "currency",
            "exchange",
            "market_cap",
            "year_high",
            "year_low",
            "fifty_day_average",
            "two_hundred_day_average",
        ),
    )

    quote = AssetQuote(
        symbol=key,
        exchange=str(fast["exchange"]) if fast.get("exchange") else None,
        currency=str(fast["currency"]) if fast.get("currency") else None,
        last_price=_to_decimal(latest_bar),
        market_cap=_to_int(fast.get("market_cap")),
        year_high=_to_decimal(fast.get("year_high")),
        year_low=_to_decimal(fast.get("year_low")),
        fifty_day_average=_to_decimal(fast.get("fifty_day_average")),
        two_hundred_day_average=_to_decimal(fast.get("two_hundred_day_average")),
    )
    _quote_cache[key] = (now, quote)
    return quote


def clear_quote_cache() -> None:
    """Test helper: drop all cached quotes."""
    _quote_cache.clear()
