from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from sidecar.ingestion import yfinance_fetcher
from sidecar.ingestion.yfinance_fetcher import fetch_prices


def _single_symbol_frame() -> pd.DataFrame:
    idx = pd.to_datetime(
        ["2026-04-22 13:00:00", "2026-04-22 13:05:00"], utc=True
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.5, 102.0],
            "Low": [99.5, 100.5],
            "Close": [101.0, 101.75],
            "Volume": [1_000_000, 1_200_000],
        },
        index=idx,
    )


def _multi_symbol_frame() -> pd.DataFrame:
    idx = pd.to_datetime(["2026-04-22 13:00:00"], utc=True)
    cols = pd.MultiIndex.from_product(
        [["AAPL", "MSFT"], ["Open", "High", "Low", "Close", "Volume"]]
    )
    data = [[100.0, 101.0, 99.0, 100.5, 1_000, 250.0, 251.0, 249.0, 250.5, 2_000]]
    return pd.DataFrame(data, index=idx, columns=cols)


def test_fetch_prices_single_symbol(monkeypatch) -> None:
    monkeypatch.setattr(
        yfinance_fetcher, "_download", lambda symbols, period, interval: _single_symbol_frame()
    )
    bars = fetch_prices(["AAPL"])
    assert len(bars) == 2
    assert bars[0].symbol == "AAPL"
    assert bars[0].open == Decimal("100.0")
    assert bars[0].volume == 1_000_000
    assert bars[0].timestamp.tzinfo is not None


def test_fetch_prices_multi_symbol(monkeypatch) -> None:
    monkeypatch.setattr(
        yfinance_fetcher, "_download", lambda symbols, period, interval: _multi_symbol_frame()
    )
    bars = fetch_prices(["AAPL", "MSFT"])
    symbols = {b.symbol for b in bars}
    assert symbols == {"AAPL", "MSFT"}
    assert len(bars) == 2


def test_fetch_prices_skips_nan_rows(monkeypatch) -> None:
    idx = pd.to_datetime(["2026-04-22 13:00:00", "2026-04-22 13:05:00"], utc=True)
    df = pd.DataFrame(
        {
            "Open": [100.0, float("nan")],
            "High": [101.0, float("nan")],
            "Low": [99.0, float("nan")],
            "Close": [100.5, float("nan")],
            "Volume": [1_000, 0],
        },
        index=idx,
    )
    monkeypatch.setattr(
        yfinance_fetcher, "_download", lambda symbols, period, interval: df
    )
    bars = fetch_prices(["AAPL"])
    assert len(bars) == 1


def test_fetch_prices_empty_input() -> None:
    assert fetch_prices([]) == []


def test_normalize_ts_naive_becomes_utc() -> None:
    naive = datetime(2026, 4, 22, 13, 0)
    out = yfinance_fetcher._normalize_ts(naive)
    assert out.tzinfo is UTC


def test_normalize_ts_tz_aware_converts_to_utc() -> None:
    ny = timezone(timedelta(hours=-4))
    dt = datetime(2026, 4, 22, 9, 0, tzinfo=ny)
    out = yfinance_fetcher._normalize_ts(dt)
    assert out.tzinfo is UTC
    assert out.hour == 13
