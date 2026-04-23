"""Tests for ``sidecar.services.assets``.

Covers symbol resolution (via stubbed yfinance) and the add-asset flow.
The yfinance module is monkeypatched to a fake ``Ticker`` class so tests
don't hit the live network — the fetcher fallback path is also stubbed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType
from sidecar.services import assets as assets_service
from sidecar.services.assets import (
    AssetServiceError,
    SymbolNotFoundError,
    add_asset,
    resolve_symbol,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeFastInfo:
    """Mimics yfinance's ``fast_info`` — attribute access + dict-ish get."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)


class _FakeTicker:
    def __init__(
        self,
        ticker: str,
        fast: dict[str, Any] | None = None,
        info: dict[str, Any] | None = None,
        fast_raises: bool = False,
        info_raises: bool = False,
    ) -> None:
        self.ticker = ticker
        self._fast = fast or {}
        self._info = info or {}
        self._fast_raises = fast_raises
        self._info_raises = info_raises

    @property
    def fast_info(self) -> _FakeFastInfo:
        if self._fast_raises:
            raise RuntimeError("fast_info exploded")
        return _FakeFastInfo(self._fast)

    @property
    def info(self) -> dict[str, Any]:
        if self._info_raises:
            raise RuntimeError("info exploded")
        return self._info


def _patch_ticker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fast: dict[str, Any] | None = None,
    info: dict[str, Any] | None = None,
    fast_raises: bool = False,
    info_raises: bool = False,
) -> None:
    """Replace ``yf.Ticker`` in the service module with a canned response."""

    def _factory(symbol: str) -> _FakeTicker:
        return _FakeTicker(
            ticker=symbol,
            fast=fast,
            info=info,
            fast_raises=fast_raises,
            info_raises=info_raises,
        )

    monkeypatch.setattr(assets_service.yf, "Ticker", _factory)


# ---------------------------------------------------------------------------
# resolve_symbol
# ---------------------------------------------------------------------------


def test_resolve_symbol_normalises_to_upper(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "EQUITY", "currency": "USD", "exchange": "NMS"},
        info={"longName": "Apple Inc."},
    )

    r = resolve_symbol("  aapl  ")
    assert r.symbol == "AAPL"
    assert r.name == "Apple Inc."
    assert r.asset_type is AssetType.STOCK
    assert r.currency == "USD"
    assert r.exchange == "NMS"


def test_resolve_symbol_maps_quote_types(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "CRYPTOCURRENCY"},
        info={"shortName": "Bitcoin USD"},
    )
    r = resolve_symbol("BTC-USD")
    assert r.asset_type is AssetType.CRYPTO
    assert r.name == "Bitcoin USD"


def test_resolve_symbol_unknown_quote_type_falls_back_to_stock(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "MYSTERY_MEAT"},
        info={"longName": "Unknown Thing"},
    )
    r = resolve_symbol("???")
    assert r.asset_type is AssetType.STOCK


def test_resolve_symbol_etf(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "ETF"},
        info={"longName": "SPDR S&P 500"},
    )
    assert resolve_symbol("SPY").asset_type is AssetType.ETF


def test_resolve_symbol_future_maps_to_commodity(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "FUTURE"},
        info={"longName": "Gold Dec 26"},
    )
    assert resolve_symbol("GC=F").asset_type is AssetType.COMMODITY


def test_resolve_symbol_empty_raises(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(monkeypatch)
    with pytest.raises(AssetServiceError):
        resolve_symbol("   ")


def test_resolve_symbol_too_long_raises(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(monkeypatch)
    with pytest.raises(AssetServiceError):
        resolve_symbol("A" * 33)


def test_resolve_symbol_not_found_with_fallback(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fast_info + info both empty, fallback download also empty."""
    _patch_ticker(monkeypatch, fast={}, info={})
    monkeypatch.setattr(assets_service, "fetch_prices", lambda *a, **kw: [])
    with pytest.raises(SymbolNotFoundError):
        resolve_symbol("DEFINITELY-FAKE")


def test_resolve_symbol_fallback_rescues_unknown_metadata(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No metadata but the fallback download returns bars → accept it."""
    _patch_ticker(monkeypatch, fast={}, info={})

    class _Bar:
        pass

    monkeypatch.setattr(assets_service, "fetch_prices", lambda *a, **kw: [_Bar()])
    r = resolve_symbol("OBSCURE")
    assert r.symbol == "OBSCURE"
    # No name → falls back to the symbol string.
    assert r.name == "OBSCURE"
    assert r.asset_type is AssetType.STOCK


def test_resolve_symbol_fast_info_raises_still_reads_info(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast_raises=True,
        info={"quoteType": "EQUITY", "longName": "Foo Corp"},
    )
    r = resolve_symbol("FOO")
    assert r.asset_type is AssetType.STOCK
    assert r.name == "Foo Corp"


def test_resolve_symbol_uses_last_price_as_liveness_signal(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If fast_info gives a price but no quote_type, we should still resolve."""
    _patch_ticker(
        monkeypatch,
        fast={"last_price": 42.0},
        info={},
    )
    # fetch_prices fallback not even called because liveness is satisfied.
    monkeypatch.setattr(
        assets_service,
        "fetch_prices",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    r = resolve_symbol("WEIRD")
    assert r.symbol == "WEIRD"


# ---------------------------------------------------------------------------
# add_asset
# ---------------------------------------------------------------------------


def test_add_asset_persists_and_runs_ingest(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "EQUITY"},
        info={"longName": "Palantir Technologies"},
    )

    calls: list[tuple[list[str], str, str]] = []

    def _fake_ingest(
        symbols: list[str], *, period: str = "1d", interval: str = "5m"
    ) -> int:
        calls.append((list(symbols), period, interval))
        return 42

    # Patch at the import source — add_asset imports the symbol lazily.
    import sidecar.scheduler.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "ingest_prices_for_symbols", _fake_ingest)

    result = add_asset("pltr")
    assert result.symbol == "PLTR"
    assert result.name == "Palantir Technologies"
    assert result.asset_type is AssetType.STOCK
    assert result.bars_ingested == 42
    assert result.newly_added is True
    # add_asset must request a 60-day/5-minute backfill, not the scheduler's
    # 1-day default — otherwise the chart only has today's bars and every
    # timeframe longer than ~1H reads "no data" right after the user clicks
    # Add.
    assert calls == [(["PLTR"], "60d", "5m")]

    with session_scope() as s:
        from sqlalchemy import select

        row = s.execute(
            select(Asset).where(Asset.symbol == "PLTR")
        ).scalar_one()
        assert row.is_active is True
        assert row.name == "Palantir Technologies"


def test_add_asset_idempotent_on_duplicate_case_insensitive(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding an already-tracked symbol returns the existing row with
    ``newly_added=False`` — no yfinance hit, no re-ingest. Lets the
    "Track new…" on a non-default watchlist succeed when the asset is
    already in another list.
    """
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK))

    # yfinance + ingest shims that WOULD run on a slow-path add. We assert
    # neither was invoked — the fast path short-circuits before them.
    resolve_calls = {"n": 0}

    def _unexpected_ticker(_sym: str) -> object:
        resolve_calls["n"] += 1
        raise AssertionError("resolve_symbol should not run on idempotent add")

    monkeypatch.setattr("sidecar.services.assets.yf.Ticker", _unexpected_ticker)

    ingest_calls: list[list[str]] = []

    def _spy_ingest(
        symbols: list[str], *, period: str = "1d", interval: str = "5m"
    ) -> int:
        ingest_calls.append(list(symbols))
        return 0

    import sidecar.scheduler.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "ingest_prices_for_symbols", _spy_ingest)

    result = add_asset("aapl")
    assert result.symbol == "AAPL"
    assert result.newly_added is False
    assert result.bars_ingested == 0
    # The whole point of the short-circuit: no fresh yfinance lookup,
    # no 60-day re-ingest.
    assert resolve_calls["n"] == 0
    assert ingest_calls == []


def test_add_asset_ingest_failure_is_non_fatal(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "EQUITY"},
        info={"longName": "Novel Co"},
    )

    def _boom(
        symbols: list[str], *, period: str = "1d", interval: str = "5m"
    ) -> int:
        raise RuntimeError("yahoo melted")

    import sidecar.scheduler.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "ingest_prices_for_symbols", _boom)

    result = add_asset("NVL")
    assert result.symbol == "NVL"
    assert result.bars_ingested == 0  # swallowed, default

    with session_scope() as s:
        from sqlalchemy import select

        row = s.execute(
            select(Asset).where(Asset.symbol == "NVL")
        ).scalar_one()
        assert row is not None


def test_add_asset_propagates_resolve_errors(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ticker(monkeypatch, fast={}, info={})
    monkeypatch.setattr(assets_service, "fetch_prices", lambda *a, **kw: [])

    with pytest.raises(SymbolNotFoundError):
        add_asset("GARBAGE")


# ---------------------------------------------------------------------------
# search_symbols
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Bare-minimum ``requests.Response`` stand-in."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_payload: Any | None = None,
        raise_on_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._json_payload = json_payload
        self._raise_on_json = raise_on_json

    def json(self) -> Any:
        if self._raise_on_json:
            raise ValueError("not JSON")
        return self._json_payload


def _patch_search(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: _FakeResponse | None = None,
    exc: BaseException | None = None,
    calls: list[dict[str, Any]] | None = None,
) -> None:
    """Replace ``requests.get`` used by :func:`search_symbols`."""

    def _get(url: str, **kwargs: Any) -> _FakeResponse:
        if calls is not None:
            calls.append({"url": url, **kwargs})
        if exc is not None:
            raise exc
        assert response is not None
        return response

    monkeypatch.setattr(assets_service.requests, "get", _get)


def test_search_symbols_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "quotes": [
            {
                "symbol": "AAPL",
                "shortname": "Apple Inc.",
                "longname": "Apple Inc.",
                "quoteType": "EQUITY",
                "exchDisp": "NASDAQ",
            },
            {
                "symbol": "APLE",
                "shortname": "Apple Hospitality REIT",
                "quoteType": "EQUITY",
                "exchDisp": "NYSE",
            },
        ]
    }
    _patch_search(monkeypatch, response=_FakeResponse(json_payload=payload))

    hits = assets_service.search_symbols("apple")
    assert len(hits) == 2
    assert hits[0].symbol == "AAPL"
    assert hits[0].name == "Apple Inc."
    assert hits[0].asset_type == AssetType.STOCK
    assert hits[0].exchange == "NASDAQ"
    assert hits[1].symbol == "APLE"
    # shortname fallback when longname is missing
    assert hits[1].name == "Apple Hospitality REIT"


def test_search_symbols_maps_quote_types(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "quotes": [
            {"symbol": "BTC-USD", "shortname": "Bitcoin", "quoteType": "CRYPTOCURRENCY"},
            {"symbol": "SPY", "shortname": "SPDR S&P 500", "quoteType": "ETF"},
            {"symbol": "^GSPC", "shortname": "S&P 500", "quoteType": "INDEX"},
            {"symbol": "CL=F", "shortname": "Crude Oil", "quoteType": "FUTURE"},
        ]
    }
    _patch_search(monkeypatch, response=_FakeResponse(json_payload=payload))

    hits = assets_service.search_symbols("a")
    by_sym = {h.symbol: h.asset_type for h in hits}
    assert by_sym == {
        "BTC-USD": AssetType.CRYPTO,
        "SPY": AssetType.ETF,
        "^GSPC": AssetType.INDEX,
        "CL=F": AssetType.COMMODITY,
    }


def test_search_symbols_dedupes_on_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yahoo sometimes returns the same symbol across multiple exchanges."""
    payload = {
        "quotes": [
            {"symbol": "AAPL", "shortname": "Apple Inc.", "quoteType": "EQUITY", "exchDisp": "NASDAQ"},
            {"symbol": "AAPL", "shortname": "Apple Inc.", "quoteType": "EQUITY", "exchDisp": "NEO"},
        ]
    }
    _patch_search(monkeypatch, response=_FakeResponse(json_payload=payload))

    hits = assets_service.search_symbols("apple")
    assert len(hits) == 1
    assert hits[0].exchange == "NASDAQ"  # first wins


def test_search_symbols_skips_malformed_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "quotes": [
            {"quoteType": "EQUITY"},  # missing symbol
            {"symbol": "AAPL"},  # missing quoteType
            {"symbol": 42, "quoteType": "EQUITY"},  # non-string symbol
            "not a dict",
            {"symbol": "TSLA", "shortname": "Tesla", "quoteType": "EQUITY"},
        ]
    }
    _patch_search(monkeypatch, response=_FakeResponse(json_payload=payload))

    hits = assets_service.search_symbols("test")
    assert [h.symbol for h in hits] == ["TSLA"]


def test_search_symbols_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "quotes": [
            {"symbol": f"SYM{i}", "shortname": f"Thing {i}", "quoteType": "EQUITY"}
            for i in range(15)
        ]
    }
    _patch_search(monkeypatch, response=_FakeResponse(json_payload=payload))

    hits = assets_service.search_symbols("s", limit=5)
    assert len(hits) == 5
    assert [h.symbol for h in hits] == [f"SYM{i}" for i in range(5)]


def test_search_symbols_empty_payload_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_search(monkeypatch, response=_FakeResponse(json_payload={"quotes": []}))
    assert assets_service.search_symbols("xyzzy") == []


def test_search_symbols_missing_quotes_key_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_search(monkeypatch, response=_FakeResponse(json_payload={"news": []}))
    assert assets_service.search_symbols("xyzzy") == []


def test_search_symbols_rejects_empty_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub requests so a leaked network call would be loud.
    _patch_search(monkeypatch, response=_FakeResponse(json_payload={"quotes": []}))
    with pytest.raises(AssetServiceError):
        assets_service.search_symbols("")
    with pytest.raises(AssetServiceError):
        assets_service.search_symbols("   ")


def test_search_symbols_rejects_too_long_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_search(monkeypatch, response=_FakeResponse(json_payload={"quotes": []}))
    with pytest.raises(AssetServiceError):
        assets_service.search_symbols("a" * 100)


def test_search_symbols_rejects_bad_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_search(monkeypatch, response=_FakeResponse(json_payload={"quotes": []}))
    with pytest.raises(AssetServiceError):
        assets_service.search_symbols("aapl", limit=0)
    with pytest.raises(AssetServiceError):
        assets_service.search_symbols("aapl", limit=1000)


def test_search_symbols_http_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidecar.services.assets import SymbolSearchError

    _patch_search(monkeypatch, response=_FakeResponse(status_code=503))
    with pytest.raises(SymbolSearchError):
        assets_service.search_symbols("aapl")


def test_search_symbols_network_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests as real_requests

    from sidecar.services.assets import SymbolSearchError

    _patch_search(monkeypatch, exc=real_requests.ConnectionError("DNS down"))
    with pytest.raises(SymbolSearchError):
        assets_service.search_symbols("aapl")


def test_search_symbols_bad_json_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidecar.services.assets import SymbolSearchError

    _patch_search(
        monkeypatch,
        response=_FakeResponse(json_payload=None, raise_on_json=True),
    )
    with pytest.raises(SymbolSearchError):
        assets_service.search_symbols("aapl")


def test_search_symbols_forwards_query_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_search(
        monkeypatch,
        response=_FakeResponse(json_payload={"quotes": []}),
        calls=calls,
    )
    assets_service.search_symbols("BTC-USD", limit=7)
    assert len(calls) == 1
    params = calls[0]["params"]
    assert params["q"] == "BTC-USD"
    assert params["quotesCount"] == 7
    assert params["newsCount"] == 0
