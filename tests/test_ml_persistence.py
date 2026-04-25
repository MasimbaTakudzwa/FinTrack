"""Tests for `ml.persistence` — save / load / upsert / delete semantics.

No SARIMAX fit here; we construct `ForecastResult` instances directly so the
tests stay fast (<100 ms each) and deterministic. The forecast math is
covered in `test_ml_forecast.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from ml.forecast import ForecastPoint, ForecastResult
from ml.persistence import (
    _decode_points,
    _encode_points,
    all_forecast_asset_ids,
    delete_forecast,
    load_forecast,
    load_forecast_by_symbol,
    load_snapshots,
    save_forecast,
)
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, Forecast, ForecastSnapshot


def _make_result(horizon: int = 14, training_rows: int = 150) -> ForecastResult:
    points = [
        ForecastPoint(
            forecast_date=date(2026, 4, 20) + timedelta(days=i + 1),
            yhat=150.0 + 0.5 * i,
            lower_80=149.0 + 0.5 * i,
            upper_80=151.0 + 0.5 * i,
            lower_95=148.5 + 0.5 * i,
            upper_95=151.5 + 0.5 * i,
        )
        for i in range(horizon)
    ]
    return ForecastResult(
        model="SARIMAX(1,1,1)",
        horizon_days=horizon,
        training_rows=training_rows,
        last_close=Decimal("150.00"),
        last_close_date=date(2026, 4, 20),
        generated_at=datetime(2026, 4, 21, 12, 0, tzinfo=UTC),
        points=points,
    )


def _seed_asset(symbol: str = "AAPL") -> int:
    with session_scope() as s:
        asset = Asset(symbol=symbol, name=symbol, asset_type=AssetType.STOCK)
        s.add(asset)
        s.flush()
        return asset.id


def test_encode_decode_round_trip() -> None:
    result = _make_result(horizon=5)
    raw = _encode_points(result.points)
    decoded = _decode_points(raw)
    assert len(decoded) == 5
    for orig, back in zip(result.points, decoded, strict=True):
        assert orig.forecast_date == back.forecast_date
        assert orig.yhat == back.yhat
        assert orig.lower_80 == back.lower_80
        assert orig.upper_80 == back.upper_80
        assert orig.lower_95 == back.lower_95
        assert orig.upper_95 == back.upper_95


def test_save_and_load_forecast_round_trip(isolated_db: Path) -> None:
    asset_id = _seed_asset()
    result = _make_result()

    save_forecast(asset_id, result)
    loaded = load_forecast(asset_id)
    assert loaded is not None
    assert loaded.model == result.model
    assert loaded.horizon_days == result.horizon_days
    assert loaded.training_rows == result.training_rows
    assert loaded.last_close == result.last_close
    assert loaded.last_close_date == result.last_close_date
    # SQLite strips tz on storage; the service layer re-stamps UTC on read.
    assert loaded.generated_at.tzinfo is not None
    assert loaded.generated_at.replace(tzinfo=None) == result.generated_at.replace(
        tzinfo=None
    )
    assert len(loaded.points) == len(result.points)


def test_load_forecast_returns_none_when_missing(isolated_db: Path) -> None:
    _seed_asset()
    # Never called save_forecast, so nothing persisted.
    assert load_forecast(999) is None


def test_save_forecast_upserts_over_previous_row(isolated_db: Path) -> None:
    """The unique constraint on asset_id means a retrain replaces the row,
    never accumulates. Verifies both `on_conflict_do_update` wins AND that
    we never end up with two rows per asset."""
    asset_id = _seed_asset()
    first = _make_result(horizon=7, training_rows=100)
    second = _make_result(horizon=14, training_rows=200)

    save_forecast(asset_id, first)
    save_forecast(asset_id, second)

    loaded = load_forecast(asset_id)
    assert loaded is not None
    assert loaded.horizon_days == 14
    assert loaded.training_rows == 200
    assert len(loaded.points) == 14

    # Defence-in-depth: directly count rows for this asset.
    with session_scope() as s:
        rows = s.execute(
            select(Forecast).where(Forecast.asset_id == asset_id)
        ).scalars().all()
        assert len(rows) == 1


def test_load_forecast_by_symbol(isolated_db: Path) -> None:
    asset_id = _seed_asset("AAPL")
    save_forecast(asset_id, _make_result())

    hit = load_forecast_by_symbol("aapl")  # case-insensitive
    assert hit is not None
    aid, result = hit
    assert aid == asset_id
    assert result.horizon_days == 14


def test_load_forecast_by_symbol_unknown_symbol(isolated_db: Path) -> None:
    _seed_asset("AAPL")
    assert load_forecast_by_symbol("NOPE") is None


def test_load_forecast_by_symbol_known_symbol_no_forecast(isolated_db: Path) -> None:
    _seed_asset("AAPL")
    assert load_forecast_by_symbol("AAPL") is None


def test_delete_forecast(isolated_db: Path) -> None:
    asset_id = _seed_asset()
    save_forecast(asset_id, _make_result())
    assert load_forecast(asset_id) is not None

    assert delete_forecast(asset_id) is True
    assert load_forecast(asset_id) is None
    # Second delete is a no-op.
    assert delete_forecast(asset_id) is False


def test_all_forecast_asset_ids(isolated_db: Path) -> None:
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT")
    save_forecast(aapl, _make_result())
    save_forecast(msft, _make_result())

    assert set(all_forecast_asset_ids()) == {aapl, msft}


def test_forecast_deleted_when_asset_deleted(isolated_db: Path) -> None:
    """CASCADE FK — removing an asset should take its forecast with it."""
    asset_id = _seed_asset()
    save_forecast(asset_id, _make_result())
    assert load_forecast(asset_id) is not None

    with session_scope() as s:
        # `PRAGMA foreign_keys=ON` is installed on every connection; deleting
        # the asset should cascade through the forecasts row.
        s.execute(
            Asset.__table__.delete().where(Asset.id == asset_id)
        )

    assert load_forecast(asset_id) is None


# ---------------------------------------------------------------------------
# forecast_snapshots — append-only history backing the accuracy module.
# ---------------------------------------------------------------------------


def test_save_forecast_appends_snapshot(isolated_db: Path) -> None:
    """Each save_forecast inserts a row into forecast_snapshots in addition
    to upserting the latest-row ``forecasts`` table."""
    asset_id = _seed_asset()
    save_forecast(asset_id, _make_result(horizon=7, training_rows=100))
    save_forecast(asset_id, _make_result(horizon=14, training_rows=200))

    with session_scope() as s:
        snaps = (
            s.execute(
                select(ForecastSnapshot).where(
                    ForecastSnapshot.asset_id == asset_id
                )
            )
            .scalars()
            .all()
        )
        # Two saves → two snapshots (append-only, no upsert).
        assert len(snaps) == 2
        # Both snapshots survive even though the latest-row table was
        # overwritten in place.
        horizons = sorted(s.horizon_days for s in snaps)
        assert horizons == [7, 14]


def test_load_snapshots_returns_oldest_first(isolated_db: Path) -> None:
    """``load_snapshots`` orders rows by generated_at ascending so accuracy
    code can iterate them in chronological order without resorting."""
    asset_id = _seed_asset()

    older = _make_result(horizon=14, training_rows=50)
    older = ForecastResult(
        model=older.model,
        horizon_days=older.horizon_days,
        training_rows=older.training_rows,
        last_close=older.last_close,
        last_close_date=older.last_close_date,
        generated_at=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
        points=older.points,
    )
    newer = _make_result(horizon=14, training_rows=150)
    newer = ForecastResult(
        model=newer.model,
        horizon_days=newer.horizon_days,
        training_rows=newer.training_rows,
        last_close=newer.last_close,
        last_close_date=newer.last_close_date,
        generated_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        points=newer.points,
    )

    save_forecast(asset_id, older)
    save_forecast(asset_id, newer)

    snaps = load_snapshots(asset_id)
    assert len(snaps) == 2
    assert snaps[0].training_rows == 50  # older one first
    assert snaps[1].training_rows == 150


def test_load_snapshots_filters_by_window(isolated_db: Path) -> None:
    asset_id = _seed_asset()
    long_ago = _make_result()
    long_ago = ForecastResult(
        model=long_ago.model,
        horizon_days=long_ago.horizon_days,
        training_rows=long_ago.training_rows,
        last_close=long_ago.last_close,
        last_close_date=long_ago.last_close_date,
        # 200 days back — outside any reasonable accuracy window.
        generated_at=datetime.now(UTC) - timedelta(days=200),
        points=long_ago.points,
    )
    recent = _make_result()
    recent = ForecastResult(
        model=recent.model,
        horizon_days=recent.horizon_days,
        training_rows=recent.training_rows,
        last_close=recent.last_close,
        last_close_date=recent.last_close_date,
        generated_at=datetime.now(UTC) - timedelta(days=5),
        points=recent.points,
    )
    save_forecast(asset_id, long_ago)
    save_forecast(asset_id, recent)

    # 30-day window keeps only the recent snapshot.
    snaps = load_snapshots(asset_id, since_days=30)
    assert len(snaps) == 1


def test_snapshots_cascade_with_asset_delete(isolated_db: Path) -> None:
    """CASCADE FK — deleting an asset wipes its snapshot history too."""
    asset_id = _seed_asset()
    save_forecast(asset_id, _make_result())

    with session_scope() as s:
        s.execute(Asset.__table__.delete().where(Asset.id == asset_id))

    assert load_snapshots(asset_id) == []
