"""Effective-config service — DB > env > default precedence.

The `settings` table stores user-provided overrides for a fixed set of keys
(see `SETTINGS_SPECS` below). `load_effective_config()` returns the merged
view that scheduler jobs and API endpoints should consult.

Design notes:
- env_settings (pydantic-settings) stays the source of truth for values that
  can't be mutated at runtime: port, db_path, log_level, enable_scheduler,
  enable_seed. Those never appear in this service.
- Mutable settings' env vars remain read on startup as the initial default,
  but once a DB row is written it takes precedence. To revert, delete the row
  (SECRET type supports this via empty-string PUT; other types require a
  DB-level reset — not exposed in the UI today).
- Secret values (currently just the FRED API key) are never returned verbatim
  from the API; only `has_value` is exposed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from sidecar.config import settings as env_settings
from sidecar.db.engine import session_scope
from sidecar.db.models import Setting

logger = logging.getLogger(__name__)


class SettingType(StrEnum):
    BOOL = "bool"
    INT = "int"
    STRING = "string"
    SECRET = "secret"


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: SettingType
    env_attr: str | None
    default: Any
    label: str
    description: str = ""
    min: int | None = None
    max: int | None = None

    @property
    def env_name(self) -> str | None:
        if self.env_attr is None:
            return None
        return f"FINTRACK_{self.env_attr.upper()}"


SETTINGS_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="ingest_prices.interval_minutes",
        type=SettingType.INT,
        env_attr="ingest_prices_interval_minutes",
        default=5,
        label="Price ingest interval (minutes)",
        description="How often yfinance is polled for new OHLCV bars.",
        min=1,
        max=1440,
    ),
    SettingSpec(
        key="ingest_crypto.enabled",
        type=SettingType.BOOL,
        env_attr="enable_crypto_job",
        default=False,
        label="Enable CoinGecko crypto ingest",
        description=(
            "Run a separate CoinGecko OHLC job in addition to yfinance. "
            "yfinance already covers BTC/ETH/SOL — this is a fallback/supplement."
        ),
    ),
    SettingSpec(
        key="ingest_crypto.interval_minutes",
        type=SettingType.INT,
        env_attr="ingest_crypto_interval_minutes",
        default=15,
        label="Crypto ingest interval (minutes)",
        description="Only used when CoinGecko crypto ingest is enabled.",
        min=1,
        max=1440,
    ),
    SettingSpec(
        key="ingest_macro.cron_hour_utc",
        type=SettingType.INT,
        env_attr="ingest_macro_cron_hour",
        default=6,
        label="Macro ingest hour (UTC)",
        description="Hour of day (0-23 UTC) when the daily FRED pull runs.",
        min=0,
        max=23,
    ),
    SettingSpec(
        key="fred_api_key",
        type=SettingType.SECRET,
        env_attr="fred_api_key",
        default="",
        label="FRED API key",
        description=(
            "Required for macro ingest. Free key at fred.stlouisfed.org. "
            "Leave blank to disable the macro job."
        ),
    ),
)

SPECS_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in SETTINGS_SPECS}


# ---------------------------------------------------------------------------
# Parsing / serialization
# ---------------------------------------------------------------------------


def _parse_stored(spec: SettingSpec, raw: str) -> Any:
    if spec.type == SettingType.BOOL:
        return raw.lower() in ("true", "1", "yes", "on")
    if spec.type == SettingType.INT:
        return int(raw)
    # STRING / SECRET
    return raw


def _serialize_for_storage(spec: SettingSpec, value: Any) -> str:
    if spec.type == SettingType.BOOL:
        return "true" if bool(value) else "false"
    return str(value)


def _env_value(spec: SettingSpec) -> Any:
    """Read the env-backed default for a setting, coerced to its typed form."""
    if spec.env_attr is None:
        return None
    raw = getattr(env_settings, spec.env_attr, None)
    if raw is None:
        return None
    # pydantic-settings has already coerced ints/bools; pass through.
    if spec.type == SettingType.STRING or spec.type == SettingType.SECRET:
        return raw if raw != "" else None
    return raw


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_and_serialize(key: str, raw_value: Any) -> str:
    """Validate `raw_value` for `key` and return the string to persist.

    Raises ValueError on type / bounds failure.
    """
    spec = SPECS_BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"unknown setting: {key}")

    if spec.type == SettingType.BOOL:
        if isinstance(raw_value, bool):
            v_bool = raw_value
        elif isinstance(raw_value, str):
            v_bool = raw_value.lower() in ("true", "1", "yes", "on")
        elif isinstance(raw_value, int):
            v_bool = bool(raw_value)
        else:
            raise ValueError(f"{key}: expected bool, got {type(raw_value).__name__}")
        return "true" if v_bool else "false"

    if spec.type == SettingType.INT:
        try:
            v_int = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: expected integer") from exc
        if spec.min is not None and v_int < spec.min:
            raise ValueError(f"{key}: must be >= {spec.min}")
        if spec.max is not None and v_int > spec.max:
            raise ValueError(f"{key}: must be <= {spec.max}")
        return str(v_int)

    # STRING / SECRET
    if not isinstance(raw_value, str):
        raise ValueError(f"{key}: expected string")
    return raw_value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_effective_config() -> dict[str, Any]:
    """Return the effective value for every known setting key.

    Precedence: DB row (if present) > env var (if set) > hardcoded default.
    """
    with session_scope() as s:
        db_map: dict[str, str] = {
            row.key: row.value for row in s.execute(select(Setting)).scalars()
        }

    config: dict[str, Any] = {}
    for spec in SETTINGS_SPECS:
        if spec.key in db_map:
            try:
                config[spec.key] = _parse_stored(spec, db_map[spec.key])
                continue
            except (ValueError, TypeError):
                logger.warning(
                    "settings: failed to parse stored value for %s (raw=%r); "
                    "falling back to env/default",
                    spec.key,
                    db_map[spec.key],
                )
        env_val = _env_value(spec)
        if env_val is not None:
            config[spec.key] = env_val
        else:
            config[spec.key] = spec.default
    return config


def load_sources() -> dict[str, str]:
    """Return the source ('db'|'env'|'default') for every known setting key."""
    with session_scope() as s:
        db_keys = {row for row in s.execute(select(Setting.key)).scalars()}

    sources: dict[str, str] = {}
    for spec in SETTINGS_SPECS:
        if spec.key in db_keys:
            sources[spec.key] = "db"
        elif _env_value(spec) is not None:
            sources[spec.key] = "env"
        else:
            sources[spec.key] = "default"
    return sources


def apply_updates(updates: dict[str, Any]) -> None:
    """Validate and persist a batch of setting updates atomically.

    For SECRET-type settings, an empty-string value deletes the DB row
    (reverts to env/default). For other types, empty string is stored as-is.

    Raises ValueError on any validation failure — the whole batch is rejected.
    """
    # Validate everything first so a bad value in the middle doesn't half-apply.
    unknown = set(updates) - SPECS_BY_KEY.keys()
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")

    prepared: list[tuple[SettingSpec, str | None]] = []  # None = delete
    for key, raw in updates.items():
        spec = SPECS_BY_KEY[key]
        if spec.type == SettingType.SECRET and (raw is None or raw == ""):
            prepared.append((spec, None))
            continue
        prepared.append((spec, validate_and_serialize(key, raw)))

    now = datetime.now(UTC)
    with session_scope() as s:
        for spec, serialized in prepared:
            if serialized is None:
                s.execute(delete(Setting).where(Setting.key == spec.key))
                continue
            stmt = (
                sqlite_insert(Setting)
                .values(key=spec.key, value=serialized, updated_at=now)
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"value": serialized, "updated_at": now},
                )
            )
            s.execute(stmt)


def reset_to_default(key: str) -> None:
    """Delete the DB override for a single key (revert to env/default)."""
    if key not in SPECS_BY_KEY:
        raise ValueError(f"unknown setting: {key}")
    with session_scope() as s:
        s.execute(delete(Setting).where(Setting.key == key))
