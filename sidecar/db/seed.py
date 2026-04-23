from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, MacroIndicator

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


@dataclass(frozen=True)
class SeedMacroIndicator:
    series_id: str
    name: str
    description: str | None
    units: str | None
    frequency: str | None


DEFAULT_MACRO_INDICATORS: tuple[SeedMacroIndicator, ...] = (
    # --- Original five (v0.1.0) ---
    SeedMacroIndicator(
        "CPIAUCSL",
        "Consumer Price Index (CPI-U)",
        "All Urban Consumers, seasonally adjusted",
        "Index 1982-84=100",
        "monthly",
    ),
    SeedMacroIndicator(
        "UNRATE",
        "Unemployment Rate",
        "Civilian unemployment rate, seasonally adjusted",
        "%",
        "monthly",
    ),
    SeedMacroIndicator(
        "FEDFUNDS",
        "Federal Funds Effective Rate",
        "Monthly average of daily effective federal funds rate",
        "%",
        "monthly",
    ),
    SeedMacroIndicator(
        "DGS10",
        "10-Year Treasury Constant Maturity Rate",
        "Market yield on U.S. Treasury securities at 10-year constant maturity",
        "%",
        "daily",
    ),
    SeedMacroIndicator(
        "GDP",
        "Gross Domestic Product",
        "Quarterly GDP, seasonally adjusted annual rate",
        "Billions of Dollars",
        "quarterly",
    ),
    # --- Labour + inflation (expansion) ---
    SeedMacroIndicator(
        "PAYEMS",
        "Total Nonfarm Payrolls",
        "All Employees, Total Nonfarm, seasonally adjusted",
        "Thousands of Persons",
        "monthly",
    ),
    SeedMacroIndicator(
        "PCEPILFE",
        "Core PCE Price Index",
        "Personal Consumption Expenditures excluding food and energy, the Fed's preferred inflation gauge",
        "Index 2017=100",
        "monthly",
    ),
    # --- Money supply + output ---
    SeedMacroIndicator(
        "M2SL",
        "M2 Money Stock",
        "M2 money supply, seasonally adjusted",
        "Billions of Dollars",
        "monthly",
    ),
    SeedMacroIndicator(
        "INDPRO",
        "Industrial Production Index",
        "Industrial production, seasonally adjusted",
        "Index 2017=100",
        "monthly",
    ),
    # --- Housing ---
    SeedMacroIndicator(
        "HOUST",
        "Housing Starts",
        "New privately-owned housing units started, seasonally adjusted annual rate",
        "Thousands of Units",
        "monthly",
    ),
    SeedMacroIndicator(
        "MORTGAGE30US",
        "30-Year Fixed Rate Mortgage Average",
        "Freddie Mac primary mortgage market survey, 30-year fixed rate",
        "%",
        "weekly",
    ),
    # --- Market stress + curve ---
    SeedMacroIndicator(
        "VIXCLS",
        "CBOE Volatility Index: VIX",
        "Implied volatility of S&P 500 index options; market 'fear gauge'",
        "Index",
        "daily",
    ),
    SeedMacroIndicator(
        "T10Y2Y",
        "10-Year Minus 2-Year Treasury Spread",
        "10-Year Treasury Constant Maturity minus 2-Year Treasury Constant Maturity; recession indicator when negative",
        "%",
        "daily",
    ),
    # --- Consumer + commodities ---
    SeedMacroIndicator(
        "UMCSENT",
        "University of Michigan Consumer Sentiment",
        "Index of Consumer Sentiment from the Survey of Consumers",
        "Index 1966:Q1=100",
        "monthly",
    ),
    SeedMacroIndicator(
        "DCOILWTICO",
        "WTI Crude Oil Spot Price",
        "West Texas Intermediate crude oil, spot price FOB",
        "Dollars per Barrel",
        "daily",
    ),
)


def _seed_assets_with(session: Session, assets: tuple[SeedAsset, ...]) -> int:
    existing = set(session.execute(select(Asset.symbol)).scalars().all())
    created = 0
    for spec in assets:
        if spec.symbol in existing:
            continue
        session.add(Asset(symbol=spec.symbol, name=spec.name, asset_type=spec.asset_type))
        created += 1
    return created


def _seed_macro_with(
    session: Session, indicators: tuple[SeedMacroIndicator, ...]
) -> int:
    existing = set(session.execute(select(MacroIndicator.series_id)).scalars().all())
    created = 0
    for spec in indicators:
        if spec.series_id in existing:
            continue
        session.add(
            MacroIndicator(
                series_id=spec.series_id,
                name=spec.name,
                description=spec.description,
                units=spec.units,
                frequency=spec.frequency,
            )
        )
        created += 1
    return created


def seed_default_assets() -> int:
    with session_scope() as s:
        created = _seed_assets_with(s, DEFAULT_ASSETS)
    if created:
        logger.info("Seeded %d default assets", created)
    return created


def seed_default_macro_indicators() -> int:
    with session_scope() as s:
        created = _seed_macro_with(s, DEFAULT_MACRO_INDICATORS)
    if created:
        logger.info("Seeded %d default macro indicators", created)
    return created


def seed_all_defaults() -> tuple[int, int]:
    """Seed assets and macro indicators. Returns (assets_created, indicators_created)."""
    return seed_default_assets(), seed_default_macro_indicators()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    a, m = seed_all_defaults()
    print(f"Inserted {a} new assets and {m} new macro indicators")
