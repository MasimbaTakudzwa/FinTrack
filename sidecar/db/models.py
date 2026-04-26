from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
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


class AlertDirection(StrEnum):
    ABOVE = "above"
    BELOW = "below"


class AlertMetric(StrEnum):
    """Which signal an alert thresholds.

    ``PRICE`` (default) — fires when the latest close crosses the threshold.
    ``SENTIMENT`` — fires when the rolling-mean compound sentiment over
    ``window_days`` crosses the threshold (range typically -1..+1).
    """

    PRICE = "price"
    SENTIMENT = "sentiment"


class TransactionType(StrEnum):
    """Buy/sell side of a portfolio transaction."""

    BUY = "buy"
    SELL = "sell"


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
    # Resolution of this bar. Two values are produced today: "5m" by the
    # 5-minute intraday ingest (default for `ingest_prices`) and "1d" by the
    # daily ingest added in Phase 2 as the training base for the forecasting
    # engine. The column is free-form so future intervals ("1h", "15m", …)
    # don't require another migration.
    interval: Mapped[str] = mapped_column(String(16), default="5m")
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    volume: Mapped[int] = mapped_column(BigInteger, default=0)

    asset: Mapped[Asset] = relationship(back_populates="price_points")

    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "timestamp",
            "interval",
            name="uq_price_points_asset_ts_interval",
        ),
        Index("ix_price_points_asset_ts", "asset_id", "timestamp"),
    )


class MacroIndicator(Base):
    __tablename__ = "macro_indicators"

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    units: Mapped[str | None] = mapped_column(String(128), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    data_points: Mapped[list[MacroDataPoint]] = relationship(
        back_populates="indicator", cascade="all, delete-orphan"
    )


class MacroDataPoint(Base):
    __tablename__ = "macro_data_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    indicator_id: Mapped[int] = mapped_column(
        ForeignKey("macro_indicators.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6))

    indicator: Mapped[MacroIndicator] = relationship(back_populates="data_points")

    __table_args__ = (
        UniqueConstraint("indicator_id", "date", name="uq_macro_data_points_ind_date"),
        Index("ix_macro_data_points_ind_date", "indicator_id", "date"),
    )


class Article(Base):
    """News article harvested from RSS feeds.

    `url` is the dedup key — the same article appearing on multiple asset feeds
    is stored once and linked via `article_assets` to every associated asset.
    """

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    headline: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(128))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # VADER compound score in [-1.0, +1.0]. Nullable means "not yet scored" —
    # set by `ingest_news` inline on insert and by the `score_articles`
    # backfill job for historical rows imported before sentiment was wired.
    sentiment: Mapped[float | None] = mapped_column(
        Float, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    assets: Mapped[list[Asset]] = relationship(
        secondary="article_assets",
        backref="articles",
    )


class ArticleAsset(Base):
    """Many-to-many association between articles and assets."""

    __tablename__ = "article_assets"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Watchlist(Base):
    """A named list of assets the user is tracking.

    Single-user app: `name` is unique; exactly one watchlist has `is_default=True`
    at any given time (the Dashboard reads from this one). The default cannot be
    deleted; renaming is allowed.
    """

    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    items: Mapped[list[WatchlistItem]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
        order_by="WatchlistItem.position",
    )


class WatchlistItem(Base):
    """An asset on a watchlist. `position` is 0-indexed, dense, caller-maintained."""

    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")
    asset: Mapped[Asset] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "asset_id", name="uq_watchlist_items_list_asset"
        ),
    )


class PriceAlert(Base):
    """A user-configured threshold crossing alert for a single asset.

    Semantics are one-shot: when the scheduler detects a crossing it stamps
    ``triggered_at`` (and the alert stops re-firing). The shell polls for rows
    with ``triggered_at IS NOT NULL AND notified_at IS NULL``, fires a native
    desktop notification, then calls the mark-notified endpoint to set
    ``notified_at`` — this is resilient across shell restarts since the
    polling snapshot is always based on persisted state. To re-arm, the user
    toggles via the UI which clears both timestamps.

    ``is_active`` lets the user pause an alert without deleting it (e.g. to
    avoid noise during earnings) while preserving its config.
    """

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    direction: Mapped[AlertDirection] = mapped_column(
        SQLEnum(
            AlertDirection,
            name="alert_direction_enum",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    # Which signal the alert thresholds — "price" (latest close) or
    # "sentiment" (rolling-mean compound score over ``window_days``).
    # Stored as a free-form String rather than SQLEnum so adding a third
    # metric (volatility, volume, …) is a no-migration change.
    metric: Mapped[str] = mapped_column(
        String(32), default="price", server_default="price"
    )
    # Rolling-window length in days — only meaningful for sentiment
    # alerts. NULL for price alerts.
    window_days: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    asset: Mapped[Asset] = relationship()


class Setting(Base):
    """Key-value runtime settings persisted in SQLite.

    Effective config precedence: DB value (this table) > env var > hardcoded default.
    See sidecar.services.settings for the list of known keys and their types.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Forecast(Base):
    """Latest forecast for a single asset.

    At most one row per asset — a fresh retrain UPSERTs over the previous
    result. We keep the points payload as JSON (rather than a separate
    ``forecast_points`` table) because a 14-day horizon is only ~14 small
    objects; the read path is always "hydrate the whole thing at once for
    the chart" and the write path replaces the row wholesale. JSON keeps the
    schema simple and makes it trivial to expand the point shape (e.g. add
    99% CIs) without a migration.

    ``last_close_*`` fields mirror the training-tail state at the moment the
    forecast was generated — the UI compares them against today's latest
    close to decide whether the forecast has drifted and is worth displaying.
    """

    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    model: Mapped[str] = mapped_column(String(64))
    horizon_days: Mapped[int] = mapped_column(default=14)
    training_rows: Mapped[int] = mapped_column(default=0)
    last_close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    last_close_date: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    # JSON-encoded list[ForecastPoint]. Decoded by the service layer so the
    # DB model stays schema-free. See ml/persistence.py for the shape.
    points_json: Mapped[str] = mapped_column(Text)

    asset: Mapped[Asset] = relationship()


class PortfolioTransaction(Base):
    """Append-only buy/sell log driving position computation.

    No mutable position state lives in the DB — every read derives
    quantity, average cost, and realized P&L from the transaction
    history (see :mod:`sidecar.services.portfolio`). Append-only keeps
    the audit trail intact and avoids the sync issues a parallel
    "positions" table would introduce.
    """

    __tablename__ = "portfolio_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE")
    )
    # Stored as a free-form String rather than SQLEnum so adding a
    # third type ("dividend") later is a no-migration change. The
    # application enum is ``TransactionType``.
    transaction_type: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    price_per_unit: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    transaction_date: Mapped[date] = mapped_column(Date)
    fee: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), default=Decimal("0"), server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    asset: Mapped[Asset] = relationship()

    __table_args__ = (
        Index(
            "ix_portfolio_transactions_asset_date",
            "asset_id",
            "transaction_date",
        ),
    )


class ForecastSnapshot(Base):
    """Append-only log of every forecast ever generated for an asset.

    Hot path (``forecasts`` table) keeps the latest row per asset for
    fast chart overlay. This table preserves history so accuracy
    metrics — MAPE, RMSE, directional accuracy — can be computed
    *after* the horizon elapses (was the forecast we made 14 days ago
    actually any good?).

    Rows are never updated or deleted by application code; the cascading
    FK on ``asset_id`` is the only delete path.
    """

    __tablename__ = "forecast_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE")
    )
    model: Mapped[str] = mapped_column(String(64))
    horizon_days: Mapped[int] = mapped_column(default=14)
    training_rows: Mapped[int] = mapped_column(default=0)
    last_close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    last_close_date: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    points_json: Mapped[str] = mapped_column(Text)

    asset: Mapped[Asset] = relationship()

    __table_args__ = (
        Index(
            "ix_forecast_snapshots_asset_time",
            "asset_id",
            "generated_at",
        ),
    )
