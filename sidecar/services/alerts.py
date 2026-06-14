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
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update

from sidecar.db.engine import session_scope
from sidecar.db.models import (
    AlertDirection,
    AlertMetric,
    Article,
    ArticleAsset,
    Asset,
    PriceAlert,
    PricePoint,
)

logger = logging.getLogger(__name__)


SENTIMENT_WINDOW_MIN_DAYS = 1
SENTIMENT_WINDOW_MAX_DAYS = 365
SENTIMENT_THRESHOLD_MIN = Decimal("-1")
SENTIMENT_THRESHOLD_MAX = Decimal("1")


class AlertError(ValueError):
    """Business-rule violation (not found, invalid input, etc.)."""


class AlertNotFoundError(AlertError):
    pass


class AssetNotFoundError(AlertError):
    pass


class AlreadyCrossedError(AlertError):
    """A price alert's threshold is already satisfied at creation time.

    Creating e.g. an ``above 150`` price alert while the price is already 200
    would fire on the very next scan — a false positive against stale data, not
    a genuine crossing. Only applies to PRICE alerts; sentiment alerts threshold
    a rolling mean where "already crossed" has no meaningful semantics.
    """


@dataclass(frozen=True)
class AlertOut:
    id: int
    asset_id: int
    symbol: str
    asset_name: str
    threshold: Decimal
    direction: AlertDirection
    metric: AlertMetric
    window_days: int | None
    is_active: bool
    triggered_at: datetime | None
    notified_at: datetime | None
    note: str | None
    created_at: datetime
    # Always populated for price alerts; populated for sentiment alerts
    # via ``current_value`` instead — this field stays as the latest
    # close so the UI can show "$X today" alongside any alert type.
    last_price: Decimal | None
    last_price_at: datetime | None
    # The metric's current observed value — for price alerts this is
    # the latest close; for sentiment alerts it's the rolling-mean
    # compound score over ``window_days``. None when no observation is
    # available (e.g. brand-new asset with no closes yet, or sentiment
    # alert against a window with no scored articles).
    current_value: Decimal | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_decimal(value: Any, *, field: str, allow_negative: bool = False) -> Decimal:
    """Coerce a free-form numeric input to a Decimal with validation.

    By default rejects ``<= 0`` (the price-threshold contract). When
    ``allow_negative`` is True the bounds check is skipped — used by
    the sentiment threshold path which accepts the [-1, +1] range.
    """
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise AlertError(f"{field}: not a valid decimal") from exc
    if not allow_negative and d <= 0:
        raise AlertError(f"{field}: must be > 0")
    return d


def _parse_metric(value: AlertMetric | str | None) -> AlertMetric:
    if value is None:
        return AlertMetric.PRICE
    if isinstance(value, AlertMetric):
        return value
    if not isinstance(value, str):
        raise AlertError(
            f"metric: expected string, got {type(value).__name__}"
        )
    try:
        return AlertMetric(value.lower())
    except ValueError as exc:
        raise AlertError(
            f"metric: must be 'price' or 'sentiment' (got {value!r})"
        ) from exc


def _validate_sentiment_threshold(threshold: Decimal) -> None:
    if threshold < SENTIMENT_THRESHOLD_MIN or threshold > SENTIMENT_THRESHOLD_MAX:
        raise AlertError(
            f"threshold: sentiment alerts require value in "
            f"[{SENTIMENT_THRESHOLD_MIN}, {SENTIMENT_THRESHOLD_MAX}], "
            f"got {threshold}"
        )


def _validate_window_days(window_days: int | None, *, required: bool) -> int | None:
    if not required:
        if window_days is not None:
            raise AlertError(
                "window_days: only valid for sentiment alerts"
            )
        return None
    if window_days is None:
        raise AlertError("window_days: required for sentiment alerts")
    if (
        window_days < SENTIMENT_WINDOW_MIN_DAYS
        or window_days > SENTIMENT_WINDOW_MAX_DAYS
    ):
        raise AlertError(
            f"window_days: must be in "
            f"[{SENTIMENT_WINDOW_MIN_DAYS}, {SENTIMENT_WINDOW_MAX_DAYS}]"
        )
    return window_days


def _compute_sentiment_for_asset(
    session: Any, asset_id: int, window_days: int
) -> Decimal | None:
    """Rolling-mean compound score for one asset over the last ``window_days``.

    Returns None when the window has zero scored articles — distinguishes
    "no signal" from "signal of 0.0" so alert evaluation can skip rather
    than misfire on a numerical neutral.
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    row = session.execute(
        select(func.avg(Article.sentiment))
        .join(ArticleAsset, ArticleAsset.article_id == Article.id)
        .where(
            ArticleAsset.asset_id == asset_id,
            Article.published_at >= cutoff,
            Article.sentiment.is_not(None),
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return Decimal(str(float(row)))


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


def _is_crossed(direction: AlertDirection, observed: Decimal, threshold: Decimal) -> bool:
    """Generic crossing check — works for both price and sentiment metrics
    since the directional semantics are identical (greater-equal / less-equal)."""
    if direction == AlertDirection.ABOVE:
        return observed >= threshold
    return observed <= threshold


def _hydrate(
    alert: PriceAlert,
    asset: Asset,
    latest: PricePoint | None,
    *,
    current_value: Decimal | None = None,
) -> AlertOut:
    """Build an ``AlertOut`` from the persisted row + caller-provided context.

    ``current_value`` is the metric's most recent observed value (latest
    close for price alerts, rolling-mean sentiment for sentiment alerts).
    Defaults to the latest close so the field is always populated for
    price alerts without a separate code path.
    """
    metric = AlertMetric(alert.metric) if alert.metric else AlertMetric.PRICE
    last_price = latest.close if latest is not None else None
    if current_value is None and metric == AlertMetric.PRICE:
        current_value = last_price
    return AlertOut(
        id=alert.id,
        asset_id=alert.asset_id,
        symbol=asset.symbol,
        asset_name=asset.name,
        threshold=alert.threshold,
        direction=alert.direction,
        metric=metric,
        window_days=alert.window_days,
        is_active=alert.is_active,
        triggered_at=alert.triggered_at,
        notified_at=alert.notified_at,
        note=alert.note,
        created_at=alert.created_at,
        last_price=last_price,
        last_price_at=latest.timestamp if latest is not None else None,
        current_value=current_value,
    )


def _hydrate_with_metric_value(
    session: Any, alert: PriceAlert, asset: Asset, latest: PricePoint | None
) -> AlertOut:
    """Hydrate path that fetches the sentiment current value when needed.

    Used by the read endpoints — list/get/pending — so the UI can show
    "current sentiment: -0.42" alongside the threshold without a second
    round-trip.
    """
    metric = AlertMetric(alert.metric) if alert.metric else AlertMetric.PRICE
    if metric != AlertMetric.SENTIMENT:
        return _hydrate(alert, asset, latest)
    if alert.window_days is None:  # pragma: no cover — schema invariant
        return _hydrate(alert, asset, latest, current_value=None)
    cv = _compute_sentiment_for_asset(session, asset.id, alert.window_days)
    return _hydrate(alert, asset, latest, current_value=cv)


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

        return [
            _hydrate_with_metric_value(s, alert, asset, latest.get(asset.id))
            for alert, asset in rows
        ]


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
        return _hydrate_with_metric_value(s, alert, asset, latest)


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
        return [
            _hydrate_with_metric_value(s, alert, asset, latest.get(asset.id))
            for alert, asset in rows
        ]


# ---------------------------------------------------------------------------
# Public API — mutations
# ---------------------------------------------------------------------------


def create_alert(
    *,
    asset_id: int,
    threshold: Any,
    direction: AlertDirection | str,
    note: str | None = None,
    metric: AlertMetric | str | None = None,
    window_days: int | None = None,
) -> AlertOut:
    metric_enum = _parse_metric(metric)
    is_sentiment = metric_enum == AlertMetric.SENTIMENT
    thr = _to_decimal(threshold, field="threshold", allow_negative=is_sentiment)
    if is_sentiment:
        _validate_sentiment_threshold(thr)
    window_days_validated = _validate_window_days(
        window_days, required=is_sentiment
    )
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

        latest = _latest_point_by_asset(s, [asset_id]).get(asset_id)
        # Reject a PRICE alert whose threshold is already crossed — it would
        # fire instantly against a possibly-stale bar. Sentiment alerts are
        # exempt (their observable is a rolling mean, not the last close).
        if not is_sentiment and latest is not None and _is_crossed(
            dir_enum, latest.close, thr
        ):
            word = "above" if dir_enum == AlertDirection.ABOVE else "below"
            raise AlreadyCrossedError(
                f"{asset.symbol} is already {word} {thr} "
                f"(last price {latest.close}); the alert would fire immediately"
            )

        alert = PriceAlert(
            asset_id=asset_id,
            threshold=thr,
            direction=dir_enum,
            metric=metric_enum.value,
            window_days=window_days_validated,
            is_active=True,
            note=note_clean,
        )
        s.add(alert)
        s.flush()
        return _hydrate_with_metric_value(s, alert, asset, latest)


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
            metric_enum = (
                AlertMetric(alert.metric) if alert.metric else AlertMetric.PRICE
            )
            is_sentiment = metric_enum == AlertMetric.SENTIMENT
            new_thr = _to_decimal(
                threshold, field="threshold", allow_negative=is_sentiment
            )
            if is_sentiment:
                _validate_sentiment_threshold(new_thr)
            alert.threshold = new_thr
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
        return _hydrate_with_metric_value(s, alert, asset, latest)


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
        # Atomic guarded stamp: only set ``notified_at`` while the row is still
        # triggered-and-unnotified. A concurrent ``update_alert(reset=True)``
        # that clears ``triggered_at`` between the read above and this write
        # makes the WHERE fail (0 rows), so we never produce the impossible
        # ``notified_at set / triggered_at null`` state that would silently
        # swallow the next genuine trigger.
        cast(
            CursorResult[Any],
            s.execute(
                update(PriceAlert)
                .where(
                    PriceAlert.id == alert_id,
                    PriceAlert.triggered_at.is_not(None),
                    PriceAlert.notified_at.is_(None),
                )
                .values(notified_at=now)
            ),
        )
        s.refresh(alert)
        latest = _latest_point_by_asset(s, [asset.id]).get(asset.id)
        return _hydrate_with_metric_value(s, alert, asset, latest)


# ---------------------------------------------------------------------------
# Scheduler-facing API
# ---------------------------------------------------------------------------


def check_alerts() -> int:
    """Scan active alerts for threshold crossings and stamp ``triggered_at``.

    Handles both metric types in one pass:
    - Price alerts compare the latest close against the threshold.
    - Sentiment alerts compute the rolling-mean compound score over the
      alert's ``window_days`` and compare that against the threshold.
      Sentiment alerts whose window has zero scored articles are
      skipped (no signal → no firing).

    Returns the number of alerts newly fired. Safe to call concurrently
    with CRUD writes — we only touch active+untriggered rows and always
    re-check ``triggered_at`` inside the transaction.
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

        # Pre-load latest prices for any price alerts in one batch — keeps
        # the loop tight and lets sentiment alerts skip the lookup.
        price_asset_ids = [
            alert.asset_id
            for alert in alerts
            if (alert.metric or AlertMetric.PRICE.value) == AlertMetric.PRICE.value
        ]
        latest = _latest_point_by_asset(s, list(set(price_asset_ids)))
        now = datetime.now(UTC)

        for alert in alerts:
            if alert.triggered_at is not None:  # defensive re-check
                continue
            metric = (
                AlertMetric(alert.metric) if alert.metric else AlertMetric.PRICE
            )
            if metric == AlertMetric.PRICE:
                point = latest.get(alert.asset_id)
                if point is None:
                    continue
                if _is_crossed(alert.direction, point.close, alert.threshold):
                    alert.triggered_at = now
                    fired += 1
            else:  # AlertMetric.SENTIMENT
                if alert.window_days is None:  # schema invariant; defensive
                    continue
                value = _compute_sentiment_for_asset(
                    s, alert.asset_id, alert.window_days
                )
                if value is None:
                    continue
                if _is_crossed(alert.direction, value, alert.threshold):
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
