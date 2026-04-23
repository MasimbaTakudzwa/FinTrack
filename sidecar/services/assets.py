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
import threading
import time
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


class SymbolSearchError(AssetServiceError):
    """Yahoo's search endpoint was unreachable or returned an unusable shape."""


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


@dataclass(frozen=True)
class SymbolSearchHit:
    """A single Yahoo Finance search autocomplete result.

    Deliberately thinner than :class:`ResolvedSymbol` — we don't hit the
    (slow) per-symbol ``info``/``fast_info`` endpoints here. Resolution
    happens on click: the user picks a hit from the dropdown, the shell
    then calls ``POST /api/assets/lookup/`` for the preview, then
    ``POST /api/assets/`` to persist.
    """

    symbol: str
    name: str
    asset_type: AssetType
    exchange: str | None


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


# ---------------------------------------------------------------------------
# Symbol search (name-based autocomplete)
# ---------------------------------------------------------------------------

_SEARCH_MAX_LIMIT = 20
_SEARCH_MIN_QUERY_LEN = 1
_SEARCH_MAX_QUERY_LEN = 64

# ---------------------------------------------------------------------------
# Search cache
# ---------------------------------------------------------------------------
#
# Yahoo's autocomplete endpoint 429s aggressively when our IP's request rate
# spikes — and it shares a budget with the sidecar's scheduled price+news
# ingestion. A tiny in-memory cache smooths this over for common user
# patterns: typing a query, deleting it, retyping the same thing. The
# second+ attempts served from cache cost Yahoo nothing.
#
# Intentionally small + simple: dict keyed on (lowercased_query, limit),
# value is (stored_at_monotonic, hits). Plain threading.Lock — the jobstore
# thread and FastAPI worker threads both use this service, but contention
# is negligible (search is only called from API handlers, not scheduled
# jobs). Max size bounded to avoid unbounded growth from random probes.
#
# No stale-while-revalidate, no background refresh — the TTL keeps data
# fresh enough for a finance-search use case (a new Yahoo listing won't
# appear for up to 5 min, which is fine). Errors deliberately aren't
# cached — a 429 should be retryable on the next call.
_SEARCH_CACHE_TTL_SECONDS = 300.0
_SEARCH_CACHE_MAX_SIZE = 128

_search_cache: dict[tuple[str, int], tuple[float, list[SymbolSearchHit]]] = {}
_search_cache_lock = threading.Lock()


def _cache_get(key: tuple[str, int]) -> list[SymbolSearchHit] | None:
    """Return cached hits if present and still within TTL, else ``None``.

    Expired entries are evicted on read so the cache self-heals without a
    background sweeper.
    """
    with _search_cache_lock:
        entry = _search_cache.get(key)
        if entry is None:
            return None
        stored_at, hits = entry
        if time.monotonic() - stored_at > _SEARCH_CACHE_TTL_SECONDS:
            _search_cache.pop(key, None)
            return None
        return hits


def _cache_put(key: tuple[str, int], hits: list[SymbolSearchHit]) -> None:
    """Store hits under ``key``. Evicts the oldest entry if at capacity."""
    with _search_cache_lock:
        if (
            len(_search_cache) >= _SEARCH_CACHE_MAX_SIZE
            and key not in _search_cache
        ):
            # Capacity reached — evict the entry with the oldest timestamp.
            oldest = min(_search_cache, key=lambda k: _search_cache[k][0])
            _search_cache.pop(oldest, None)
        _search_cache[key] = (time.monotonic(), hits)


def _reset_search_cache() -> None:
    """Clear the cache. Intended for tests; not part of the public API."""
    with _search_cache_lock:
        _search_cache.clear()


def _fetch_search_quotes(query: str, limit: int) -> list[Any]:
    """Raw Yahoo search quote dicts for ``query``, capped at ``limit``.

    Uses yfinance's ``Search`` helper because the bare
    ``/v1/finance/search`` endpoint 429s aggressively for unauthenticated
    IPs — yfinance carries the same cookie + crumb that makes our
    ``Ticker``-based price ingestion work. No extra dep; yfinance is
    already shipped for price/info fetching.

    Isolated from :func:`search_symbols` so tests can monkeypatch it
    directly without stubbing yfinance's class hierarchy.

    Raises:
        SymbolSearchError: yfinance could not complete the search
            (network error, upstream block, unparsable response).
    """
    try:
        result = yf.Search(
            query,
            max_results=limit,
            news_count=0,
            enable_fuzzy_query=False,
        )
        quotes = result.quotes
    except Exception as exc:
        # yfinance can raise anything from requests.HTTPError to
        # JSONDecodeError to bare KeyError depending on what Yahoo
        # returned. Collapse to a single error the API layer can map
        # cleanly to HTTP 502.
        raise SymbolSearchError(
            f"search upstream unreachable: {exc}"
        ) from exc
    if not isinstance(quotes, list):
        return []
    return quotes


def search_symbols(query: str, limit: int = 10) -> list[SymbolSearchHit]:
    """Search Yahoo Finance for symbols matching a free-text query.

    Users shouldn't need to know tickers — they type "apple" or "bitcoin"
    or "total return" and we show a ranked list of matching instruments.
    Selection drives the existing lookup + persist flow.

    Args:
        query: User-supplied free text (company name, partial ticker, etc).
        limit: Max hits to return (1-20). Clamped; server-side default 10.

    Returns:
        Up to ``limit`` hits, Yahoo's ranking preserved, deduplicated on
        uppercased symbol. Empty list when Yahoo has no matches.

    Raises:
        AssetServiceError: invalid query (empty, too long, bad limit).
        SymbolSearchError: Yahoo endpoint unreachable or returned a shape
            we can't parse.
    """
    q = query.strip()
    if len(q) < _SEARCH_MIN_QUERY_LEN:
        raise AssetServiceError("query must not be empty")
    if len(q) > _SEARCH_MAX_QUERY_LEN:
        raise AssetServiceError(
            f"query too long (max {_SEARCH_MAX_QUERY_LEN} chars)"
        )
    if limit < 1 or limit > _SEARCH_MAX_LIMIT:
        raise AssetServiceError(
            f"limit out of range (1..{_SEARCH_MAX_LIMIT})"
        )

    # Cache lookup. Key on lowercased query so "APPLE" / "apple" / "Apple"
    # hit the same slot. ``limit`` is part of the key because different
    # limits yield different result lengths — we don't try to satisfy a
    # limit=10 request from a limit=5 cache entry.
    cache_key = (q.lower(), limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    quotes = _fetch_search_quotes(q, limit)

    seen: set[str] = set()
    hits: list[SymbolSearchHit] = []
    for q_entry in quotes:
        if not isinstance(q_entry, dict):
            continue
        symbol = q_entry.get("symbol")
        if not isinstance(symbol, str):
            continue
        sym_up = symbol.strip().upper()
        if not sym_up or sym_up in seen:
            continue
        # Drop results that aren't instruments we can track. Yahoo sometimes
        # returns currencies-as-quotes with ``quoteType`` we'd otherwise map
        # to ``stock`` incorrectly; the map already handles this, but we skip
        # entries that have no quote_type at all (typically news spillover).
        quote_type_raw = q_entry.get("quoteType")
        if not isinstance(quote_type_raw, str) or not quote_type_raw:
            continue
        # Prefer longname for display; fall back to shortname then symbol.
        name = (
            str(q_entry.get("longname") or q_entry.get("shortname") or sym_up)
            .strip()
        )
        exch = q_entry.get("exchDisp") or q_entry.get("exchange")
        hits.append(
            SymbolSearchHit(
                symbol=sym_up,
                name=name or sym_up,
                asset_type=_map_quote_type(quote_type_raw),
                exchange=str(exch) if exch else None,
            )
        )
        seen.add(sym_up)
        if len(hits) >= limit:
            break
    # Cache the successful response (including empty-result lookups — "no
    # matches for foo" is worth remembering). Errors raised above bypass
    # this store, so a 429 or network blip stays retryable.
    _cache_put(cache_key, hits)
    return hits
