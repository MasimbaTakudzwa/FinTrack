"""Price alert service — CRUD, crossing detection, and shell-poller handoff.

Domain model
------------
A ``PriceAlert`` is a user-configured threshold-crossing alert on a single
asset. It is one-shot: the scheduler stamps ``triggered_at`` the first time
the latest close crosses the threshold in the configured direction, and the
alert then becomes quiescent until the user explicitly resets it. Resetting
clears both ``triggered_at`` and ``notified_at`` so it becomes eligible again.

Shell poller handshake
----------------------
Delivery to the OS notification surface goes through a polling handshake
(simpler than SSE, and resilient across shell restarts):

1. Scheduler job calls ``check_alerts()`` which finds active+untriggered
   alerts whose latest PricePoint satisfies the crossing and stamps
   ``triggered_at``.
2. The shell polls ``GET /api/alerts/pending-notifications/`` → returns rows
   where ``triggered_at IS NOT NULL AND notified_at IS NULL``.
3. The shell fires a native notification for each, then POSTs to
   ``/api/alerts/{id}/mark-notified`` → stamps ``notified_at``.
4. Because both timestamps are persisted, a shell crash between steps 2 and
   3 simply replays the same notification next poll — no loss, at worst a
   duplicate ping.

All functions raise ``AlertError`` subclasses on business-rule violations.
API endpoints translate those into HTTP 4xx; the service itself has no
FastAPI dependency so it can be invoked from scripts + tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import AlertDirection, Asset, PriceAlert, PricePoint

logger = logging.getLogger(__name__)


class AlertError(ValueError):
    """Business-rule violation (not found, invalid input, etc.)."""


class AlertNotFoundError(AlertError):
    pass


class AssetNotFoundError(AlertError):
    pass


@dataclass(frozen=True)
class AlertOut:
    id: int
    asset_id: int
    symbol: str
    asset_name: str
    threshold: Decimal
    direction: AlertDirection
    is_active: bool
    triggered_at: datetime | None
    notified_at: datetime | None
    note: str | None
    created_at: datetime
    last_price: Decimal | None
    last_price_at: datetime | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise AlertError(f"{field}: not a valid decimal") from exc
    if d <= 0:
        raise AlertError(f"{field}: must be > 0")
    return d


def _latest_point_by_asset(session: Any, asset_ids: list[int]) -> dict[int, PricePoint]:
    """Return a {asset_id: latest PricePoint} map.

    Done with a correlated subquery per asset to stay portable across SQLite
    versions — there are at most a few dozen asset rows so the cost is fine.
    """
    out: dict[int, PricePoint] = {}
    if not asset_ids:
        return out
    for aid in asset_ids:
        p = session.execute(
            select(PricePoint)
            .where(PricePoint.asset_id == aid)
            .order_by(PricePoint.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()
        if p is not None:
            out[aid] = p
    return out


def _is_crossed(direction: AlertDirection, close: Decimal, threshold: Decimal) -> bool:
    if direction == AlertDirection.ABOVE:
        return close >= threshold
    return close <= threshold


def _hydrate(
    alert: PriceAlert,
    asset: Asset,
    latest: PricePoint | None,
) -> AlertOut:
    return AlertOut(
        id=alert.id,
        asset_id=alert.asset_id,
        symbol=asset.symbol,
        asset_name=asset.name,
        threshold=alert.threshold,
        direction=alert.direction,
        is_active=alert.is_active,
        triggered_at=alert.triggered_at,
        notified_at=alert.notified_at,
        note=alert.note,
        created_at=alert.created_at,
        last_price=latest.close if latest is not None else None,
        last_price_at=latest.timestamp if latest is not None else None,
    )


# ---------------------------------------------------------------------------
# Public API — queries
# ---------------------------------------------------------------------------


def list_alerts(
    *, asset_id: int | None = None, active_only: bool = False
) -> list[AlertOut]:
    with session_scope() as s:
        stmt = select(PriceAlert, Asset).join(Asset, Asset.id == PriceAlert.asset_id)
        if asset_id is not None:
            stmt = stmt.where(PriceAlert.asset_id == asset_id)
        if active_only:
            stmt = stmt.where(PriceAlert.is_active.is_(True))
        stmt = stmt.order_by(PriceAlert.created_at.desc())
        rows = list(s.execute(stmt).all())

        asset_ids = list({int(r[1].id) for r in rows})
        latest = _latest_point_by_asset(s, asset_ids)

        return [_hydrate(alert, asset, latest.get(asset.id)) for alert, asset in rows]


def get_alert(alert_id: int) -> AlertOut:
    with session_scope() as s:
        row = s.execute(
            select(PriceAlert, Asset)
            .join(Asset, Asset.id == PriceAlert.asset_id)
            .where(PriceAlert.id == alert_id)
        ).one_or_none()
        if row is None:
            raise AlertNotFoundError(f"alert {alert_id} not found")
        alert, asset = row
        latest = _latest_point_by_asset(s, [asset.id]).get(asset.id)
        return _hydrate(alert, asset, latest)


def list_pending_notifications() -> list[AlertOut]:
    """Alerts that have fired but haven't been shown as an OS notification yet."""
    with session_scope() as s:
        rows = list(
            s.execute(
                select(PriceAlert, Asset)
                .join(Asset, Asset.id == PriceAlert.asset_id)
                .where(
                    PriceAlert.triggered_at.is_not(None),
                    PriceAlert.notified_at.is_(None),
                )
                .order_by(PriceAlert.triggered_at)
            ).all()
        )
        asset_ids = list({int(r[1].id) for r in rows})
        latest = _latest_point_by_asset(s, asset_ids)
        return [_hydrate(alert, asset, latest.get(asset.id)) for alert, asset in rows]


# ---------------------------------------------------------------------------
# Public API — mutations
# ---------------------------------------------------------------------------


def create_alert(
    *,
    asset_id: int,
    threshold: Any,
    direction: AlertDirection | str,
    note: str | None = None,
) -> AlertOut:
    thr = _to_decimal(threshold, field="threshold")
    dir_enum = _parse_direction(direction)
    note_clean: str | None = None
    if note is not None:
        note_clean = note.strip()
        if len(note_clean) > 256:
            raise AlertError("note: must be <= 256 chars")
        if not note_clean:
            note_clean = None

    with session_scope() as s:
        asset = s.get(Asset, asset_id)
        if asset is None:
            raise AssetNotFoundError(f"asset {asset_id} not found")
        alert = PriceAlert(
            asset_id=asset_id,
            threshold=thr,
            direction=dir_enum,
            is_active=True,
            note=note_clean,
        )
        s.add(alert)
        s.flush()
        latest = _latest_point_by_asset(s, [asset_id]).get(asset_id)
        return _hydrate(alert, asset, latest)


def update_alert(
    alert_id: int,
    *,
    threshold: Any | None = None,
    direction: AlertDirection | str | None = None,
    is_active: bool | None = None,
    note: str | None = None,
    update_note: bool = False,
    reset: bool = False,
) -> AlertOut:
    """Patch-style update. Any of the fields may be None to leave unchanged.

    ``reset=True`` clears both ``triggered_at`` and ``notified_at`` so the
    alert is re-armed.

    ``note`` is only applied when ``update_note=True`` — this lets callers pass
    ``note=None`` to explicitly clear the field (vs. just omitting it). The
    API layer sets ``update_note=True`` exactly when the client sent the
    ``note`` key in the PATCH body.
    """
    with session_scope() as s:
        alert = s.get(PriceAlert, alert_id)
        if alert is None:
            raise AlertNotFoundError(f"alert {alert_id} not found")
        asset = s.get(Asset, alert.asset_id)
        if asset is None:  # pragma: no cover — FK cascade makes this unreachable
            raise AssetNotFoundError(f"asset {alert.asset_id} not found")

        if threshold is not None:
            alert.threshold = _to_decimal(threshold, field="threshold")
        if direction is not None:
            alert.direction = _parse_direction(direction)
        if is_active is not None:
            alert.is_active = bool(is_active)
        if update_note:
            if note is None:
                alert.note = None
            else:
                clean = note.strip()
                if len(clean) > 256:
                    raise AlertError("note: must be <= 256 chars")
                alert.note = clean or None
        if reset:
            alert.triggered_at = None
            alert.notified_at = None

        s.flush()
        latest = _latest_point_by_asset(s, [asset.id]).get(asset.id)
        return _hydrate(alert, asset, latest)


def delete_alert(alert_id: int) -> None:
    with session_scope() as s:
        alert = s.get(PriceAlert, alert_id)
        if alert is None:
            raise AlertNotFoundError(f"alert {alert_id} not found")
        s.delete(alert)


def mark_notified(alert_id: int) -> AlertOut:
    """Stamp ``notified_at`` — called by the shell after firing the OS notification.

    Idempotent: re-marking an already-notified alert leaves the first stamp in
    place. Refuses alerts that haven't been triggered (programmer error on the
    shell side).
    """
    now = datetime.now(UTC)
    with session_scope() as s:
        alert = s.get(PriceAlert, alert_id)
        if alert is None:
            raise AlertNotFoundError(f"alert {alert_id} not found")
        asset = s.get(Asset, alert.asset_id)
        if asset is None:  # pragma: no cover
            raise AssetNotFoundError(f"asset {alert.asset_id} not found")
        if alert.triggered_at is None:
            raise AlertError(
                f"alert {alert_id} has not triggered — nothing to mark as notified"
            )
        if alert.notified_at is None:
            alert.notified_at = now
            s.flush()
        latest = _latest_point_by_asset(s, [asset.id]).get(asset.id)
        return _hydrate(alert, asset, latest)


# ---------------------------------------------------------------------------
# Scheduler-facing API
# ---------------------------------------------------------------------------


def check_alerts() -> int:
    """Scan active alerts for threshold crossings and stamp ``triggered_at``.

    Returns the number of alerts newly fired by this pass. Safe to call
    concurrently with CRUD writes — we only touch active+untriggered rows and
    always re-check ``triggered_at`` inside the transaction.
    """
    fired = 0
    with session_scope() as s:
        alerts = list(
            s.execute(
                select(PriceAlert).where(
                    PriceAlert.is_active.is_(True),
                    PriceAlert.triggered_at.is_(None),
                )
            ).scalars()
        )
        if not alerts:
            return 0

        asset_ids = list({alert.asset_id for alert in alerts})
        latest = _latest_point_by_asset(s, asset_ids)
        now = datetime.now(UTC)

        for alert in alerts:
            point = latest.get(alert.asset_id)
            if point is None:
                continue
            if alert.triggered_at is not None:  # defensive re-check
                continue
            if _is_crossed(alert.direction, point.close, alert.threshold):
                alert.triggered_at = now
                fired += 1

    if fired:
        logger.info("check_alerts: fired %d alerts", fired)
    return fired


# ---------------------------------------------------------------------------
# Direction helper
# ---------------------------------------------------------------------------


def _parse_direction(value: AlertDirection | str) -> AlertDirection:
    if isinstance(value, AlertDirection):
        return value
    if not isinstance(value, str):
        raise AlertError(f"direction: expected string, got {type(value).__name__}")
    try:
        return AlertDirection(value.lower())
    except ValueError as exc:
        raise AlertError(
            f"direction: must be 'above' or 'below' (got {value!r})"
        ) from exc
