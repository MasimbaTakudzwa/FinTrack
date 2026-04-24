from __future__ import annotations

from datetime import UTC
from decimal import Decimal
from typing import Any

import pytest

from sidecar.ingestion import coingecko_fetcher
from sidecar.ingestion.coingecko_fetcher import (
    FetcherError,
    _bars_from_ohlc,
    fetch_crypto_prices,
)


def test_fetch_crypto_prices_maps_known_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch_ohlc(coin_id: str, *, vs_currency: str = "usd", days: int = 1) -> list[list[float]]:
        calls.append(coin_id)
        return [[1745323200000, 68000.0, 68500.0, 67800.0, 68200.0]]

    monkeypatch.setattr(coingecko_fetcher, "_fetch_ohlc", fake_fetch_ohlc)
    bars = fetch_crypto_prices(["BTC-USD", "ETH-USD"])
    assert calls == ["bitcoin", "ethereum"]
    assert len(bars) == 2
    assert {b.symbol for b in bars} == {"BTC-USD", "ETH-USD"}
    assert all(b.volume == 0 for b in bars)
    assert all(b.timestamp.tzinfo is UTC for b in bars)


def test_fetch_crypto_prices_skips_unknown_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fetch_ohlc(coin_id: str, *, vs_currency: str = "usd", days: int = 1) -> list[list[float]]:
        return [[1745323200000, 1.0, 2.0, 0.5, 1.5]]

    monkeypatch.setattr(coingecko_fetcher, "_fetch_ohlc", fake_fetch_ohlc)
    bars = fetch_crypto_prices(["GHOST-USD", "BTC-USD"])
    assert {b.symbol for b in bars} == {"BTC-USD"}


def test_fetch_crypto_prices_dedupes_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch_ohlc(coin_id: str, *, vs_currency: str = "usd", days: int = 1) -> list[list[float]]:
        calls.append(coin_id)
        return [[1745323200000, 1.0, 2.0, 0.5, 1.5]]

    monkeypatch.setattr(coingecko_fetcher, "_fetch_ohlc", fake_fetch_ohlc)
    fetch_crypto_prices(["BTC-USD", "btc-usd", "BTC-USD"])
    assert calls == ["bitcoin"]


def test_fetch_crypto_prices_continues_on_single_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fetch_ohlc(coin_id: str, *, vs_currency: str = "usd", days: int = 1) -> list[list[float]]:
        if coin_id == "bitcoin":
            raise FetcherError("boom")
        return [[1745323200000, 1.0, 2.0, 0.5, 1.5]]

    monkeypatch.setattr(coingecko_fetcher, "_fetch_ohlc", fake_fetch_ohlc)
    bars = fetch_crypto_prices(["BTC-USD", "ETH-USD"])
    assert {b.symbol for b in bars} == {"ETH-USD"}


def test_bars_from_ohlc_parses_rows() -> None:
    rows = [
        [1745323200000, 100.0, 110.0, 95.0, 105.0],
        [1745326800000, 105.0, 115.0, 100.0, 112.5],
    ]
    bars = _bars_from_ohlc("BTC-USD", rows, "4h")
    assert len(bars) == 2
    assert bars[0].open == Decimal("100.0")
    assert bars[0].close == Decimal("105.0")
    assert bars[1].high == Decimal("115.0")
    assert {b.interval for b in bars} == {"4h"}


def test_bars_from_ohlc_skips_malformed_rows() -> None:
    rows: list[list[Any]] = [
        [1745323200000, 100.0, 110.0, 95.0, 105.0],
        [1745326800000, 100.0],
        [1745330400000, "bad", "data", "here", "sorry"],
    ]
    bars = _bars_from_ohlc("BTC-USD", rows, "4h")
    assert len(bars) == 1


def test_http_get_retries_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        def __init__(self, status_code: int, payload: Any) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> Any:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError("should not be called for 200 or 429")

    call_count = {"n": 0}

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> Any:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return FakeResp(429, None)
        return FakeResp(200, [[1, 1.0, 2.0, 0.5, 1.5]])

    monkeypatch.setattr(coingecko_fetcher.requests, "get", fake_get)
    monkeypatch.setattr(coingecko_fetcher, "_backoff_sleep", lambda attempt: None)

    data = coingecko_fetcher._http_get("http://fake", {"k": "v"})
    assert call_count["n"] == 3
    assert isinstance(data, list)
