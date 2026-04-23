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
    AssetAlreadyExistsError,
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


def test_add_asset_duplicate_case_insensitive(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK))

    _patch_ticker(
        monkeypatch,
        fast={"quote_type": "EQUITY"},
        info={"longName": "Apple Inc."},
    )

    with pytest.raises(AssetAlreadyExistsError):
        add_asset("aapl")


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
