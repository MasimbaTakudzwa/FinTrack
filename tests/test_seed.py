from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, MacroIndicator
from sidecar.db.seed import (
    DEFAULT_ASSETS,
    DEFAULT_MACRO_INDICATORS,
    seed_all_defaults,
    seed_default_assets,
    seed_default_macro_indicators,
)


def test_seed_default_assets_inserts_all(isolated_db: Path) -> None:
    created = seed_default_assets()
    assert created == len(DEFAULT_ASSETS)

    with session_scope() as s:
        rows = s.execute(select(Asset).order_by(Asset.symbol)).scalars().all()
        symbols = {a.symbol for a in rows}
        assert symbols == {spec.symbol for spec in DEFAULT_ASSETS}

        by_symbol = {a.symbol: a for a in rows}
        assert by_symbol["AAPL"].asset_type == AssetType.STOCK
        assert by_symbol["SPY"].asset_type == AssetType.ETF
        assert by_symbol["BTC-USD"].asset_type == AssetType.CRYPTO


def test_seed_default_assets_is_idempotent(isolated_db: Path) -> None:
    assert seed_default_assets() == len(DEFAULT_ASSETS)
    assert seed_default_assets() == 0

    with session_scope() as s:
        count = s.execute(select(Asset)).scalars().all()
        assert len(count) == len(DEFAULT_ASSETS)


def test_seed_default_macro_indicators_inserts_all(isolated_db: Path) -> None:
    created = seed_default_macro_indicators()
    assert created == len(DEFAULT_MACRO_INDICATORS)

    with session_scope() as s:
        rows = s.execute(select(MacroIndicator)).scalars().all()
        series = {r.series_id for r in rows}
        assert series == {spec.series_id for spec in DEFAULT_MACRO_INDICATORS}


def test_seed_default_macro_indicators_is_idempotent(isolated_db: Path) -> None:
    assert seed_default_macro_indicators() == len(DEFAULT_MACRO_INDICATORS)
    assert seed_default_macro_indicators() == 0


def test_seed_all_defaults_returns_both_counts(isolated_db: Path) -> None:
    assets, indicators = seed_all_defaults()
    assert assets == len(DEFAULT_ASSETS)
    assert indicators == len(DEFAULT_MACRO_INDICATORS)
