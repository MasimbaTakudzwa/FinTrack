from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class FetcherError(RuntimeError):
    pass


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return Decimal(str(f))


def _to_int_volume(v: Any) -> int:
    if v is None:
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f != f:
        return 0
    return int(f)


def _normalize_ts(ts: Any) -> datetime:
    dt: datetime
    if hasattr(ts, "to_pydatetime"):
        dt = ts.to_pydatetime()
    elif isinstance(ts, datetime):
        dt = ts
    else:
        raise FetcherError(f"Unrecognised timestamp type: {type(ts)!r}")
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _backoff_sleep(attempt: int) -> None:
    delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
    jitter = random.uniform(0, delay * 0.25)
    time.sleep(delay + jitter)


def _download(symbols: Sequence[str], *, period: str, interval: str) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            df = yf.download(
                tickers=list(symbols),
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if df is None or df.empty:
                logger.warning(
                    "yfinance returned empty frame for %s (attempt %d)",
                    symbols,
                    attempt,
                )
                if attempt < MAX_ATTEMPTS:
                    _backoff_sleep(attempt)
                    continue
            return df
        except Exception as exc:  # yfinance raises many ad-hoc exception types
            last_exc = exc
            logger.warning(
                "yfinance download failed (attempt %d/%d): %s",
                attempt,
                MAX_ATTEMPTS,
                exc,
            )
            if attempt < MAX_ATTEMPTS:
                _backoff_sleep(attempt)
    raise FetcherError(f"yfinance download failed after {MAX_ATTEMPTS} attempts") from last_exc


def _bars_for_symbol(symbol: str, frame: Any) -> list[PriceBar]:
    if frame is None or frame.empty:
        return []
    bars: list[PriceBar] = []
    for ts, row in frame.iterrows():
        o = _to_decimal(row.get("Open"))
        h = _to_decimal(row.get("High"))
        low = _to_decimal(row.get("Low"))
        c = _to_decimal(row.get("Close"))
        if None in (o, h, low, c):
            continue
        assert o is not None and h is not None and low is not None and c is not None
        bars.append(
            PriceBar(
                symbol=symbol,
                timestamp=_normalize_ts(ts),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=_to_int_volume(row.get("Volume")),
            )
        )
    return bars


def fetch_prices(
    symbols: Iterable[str],
    *,
    period: str = "1d",
    interval: str = "5m",
) -> list[PriceBar]:
    """Fetch OHLCV bars from Yahoo Finance for one or more symbols.

    Results are normalised to UTC and deduplicated is left to the caller (via
    the unique (asset_id, timestamp) index).
    """
    unique = tuple(dict.fromkeys(symbols))
    if not unique:
        return []

    df = _download(unique, period=period, interval=interval)
    if df is None or df.empty:
        return []

    all_bars: list[PriceBar] = []
    if len(unique) == 1:
        all_bars.extend(_bars_for_symbol(unique[0], df))
    else:
        for sym in unique:
            try:
                sub = df[sym]
            except KeyError:
                logger.warning("yfinance frame missing symbol %s", sym)
                continue
            all_bars.extend(_bars_for_symbol(sym, sub))
    return all_bars
