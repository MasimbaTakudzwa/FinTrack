from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from sidecar.ingestion.yfinance_fetcher import FetcherError, PriceBar

logger = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0

SYMBOL_TO_COINGECKO_ID: dict[str, str] = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "SOL-USD": "solana",
    "ADA-USD": "cardano",
    "DOGE-USD": "dogecoin",
    "XRP-USD": "ripple",
    "DOT-USD": "polkadot",
    "MATIC-USD": "matic-network",
    "AVAX-USD": "avalanche-2",
    "LINK-USD": "chainlink",
}


def _backoff_sleep(attempt: int) -> None:
    delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
    jitter = random.uniform(0, delay * 0.25)
    time.sleep(delay + jitter)


def _http_get(url: str, params: dict[str, Any]) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 429:
                logger.warning("CoinGecko rate-limited (attempt %d/%d)", attempt, MAX_ATTEMPTS)
                if attempt < MAX_ATTEMPTS:
                    _backoff_sleep(attempt)
                    continue
                raise FetcherError(f"CoinGecko rate-limited after {MAX_ATTEMPTS} attempts")
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "CoinGecko request failed (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, exc
            )
            if attempt < MAX_ATTEMPTS:
                _backoff_sleep(attempt)
    raise FetcherError(f"CoinGecko request failed after {MAX_ATTEMPTS} attempts") from last_exc


def _fetch_ohlc(coin_id: str, *, vs_currency: str = "usd", days: int = 1) -> list[list[float]]:
    url = f"{COINGECKO_BASE}/coins/{coin_id}/ohlc"
    params: dict[str, Any] = {"vs_currency": vs_currency, "days": days}
    data = _http_get(url, params)
    if not isinstance(data, list):
        raise FetcherError(f"CoinGecko returned non-list payload for {coin_id}: {type(data)!r}")
    return data


def _bars_from_ohlc(symbol: str, rows: list[list[float]]) -> list[PriceBar]:
    bars: list[PriceBar] = []
    for row in rows:
        if len(row) < 5:
            continue
        try:
            ts = datetime.fromtimestamp(float(row[0]) / 1000.0, tz=UTC)
            o = Decimal(str(row[1]))
            h = Decimal(str(row[2]))
            low = Decimal(str(row[3]))
            c = Decimal(str(row[4]))
        except (TypeError, ValueError, InvalidOperation):
            continue
        bars.append(
            PriceBar(
                symbol=symbol,
                timestamp=ts,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=0,
            )
        )
    return bars


def fetch_crypto_prices(symbols: Iterable[str], *, days: int = 1) -> list[PriceBar]:
    """Fetch OHLC bars from CoinGecko for supported crypto symbols.

    CoinGecko's /coins/{id}/ohlc endpoint does not include volume, so bars
    are emitted with volume=0. Symbols without a CoinGecko mapping are
    skipped with a warning.
    """
    all_bars: list[PriceBar] = []
    seen: set[str] = set()
    for sym in symbols:
        sym_up = sym.upper()
        if sym_up in seen:
            continue
        seen.add(sym_up)
        coin_id = SYMBOL_TO_COINGECKO_ID.get(sym_up)
        if coin_id is None:
            logger.warning("No CoinGecko mapping for %s, skipping", sym_up)
            continue
        try:
            rows = _fetch_ohlc(coin_id, days=days)
        except FetcherError as exc:
            logger.warning("CoinGecko fetch failed for %s (%s): %s", sym_up, coin_id, exc)
            continue
        all_bars.extend(_bars_from_ohlc(sym_up, rows))
    return all_bars
