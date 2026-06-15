from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sidecar.db.engine import get_engine, session_scope
from sidecar.db.models import Asset, AssetType, PricePoint

# Migration files live outside an importable package (names start with a digit),
# so load 0014 by file path to reach its `_repair` helper.
_MIG = (
    Path(__file__).resolve().parents[1]
    / "sidecar/db/migrations/versions/0014_repair_price_intervals.py"
)
_spec = importlib.util.spec_from_file_location("mig_0014", _MIG)
assert _spec and _spec.loader
mig_0014 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig_0014)


def _asset(symbol: str = "AAPL") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=f"{symbol} Inc.", asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _bar(asset_id: int, ts: datetime, interval: str, close: str = "100") -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=ts,
                interval=interval,
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=0,
            )
        )


def _intervals_at(asset_id: int, ts: datetime) -> list[str]:
    with session_scope() as s:
        rows = s.execute(
            PricePoint.__table__.select().where(
                PricePoint.asset_id == asset_id, PricePoint.timestamp == ts
            )
        ).all()
    return sorted(r.interval for r in rows)


def test_repair_relabels_by_timestamp(isolated_db: Path) -> None:
    aid = _asset()
    midnight = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    intraday = datetime(2026, 6, 12, 15, 55, 0, tzinfo=UTC)
    _bar(aid, midnight, "5m")   # daily bar mislabeled 5m
    _bar(aid, intraday, "1d")   # intraday bar mislabeled 1d

    with get_engine().begin() as conn:
        mig_0014._repair(conn)

    assert _intervals_at(aid, midnight) == ["1d"]
    assert _intervals_at(aid, intraday) == ["5m"]

    # Idempotent: a second pass relabels nothing.
    with get_engine().begin() as conn:
        again = mig_0014._repair(conn)
    assert again["relabel_5m_to_1d"] == 0
    assert again["relabel_1d_to_5m"] == 0


def test_repair_dedupes_collisions(isolated_db: Path) -> None:
    aid = _asset()
    ts_mid = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    ts_intra = datetime(2026, 6, 12, 15, 55, 0, tzinfo=UTC)
    _bar(aid, ts_mid, "1d", close="111")    # correct daily already present
    _bar(aid, ts_mid, "5m", close="999")    # mislabeled daily → would collide
    _bar(aid, ts_intra, "5m", close="222")  # correct intraday already present
    _bar(aid, ts_intra, "1d", close="888")  # mislabeled intraday → would collide

    with get_engine().begin() as conn:
        mig_0014._repair(conn)

    # Mislabeled duplicates dropped; the originally-correct bars survive.
    assert _intervals_at(aid, ts_mid) == ["1d"]
    assert _intervals_at(aid, ts_intra) == ["5m"]
    with session_scope() as s:
        mid = s.execute(
            PricePoint.__table__.select().where(
                PricePoint.asset_id == aid, PricePoint.timestamp == ts_mid
            )
        ).one()
        assert mid.close == Decimal("111")  # kept correct daily, dropped 999
