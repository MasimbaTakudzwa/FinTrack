from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sidecar.db.base import Base


class AssetType(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"
    COMMODITY = "commodity"
    INDEX = "index"


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    asset_type: Mapped[AssetType] = mapped_column(
        SQLEnum(
            AssetType,
            name="asset_type_enum",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    price_points: Mapped[list[PricePoint]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class PricePoint(Base):
    __tablename__ = "price_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    volume: Mapped[int] = mapped_column(BigInteger, default=0)

    asset: Mapped[Asset] = relationship(back_populates="price_points")

    __table_args__ = (
        UniqueConstraint("asset_id", "timestamp", name="uq_price_points_asset_ts"),
        Index("ix_price_points_asset_ts", "asset_id", "timestamp"),
    )
