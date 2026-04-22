from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sidecar import scheduler as sched_mod
from sidecar.config import settings as env_settings
from sidecar.services.settings import (
    SETTINGS_SPECS,
    SettingType,
    apply_updates,
    load_effective_config,
    load_sources,
)

router = APIRouter(prefix="/api/config", tags=["config"])
logger = logging.getLogger(__name__)


class SettingOut(BaseModel):
    key: str
    type: SettingType
    label: str
    description: str
    value: Any  # int | bool | str, or null for secrets
    source: str  # "default" | "env" | "db"
    env_name: str | None
    min: int | None = None
    max: int | None = None
    has_value: bool


class ReadonlyConfigOut(BaseModel):
    db_path: str
    port: int
    log_level: str
    enable_scheduler: bool
    enable_seed: bool


class ConfigOut(BaseModel):
    settings: list[SettingOut]
    readonly: ReadonlyConfigOut


class ConfigUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: dict[str, Any] = Field(default_factory=dict)


def _build_response() -> ConfigOut:
    effective = load_effective_config()
    sources = load_sources()

    out_settings: list[SettingOut] = []
    for spec in SETTINGS_SPECS:
        val = effective[spec.key]
        is_secret = spec.type == SettingType.SECRET
        has_value = bool(val) if is_secret else True
        out_settings.append(
            SettingOut(
                key=spec.key,
                type=spec.type,
                label=spec.label,
                description=spec.description,
                value=None if is_secret else val,
                source=sources[spec.key],
                env_name=spec.env_name,
                min=spec.min,
                max=spec.max,
                has_value=has_value,
            )
        )

    return ConfigOut(
        settings=out_settings,
        readonly=ReadonlyConfigOut(
            db_path=env_settings.resolved_db_path(),
            port=env_settings.port,
            log_level=env_settings.log_level,
            enable_scheduler=env_settings.enable_scheduler,
            enable_seed=env_settings.enable_seed,
        ),
    )


@router.get("/", response_model=ConfigOut)
def get_config() -> ConfigOut:
    return _build_response()


@router.put("/", response_model=ConfigOut)
def put_config(payload: ConfigUpdateIn) -> ConfigOut:
    if not payload.updates:
        # Nothing to do — return current state.
        return _build_response()

    try:
        apply_updates(payload.updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Reconfigure the running scheduler best-effort. Log and continue on
    # failure so the API still returns the persisted state — the next
    # scheduler start will pick up the new config regardless.
    try:
        sched_mod.reconfigure()
    except Exception:
        logger.exception("scheduler reconfigure failed after settings update")

    return _build_response()
