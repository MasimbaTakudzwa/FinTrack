"""Storage layer for the forecasting engine.

Responsibilities:
- Encode a `ForecastResult` into the `forecasts` row shape (JSON for the
  variable-length points list, scalar columns for everything else).
- Decode a row back into a `ForecastResult` the UI / API layer can hand off.
- Upsert over the previous row for an asset — one forecast per asset, always
  the latest. See `0009_create_forecasts.py` for the table-level rationale.

We use SQLite's `INSERT ... ON CONFLICT(asset_id) DO UPDATE` so the happy path
is a single round-trip, and the unique constraint guarantees we can't ever
hold two rows for the same asset (even under a race between the scheduled
weekly retrain and a user-triggered "retrain now").
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ml.forecast import ForecastPoint, ForecastResult
from sidecar.db.engine import session_scope
from sidecar.db.models import Forecast

logger = logging.getLogger(__name__)


def _encode_points(points: list[ForecastPoint]) -> str:
    """Serialize the forecast points to a JSON string for the Text column.

    Dates are emitted as ISO-8601 (``YYYY-MM-DD``) so the decode path can
    round-trip them via `date.fromisoformat` without a custom parser. All
    numeric values stay as floats — matches the dataclass field types.
    """
    payload = [
        {
            "forecast_date": p.forecast_date.isoformat(),
            "yhat": p.yhat,
            "lower_80": p.lower_80,
            "upper_80": p.upper_80,
            "lower_95": p.lower_95,
            "upper_95": p.upper_95,
        }
        for p in points
    ]
    return json.dumps(payload, separators=(",", ":"))


def _decode_points(raw: str) -> list[ForecastPoint]:
    """Inverse of ``_encode_points``. Tolerant of missing CI fields so a future
    schema bump that ships without 95% bands (for example) still loads as a
    partial forecast rather than raising here.
    """
    data = json.loads(raw)
    out: list[ForecastPoint] = []
    for item in data:
        out.append(
            ForecastPoint(
                forecast_date=date.fromisoformat(item["forecast_date"]),
                yhat=float(item["yhat"]),
                lower_80=float(item.get("lower_80", item["yhat"])),
                upper_80=float(item.get("upper_80", item["yhat"])),
                lower_95=float(item.get("lower_95", item["yhat"])),
                upper_95=float(item.get("upper_95", item["yhat"])),
            )
        )
    return out


def _row_to_result(row: Forecast) -> ForecastResult:
    """Hydrate a `Forecast` ORM row into the dataclass the API / UI consume.

    SQLite doesn't store timezone info on DateTime columns, so ``generated_at``
    comes back naive — we stamp it back to UTC so callers can always assume
    aware datetimes (consistent with how every other datetime flows through
    the app).
    """
    generated = row.generated_at
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    return ForecastResult(
        model=row.model,
        horizon_days=row.horizon_days,
        training_rows=row.training_rows,
        last_close=row.last_close,
        last_close_date=row.last_close_date,
        generated_at=generated,
        points=_decode_points(row.points_json),
    )


def save_forecast(asset_id: int, result: ForecastResult) -> None:
    """Upsert the latest forecast for ``asset_id``.

    At most one row exists per asset (`uq_forecasts_asset_id`), so a retrain
    replaces the previous row wholesale. Safe to call concurrently — SQLite
    serialises writes and the unique constraint + `ON CONFLICT DO UPDATE`
    guarantees idempotency.
    """
    payload = {
        "asset_id": asset_id,
        "model": result.model,
        "horizon_days": result.horizon_days,
        "training_rows": result.training_rows,
        "last_close": result.last_close,
        "last_close_date": result.last_close_date,
        "generated_at": result.generated_at,
        "points_json": _encode_points(result.points),
    }
    # Everything except the PK (id) and FK (asset_id) is overwritten on
    # conflict — a retrain is semantically a full replacement, not a merge.
    update_set = {k: v for k, v in payload.items() if k != "asset_id"}

    with session_scope() as session:
        stmt = (
            sqlite_insert(Forecast)
            .values(**payload)
            .on_conflict_do_update(
                index_elements=["asset_id"],
                set_=update_set,
            )
        )
        session.execute(stmt)
    logger.info(
        "save_forecast: asset_id=%d horizon=%d training_rows=%d last_close=%s",
        asset_id,
        result.horizon_days,
        result.training_rows,
        result.last_close,
    )


def _load_in_session(session: Session, asset_id: int) -> ForecastResult | None:
    row = session.execute(
        select(Forecast).where(Forecast.asset_id == asset_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    return _row_to_result(row)


def load_forecast(asset_id: int) -> ForecastResult | None:
    """Return the latest forecast for ``asset_id`` or None if none exists."""
    with session_scope() as session:
        return _load_in_session(session, asset_id)


def load_forecast_by_symbol(symbol: str) -> tuple[int, ForecastResult] | None:
    """Convenience lookup: forecast + owning asset id by symbol (case-insensitive).

    Returns ``(asset_id, result)`` so callers (API endpoint) can decide
    whether to surface the result or kick off a retrain, without a second
    DB query. Returns None when either the symbol is unknown or the asset
    has no forecast row yet.
    """
    from sidecar.db.models import Asset  # local import to avoid circular deps

    sym = symbol.strip().upper()
    with session_scope() as session:
        asset_id = session.execute(
            select(Asset.id).where(Asset.symbol == sym)
        ).scalar_one_or_none()
        if asset_id is None:
            return None
        result = _load_in_session(session, asset_id)
        if result is None:
            return None
        return asset_id, result


def delete_forecast(asset_id: int) -> bool:
    """Remove the forecast row for an asset. Returns True if a row was deleted."""
    with session_scope() as session:
        # SQLAlchemy's type stubs return `Result[Any]` which doesn't expose
        # ``rowcount``; the runtime object is a ``CursorResult`` for DML, so
        # cast through to satisfy mypy --strict. Same pattern used elsewhere
        # in the codebase (see Sprint 2 session notes).
        result = cast(
            "CursorResult[Any]",
            session.execute(
                delete(Forecast).where(Forecast.asset_id == asset_id)
            ),
        )
        deleted = (result.rowcount or 0) > 0
    if deleted:
        logger.info("delete_forecast: removed row for asset_id=%d", asset_id)
    return deleted


def all_forecast_asset_ids() -> list[int]:
    """List every asset id that has a stored forecast, for cleanup / admin paths."""
    with session_scope() as session:
        rows = session.execute(select(Forecast.asset_id)).scalars().all()
    return list(rows)
