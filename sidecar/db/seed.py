from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedAsset:
    symbol: str
    name: str
    asset_type: AssetType


DEFAULT_ASSETS: tuple[SeedAsset, ...] = (
    SeedAsset("AAPL", "Apple Inc.", AssetType.STOCK),
    SeedAsset("MSFT", "Microsoft Corporation", AssetType.STOCK),
    SeedAsset("GOOGL", "Alphabet Inc. Class A", AssetType.STOCK),
    SeedAsset("NVDA", "NVIDIA Corporation", AssetType.STOCK),
    SeedAsset("SPY", "SPDR S&P 500 ETF Trust", AssetType.ETF),
    SeedAsset("QQQ", "Invesco QQQ Trust", AssetType.ETF),
    SeedAsset("GLD", "SPDR Gold Shares", AssetType.ETF),
    SeedAsset("BTC-USD", "Bitcoin", AssetType.CRYPTO),
    SeedAsset("ETH-USD", "Ethereum", AssetType.CRYPTO),
    SeedAsset("SOL-USD", "Solana", AssetType.CRYPTO),
)


def _seed_with(session: Session, assets: tuple[SeedAsset, ...]) -> int:
    existing = set(
        session.execute(select(Asset.symbol)).scalars().all()
    )
    created = 0
    for spec in assets:
        if spec.symbol in existing:
            continue
        session.add(Asset(symbol=spec.symbol, name=spec.name, asset_type=spec.asset_type))
        created += 1
    return created


def seed_default_assets() -> int:
    with session_scope() as s:
        created = _seed_with(s, DEFAULT_ASSETS)
    if created:
        logger.info("Seeded %d default assets", created)
    return created


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = seed_default_assets()
    print(f"Inserted {n} new assets")
