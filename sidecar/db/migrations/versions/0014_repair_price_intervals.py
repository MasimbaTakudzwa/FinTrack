"""repair mislabeled price_points intervals

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-15 00:00:00

Historical data accumulated across app versions left the ``interval`` column on
``price_points`` unreliable: many genuine DAILY bars (timestamped at midnight)
were tagged ``"5m"`` — chiefly because migration 0008 backfilled every
pre-Phase-2 row as ``"5m"`` — and many genuine INTRADAY bars (non-midnight
timestamps) were tagged ``"1d"`` because the daily ingest stamped the
in-progress "today" bar at the live market time and every re-run created a new
mislabeled row.

The fix is a one-time reclassification by the only reliable signal we have, the
timestamp's time-of-day: a midnight bar is daily, anything else is intraday.
Conflicts (a row already exists at the same ``(asset_id, timestamp)`` with the
target interval) are resolved by dropping the duplicate before relabeling, so
the ``uq_price_points_asset_ts_interval`` constraint is never violated.

Idempotent: after one pass no midnight ``"5m"`` or non-midnight ``"1d"`` rows
remain, so a second run is a no-op. Forward recurrence is prevented separately
by flooring daily-bar timestamps to midnight in the fetcher.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# substr(timestamp, 12, 8) -> "HH:MM:SS" (positions 12-19 of "YYYY-MM-DD HH:MM:SS...").
_MIDNIGHT = "substr(timestamp, 12, 8) = '00:00:00'"
_INTRADAY = "substr(timestamp, 12, 8) <> '00:00:00'"


def _repair(bind: sa.engine.Connection) -> dict[str, int]:
    """Reclassify rows by timestamp; return per-step affected counts."""
    counts: dict[str, int] = {}

    # 1. Midnight bars mislabeled "5m" are really daily → "1d".
    #    Drop any that would collide with an existing "1d" at the same key.
    counts["drop_5m_midnight_dupes"] = bind.execute(
        sa.text(
            f"""
            DELETE FROM price_points
            WHERE interval = '5m' AND {_MIDNIGHT}
              AND EXISTS (
                SELECT 1 FROM price_points x
                WHERE x.asset_id = price_points.asset_id
                  AND x.timestamp = price_points.timestamp
                  AND x.interval = '1d'
              )
            """
        )
    ).rowcount
    counts["relabel_5m_to_1d"] = bind.execute(
        sa.text(f"UPDATE price_points SET interval = '1d' WHERE interval = '5m' AND {_MIDNIGHT}")
    ).rowcount

    # 2. Intraday-timestamped bars mislabeled "1d" are really intraday → "5m".
    counts["drop_1d_intraday_dupes"] = bind.execute(
        sa.text(
            f"""
            DELETE FROM price_points
            WHERE interval = '1d' AND {_INTRADAY}
              AND EXISTS (
                SELECT 1 FROM price_points x
                WHERE x.asset_id = price_points.asset_id
                  AND x.timestamp = price_points.timestamp
                  AND x.interval = '5m'
              )
            """
        )
    ).rowcount
    counts["relabel_1d_to_5m"] = bind.execute(
        sa.text(f"UPDATE price_points SET interval = '5m' WHERE interval = '1d' AND {_INTRADAY}")
    ).rowcount

    return counts


def upgrade() -> None:
    _repair(op.get_bind())


def downgrade() -> None:
    # One-way data repair — the original (mislabeled) state can't be
    # reconstructed and we wouldn't want to. No-op.
    pass
