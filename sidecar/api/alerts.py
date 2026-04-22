"""Price alert endpoints.

CRUD + the shell-poller handshake (``/pending-notifications/`` +
``/{id}/mark-notified``). See :mod:`sidecar.services.alerts` for the design
notes on why polling rather than SSE.

Error mapping:
- 404 — alert / asset not found
- 400 — validation error (bad threshold, bad direction, note too long)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sidecar.services.alerts import (
    AlertError,
    AlertNotFoundError,
    AlertOut,
    AssetNotFoundError,
    create_alert,
    delete_alert,
    get_alert,
    list_alerts,
    list_pending_notifications,
    mark_notified,
    update_alert,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


AlertDirectionLiteral = Literal["above", "below"]


class AlertOutModel(BaseModel):
    id: int
    asset_id: int
    symbol: str
    asset_name: str
    threshold: Decimal
    direction: AlertDirectionLiteral
    is_active: bool
    triggered_at: datetime | None
    notified_at: datetime | None
    note: str | None
    created_at: datetime
    last_price: Decimal | None
    last_price_at: datetime | None


class AlertListOut(BaseModel):
    count: int
    alerts: list[AlertOutModel]


class CreateAlertIn(BaseModel):
    asset_id: int
    threshold: Decimal = Field(gt=0)
    direction: AlertDirectionLiteral
    note: str | None = Field(default=None, max_length=256)


class UpdateAlertIn(BaseModel):
    threshold: Decimal | None = Field(default=None, gt=0)
    direction: AlertDirectionLiteral | None = None
    is_active: bool | None = None
    note: str | None = Field(default=None, max_length=256)
    reset: bool = False

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _to_model(a: AlertOut) -> AlertOutModel:
    return AlertOutModel(
        id=a.id,
        asset_id=a.asset_id,
        symbol=a.symbol,
        asset_name=a.asset_name,
        threshold=a.threshold,
        direction=a.direction.value,
        is_active=a.is_active,
        triggered_at=a.triggered_at,
        notified_at=a.notified_at,
        note=a.note,
        created_at=a.created_at,
        last_price=a.last_price,
        last_price_at=a.last_price_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=AlertListOut)
def list_alerts_route(
    asset_id: Annotated[int | None, Query()] = None,
    active_only: Annotated[bool, Query()] = False,
) -> AlertListOut:
    alerts = list_alerts(asset_id=asset_id, active_only=active_only)
    return AlertListOut(count=len(alerts), alerts=[_to_model(a) for a in alerts])


@router.get("/pending-notifications/", response_model=AlertListOut)
def list_pending_notifications_route() -> AlertListOut:
    alerts = list_pending_notifications()
    return AlertListOut(count=len(alerts), alerts=[_to_model(a) for a in alerts])


@router.post("/", response_model=AlertOutModel, status_code=201)
def create_alert_route(body: CreateAlertIn) -> AlertOutModel:
    try:
        return _to_model(
            create_alert(
                asset_id=body.asset_id,
                threshold=body.threshold,
                direction=body.direction,
                note=body.note,
            )
        )
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AlertError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{alert_id}/", response_model=AlertOutModel)
def get_alert_route(alert_id: int) -> AlertOutModel:
    try:
        return _to_model(get_alert(alert_id))
    except AlertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{alert_id}/", response_model=AlertOutModel)
def update_alert_route(alert_id: int, body: UpdateAlertIn) -> AlertOutModel:
    # Distinguish "note omitted" from "note=null". Pydantic v2 exposes
    # model_fields_set — if the client didn't send the key, don't touch it.
    sent_note = "note" in body.model_fields_set
    try:
        return _to_model(
            update_alert(
                alert_id,
                threshold=body.threshold,
                direction=body.direction,
                is_active=body.is_active,
                note=body.note,
                update_note=sent_note,
                reset=body.reset,
            )
        )
    except AlertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AlertError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{alert_id}/", status_code=204)
def delete_alert_route(alert_id: int) -> None:
    try:
        delete_alert(alert_id)
    except AlertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{alert_id}/mark-notified/", response_model=AlertOutModel)
def mark_notified_route(alert_id: int) -> AlertOutModel:
    try:
        return _to_model(mark_notified(alert_id))
    except AlertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AlertError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
