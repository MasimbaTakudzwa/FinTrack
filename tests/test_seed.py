from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType
from sidecar.db.seed import DEFAULT_ASSETS, seed_default_assets


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
